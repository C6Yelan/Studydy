from __future__ import annotations

import math
from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from .contract_hashing import (
    _valid_input_record_hash,
    record_canonical_sha256,
)
from .contract_schema_metadata import CONTEXT_POLICY_VERSION
from .contract_schema_values import (
    _integer,
    _non_empty_string,
)


MAX_CONTEXT_UNITS = 3

MAX_CONTEXT_CODE_POINTS = 1200

MINIMUM_HORIZONTAL_OVERLAP = 0.60

MINIMUM_VERTICAL_GAP_LIMIT = 24.0

FONT_GAP_MULTIPLIER = 2.5

_FORBIDDEN_CONTEXT_KINDS = {
    "caption",
    "figure",
    "heading",
    "image",
    "omission",
    "table",
    "unknown",
}

_SENTENCE_TERMINALS = ("。", ".", "！", "!", "？", "?")

def _rebuild_contexts(
    contexts: list[Any],
    origins: list[Any],
    source_units: list[Any],
) -> list[Any]:
    """只替可驗證的原始 anchor context 擴充局部內容；身分、雜湊或定位不明時保留原值。"""
    if not all(isinstance(unit, Mapping) for unit in source_units):
        return deepcopy(contexts)
    ordered_units = sorted(
        source_units,
        key=lambda unit: (
            _sort_value(unit.get("material_id")),
            _sort_value(unit.get("pdf_page")),
            _sort_value(unit.get("block_id")),
            _sort_value(unit.get("reading_order")),
            _sort_value(unit.get("layout_unit_id")),
        ),
    )
    unit_indexes = {
        unit["layout_unit_id"]: index
        for index, unit in enumerate(ordered_units)
    }
    output = []
    for raw_context in contexts:
        context = deepcopy(raw_context)
        if not isinstance(context, dict):
            output.append(context)
            continue
        context_id = context.get("context_id")
        primary_ids = context.get("primary_candidate_ids")
        if (
            not _valid_input_record_hash(context)
            or not _non_empty_string(context_id)
            or not isinstance(primary_ids, list)
            or not all(_non_empty_string(value) for value in primary_ids)
        ):
            output.append(context)
            continue
        anchor_ids = {
            origin.get("layout_unit_id")
            for origin in origins
            if isinstance(origin, Mapping)
            and _valid_input_record_hash(origin)
            and origin.get("safe_context_id") == context_id
            and origin.get("candidate_id") in primary_ids
            and _non_empty_string(origin.get("layout_unit_id"))
        }
        if len(anchor_ids) != 1:
            output.append(context)
            continue
        anchor_id = next(iter(anchor_ids))
        anchor_index = unit_indexes.get(anchor_id)
        if anchor_index is None:
            output.append(context)
            continue
        anchor = ordered_units[anchor_index]
        if not _matches_provider_anchor_context(context, anchor):
            output.append(context)
            continue
        selected, previous_reason, next_reason = _bounded_context_units(
            ordered_units,
            anchor_index,
        )
        if not selected:
            output.append(context)
            continue
        texts = [unit.get("text") for unit in selected]
        if not all(isinstance(text, str) and text for text in texts):
            output.append(context)
            continue
        normalized_texts = [
            unit.get("normalized_text", unit["text"])
            for unit in selected
        ]
        if not all(
            isinstance(text, str) and text
            for text in normalized_texts
        ):
            output.append(context)
            continue
        limits = sorted(
            {
                reason
                for reason in (previous_reason, next_reason)
                if reason
                in {
                    "anchor_overflow",
                    "code_point_limit",
                    "unit_limit",
                }
            }
        )
        context.update(
            {
                "text": "\n".join(texts),
                "normalized_text": "\n".join(normalized_texts),
                "layout_unit_refs": [
                    {"layout_unit_id": unit["layout_unit_id"]}
                    for unit in selected
                ],
                "context_scope": CONTEXT_POLICY_VERSION,
                "start_locator": selected[0]["locator"],
                "end_locator": selected[-1]["locator"],
                "boundary_reason": {
                    "previous": previous_reason,
                    "next": next_reason,
                    "limits": limits,
                },
                "code_point_count": len("\n".join(texts)),
            }
        )
        context["canonical_sha256"] = record_canonical_sha256(context)
        output.append(context)
    return output

def _matches_provider_anchor_context(
    context: Mapping[str, Any],
    anchor: Mapping[str, Any],
) -> bool:
    """確認 provider context 仍是只包含 anchor 的原始形態，避免重建已被改動的內容。"""
    anchor_id = anchor.get("layout_unit_id")
    text = anchor.get("text")
    locator = anchor.get("locator")
    return (
        _non_empty_string(anchor_id)
        and _non_empty_string(text)
        and locator is not None
        and context.get("text") == text
        and context.get("normalized_text") == text
        and context.get("layout_unit_refs")
        == [{"layout_unit_id": anchor_id}]
        and context.get("context_scope") == CONTEXT_POLICY_VERSION
        and context.get("start_locator") == locator
        and context.get("end_locator") == locator
        and context.get("code_point_count") == len(text)
    )

def _bounded_context_units(
    units: list[Mapping[str, Any]],
    anchor_index: int,
) -> tuple[list[Mapping[str, Any]], str, str]:
    """從 anchor 向相鄰單元擴張，並在邊界或大小上限前停止且回傳兩側原因。"""
    anchor = units[anchor_index]
    if (
        anchor.get("unit_kind") != "text"
        or not isinstance(anchor.get("text"), str)
        or not anchor["text"]
    ):
        return [], "unknown_boundary", "unknown_boundary"
    selected = [anchor]
    first_index = anchor_index
    last_index = anchor_index

    if len(anchor["text"]) <= MAX_CONTEXT_CODE_POINTS:
        previous_index = anchor_index - 1
        if previous_index >= 0:
            previous = units[previous_index]
            if (
                _adjacent_boundary(previous, selected[0]) is None
                and _fits_context([previous, *selected])
            ):
                selected.insert(0, previous)
                first_index = previous_index

        next_index = anchor_index + 1
        while next_index < len(units) and len(selected) < MAX_CONTEXT_UNITS:
            candidate = units[next_index]
            if _adjacent_boundary(selected[-1], candidate) is not None:
                break
            if not _fits_context([*selected, candidate]):
                break
            selected.append(candidate)
            last_index = next_index
            next_index += 1

        previous_index = first_index - 1
        while previous_index >= 0 and len(selected) < MAX_CONTEXT_UNITS:
            candidate = units[previous_index]
            if _adjacent_boundary(candidate, selected[0]) is not None:
                break
            if not _fits_context([candidate, *selected]):
                break
            selected.insert(0, candidate)
            first_index = previous_index
            previous_index -= 1

    previous_reason = _edge_reason(
        units,
        first_index,
        first_index - 1,
        before=True,
        selected=selected,
    )
    next_reason = _edge_reason(
        units,
        last_index,
        last_index + 1,
        before=False,
        selected=selected,
    )
    if len(anchor["text"]) > MAX_CONTEXT_CODE_POINTS:
        previous_reason = "anchor_overflow"
        next_reason = "anchor_overflow"
    return selected, previous_reason, next_reason

def _edge_reason(
    units: list[Mapping[str, Any]],
    edge_index: int,
    neighbor_index: int,
    *,
    before: bool,
    selected: list[Mapping[str, Any]],
) -> str:
    """說明 context 一側停止擴張的原因，區分來源邊界、結構邊界與大小限制。"""
    if neighbor_index < 0:
        return "material_start"
    if neighbor_index >= len(units):
        return "material_end"
    neighbor = units[neighbor_index]
    edge = units[edge_index]
    reason = (
        _adjacent_boundary(neighbor, edge)
        if before
        else _adjacent_boundary(edge, neighbor)
    )
    if reason is not None:
        return reason
    if len(selected) >= MAX_CONTEXT_UNITS:
        return "unit_limit"
    proposed = (
        [neighbor, *selected]
        if before
        else [*selected, neighbor]
    )
    if not _fits_context(proposed):
        return "code_point_limit"
    return "bounded"

def _adjacent_boundary(
    left: Mapping[str, Any],
    right: Mapping[str, Any],
) -> str | None:
    """比較相鄰單元的來源、順序、文字結構與幾何，回傳第一個不可跨越的邊界。"""
    for field, reason in (
        ("material_id", "material_boundary"),
        ("pdf_page", "page_boundary"),
        ("block_id", "block_boundary"),
    ):
        left_value = left.get(field)
        right_value = right.get(field)
        if left_value is None or right_value is None:
            return "unknown_boundary"
        if left_value != right_value:
            return reason

    left_column = left.get("column_id")
    right_column = right.get("column_id")
    if (
        not _non_empty_string(left_column)
        or not _non_empty_string(right_column)
    ):
        return "unknown_column"
    if left_column != right_column:
        return "column_boundary"
    left_order = left.get("reading_order")
    right_order = right.get("reading_order")
    if (
        not _integer(left_order)
        or not _integer(right_order)
        or right_order != left_order + 1
    ):
        return "non_consecutive_order"
    if (
        left.get("unit_kind") in _FORBIDDEN_CONTEXT_KINDS
        or right.get("unit_kind") in _FORBIDDEN_CONTEXT_KINDS
        or left.get("unit_kind") != "text"
        or right.get("unit_kind") != "text"
    ):
        return "structural_boundary"
    if (
        left.get("heading_transition_after") is True
        or right.get("heading_transition_before") is True
    ):
        return "heading_boundary"
    intervening_kind = left.get("intervening_kind_after")
    if intervening_kind in _FORBIDDEN_CONTEXT_KINDS:
        return "structural_boundary"
    for value in (
        left.get("boundary_after"),
        right.get("boundary_before"),
    ):
        if value not in {None, "none", "safe"}:
            return "unknown_boundary"
    left_text = left.get("text")
    if (
        isinstance(left_text, str)
        and left_text.rstrip().endswith(_SENTENCE_TERMINALS)
    ):
        return "sentence_terminal"

    left_bbox = left.get("bbox")
    right_bbox = right.get("bbox")
    if (
        not _valid_context_bbox(left_bbox)
        or not _valid_context_bbox(right_bbox)
    ):
        return "unknown_geometry"
    overlap = max(
        0.0,
        min(left_bbox[2], right_bbox[2])
        - max(left_bbox[0], right_bbox[0]),
    )
    minimum_width = min(
        left_bbox[2] - left_bbox[0],
        right_bbox[2] - right_bbox[0],
    )
    if overlap / minimum_width < MINIMUM_HORIZONTAL_OVERLAP:
        return "horizontal_overlap"
    gap = max(0.0, right_bbox[1] - left_bbox[3])
    font_sizes = [
        value
        for value in (
            left.get("font_size_max"),
            right.get("font_size_max"),
        )
        if _context_finite_number(value) and value > 0
    ]
    if any(
        value is not None
        and (not _context_finite_number(value) or value <= 0)
        for value in (
            left.get("font_size_max"),
            right.get("font_size_max"),
        )
    ):
        return "unknown_geometry"
    gap_limit = max(
        FONT_GAP_MULTIPLIER * max(font_sizes, default=0.0),
        MINIMUM_VERTICAL_GAP_LIMIT,
    )
    if gap > gap_limit:
        return "vertical_gap"
    return None

def _fits_context(units: list[Mapping[str, Any]]) -> bool:
    """確認 context 單元未超過數量與字數上限，且每筆都有可用文字。"""
    texts = [unit.get("text") for unit in units]
    return (
        len(units) <= MAX_CONTEXT_UNITS
        and all(isinstance(text, str) and text for text in texts)
        and len("\n".join(texts)) <= MAX_CONTEXT_CODE_POINTS
    )

def _context_finite_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )

def _valid_context_bbox(value: Any) -> bool:
    return (
        isinstance(value, list)
        and len(value) == 4
        and all(_context_finite_number(coordinate) for coordinate in value)
        and value[2] > value[0]
        and value[3] > value[1]
    )

def _sort_value(value: Any) -> tuple[int, int | str]:
    if isinstance(value, int) and not isinstance(value, bool):
        return (0, value)
    if isinstance(value, str):
        return (1, value)
    return (2, "")
