from __future__ import annotations

import copy
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from typing import Any

from .contract_hashing import (
    canonical_sha256,
    package_content_sha256,
    package_envelope_sha256,
    record_canonical_sha256,
)
from .contract_schema_fields import (
    _HASH_FIELDS,
    _INVALID_RECORD_COLLECTION,
    _add_failure,
    _record_id,
)
from .contract_schema_metadata import (
    COLLECTION_ID_FIELDS,
    COLLECTION_KEYS,
    FIELD_METADATA,
    RESERVED_NON_EMITTED_CODES,
    VALIDATOR_VERSION,
)
from .contract_schema_values import (
    _binding_matches_source,
    _finite_number,
    _integer,
    _layout_unit_id,
    _non_empty_string,
    _text_sha256,
    _valid_bbox,
    _valid_literal_span,
    _valid_normalized_source_mapping,
)


_CONTEXT_FORBIDDEN_KINDS = {
    "caption": "CONTEXT_CROSSES_CAPTION",
    "figure": "CONTEXT_CROSSES_FIGURE",
    "table": "CONTEXT_CROSSES_TABLE",
    "omission": "CONTEXT_CROSSES_OMISSION",
    "image": "CONTEXT_CROSSES_IMAGE",
    "heading": "CONTEXT_CROSSES_HEADING",
}

_SENTENCE_TERMINALS = ("。", ".", "！", "!", "？", "?")

def _validate_input_hashes(
    package: Mapping[str, Any],
    failures: list[tuple[str, str, str, str]],
) -> None:
    """重新計算 records 與 package hashes，並核對 validation summary 的狀態及錯誤統計。"""
    for collection, package_key in COLLECTION_KEYS.items():
        records = package.get(package_key)
        if not isinstance(records, list):
            continue
        for index, record in enumerate(records):
            if not isinstance(record, Mapping):
                continue
            if record.get("canonical_sha256") != record_canonical_sha256(record):
                _add_failure(
                    failures,
                    collection,
                    _record_id(record, collection, index),
                    _HASH_FIELDS[collection],
                    "canonical_sha256",
                )

    package_id = _record_id(package, "package", 0)
    content_valid = package.get("content_sha256") == package_content_sha256(package)
    if not content_valid:
        _add_failure(
            failures,
            "package",
            package_id,
            "PKG_CONTENT_HASH_MISMATCH",
            "content_sha256",
        )
    summary = package.get("validation_summary")
    if isinstance(summary, Mapping):
        if (
            content_valid
            and summary.get("validated_content_sha256")
            != package.get("content_sha256")
        ):
            _add_failure(
                failures,
                "validation_summary",
                package_id,
                "VALIDATED_CONTENT_HASH_MISMATCH",
                "validated_content_sha256",
            )
        invalid_records = package.get("invalid_records")
        if isinstance(invalid_records, list):
            if summary.get("failure_count") != len(invalid_records):
                _add_failure(
                    failures,
                    "validation_summary",
                    package_id,
                    "VALIDATION_FAILURE_COUNT_MISMATCH",
                    "failure_count",
                )
            expected_counts = _failure_code_counts(invalid_records)
            if summary.get("failure_code_counts") != expected_counts:
                _add_failure(
                    failures,
                    "validation_summary",
                    package_id,
                    "VALIDATION_FAILURE_AGGREGATE_MISMATCH",
                    "failure_code_counts",
                )
            expected_status = "FAIL" if invalid_records else "PASS"
            if summary.get("status") != expected_status:
                _add_failure(
                    failures,
                    "validation_summary",
                    package_id,
                    "VALIDATION_STATUS_INVALID",
                    "status",
                )
    if package.get("canonical_sha256") != package_envelope_sha256(package):
        _add_failure(
            failures,
            "package",
            package_id,
            "PKG_ENVELOPE_HASH_MISMATCH",
            "canonical_sha256",
        )

def _record_indexes(
    package: Mapping[str, Any],
) -> dict[str, dict[str, Mapping[str, Any]]]:
    """依 record 類型與正式 ID 建立查找表，供後續跨 record 驗證使用。"""
    indexes: dict[str, dict[str, Mapping[str, Any]]] = {}
    for collection, package_key in COLLECTION_KEYS.items():
        records = package.get(package_key)
        index: dict[str, Mapping[str, Any]] = {}
        if isinstance(records, list):
            id_field = COLLECTION_ID_FIELDS[collection]
            for record in records:
                if not isinstance(record, Mapping):
                    continue
                record_id = record.get(id_field)
                if _non_empty_string(record_id):
                    index[record_id] = record
        indexes[collection] = index
    return indexes

# 每一行都在說：哪一種資料的哪個欄位，要去找哪一種資料；
# 找不到時要報哪個錯，找到但教材不同時又要報哪個錯。
_CROSS_REFERENCES = (
    # Candidate 要能找到它來自哪裡、周圍文字、支持證據和延伸結果。
    ("candidate", "origin_ids", "origin", "XREF_CANDIDATE_ORIGIN_DANGLING", "XREF_CANDIDATE_ORIGIN_CROSS_MATERIAL"),
    ("candidate", "context_ids", "context", "XREF_CANDIDATE_CONTEXT_DANGLING", "XREF_CANDIDATE_CONTEXT_CROSS_MATERIAL"),
    ("candidate", "evidence_ids", "evidence", "XREF_CANDIDATE_EVIDENCE_DANGLING", "XREF_CANDIDATE_EVIDENCE_CROSS_MATERIAL"),
    ("candidate", "projection_ids", "projection", "XREF_CANDIDATE_PROJECTION_DANGLING", "XREF_CANDIDATE_PROJECTION_CROSS_MATERIAL"),

    # Origin 要指出它屬於哪個 candidate，以及可以搭配哪段 context。
    ("origin", "candidate_id", "candidate", "XREF_ORIGIN_CANDIDATE_DANGLING", "XREF_ORIGIN_CANDIDATE_CROSS_MATERIAL"),
    ("origin", "safe_context_id", "context", "XREF_ORIGIN_CONTEXT_DANGLING", "XREF_ORIGIN_CONTEXT_CROSS_MATERIAL"),

    # Context 要指出這段文字主要支持哪些 candidates，以及證據在哪裡。
    ("context", "primary_candidate_ids", "candidate", "XREF_CONTEXT_CANDIDATE_DANGLING", "XREF_CONTEXT_CANDIDATE_CROSS_MATERIAL"),
    ("context", "evidence_ids", "evidence", "XREF_CONTEXT_EVIDENCE_DANGLING", "XREF_CONTEXT_EVIDENCE_CROSS_MATERIAL"),

    # Evidence 要說清楚它支持哪些 candidates、contexts 和 origins。
    ("evidence", "candidate_ids", "candidate", "XREF_EVIDENCE_CANDIDATE_DANGLING", "XREF_EVIDENCE_CANDIDATE_CROSS_MATERIAL"),
    ("evidence", "context_ids", "context", "XREF_EVIDENCE_CONTEXT_DANGLING", "XREF_EVIDENCE_CONTEXT_CROSS_MATERIAL"),
    ("evidence", "origin_ids", "origin", "XREF_EVIDENCE_ORIGIN_DANGLING", "XREF_EVIDENCE_ORIGIN_CROSS_MATERIAL"),

    # Projection 要保留它是根據哪些 candidate、context 和 evidence 產生的。
    ("projection", "source_candidate_ids", "candidate", "XREF_PROJECTION_CANDIDATE_DANGLING", "XREF_PROJECTION_CANDIDATE_CROSS_MATERIAL"),
    ("projection", "source_context_ids", "context", "XREF_PROJECTION_CONTEXT_DANGLING", "XREF_PROJECTION_CONTEXT_CROSS_MATERIAL"),
    ("projection", "source_evidence_ids", "evidence", "XREF_PROJECTION_EVIDENCE_DANGLING", "XREF_PROJECTION_EVIDENCE_CROSS_MATERIAL"),
)

def _validate_cross_references(
    package: Mapping[str, Any],
    indexes: Mapping[str, Mapping[str, Mapping[str, Any]]],
    failures: list[tuple[str, str, str, str]],
) -> None:
    """確認 record 間的 ID 連結存在、教材一致，並檢查每筆 origin 都被 candidate 引用。"""
    for source_collection, field, target_collection, dangling_code, material_code in _CROSS_REFERENCES:
        records = package.get(COLLECTION_KEYS[source_collection])
        if not isinstance(records, list):
            continue
        for index, record in enumerate(records):
            if not isinstance(record, Mapping):
                continue
            value = record.get(field)
            references = value if isinstance(value, list) else [value]
            if not all(_non_empty_string(reference) for reference in references):
                continue
            record_id = _record_id(record, source_collection, index)
            for reference in references:
                target = indexes[target_collection].get(reference)
                if target is None:
                    _add_failure(
                        failures,
                        source_collection,
                        record_id,
                        dangling_code,
                        field,
                    )
                elif (
                    _non_empty_string(record.get("material_id"))
                    and _non_empty_string(target.get("material_id"))
                    and record.get("material_id") != target.get("material_id")
                ):
                    _add_failure(
                        failures,
                        source_collection,
                        record_id,
                        material_code,
                        field,
                    )
    _validate_all_origins_are_referenced(package, indexes, failures)

def _validate_all_origins_are_referenced(
    package: Mapping[str, Any],
    indexes: Mapping[str, Mapping[str, Mapping[str, Any]]],
    failures: list[tuple[str, str, str, str]],
) -> None:
    """找出沒有被任何 candidate 引用的 origins，避免 package 留下失去歸屬的來源記錄。"""
    candidates = package.get("candidates")
    if not isinstance(candidates, list):
        return
    referenced_origin_ids = {
        origin_id
        for candidate in candidates
        if isinstance(candidate, Mapping)
        and isinstance(candidate.get("origin_ids"), list)
        for origin_id in candidate["origin_ids"]
        if _non_empty_string(origin_id)
    }
    unreferenced = sorted(
        set(indexes["origin"]) - referenced_origin_ids
    )
    if unreferenced:
        _add_failure(
            failures,
            "package",
            _record_id(package, "package", 0),
            "PKG_ORIGINS_INVALID",
            f"unreferenced_origins:{','.join(unreferenced)}",
        )

def _validate_materials(
    package: Mapping[str, Any],
    indexes: Mapping[str, Mapping[str, Mapping[str, Any]]],
    failures: list[tuple[str, str, str, str]],
) -> None:
    """確認 candidate、origin、context、evidence 與 projection 都屬於 package 的教材。"""
    package_material = package.get("material_id")
    if not _non_empty_string(package_material):
        return
    for collection in ("candidate", "origin", "context", "evidence", "projection"):
        code = FIELD_METADATA[collection]["material_id"]["validation_failure_code"]
        for index, record in enumerate(indexes[collection].values()):
            material_id = record.get("material_id")
            if _non_empty_string(material_id) and material_id != package_material:
                _add_failure(
                    failures,
                    collection,
                    _record_id(record, collection, index),
                    code,
                    "material_id",
                )

def _validated_source_units(
    package: Mapping[str, Any],
    normalized_source: Mapping[str, Any],
) -> dict[str, Mapping[str, Any]]:
    """在 normalized source 與 package 綁定有效時，建立可安全回查的 layout-unit 查找表。"""
    binding = package.get("normalized_source_binding")
    if (
        not _valid_normalized_source_mapping(normalized_source)
        or not isinstance(binding, Mapping)
        or not _binding_matches_source(binding, normalized_source)
    ):
        return {}
    units = normalized_source.get("layout_units")
    output: dict[str, Mapping[str, Any]] = {}
    for unit in units:
        unit_id = unit.get("layout_unit_id")
        output[unit_id] = unit
    return output

def _validate_literal_and_source_bindings(
    package: Mapping[str, Any],
    indexes: Mapping[str, Mapping[str, Mapping[str, Any]]],
    source_units: Mapping[str, Mapping[str, Any]],
    failures: list[tuple[str, str, str, str]],
) -> None:
    """核對 evidence/projection 的文字範圍，以及 origin 與 upstream layout unit 的內容綁定。"""
    for index, evidence in enumerate(indexes["evidence"].values()):
        span = evidence.get("literal_span")
        statement = evidence.get("statement")
        literal = evidence.get("literal_surface")
        if (
            _valid_literal_span(span)
            and isinstance(statement, str)
            and isinstance(literal, str)
            and statement[span["start"]:span["end"]] != literal
        ):
            _add_failure(
                failures,
                "evidence",
                _record_id(evidence, "evidence", index),
                "EVIDENCE_LITERAL_SPAN_INVALID",
                "literal_span",
            )

    for index, projection in enumerate(indexes["projection"].values()):
        span = projection.get("literal_span")
        surface = projection.get("projected_surface")
        evidence_ids = projection.get("source_evidence_ids")
        if (
            not _valid_literal_span(span)
            or not isinstance(surface, str)
            or not isinstance(evidence_ids, list)
        ):
            continue
        statements = [
            indexes["evidence"][evidence_id].get("statement")
            for evidence_id in evidence_ids
            if evidence_id in indexes["evidence"]
        ]
        if not any(
            isinstance(statement, str)
            and statement[span["start"]:span["end"]] == surface
            for statement in statements
        ):
            _add_failure(
                failures,
                "projection",
                _record_id(projection, "projection", index),
                "PROJECTION_LITERAL_SPAN_INVALID",
                "literal_span",
            )

    for index, origin in enumerate(indexes["origin"].values()):
        origin_id = _record_id(origin, "origin", index)
        unit_id = origin.get("layout_unit_id")
        if not _non_empty_string(unit_id):
            continue
        unit = source_units.get(unit_id)
        if unit is None:
            _add_failure(
                failures,
                "origin",
                origin_id,
                "ORIGIN_LAYOUT_UNIT_REF_INVALID",
                "layout_unit_id",
            )
            continue
        _compare_origin_to_source(origin, origin_id, unit, indexes, failures)

def _compare_origin_to_source(
    origin: Mapping[str, Any],
    origin_id: str,
    unit: Mapping[str, Any],
    indexes: Mapping[str, Mapping[str, Mapping[str, Any]]],
    failures: list[tuple[str, str, str, str]],
) -> None:
    """逐項比對 origin 與來源 layout unit，並確認文字 hash 及 candidate literal span。"""
    comparisons = (
        ("material_id", "ORIGIN_MATERIAL_MISMATCH"),
        ("block_id", "ORIGIN_BLOCK_REF_INVALID"),
        ("source_ref", "ORIGIN_SOURCE_REF_INVALID"),
        ("pdf_page", "ORIGIN_PAGE_INVALID"),
        ("reading_order", "ORIGIN_READING_ORDER_INVALID"),
        ("bbox", "ORIGIN_BBOX_INVALID"),
    )
    for field, code in comparisons:
        if field in origin and field in unit and origin.get(field) != unit.get(field):
            _add_failure(failures, "origin", origin_id, code, field)
    text = unit.get("text")
    if isinstance(text, str):
        if origin.get("layout_unit_text_sha256") != _text_sha256(text):
            _add_failure(
                failures,
                "origin",
                origin_id,
                "ORIGIN_TEXT_HASH_MISMATCH",
                "layout_unit_text_sha256",
            )
        candidate = indexes["candidate"].get(origin.get("candidate_id"))
        span = origin.get("literal_span")
        if (
            candidate is not None
            and isinstance(candidate.get("surface"), str)
            and _valid_literal_span(span)
            and text[span["start"]:span["end"]] != candidate.get("surface")
        ):
            _add_failure(
                failures,
                "origin",
                origin_id,
                "ORIGIN_LITERAL_SPAN_INVALID",
                "literal_span",
            )

def _validate_context_boundaries(
    package: Mapping[str, Any],
    source_units: Mapping[str, Mapping[str, Any]],
    failures: list[tuple[str, str, str, str]],
) -> None:
    """解析每個 context 引用的來源單元，再檢查組合邊界與主要 candidate 的 anchor 限制。"""
    contexts = package.get("contexts")
    if not isinstance(contexts, list):
        return
    for index, context in enumerate(contexts):
        if not isinstance(context, Mapping):
            continue
        record_id = _record_id(context, "context", index)
        refs = context.get("layout_unit_refs")
        if not isinstance(refs, list):
            continue
        units = [
            source_units.get(unit_id)
            for unit_id in (_layout_unit_id(ref) for ref in refs)
            if unit_id is not None
        ]
        if len(units) != len(refs) or any(unit is None for unit in units):
            _add_failure(
                failures,
                "context",
                record_id,
                "CONTEXT_LAYOUT_REFS_INVALID",
                "layout_unit_refs",
            )
            continue
        resolved = [unit for unit in units if unit is not None]
        _validate_context_units(context, record_id, resolved, failures)
        _validate_anchor_overflow(
            package,
            context,
            record_id,
            source_units,
            failures,
        )

def _validate_anchor_overflow(
    package: Mapping[str, Any],
    context: Mapping[str, Any],
    record_id: str,
    source_units: Mapping[str, Mapping[str, Any]],
    failures: list[tuple[str, str, str, str]],
) -> None:
    """確認主要 candidate 所在的單一來源單元沒有超出 context 可安全承載的長度。"""
    primary_candidates = context.get("primary_candidate_ids")
    origins = package.get("origins")
    if not isinstance(primary_candidates, list) or not isinstance(origins, list):
        return
    anchor_unit_ids = {
        origin.get("layout_unit_id")
        for origin in origins
        if isinstance(origin, Mapping)
        and origin.get("candidate_id") in primary_candidates
        and origin.get("safe_context_id") == context.get("context_id")
        and _non_empty_string(origin.get("layout_unit_id"))
    }
    for unit_id in anchor_unit_ids:
        unit = source_units.get(unit_id)
        if unit is not None and isinstance(unit.get("text"), str):
            if len(unit["text"]) > 1200:
                _add_failure(
                    failures,
                    "context",
                    record_id,
                    "CONTEXT_ANCHOR_OVERFLOW",
                    "layout_unit_refs",
                )

def _validate_context_units(
    context: Mapping[str, Any],
    record_id: str,
    units: list[Mapping[str, Any]],
    failures: list[tuple[str, str, str, str]],
) -> None:
    """確認組成 context 的單元在教材、頁面、順序、文字、位置與數量上形成安全範圍。"""
    material_ids = {unit.get("material_id") for unit in units}
    if len(material_ids) != 1 or context.get("material_id") not in material_ids:
        _add_failure(failures, "context", record_id, "CONTEXT_CROSS_MATERIAL", "layout_unit_refs")
    pages = {unit.get("pdf_page") for unit in units}
    if len(pages) != 1:
        _add_failure(failures, "context", record_id, "CONTEXT_CROSS_PAGE", "layout_unit_refs")
    columns = {unit.get("column_id") for unit in units if unit.get("column_id") is not None}
    if len(columns) > 1:
        _add_failure(failures, "context", record_id, "CONTEXT_CROSS_COLUMN", "layout_unit_refs")
    for unit in units:
        code = _CONTEXT_FORBIDDEN_KINDS.get(unit.get("unit_kind"))
        if code is not None:
            _add_failure(failures, "context", record_id, code, "layout_unit_refs")

    orders = [unit.get("reading_order") for unit in units]
    if (
        not all(_integer(order) for order in orders)
        or any(right != left + 1 for left, right in zip(orders, orders[1:]))
    ):
        _add_failure(
            failures,
            "context",
            record_id,
            "CONTEXT_NON_CONSECUTIVE_ORDER",
            "layout_unit_refs",
        )
    for left, right in zip(units, units[1:]):
        _validate_adjacent_units(record_id, left, right, failures)

    texts = [unit.get("text") for unit in units]
    if all(isinstance(text, str) for text in texts):
        joined = "\n".join(texts)
        if context.get("text") != joined:
            _add_failure(failures, "context", record_id, "CONTEXT_TEXT_INVALID", "text")
        normalized = [unit.get("normalized_text") for unit in units]
        if all(isinstance(text, str) for text in normalized):
            if context.get("normalized_text") != "\n".join(normalized):
                _add_failure(
                    failures,
                    "context",
                    record_id,
                    "CONTEXT_NORMALIZED_TEXT_INVALID",
                    "normalized_text",
                )
    first_locator = units[0].get("locator")
    last_locator = units[-1].get("locator")
    if first_locator is not None and context.get("start_locator") != first_locator:
        _add_failure(
            failures,
            "context",
            record_id,
            "CONTEXT_START_LOCATOR_INVALID",
            "start_locator",
        )
    if last_locator is not None and context.get("end_locator") != last_locator:
        _add_failure(
            failures,
            "context",
            record_id,
            "CONTEXT_END_LOCATOR_INVALID",
            "end_locator",
        )
    if len(units) > 3:
        _add_failure(
            failures,
            "context",
            record_id,
            "CONTEXT_UNIT_LIMIT_EXCEEDED",
            "layout_unit_refs",
        )
    text = context.get("text")
    if isinstance(text, str) and len(text) > 1200:
        _add_failure(
            failures,
            "context",
            record_id,
            "CONTEXT_LENGTH_LIMIT_EXCEEDED",
            "text",
        )

def _validate_adjacent_units(
    record_id: str,
    left: Mapping[str, Any],
    right: Mapping[str, Any],
    failures: list[tuple[str, str, str, str]],
) -> None:
    """檢查相鄰 layout units 的水平重疊、垂直距離及句子或版面分界是否允許串接。"""
    left_bbox = left.get("bbox")
    right_bbox = right.get("bbox")
    if _valid_bbox(left_bbox) and _valid_bbox(right_bbox):
        overlap = max(
            0.0,
            min(left_bbox[2], right_bbox[2])
            - max(left_bbox[0], right_bbox[0]),
        )
        minimum_width = min(
            left_bbox[2] - left_bbox[0],
            right_bbox[2] - right_bbox[0],
        )
        if overlap / minimum_width < 0.60:
            _add_failure(
                failures,
                "context",
                record_id,
                "CONTEXT_HORIZONTAL_OVERLAP_LOW",
                "layout_unit_refs",
            )
        gap = max(0.0, right_bbox[1] - left_bbox[3])
        font_sizes = [
            value
            for value in (
                left.get("font_size_max"),
                right.get("font_size_max"),
            )
            if _finite_number(value)
        ]
        if not font_sizes:
            font_sizes = [0.0]
        if gap > max(2.5 * max(font_sizes), 24.0):
            _add_failure(
                failures,
                "context",
                record_id,
                "CONTEXT_VERTICAL_GAP_HIGH",
                "layout_unit_refs",
            )
    left_text = left.get("text")
    if (
        isinstance(left_text, str)
        and left_text.rstrip().endswith(_SENTENCE_TERMINALS)
    ):
        _add_failure(
            failures,
            "context",
            record_id,
            "CONTEXT_SENTENCE_TERMINAL_BOUNDARY",
            "layout_unit_refs",
        )
    if left.get("heading_transition_after") is True or right.get("heading_transition_before") is True:
        _add_failure(
            failures,
            "context",
            record_id,
            "CONTEXT_CROSSES_HEADING",
            "layout_unit_refs",
        )
    intervening_kind = left.get("intervening_kind_after")
    code = _CONTEXT_FORBIDDEN_KINDS.get(intervening_kind)
    if code is not None:
        _add_failure(failures, "context", record_id, code, "layout_unit_refs")

def _generated_invalid_records(
    failures: Sequence[tuple[str, str, str, str]],
) -> list[dict[str, Any]]:
    """將收集到的 failures 分組、去重並排序，產生可追溯且 deterministic 的 invalid records。"""
    grouped: dict[tuple[str, str], dict[str, set[str]]] = defaultdict(
        lambda: {"codes": set(), "details": set()}
    )
    for collection, record_id, code, detail in failures:
        if code in RESERVED_NON_EMITTED_CODES:
            continue
        target = (collection, record_id)
        grouped[target]["codes"].add(code)
        grouped[target]["details"].add(f"{code}:{detail}")

    records: list[dict[str, Any]] = []
    for (collection, record_id), values in sorted(grouped.items()):
        failure_codes = sorted(values["codes"])
        identity = {
            "collection": _INVALID_RECORD_COLLECTION[collection],
            "record_id": record_id,
            "failure_codes": failure_codes,
        }
        record = {
            "invalid_record_id": f"invalid-{canonical_sha256(identity)[:24]}",
            **identity,
            "reason": ";".join(sorted(values["details"]))[:512],
        }
        record["canonical_sha256"] = record_canonical_sha256(record)
        records.append(record)
    return records

def _validation_summary(
    sealed: Mapping[str, Any],
    *,
    input_summary: Any,
) -> dict[str, Any]:
    """依 sealed package 產生 deterministic 驗證摘要，並保留輸入中未知欄位的失敗證據。"""
    invalid_records = sealed["invalid_records"]
    counts = _failure_code_counts(invalid_records)
    status = "FAIL" if invalid_records else "PASS"
    unknown_fields = (
        {
            key: copy.deepcopy(value)
            for key, value in input_summary.items()
            if key not in FIELD_METADATA["validation_summary"]
        }
        if isinstance(input_summary, Mapping)
        else {}
    )
    identity = {
        "package_id": sealed.get("package_id"),
        "content_sha256": sealed["content_sha256"],
        "validator_version": VALIDATOR_VERSION,
    }
    return {
        **unknown_fields,
        "validation_run_id": f"validation-{canonical_sha256(identity)[:24]}",
        "validator_version": VALIDATOR_VERSION,
        "validated_content_sha256": sealed["content_sha256"],
        "status": status,
        "failure_count": len(invalid_records),
        "failure_code_counts": counts,
    }

def _failure_code_counts(invalid_records: Any) -> dict[str, int]:
    """統計 invalid records 內可輸出的 failure codes，並依代碼排序回傳。"""
    counts: Counter[str] = Counter()
    if isinstance(invalid_records, list):
        for record in invalid_records:
            if not isinstance(record, Mapping):
                continue
            codes = record.get("failure_codes")
            if isinstance(codes, list):
                counts.update(
                    code
                    for code in codes
                    if _non_empty_string(code)
                    and code not in RESERVED_NON_EMITTED_CODES
                )
    return dict(sorted(counts.items()))
