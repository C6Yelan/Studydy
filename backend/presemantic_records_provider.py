from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from material_runtime_files import canonical_json_bytes
from handoff.contract_hashing import record_canonical_sha256


# 定義 provider package input 的 schema contract。
PACKAGE_INPUT_SCHEMA_VERSION = "presemantic-records-package-input/v1"
# 定義 presemantic records artifact 的 schema contract。
RECORDS_SCHEMA_VERSION = "presemantic-records/v1"
# 定義 normalized layout source artifact 的 schema contract。
NORMALIZED_SOURCE_SCHEMA_VERSION = "normalized-layout-source/v1"
# 識別 same-material local layout 的 context policy 版本。
CONTEXT_POLICY_VERSION = "same-material-local-layout-v1"
# 識別 literal projection 的 projection policy 版本。
PROJECTION_POLICY_VERSION = "literal-projection/v1"


def build_presemantic_records_package_input(
    normalized_blocks: Mapping[str, Any],
    material_id: str,
) -> dict[str, Any]:
    """把指定教材的 normalized blocks 轉成 deterministic records package input。

    此階段只建立可回查來源的 literal records 與 source failure evidence，
    不判斷 Concept 語意，也不把 literal fallback 視為最終 candidate authority。
    """
    if normalized_blocks.get("schema_version") != "normalized-material-blocks/v2":
        raise ValueError("normalized_blocks_schema_mismatch")
    if not isinstance(material_id, str) or not material_id:
        raise ValueError("material_id_invalid")
    materials = normalized_blocks.get("materials")
    if not isinstance(materials, list):
        raise ValueError("normalized_materials_invalid")
    matches = [
        material
        for material in materials
        if isinstance(material, Mapping)
        and material.get("material_id") == material_id
    ]
    if len(matches) != 1:
        raise ValueError("material_identity_unresolved")

    material = matches[0]
    case_id = _required_string(material, "case_id")
    artifact_ref = _required_string(material, "artifact_ref")
    blocks = material.get("blocks")
    if not isinstance(blocks, list):
        raise ValueError("material_blocks_invalid")

    normalized_units: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    origins: list[dict[str, Any]] = []
    contexts: list[dict[str, Any]] = []
    evidence_records: list[dict[str, Any]] = []
    source_failures: list[dict[str, Any]] = []
    block_identities: set[tuple[str, int, str]] = set()
    unit_ids: set[str] = set()

    ordered_blocks = sorted(
        blocks,
        key=lambda block: _block_sort_key(block),
    )
    for raw_block in ordered_blocks:
        if not isinstance(raw_block, Mapping):
            raise ValueError("normalized_block_invalid")
        block = raw_block
        if (
            block.get("material_id") != material_id
            or block.get("case_id") != case_id
            or block.get("artifact_ref") != artifact_ref
        ):
            raise ValueError("normalized_block_identity_mismatch")
        block_id = _required_string(block, "block_id")
        locator = block.get("locator")
        if not isinstance(locator, Mapping):
            raise ValueError("normalized_block_locator_invalid")
        pdf_page = locator.get("pdf_page")
        source_ref = locator.get("source_ref")
        if (
            not isinstance(pdf_page, int)
            or isinstance(pdf_page, bool)
            or pdf_page < 1
            or not isinstance(source_ref, str)
            or not source_ref
            or block.get("pdf_page") != pdf_page
            or block.get("source_ref") != source_ref
        ):
            raise ValueError("normalized_block_locator_invalid")
        block_identity = (block_id, pdf_page, source_ref)
        if block_identity in block_identities:
            raise ValueError("normalized_block_identity_ambiguous")
        block_identities.add(block_identity)
        selection_status = block.get("selection_status")
        if selection_status not in {"selected", "failed"}:
            raise ValueError("normalized_block_status_invalid")
        provenance = block.get("provenance")
        if not isinstance(provenance, Mapping) or not provenance:
            raise ValueError("normalized_block_provenance_invalid")

        block_reasons = _reason_list(block.get("reasons"))
        omissions = block.get("layout_unit_omissions", [])
        if not isinstance(omissions, list):
            raise ValueError("layout_unit_omissions_invalid")
        omission_reasons: set[str] = set()
        ordered_omissions = sorted(omissions, key=_omission_sort_key)
        omission_orders = [
            omission.get("locator", {}).get("omission_order")
            if isinstance(omission, Mapping)
            and isinstance(omission.get("locator"), Mapping)
            else None
            for omission in ordered_omissions
        ]
        if omission_orders != list(range(len(ordered_omissions))):
            raise ValueError("layout_unit_omissions_order_invalid")
        for omission in ordered_omissions:
            failure = _source_failure_from_omission(
                omission,
                material_id=material_id,
                case_id=case_id,
                artifact_ref=artifact_ref,
                block_id=block_id,
                pdf_page=pdf_page,
                source_ref=source_ref,
            )
            omission_reasons.update(failure["source_failure_reasons"])
            source_failures.append(failure)

        remaining_reasons = sorted(set(block_reasons) - omission_reasons)
        if selection_status == "failed" and not block_reasons:
            raise ValueError("failed_block_reasons_missing")
        if selection_status == "failed" or remaining_reasons:
            failure_reasons = (
                block_reasons
                if selection_status == "failed"
                else remaining_reasons
            )
            source_failures.append(
                _source_failure_from_block(
                    material_id=material_id,
                    case_id=case_id,
                    artifact_ref=artifact_ref,
                    block_id=block_id,
                    pdf_page=pdf_page,
                    source_ref=source_ref,
                    source_status=selection_status,
                    reasons=failure_reasons,
                    provenance=provenance,
                )
            )
        if selection_status == "failed":
            continue

        units = block.get("layout_units")
        if not isinstance(units, list) or not units:
            raise ValueError("selected_block_layout_units_invalid")
        ordered_units = sorted(units, key=_unit_sort_key)
        reading_orders = [
            unit.get("reading_order")
            if isinstance(unit, Mapping)
            else None
            for unit in ordered_units
        ]
        if reading_orders != list(range(len(ordered_units))):
            raise ValueError("layout_unit_reading_order_invalid")
        for unit in ordered_units:
            source_unit = _normalized_source_unit(
                unit,
                material_id=material_id,
                block_id=block_id,
                pdf_page=pdf_page,
                source_ref=source_ref,
            )
            unit_id = source_unit["layout_unit_id"]
            if unit_id in unit_ids:
                raise ValueError("layout_unit_identity_ambiguous")
            unit_ids.add(unit_id)
            normalized_units.append(source_unit)
            if source_unit["unit_kind"] != "text":
                continue
            literal_span = _bounded_literal_span(source_unit["text"])
            if literal_span is None:
                continue
            (
                candidate,
                origin,
                context,
                evidence,
            ) = _literal_records(source_unit, literal_span)
            candidates.append(candidate)
            origins.append(origin)
            contexts.append(context)
            evidence_records.append(evidence)

    normalized_units.sort(key=_source_unit_sort_key)
    candidates.sort(key=lambda record: record["candidate_id"])
    origins.sort(key=lambda record: record["origin_id"])
    contexts.sort(key=lambda record: record["context_id"])
    evidence_records.sort(key=lambda record: record["evidence_id"])
    source_failures.sort(key=lambda record: record["source_failure_id"])

    normalized_source_payload = {
        "material_id": material_id,
        "case_id": case_id,
        "artifact_ref": artifact_ref,
        "layout_units": normalized_units,
    }
    normalized_source = {
        "artifact_id": _stable_id(
            "normalized-layout-source",
            {
                "artifact_ref": artifact_ref,
                "case_id": case_id,
                "material_id": material_id,
            },
        ),
        "schema_version": NORMALIZED_SOURCE_SCHEMA_VERSION,
        "material_id": material_id,
        "raw_sha256": _canonical_sha256(normalized_source_payload),
        "locator": f"studydy://handoff/normalized-layout-source/{material_id}",
        "layout_units": normalized_units,
    }
    normalized_source_binding = {
        field: normalized_source[field]
        for field in (
            "artifact_id",
            "schema_version",
            "material_id",
            "raw_sha256",
            "locator",
        )
    }

    records_artifact = {
        "artifact_id": _stable_id(
            "presemantic-records",
            {
                "artifact_ref": artifact_ref,
                "case_id": case_id,
                "material_id": material_id,
            },
        ),
        "schema_version": RECORDS_SCHEMA_VERSION,
        "material_id": material_id,
        "locator": f"studydy://handoff/presemantic-records/{material_id}",
        "candidates": candidates,
        "origins": origins,
        "contexts": contexts,
        "evidence_records": evidence_records,
        "projection_records": [],
        "source_failures": source_failures,
    }
    deterministic_records_binding = {
        "artifact_id": records_artifact["artifact_id"],
        "schema_version": records_artifact["schema_version"],
        "material_id": material_id,
        "raw_sha256": _canonical_sha256(records_artifact),
        "locator": records_artifact["locator"],
    }
    context_policy_binding = {
        "policy_version": CONTEXT_POLICY_VERSION,
        "canonical_sha256": _canonical_sha256(
            {
                "boundary": "single-layout-unit-only",
                "policy_version": CONTEXT_POLICY_VERSION,
            }
        ),
    }
    projection_policy_binding = {
        "policy_version": PROJECTION_POLICY_VERSION,
        "canonical_sha256": _canonical_sha256(
            {
                "emitted_projection_records": 0,
                "policy_version": PROJECTION_POLICY_VERSION,
            }
        ),
    }
    return {
        "schema_version": PACKAGE_INPUT_SCHEMA_VERSION,
        "package_id": _stable_id(
            "presemantic-records-package-input",
            {
                "material_id": material_id,
                "normalized_source_sha256": normalized_source["raw_sha256"],
                "records_sha256": deterministic_records_binding["raw_sha256"],
            },
        ),
        "material_id": material_id,
        "normalized_source": normalized_source,
        "normalized_source_binding": normalized_source_binding,
        "deterministic_records_binding": deterministic_records_binding,
        "context_policy_binding": context_policy_binding,
        "projection_policy_binding": projection_policy_binding,
        "records_artifact": records_artifact,
    }


def _normalized_source_unit(
    unit: Any,
    *,
    material_id: str,
    block_id: str,
    pdf_page: int,
    source_ref: str,
) -> dict[str, Any]:
    """驗證 layout unit 的來源身分與結構，轉成封存流程使用的統一格式。"""
    if not isinstance(unit, Mapping):
        raise ValueError("layout_unit_invalid")
    unit_id = _required_string(unit, "layout_unit_id")
    if unit.get("parent_block_id") != block_id:
        raise ValueError("layout_unit_block_mismatch")
    reading_order = unit.get("reading_order")
    bbox = unit.get("bbox")
    kind = unit.get("kind")
    if (
        not isinstance(reading_order, int)
        or isinstance(reading_order, bool)
        or reading_order < 0
        or not _valid_bbox(bbox)
        or kind not in {"text", "image"}
    ):
        raise ValueError("layout_unit_structure_invalid")
    output = {
        "layout_unit_id": unit_id,
        "material_id": material_id,
        "block_id": block_id,
        "source_ref": source_ref,
        "pdf_page": pdf_page,
        "reading_order": reading_order,
        "bbox": list(bbox),
        "unit_kind": kind,
        "locator": f"{source_ref}#layout-unit={unit_id}",
    }
    if kind == "text":
        text = unit.get("text")
        style = unit.get("style_summary")
        if (
            not isinstance(text, str)
            or not text.strip()
            or not isinstance(style, Mapping)
            or "font_size_max" not in style
        ):
            raise ValueError("layout_unit_text_invalid")
        font_size_max = style.get("font_size_max")
        if font_size_max is not None and (
            not isinstance(font_size_max, (int, float))
            or isinstance(font_size_max, bool)
            or not math.isfinite(font_size_max)
            or font_size_max <= 0
        ):
            raise ValueError("layout_unit_font_max_invalid")
        output["text"] = text
        output["font_size_max"] = font_size_max
    return output


def _literal_records(
    unit: Mapping[str, Any],
    literal_span: Mapping[str, int],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    """從 bounded literal span 建立互相綁定的 candidate、origin、context 與 evidence。"""
    text = unit["text"]
    identity = {
        "layout_unit_id": unit["layout_unit_id"],
        "material_id": unit["material_id"],
    }
    candidate_id = _stable_id("candidate", identity)
    origin_id = _stable_id("origin", identity)
    context_id = _stable_id("context", identity)
    evidence_id = _stable_id("evidence", identity)
    surface = text[literal_span["start"]:literal_span["end"]]
    candidate = {
        "candidate_id": candidate_id,
        "material_id": unit["material_id"],
        "surface": surface,
        "normalized_surface": surface,
        "extraction_methods": ["literal"],
        "origin_ids": [origin_id],
        "context_ids": [context_id],
        "evidence_ids": [evidence_id],
        "projection_ids": [],
        "support_summary": {
            "flags": {"literal": True},
            "origin_count": 1,
            "context_count": 1,
            "hard_negative_gate": False,
        },
        "build_status": "valid",
        "failure_codes": [],
    }
    origin = {
        "origin_id": origin_id,
        "candidate_id": candidate_id,
        "material_id": unit["material_id"],
        "block_id": unit["block_id"],
        "layout_unit_id": unit["layout_unit_id"],
        "source_ref": unit["source_ref"],
        "pdf_page": unit["pdf_page"],
        "reading_order": unit["reading_order"],
        "bbox": deepcopy(unit["bbox"]),
        "literal_span": dict(literal_span),
        "safe_context_id": context_id,
        "layout_unit_text_sha256": hashlib.sha256(text.encode()).hexdigest(),
    }
    context = {
        "context_id": context_id,
        "material_id": unit["material_id"],
        "text": text,
        "normalized_text": text,
        "layout_unit_refs": [
            {"layout_unit_id": unit["layout_unit_id"]},
        ],
        "primary_candidate_ids": [candidate_id],
        "context_scope": CONTEXT_POLICY_VERSION,
        "start_locator": unit["locator"],
        "end_locator": unit["locator"],
        "boundary_reason": {
            "previous": "unknown",
            "next": "unknown",
            "limits": ["single_layout_unit"],
        },
        "evidence_ids": [evidence_id],
        "code_point_count": len(text),
    }
    evidence = {
        "evidence_id": evidence_id,
        "material_id": unit["material_id"],
        "evidence_kind": "candidate_literal",
        "statement": text,
        "normalized_statement": text,
        "literal_surface": surface,
        "literal_span": dict(literal_span),
        "candidate_ids": [candidate_id],
        "context_ids": [context_id],
        "origin_ids": [origin_id],
    }
    for record in (candidate, origin, context, evidence):
        record["canonical_sha256"] = _record_sha256(record)
    return candidate, origin, context, evidence


def _bounded_literal_span(text: str) -> dict[str, int] | None:
    """尋找長度 2 至 64 且不等於整個 layout unit 的第一段非空白文字。"""
    for match in re.finditer(r"\S+", text):
        start = match.start()
        end = min(match.end(), start + 64)
        if end - start < 2:
            continue
        if start == 0 and end == len(text):
            continue
        return {"start": start, "end": end}
    return None


def _source_failure_from_omission(
    omission: Any,
    *,
    material_id: str,
    case_id: str,
    artifact_ref: str,
    block_id: str,
    pdf_page: int,
    source_ref: str,
) -> dict[str, Any]:
    """把局部 layout unit omission 轉成保留 locator 與 provenance 的失敗紀錄。"""
    if not isinstance(omission, Mapping):
        raise ValueError("layout_unit_omission_invalid")
    identity = omission.get("identity")
    expected_identity = {
        "material_id": material_id,
        "case_id": case_id,
        "artifact_ref": artifact_ref,
        "block_id": block_id,
        "pdf_page": pdf_page,
        "source_ref": source_ref,
    }
    reason = omission.get("reason")
    omission_id = omission.get("layout_unit_id")
    if (
        identity != expected_identity
        or not isinstance(reason, str)
        or not reason
        or not isinstance(omission_id, str)
        or not omission_id
        or omission.get("status") != "omitted"
        or not isinstance(omission.get("locator"), Mapping)
        or not isinstance(omission.get("provenance"), Mapping)
        or not omission["provenance"]
    ):
        raise ValueError("layout_unit_omission_invalid")
    failure = {
        "source_failure_id": _stable_id(
            "source-failure",
            {"layout_unit_id": omission_id, "reason": reason},
        ),
        "material_id": material_id,
        "block_id": block_id,
        "layout_unit_id": omission_id,
        "source_ref": source_ref,
        "pdf_page": pdf_page,
        "source_status": "omitted",
        "source_failure_reasons": [reason],
        "locator": deepcopy(omission["locator"]),
        "provenance": deepcopy(omission["provenance"]),
    }
    failure["canonical_sha256"] = _record_sha256(failure)
    return failure


def _source_failure_from_block(
    *,
    material_id: str,
    case_id: str,
    artifact_ref: str,
    block_id: str,
    pdf_page: int,
    source_ref: str,
    source_status: str,
    reasons: list[str],
    provenance: Any,
) -> dict[str, Any]:
    """把整個 block 的來源問題轉成可依教材、頁面及 artifact 回查的失敗紀錄。"""
    if not reasons:
        raise ValueError("source_failure_reasons_missing")
    failure = {
        "source_failure_id": _stable_id(
            "source-failure",
            {
                "artifact_ref": artifact_ref,
                "block_id": block_id,
                "case_id": case_id,
                "material_id": material_id,
                "pdf_page": pdf_page,
                "reasons": reasons,
                "source_ref": source_ref,
            },
        ),
        "material_id": material_id,
        "block_id": block_id,
        "layout_unit_id": None,
        "source_ref": source_ref,
        "pdf_page": pdf_page,
        "source_status": source_status,
        "source_failure_reasons": reasons,
        "locator": {
            "pdf_page": pdf_page,
            "source_ref": source_ref,
        },
        "provenance": deepcopy(provenance),
    }
    failure["canonical_sha256"] = _record_sha256(failure)
    return failure


def _required_string(value: Mapping[str, Any], field: str) -> str:
    """取得必要的非空字串欄位，缺少或格式錯誤時停止建置。"""
    result = value.get(field)
    if not isinstance(result, str) or not result:
        raise ValueError(f"{field}_invalid")
    return result


def _reason_list(value: Any) -> list[str]:
    """驗證 failure reasons，去除重複值後以固定順序回傳。"""
    if not isinstance(value, list) or not all(
        isinstance(reason, str) and reason
        for reason in value
    ):
        raise ValueError("source_reasons_invalid")
    return sorted(set(value))


def _valid_bbox(value: Any) -> bool:
    """判斷 bbox 是否為四個有限座標，且能形成正寬度與正高度的範圍。"""
    return (
        isinstance(value, list)
        and len(value) == 4
        and all(
            isinstance(coordinate, (int, float))
            and not isinstance(coordinate, bool)
            and math.isfinite(coordinate)
            for coordinate in value
        )
        and value[2] > value[0]
        and value[3] > value[1]
    )


def _block_sort_key(block: Any) -> tuple[Any, ...]:
    """依 PDF 頁碼、block ID 與 source reference 建立固定的 block 排序鍵。"""
    if not isinstance(block, Mapping):
        raise ValueError("normalized_block_invalid")
    locator = block.get("locator")
    if not isinstance(locator, Mapping):
        raise ValueError("normalized_block_locator_invalid")
    return (
        _sort_value(locator.get("pdf_page")),
        _sort_value(block.get("block_id")),
        _sort_value(locator.get("source_ref")),
    )


def _unit_sort_key(unit: Any) -> tuple[Any, ...]:
    """依 reading order 與 layout unit ID 建立固定的 unit 排序鍵。"""
    if not isinstance(unit, Mapping):
        raise ValueError("layout_unit_invalid")
    return (
        _sort_value(unit.get("reading_order")),
        _sort_value(unit.get("layout_unit_id")),
    )


def _source_unit_sort_key(unit: Mapping[str, Any]) -> tuple[Any, ...]:
    """依頁面、block、閱讀順序與 unit ID 排列已正規化的 source units。"""
    return (
        unit["pdf_page"],
        unit["block_id"],
        unit["reading_order"],
        unit["layout_unit_id"],
    )


def _omission_sort_key(omission: Any) -> tuple[Any, ...]:
    """依 omission order 與 layout unit ID 建立固定的 omission 排序鍵。"""
    if not isinstance(omission, Mapping):
        raise ValueError("layout_unit_omission_invalid")
    locator = omission.get("locator")
    if not isinstance(locator, Mapping):
        raise ValueError("layout_unit_omission_invalid")
    return (
        _sort_value(locator.get("omission_order")),
        _sort_value(omission.get("layout_unit_id")),
    )


def _sort_value(value: Any) -> tuple[int, int | str]:
    """把整數、字串與無效值轉成可穩定比較的排序值。"""
    if isinstance(value, int) and not isinstance(value, bool):
        return (0, value)
    if isinstance(value, str):
        return (1, value)
    return (2, "")


def _stable_id(prefix: str, value: Any) -> str:
    """以 canonical JSON 的內容雜湊產生可重播且固定的識別碼。"""
    try:
        digest = hashlib.sha256(canonical_json_bytes(value)).hexdigest()[:24]
    except (TypeError, ValueError, RecursionError):
        raise ValueError("provider_artifact_unserializable") from None
    return f"{prefix}:{digest}"


def _record_sha256(record: Mapping[str, Any]) -> str:
    """計算忽略 record 既有 canonical_sha256 欄位後的內容指紋。"""
    try:
        return record_canonical_sha256(record)
    except (TypeError, ValueError, RecursionError):
        raise ValueError("provider_artifact_unserializable") from None


def _canonical_sha256(value: Any) -> str:
    """計算任意可封存 JSON 資料的完整 canonical SHA-256。"""
    try:
        return hashlib.sha256(canonical_json_bytes(value)).hexdigest()
    except (TypeError, ValueError, RecursionError):
        raise ValueError("provider_artifact_unserializable") from None
