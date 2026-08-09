from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import hashlib
import json
from pathlib import Path
import string
from typing import Any
from urllib.parse import urlsplit

import pymupdf


CATALOG_SCHEMA = "controlled-resource-catalog/v1"
LICENSE_BOUNDARIES = {
    "cc_by": "attribution_required",
    "cc_by_nc": "noncommercial_attribution_required",
    "cc_by_sa": "attribution_share_alike_required",
    "cc_by_nc_sa": "noncommercial_attribution_share_alike_required",
}

_RESOURCE_INPUT_FIELDS = {
    "assessment",
    "subject",
    "title",
    "topics",
    "keywords",
    "source_locator",
    "artifact_ref",
    "artifact_sha256",
    "license_status",
    "use_boundary",
    "checked_at",
    "learning_use",
}
_RESOURCE_FIELDS = (_RESOURCE_INPUT_FIELDS - {"assessment"}) | {
    "resource_key",
    "processing",
    "quality",
    "decision",
    "reason_code",
}
_EXCLUSION_FIELDS = {
    "input_index",
    "processing",
    "quality",
    "decision",
    "reason_code",
}
_CATALOG_FIELDS = {
    "schema",
    "catalog_revision",
    "resources",
    "exclusions",
    "processing",
    "quality",
    "decision",
    "reason_code",
}

_REVIEW_REASONS = {"RESOURCE_SOURCE_NEEDS_REVIEW"}
_EXCLUSION_REASONS = _REVIEW_REASONS | {
    "RESOURCE_SOURCE_PROBLEM",
    "RESOURCE_CANDIDATE_INVALID",
    "RESOURCE_SUBJECT_INVALID",
    "RESOURCE_METADATA_INVALID",
    "RESOURCE_LOCATOR_INVALID",
    "RESOURCE_LICENSE_INVALID",
    "RESOURCE_ARTIFACT_MISSING",
    "RESOURCE_ARTIFACT_HASH_MISMATCH",
    "RESOURCE_PDF_INVALID",
    "RESOURCE_DUPLICATE",
}
_PLACEHOLDER_VALUES = frozenset(
    {"example", "placeholder", "sample", "tbd", "todo", "待填", "範例", "樣本"}
)


def _canonical_sha256(value: Any) -> str | None:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError):
        return None
    return hashlib.sha256(encoded).hexdigest()


def _failure(
    reason_code: str,
    schema: str = CATALOG_SCHEMA,
) -> dict[str, Any]:
    return {
        "schema": schema,
        "processing": "failed",
        "quality": "unsupported",
        "decision": "reject",
        "reason_code": reason_code,
    }


def _nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _normalized_text(value: str) -> str:
    return " ".join(value.casefold().split())


def _is_placeholder(value: str) -> bool:
    return _normalized_text(value) in _PLACEHOLDER_VALUES


def _valid_string_list(values: Any) -> bool:
    return (
        isinstance(values, list)
        and bool(values)
        and len(values) <= 32
        and all(
            _nonempty_string(value)
            and len(value) <= 100
            and not _is_placeholder(value)
            for value in values
        )
        and len(values) == len({_normalized_text(value) for value in values})
    )


def _valid_subject(value: Any) -> bool:
    if not isinstance(value, str) or not 1 <= len(value) <= 64:
        return False
    if value[0] not in string.ascii_lowercase or value[-1] == "_":
        return False
    allowed_characters = frozenset(string.ascii_lowercase + string.digits + "_")
    return (
        all(character in allowed_characters for character in value)
        and "__" not in value
    )


def _valid_locator(value: Any) -> bool:
    if not _nonempty_string(value) or len(value) > 2000 or any(
        character.isspace() for character in value
    ):
        return False
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        username = parsed.username
        password = parsed.password
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme in {"http", "https"}
        and hostname is not None
        and username is None
        and password is None
        and (port is None or 0 <= port <= 65535)
    )


def _valid_timestamp(value: Any) -> bool:
    if not _nonempty_string(value):
        return False
    try:
        checked_at = datetime.fromisoformat(value)
    except ValueError:
        return False
    return checked_at.tzinfo is not None


def _artifact_path(artifact_root: Path, artifact_ref: Any) -> Path | None:
    if not _nonempty_string(artifact_ref):
        return None
    relative_path = Path(artifact_ref)
    if relative_path.is_absolute() or ".." in relative_path.parts:
        return None
    try:
        resolved = (artifact_root / relative_path).resolve()
    except OSError:
        return None
    return resolved if resolved.is_relative_to(artifact_root) else None


def _resource_reason(resource: Any, artifact_root: Path) -> str | None:
    if not isinstance(resource, dict) or set(resource) != _RESOURCE_FIELDS:
        return "RESOURCE_METADATA_INVALID"
    if not _valid_subject(resource["subject"]):
        return "RESOURCE_SUBJECT_INVALID"
    string_fields = (
        "resource_key",
        "title",
        "artifact_ref",
        "artifact_sha256",
        "license_status",
        "use_boundary",
        "learning_use",
    )
    if any(not _nonempty_string(resource[field]) for field in string_fields):
        return "RESOURCE_METADATA_INVALID"
    if _is_placeholder(resource["title"]):
        return "RESOURCE_METADATA_INVALID"
    if not _valid_string_list(resource["topics"]) or not _valid_string_list(
        resource["keywords"]
    ):
        return "RESOURCE_METADATA_INVALID"
    if resource["learning_use"] not in {"primary", "supplemental"}:
        return "RESOURCE_METADATA_INVALID"
    if not _valid_timestamp(resource["checked_at"]):
        return "RESOURCE_METADATA_INVALID"
    if not _valid_locator(resource["source_locator"]):
        return "RESOURCE_LOCATOR_INVALID"
    expected_boundary = LICENSE_BOUNDARIES.get(resource["license_status"])
    if expected_boundary is None or resource["use_boundary"] != expected_boundary:
        return "RESOURCE_LICENSE_INVALID"
    if (
        len(resource["artifact_sha256"]) != 64
        or any(character not in "0123456789abcdef" for character in resource["artifact_sha256"])
    ):
        return "RESOURCE_METADATA_INVALID"
    artifact_path = _artifact_path(artifact_root, resource["artifact_ref"])
    if artifact_path is None or not artifact_path.is_file():
        return "RESOURCE_ARTIFACT_MISSING"
    try:
        artifact_sha256 = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
    except OSError:
        return "RESOURCE_ARTIFACT_MISSING"
    if artifact_sha256 != resource["artifact_sha256"]:
        return "RESOURCE_ARTIFACT_HASH_MISMATCH"
    try:
        document = pymupdf.open(artifact_path)
        try:
            is_readable_pdf = document.is_pdf and document.page_count >= 1
        finally:
            document.close()
    except (OSError, RuntimeError, ValueError):
        return "RESOURCE_PDF_INVALID"
    if not is_readable_pdf:
        return "RESOURCE_PDF_INVALID"
    identity = {
        "subject": resource["subject"],
        "title": resource["title"],
        "source_locator": resource["source_locator"],
    }
    expected_key = _canonical_sha256(identity)
    if expected_key is None or resource["resource_key"] != f"resource:sha256:{expected_key}":
        return "RESOURCE_METADATA_INVALID"
    status = (
        resource["processing"],
        resource["quality"],
        resource["decision"],
        resource["reason_code"],
    )
    if status != ("succeeded", "accepted", "retain", "SOURCE_ACCEPTED"):
        return "RESOURCE_METADATA_INVALID"
    return None


def _exclusion(input_index: int, reason_code: str) -> dict[str, Any]:
    if reason_code in _REVIEW_REASONS:
        status = ("partial", "needs_review", "review")
    else:
        status = ("failed", "unsupported", "reject")
    processing, quality, decision = status
    return {
        "input_index": input_index,
        "processing": processing,
        "quality": quality,
        "decision": decision,
        "reason_code": reason_code,
    }


def build_controlled_resource_catalog(
    candidates: Any,
    artifact_root: str | Path,
) -> dict[str, Any]:
    """只保留授權、locator 與本機 artifact 證據都完整的受控資源。"""
    if not isinstance(candidates, list) or not candidates:
        return _failure("RESOURCE_CATALOG_INPUT_INVALID")
    try:
        checked_artifact_root = Path(artifact_root).resolve()
    except (OSError, TypeError):
        return _failure("RESOURCE_CATALOG_ARTIFACT_ROOT_INVALID")
    if not checked_artifact_root.is_dir():
        return _failure("RESOURCE_CATALOG_ARTIFACT_ROOT_INVALID")

    resources = []
    exclusions = []
    resource_keys = set()
    source_locators = set()
    for input_index, candidate in enumerate(candidates):
        if not isinstance(candidate, dict):
            exclusions.append(_exclusion(input_index, "RESOURCE_CANDIDATE_INVALID"))
            continue
        assessment = candidate.get("assessment")
        if assessment == "review":
            exclusions.append(_exclusion(input_index, "RESOURCE_SOURCE_NEEDS_REVIEW"))
            continue
        if assessment == "problem":
            exclusions.append(_exclusion(input_index, "RESOURCE_SOURCE_PROBLEM"))
            continue
        if assessment != "accepted" or set(candidate) != _RESOURCE_INPUT_FIELDS:
            exclusions.append(_exclusion(input_index, "RESOURCE_CANDIDATE_INVALID"))
            continue

        identity = {
            "subject": candidate["subject"],
            "title": candidate["title"],
            "source_locator": candidate["source_locator"],
        }
        identity_sha256 = _canonical_sha256(identity)
        if identity_sha256 is None:
            exclusions.append(_exclusion(input_index, "RESOURCE_CANDIDATE_INVALID"))
            continue
        resource = {
            "resource_key": f"resource:sha256:{identity_sha256}",
            **{
                key: deepcopy(value)
                for key, value in candidate.items()
                if key != "assessment"
            },
            "processing": "succeeded",
            "quality": "accepted",
            "decision": "retain",
            "reason_code": "SOURCE_ACCEPTED",
        }
        reason = _resource_reason(resource, checked_artifact_root)
        if reason is not None:
            exclusions.append(_exclusion(input_index, reason))
            continue
        if (
            resource["resource_key"] in resource_keys
            or resource["source_locator"] in source_locators
        ):
            exclusions.append(_exclusion(input_index, "RESOURCE_DUPLICATE"))
            continue
        resource_keys.add(resource["resource_key"])
        source_locators.add(resource["source_locator"])
        resources.append(resource)

    resources.sort(key=lambda resource: resource["resource_key"])
    if resources and not exclusions:
        status = ("succeeded", "accepted", "retain", "RESOURCE_CATALOG_ACCEPTED")
    elif resources:
        status = ("partial", "needs_review", "review", "RESOURCE_CATALOG_PARTIAL")
    else:
        status = ("failed", "unsupported", "reject", "RESOURCE_CATALOG_EMPTY")
    processing, quality, decision, reason_code = status
    content = {
        "schema": CATALOG_SCHEMA,
        "resources": resources,
        "exclusions": exclusions,
        "processing": processing,
        "quality": quality,
        "decision": decision,
        "reason_code": reason_code,
    }
    catalog_sha256 = _canonical_sha256(content)
    if catalog_sha256 is None:
        return _failure("RESOURCE_CATALOG_CANONICALIZATION_FAILED")
    catalog = {
        "catalog_revision": f"resource-catalog:sha256:{catalog_sha256}",
        **content,
    }
    reason = validate_controlled_resource_catalog(catalog, checked_artifact_root)
    return catalog if reason is None else _failure(reason)


def validate_controlled_resource_catalog(
    catalog: Any,
    artifact_root: str | Path,
) -> str | None:
    """重驗 catalog identity、每筆資源與來源檔 hash，不接受寬鬆欄位。"""
    if not isinstance(catalog, dict) or set(catalog) != _CATALOG_FIELDS:
        return "RESOURCE_CATALOG_ROOT_INVALID"
    if catalog["schema"] != CATALOG_SCHEMA:
        return "RESOURCE_CATALOG_ROOT_INVALID"
    try:
        checked_artifact_root = Path(artifact_root).resolve()
    except (OSError, TypeError):
        return "RESOURCE_CATALOG_ARTIFACT_ROOT_INVALID"
    if not checked_artifact_root.is_dir():
        return "RESOURCE_CATALOG_ARTIFACT_ROOT_INVALID"
    content = {
        key: value for key, value in catalog.items() if key != "catalog_revision"
    }
    catalog_sha256 = _canonical_sha256(content)
    if (
        catalog_sha256 is None
        or catalog["catalog_revision"]
        != f"resource-catalog:sha256:{catalog_sha256}"
    ):
        return "RESOURCE_CATALOG_IDENTITY_INVALID"
    resources = catalog["resources"]
    exclusions = catalog["exclusions"]
    if not isinstance(resources, list) or not isinstance(exclusions, list):
        return "RESOURCE_CATALOG_ROOT_INVALID"

    resource_keys = set()
    source_locators = set()
    for resource in resources:
        reason = _resource_reason(resource, checked_artifact_root)
        if reason is not None:
            return reason
        if (
            resource["resource_key"] in resource_keys
            or resource["source_locator"] in source_locators
        ):
            return "RESOURCE_DUPLICATE"
        resource_keys.add(resource["resource_key"])
        source_locators.add(resource["source_locator"])
    if resources != sorted(resources, key=lambda resource: resource["resource_key"]):
        return "RESOURCE_CATALOG_ROOT_INVALID"

    previous_input_index = -1
    for exclusion in exclusions:
        if not isinstance(exclusion, dict) or set(exclusion) != _EXCLUSION_FIELDS:
            return "RESOURCE_CATALOG_EXCLUSION_INVALID"
        input_index = exclusion["input_index"]
        if (
            isinstance(input_index, bool)
            or not isinstance(input_index, int)
            or input_index <= previous_input_index
        ):
            return "RESOURCE_CATALOG_EXCLUSION_INVALID"
        previous_input_index = input_index
        reason_code = exclusion["reason_code"]
        if reason_code not in _EXCLUSION_REASONS:
            return "RESOURCE_CATALOG_EXCLUSION_INVALID"
        status = (
            exclusion["processing"],
            exclusion["quality"],
            exclusion["decision"],
        )
        expected_status = (
            ("partial", "needs_review", "review")
            if reason_code in _REVIEW_REASONS
            else ("failed", "unsupported", "reject")
        )
        if status != expected_status:
            return "RESOURCE_CATALOG_EXCLUSION_INVALID"

    root_status = (
        catalog["processing"],
        catalog["quality"],
        catalog["decision"],
        catalog["reason_code"],
    )
    if resources and not exclusions:
        expected_root_status = (
            "succeeded",
            "accepted",
            "retain",
            "RESOURCE_CATALOG_ACCEPTED",
        )
    elif resources:
        expected_root_status = (
            "partial",
            "needs_review",
            "review",
            "RESOURCE_CATALOG_PARTIAL",
        )
    else:
        expected_root_status = (
            "failed",
            "unsupported",
            "reject",
            "RESOURCE_CATALOG_EMPTY",
        )
    return None if root_status == expected_root_status else "RESOURCE_CATALOG_ROOT_INVALID"
