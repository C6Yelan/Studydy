from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from handoff_contract_hashing import (
    _stable_id,
    _valid_input_record_hash,
    record_canonical_sha256,
)
from handoff_contract_schema import _integer, _non_empty_string


def _classify_source_failures(
    source_failures: list[Any],
    candidates: list[Any],
    origins: list[Any],
    material_id: str,
) -> tuple[dict[str, set[str]], list[tuple[str, str]]]:
    candidate_ids = {
        candidate.get("candidate_id")
        for candidate in candidates
        if isinstance(candidate, Mapping)
        and _non_empty_string(candidate.get("candidate_id"))
    }
    candidates_by_unit: dict[str, set[str]] = {}
    candidates_by_block: dict[str, set[str]] = {}
    for origin in origins:
        if not isinstance(origin, Mapping):
            continue
        candidate_id = origin.get("candidate_id")
        unit_id = origin.get("layout_unit_id")
        block_id = origin.get("block_id")
        if candidate_id not in candidate_ids:
            continue
        if _non_empty_string(unit_id):
            candidates_by_unit.setdefault(unit_id, set()).add(
                candidate_id
            )
        if _non_empty_string(block_id):
            candidates_by_block.setdefault(block_id, set()).add(
                candidate_id
            )

    candidate_failures: dict[str, set[str]] = {}
    package_failures: list[tuple[str, str]] = []
    if not candidate_ids:
        package_failures.append(
            ("PKG_CANDIDATES_INVALID", "zero valid literal candidates")
        )
    for failure in source_failures:
        if not _valid_source_failure(failure):
            raise ValueError("invalid package input")
        reasons = failure["source_failure_reasons"]
        reason_text = ";".join(reasons)
        if failure["material_id"] != material_id:
            package_failures.append(
                ("PKG_MATERIAL_INVALID", reason_text)
            )
            continue
        package_scope_code = _package_scope_failure_code(reasons)
        if package_scope_code is not None:
            package_failures.append(
                (package_scope_code, reason_text)
            )
            continue

        unit_candidates = candidates_by_unit.get(
            failure.get("layout_unit_id"),
            set(),
        )
        if unit_candidates:
            for candidate_id in unit_candidates:
                candidate_failures.setdefault(
                    candidate_id,
                    set(),
                ).update(reasons)
            continue

        block_candidates = candidates_by_block.get(
            failure["block_id"],
            set(),
        )
        if (
            failure["source_status"] != "omitted"
            and failure["source_status"] != "selected"
            and block_candidates
        ):
            for candidate_id in block_candidates:
                candidate_failures.setdefault(
                    candidate_id,
                    set(),
                ).update(reasons)
            continue

        if failure["source_status"] in {"omitted", "selected"}:
            continue
        package_failures.append(
            (
                _package_scope_failure_code(reasons)
                or "PKG_CANDIDATES_INVALID",
                reason_text,
            )
        )
    return candidate_failures, sorted(set(package_failures))

def _valid_source_failure(value: Any) -> bool:
    if not isinstance(value, Mapping):
        return False
    reasons = value.get("source_failure_reasons")
    layout_unit_id = value.get("layout_unit_id")
    return (
        _non_empty_string(value.get("source_failure_id"))
        and _non_empty_string(value.get("material_id"))
        and _non_empty_string(value.get("block_id"))
        and (
            layout_unit_id is None
            or _non_empty_string(layout_unit_id)
        )
        and _non_empty_string(value.get("source_ref"))
        and _integer(value.get("pdf_page"))
        and value["pdf_page"] >= 1
        and value.get("source_status")
        in {"failed", "omitted", "selected"}
        and isinstance(reasons, list)
        and bool(reasons)
        and all(_non_empty_string(reason) for reason in reasons)
        and reasons == sorted(set(reasons))
        and isinstance(value.get("locator"), Mapping)
        and bool(value["locator"])
        and isinstance(value.get("provenance"), Mapping)
        and bool(value["provenance"])
        and _valid_input_record_hash(value)
    )

def _package_scope_failure_code(
    reasons: list[str],
) -> str | None:
    if any(
        "artifact" in reason or "binding" in reason
        for reason in reasons
    ):
        return "PKG_NORMALIZED_SOURCE_BINDING_INVALID"
    if any("material" in reason for reason in reasons):
        return "PKG_MATERIAL_INVALID"
    if any(
        reason.startswith("source_mapping_")
        or reason
        in {
            "document_page_count_mismatch",
            "document_unreadable",
        }
        for reason in reasons
    ):
        return "PKG_CANDIDATES_INVALID"
    return None

def _apply_candidate_failures(
    candidates: list[Any],
    failures: Mapping[str, set[str]],
) -> list[Any]:
    output = deepcopy(candidates)
    for candidate in output:
        if not isinstance(candidate, dict):
            continue
        reasons = failures.get(candidate.get("candidate_id"))
        if not reasons:
            continue
        existing = candidate.get("failure_codes")
        existing_codes = (
            existing
            if isinstance(existing, list)
            and all(_non_empty_string(code) for code in existing)
            else []
        )
        candidate["construction_status"] = "invalid"
        candidate["failure_codes"] = sorted(
            {*existing_codes, *reasons}
        )
        candidate["canonical_sha256"] = record_canonical_sha256(
            candidate
        )
    return output

def _package_invalid_records(
    package_id: str,
    failures: list[tuple[str, str]],
) -> list[dict[str, Any]]:
    records = []
    for code, reason in failures:
        identity = {
            "collection": "package",
            "record_id": package_id,
            "failure_codes": [code],
        }
        record = {
            "invalid_record_id": _stable_id(
                "invalid-package-source",
                {**identity, "reason": reason},
            ),
            **identity,
            "reason": reason[:512],
        }
        record["canonical_sha256"] = record_canonical_sha256(record)
        records.append(record)
    records.sort(key=lambda record: record["invalid_record_id"])
    return records
