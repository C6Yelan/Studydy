from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .contract_schema_metadata import (
    COLLECTION_ID_FIELDS,
    COLLECTION_KEYS,
    CONTEXT_POLICY_VERSION,
    FIELD_METADATA,
    PACKAGE_SCHEMA_VERSION,
    RECORD_HASH_MISMATCH,
    RESERVED_NON_EMITTED_CODES,
    UNKNOWN_FIELD_CODES,
)
from .contract_schema_values import (
    _binding_matches_source,
    _contains_forbidden_field,
    _integer,
    _layout_unit_id,
    _list_length,
    _non_empty_string,
    _sha256_hex,
    _sorted_unique_strings,
    _valid_artifact_binding,
    _valid_bbox,
    _valid_boundary_reason,
    _valid_input_bindings,
    _valid_literal_span,
    _valid_locator,
    _valid_normalized_source_mapping,
    _valid_policy_binding,
    _valid_record_counts,
    _valid_support,
)


_HASH_FIELDS = {
    "candidate": "CANDIDATE_HASH_MISMATCH",
    "origin": "ORIGIN_HASH_MISMATCH",
    "context": "CONTEXT_HASH_MISMATCH",
    "evidence": "EVIDENCE_HASH_MISMATCH",
    "projection": "PROJECTION_HASH_MISMATCH",
    "build_attestation": "BUILD_ATTESTATION_HASH_MISMATCH",
    "invalid_record": "INVALID_RECORD_HASH_MISMATCH",
}

_INVALID_RECORD_COLLECTION = {
    "candidate": "candidates",
    "origin": "origins",
    "context": "contexts",
    "evidence": "evidence_records",
    "projection": "projection_records",
    "build_attestation": "build_attestations",
    "invalid_record": "package",
    "validation_summary": "package",
    "package": "package",
}

_LIST_FIELD_COLLECTION = {
    "candidates": "candidate",
    "origins": "origin",
    "contexts": "context",
    "evidence_records": "evidence",
    "projection_records": "projection",
    "build_attestations": "build_attestation",
    "invalid_records": "invalid_record",
}

_PACKAGE_STATUSES = frozenset({"built", "PASS", "FAIL"})

_CANDIDATE_BUILD_STATUS_VALID = "valid"

_CANDIDATE_BUILD_STATUS_INVALID = "invalid"

_CANDIDATE_BUILD_STATUSES = frozenset(
    {
        _CANDIDATE_BUILD_STATUS_VALID,
        _CANDIDATE_BUILD_STATUS_INVALID,
    }
)

_EVIDENCE_KINDS = frozenset(
    {
        "candidate_literal",
        "explicit_alias",
        "heading",
        "definition",
        "projection_literal",
    }
)

_PROJECTION_KINDS = frozenset(
    {
        "longer_literal_substring",
        "explicit_alias",
        "heading_definition",
    }
)

_INVALID_RECORD_COLLECTIONS = frozenset(
    {
        "candidates",
        "origins",
        "contexts",
        "evidence_records",
        "projection_records",
        "build_attestations",
        "package",
    }
)

_VALIDATION_STATUSES = frozenset({"PASS", "FAIL"})

_MINIMUM_PDF_PAGE = 1

_MINIMUM_READING_ORDER = 0

_MINIMUM_CONTEXT_LAYOUT_UNITS = 1

_MAXIMUM_CONTEXT_LAYOUT_UNITS = 3

_MINIMUM_CONTEXT_CODE_POINTS = 1

_MAXIMUM_CONTEXT_CODE_POINTS = 1200

_REQUIRED_BUILD_ATTESTATION_COUNT = 1

_PRODUCTION_REPLAY_COUNT = 0

_MAXIMUM_INVALID_RECORD_REASON_LENGTH = 512

_MINIMUM_FAILURE_COUNT = 0


def _validate_package_fields(
    package: Mapping[str, Any],
    normalized_source: Mapping[str, Any],
    failures: list[tuple[str, str, str, str]],
) -> None:
    """逐一檢查 package 欄位，並把缺少、格式錯誤或禁止出現的欄位記入 failures。"""
    package_id = _record_id(package, "package", 0)
    _validate_closed_keys(package, "package", package_id, failures)
    for field, metadata in FIELD_METADATA["package"].items():
        if field not in package:
            if metadata["required"]:
                _add_failure(failures, "package", package_id, metadata["validation_failure_code"], field)
            continue
        if not _valid_package_field(field, package[field], package, normalized_source):
            _add_failure(failures, "package", package_id, metadata["validation_failure_code"], field)
    if _contains_forbidden_field(package):
        _add_failure(failures, "package", package_id, "PKG_FIELD_INVALID", "runtime_forbidden_field")

def _valid_package_field(
    field: str,
    value: Any,
    package: Mapping[str, Any],
    normalized_source: Mapping[str, Any] | None,
) -> bool:
    """依 package 欄位名稱，選用相應規則判斷欄位值是否有效。"""
    if field == "schema_version":
        return value == PACKAGE_SCHEMA_VERSION
    if field in {"package_id", "material_id"}:
        return _non_empty_string(value)
    if field == "status":
        return value in _PACKAGE_STATUSES
    if field in {"normalized_source_binding", "candidate_source_binding"}:
        return _valid_package_source_binding(
            field,
            value,
            package,
            normalized_source,
        )
    if field == "context_policy_binding":
        return (
            _valid_policy_binding(value)
            and value.get("policy_version") == CONTEXT_POLICY_VERSION
        )
    if field == "projection_policy_binding":
        return _valid_policy_binding(value)
    if field in _LIST_FIELD_COLLECTION:
        return _valid_package_record_collection(field, value)
    if field == "content_sha256":
        return _sha256_hex(value)
    if field == "validation_summary":
        return isinstance(value, Mapping)
    if field == "canonical_sha256":
        return _sha256_hex(value)
    return False

def _valid_package_source_binding(
    field: str,
    value: Any,
    package: Mapping[str, Any],
    normalized_source: Mapping[str, Any] | None,
) -> bool:
    """確認來源綁定格式正確、教材一致，且 normalized source 確實對應該綁定。"""
    if not _valid_artifact_binding(value):
        return False
    if value.get("material_id") != package.get("material_id"):
        return False
    if field == "normalized_source_binding":
        return (
            _valid_normalized_source_mapping(normalized_source)
            and _binding_matches_source(value, normalized_source)
        )
    return True

def _valid_package_record_collection(field: str, value: Any) -> bool:
    """確認 package 內的 record 清單格式、ID 排序、唯一性及必要筆數都正確。"""
    if not isinstance(value, list):
        return False
    if field == "candidates" and not value:
        return False
    collection = _LIST_FIELD_COLLECTION[field]
    id_field = COLLECTION_ID_FIELDS[collection]
    ids = [
        record.get(id_field)
        for record in value
        if isinstance(record, Mapping)
    ]
    if len(ids) != len(value):
        return False
    if all(_non_empty_string(item) for item in ids):
        if ids != sorted(ids) or len(ids) != len(set(ids)):
            return False
    if (
        field == "build_attestations"
        and len(value) != _REQUIRED_BUILD_ATTESTATION_COUNT
    ):
        return False
    return True

def _validate_record_fields(
    package: Mapping[str, Any],
    failures: list[tuple[str, str, str, str]],
) -> None:
    """檢查 package 內每種 record 與 validation summary，並收集所有欄位錯誤。"""
    for collection, package_key in COLLECTION_KEYS.items():
        records = package.get(package_key)
        if not isinstance(records, list):
            continue
        for index, raw_record in enumerate(records):
            if not isinstance(raw_record, Mapping):
                continue
            record_id = _record_id(raw_record, collection, index)
            _validate_closed_keys(raw_record, collection, record_id, failures)
            for field, metadata in FIELD_METADATA[collection].items():
                if field not in raw_record:
                    if metadata["required"]:
                        _add_failure(
                            failures,
                            collection,
                            record_id,
                            metadata["validation_failure_code"],
                            field,
                        )
                    continue
                if not _valid_record_field(
                    collection,
                    field,
                    raw_record[field],
                    raw_record,
                    package,
                ):
                    _add_failure(
                        failures,
                        collection,
                        record_id,
                        metadata["validation_failure_code"],
                        field,
                    )

    summary = package.get("validation_summary")
    if not isinstance(summary, Mapping):
        return
    package_id = _record_id(package, "package", 0)
    _validate_closed_keys(
        summary,
        "validation_summary",
        package_id,
        failures,
    )
    for field, metadata in FIELD_METADATA["validation_summary"].items():
        if field not in summary:
            if metadata["required"]:
                _add_failure(
                    failures,
                    "validation_summary",
                    package_id,
                    metadata["validation_failure_code"],
                    field,
                )
            continue
        if not _valid_summary_field(field, summary[field]):
            _add_failure(
                failures,
                "validation_summary",
                package_id,
                metadata["validation_failure_code"],
                field,
            )

def _valid_record_field(
    collection: str,
    field: str,
    value: Any,
    record: Mapping[str, Any],
    package: Mapping[str, Any],
) -> bool:
    """先檢查各 record 共用欄位，再交給該 record 類型的專用規則判斷。"""
    if field == "canonical_sha256":
        return _sha256_hex(value)
    if field in COLLECTION_ID_FIELDS.values():
        return _non_empty_string(value)
    if field == "material_id":
        return _non_empty_string(value)

    if collection == "candidate":
        return _valid_candidate_field(field, value, record)
    if collection == "origin":
        return _valid_origin_field(field, value)
    if collection == "context":
        return _valid_context_field(field, value, record)
    if collection == "evidence":
        return _valid_evidence_field(field, value)
    if collection == "projection":
        return _valid_projection_field(field, value)
    if collection == "build_attestation":
        return _valid_build_attestation_field(field, value, package)
    if collection == "invalid_record":
        return _valid_invalid_record_field(field, value)
    return False

def _valid_candidate_field(
    field: str,
    value: Any,
    record: Mapping[str, Any],
) -> bool:
    """確認 candidate 的文字、來源連結、支援摘要與建立狀態符合契約。"""
    if field in {"surface", "normalized_surface"}:
        return _non_empty_string(value)
    if field == "extraction_methods":
        return _sorted_unique_strings(value, non_empty=True)
    if field in {"origin_ids", "context_ids", "evidence_ids"}:
        return _sorted_unique_strings(value, non_empty=True)
    if field == "projection_ids":
        return _sorted_unique_strings(value)
    if field == "support_summary":
        return _valid_support(value, record)
    if field == "build_status":
        return value in _CANDIDATE_BUILD_STATUSES
    if field == "failure_codes":
        return _valid_candidate_failure_codes(value, record)
    return False

def _valid_candidate_failure_codes(
    value: Any,
    record: Mapping[str, Any],
) -> bool:
    """確認 failure codes 不重複且有排序，並與 candidate 的建立狀態一致。"""
    if not _sorted_unique_strings(value):
        return False
    status = record.get("build_status")
    return (status == _CANDIDATE_BUILD_STATUS_VALID and not value) or (
        status == _CANDIDATE_BUILD_STATUS_INVALID and bool(value)
    )

def _valid_origin_field(field: str, value: Any) -> bool:
    """確認 origin 能以有效位置、頁碼與內容指紋回查 candidate 的來源。"""
    if field in {
        "candidate_id",
        "block_id",
        "layout_unit_id",
        "source_ref",
        "safe_context_id",
    }:
        return _non_empty_string(value)
    if field == "pdf_page":
        return _integer(value) and value >= _MINIMUM_PDF_PAGE
    if field == "reading_order":
        return _integer(value) and value >= _MINIMUM_READING_ORDER
    if field == "bbox":
        return _valid_bbox(value)
    if field == "literal_span":
        return _valid_literal_span(value)
    if field == "layout_unit_text_sha256":
        return _sha256_hex(value)
    return False

def _valid_context_field(
    field: str,
    value: Any,
    record: Mapping[str, Any],
) -> bool:
    """確認 context 的文字、範圍、來源單元與 candidate/evidence 連結符合契約。"""
    if field in {"text", "normalized_text"}:
        return _non_empty_string(value)
    if field == "layout_unit_refs":
        return (
            isinstance(value, list)
            and _MINIMUM_CONTEXT_LAYOUT_UNITS
            <= len(value)
            <= _MAXIMUM_CONTEXT_LAYOUT_UNITS
            and all(_layout_unit_id(item) is not None for item in value)
        )
    if field in {"primary_candidate_ids", "evidence_ids"}:
        return _sorted_unique_strings(value, non_empty=True)
    if field == "context_scope":
        return value == CONTEXT_POLICY_VERSION
    if field in {"start_locator", "end_locator"}:
        return _valid_locator(value)
    if field == "boundary_reason":
        return _valid_boundary_reason(value)
    if field == "code_point_count":
        return (
            _integer(value)
            and _MINIMUM_CONTEXT_CODE_POINTS
            <= value
            <= _MAXIMUM_CONTEXT_CODE_POINTS
            and value == len(record.get("text", ""))
        )
    return False

def _valid_evidence_field(field: str, value: Any) -> bool:
    """確認 evidence 的種類、文字範圍及相關 record 連結符合契約。"""
    if field == "evidence_kind":
        return value in _EVIDENCE_KINDS
    if field in {
        "statement",
        "normalized_statement",
        "literal_surface",
    }:
        return _non_empty_string(value)
    if field == "literal_span":
        return _valid_literal_span(value)
    if field in {"candidate_ids", "context_ids", "origin_ids"}:
        return _sorted_unique_strings(value, non_empty=True)
    return False

def _valid_projection_field(field: str, value: Any) -> bool:
    """確認 projection 的產生方式、來源連結、文字結果與位置範圍符合契約。"""
    if field == "projection_kind":
        return value in _PROJECTION_KINDS
    if field in {
        "source_candidate_ids",
        "source_context_ids",
        "source_evidence_ids",
    }:
        return _sorted_unique_strings(value, non_empty=True)
    if field in {
        "projected_surface",
        "normalized_projected_surface",
        "algorithm_version",
    }:
        return _non_empty_string(value)
    if field == "literal_span":
        return _valid_literal_span(value)
    return False

def _valid_build_attestation_field(
    field: str,
    value: Any,
    package: Mapping[str, Any],
) -> bool:
    """確認建置聲明記錄了正確的 builder、輸入綁定、record 數量與 replay 初始值。"""
    if field in {
        "package_id",
        "builder_component",
        "builder_version",
    }:
        return _non_empty_string(value)
    if field == "input_bindings":
        return _valid_input_bindings(value)
    if field == "replay_count":
        return (
            value == _PRODUCTION_REPLAY_COUNT
            and not isinstance(value, bool)
        )
    if field == "replay_content_sha256s":
        return value == []
    if field == "deterministic_replay_pass":
        return value is False
    if field == "record_counts":
        return _valid_record_counts(value, package)
    return False

def _valid_invalid_record_field(field: str, value: Any) -> bool:
    """確認 invalid record 能明確指出失敗對象、錯誤代碼與可閱讀原因。"""
    if field == "collection":
        return value in _INVALID_RECORD_COLLECTIONS
    if field == "record_id":
        return _non_empty_string(value)
    if field == "failure_codes":
        return (
            _sorted_unique_strings(value, non_empty=True)
            and RECORD_HASH_MISMATCH not in value
        )
    if field == "reason":
        return (
            _non_empty_string(value)
            and len(value) <= _MAXIMUM_INVALID_RECORD_REASON_LENGTH
        )
    return False

def _valid_summary_field(field: str, value: Any) -> bool:
    """確認 validation summary 的狀態、內容指紋及錯誤統計格式正確。"""
    if field in {"validation_run_id", "validator_version"}:
        return _non_empty_string(value)
    if field == "validated_content_sha256":
        return _sha256_hex(value)
    if field == "status":
        return value in _VALIDATION_STATUSES
    if field == "failure_count":
        return _integer(value) and value >= _MINIMUM_FAILURE_COUNT
    if field == "failure_code_counts":
        return (
            isinstance(value, Mapping)
            and all(
                _non_empty_string(code)
                and _integer(count)
                and count >= _MINIMUM_FAILURE_COUNT
                and code not in RESERVED_NON_EMITTED_CODES
                for code, count in value.items()
            )
        )
    return False

def _validate_closed_keys(
    record: Mapping[str, Any],
    collection: str,
    record_id: str,
    failures: list[tuple[str, str, str, str]],
) -> None:
    """找出契約未允許的額外欄位，並以該 record 類型的錯誤代碼記錄。"""
    unknown = sorted(set(record) - set(FIELD_METADATA[collection]))
    if unknown:
        _add_failure(
            failures,
            collection,
            record_id,
            UNKNOWN_FIELD_CODES[collection],
            ",".join(unknown),
        )

def _validate_candidate_lifecycle(
    package: Mapping[str, Any],
    failures: list[tuple[str, str, str, str]],
) -> None:
    """把建立失敗 candidate 的 failure codes 加入整包驗證結果，維持 fail-closed。"""
    candidates = package.get("candidates")
    if not isinstance(candidates, list):
        return
    for index, candidate in enumerate(candidates):
        if not isinstance(candidate, Mapping):
            continue
        if (
            candidate.get("build_status")
            != _CANDIDATE_BUILD_STATUS_INVALID
        ):
            continue
        failure_codes = candidate.get("failure_codes")
        if not _sorted_unique_strings(failure_codes, non_empty=True):
            continue
        candidate_id = _record_id(candidate, "candidate", index)
        for failure_code in failure_codes:
            _add_failure(
                failures,
                "candidate",
                candidate_id,
                failure_code,
                "candidate.failure_codes",
            )

def _add_failure(
    failures: list[tuple[str, str, str, str]],
    collection: str,
    record_id: str,
    code: str,
    detail: str,
) -> None:
    """加入一筆不重複的驗證失敗，保留失敗類型、對象、代碼與細節。"""
    failure = (collection, record_id, code, detail)
    if failure not in failures:
        failures.append(failure)

def _record_id(record: Mapping[str, Any], collection: str, index: int) -> str:
    """取得 record 的正式 ID；缺少有效 ID 時回傳可定位的替代名稱。"""
    if collection == "package":
        value = record.get("package_id")
        return value if _non_empty_string(value) else "package"
    field = COLLECTION_ID_FIELDS.get(collection)
    value = record.get(field) if field is not None else None
    return value if _non_empty_string(value) else f"{collection}[{index}]"
