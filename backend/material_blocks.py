from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

import pymupdf


SCHEMA_VERSION = "material-blocks/v1"
CONTENT_TYPE = "pdf_page_text"
PARSER_STATUSES = frozenset({"success", "failed", "unsupported"})
INPUT_STATUSES = frozenset({"valid", "failed"})
DOCUMENT_FAILURE_REASONS = frozenset(
    {
        "input_fingerprint_mismatch",
        "declared_page_count_mismatch",
        "document_unreadable",
    }
)
BLOCK_FAILURE_REASONS = frozenset(
    {"page_unreadable", "no_extractable_text", "parser_error"}
)

ROOT_KEYS = frozenset({"schema_version", "parser_provenance", "materials"})
MATERIAL_KEYS = frozenset(
    {
        "material_id",
        "case_id",
        "artifact_ref",
        "input_status",
        "failure_reason",
        "blocks",
    }
)
BLOCK_KEYS = frozenset(
    {
        "block_id",
        "content_type",
        "text",
        "locator",
        "parser_status",
        "failure_reason",
    }
)
PROVENANCE_KEYS = frozenset(
    {
        "parser",
        "parser_version",
        "python_version",
        "extraction_policy",
        "normalization_policy",
        "input_verification",
    }
)


class MaterialBlockContractError(ValueError):
    pass


@dataclass(frozen=True)
class ActiveMaterial:
    case_id: str
    artifact_ref: str
    pdf_path: Path
    declared_pages: int
    expected_sha256: str
    source_refs: Mapping[int, str] = field(default_factory=dict)


def normalize_text(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(line.rstrip() for line in normalized.split("\n")).rstrip("\n")


def parser_provenance() -> dict[str, str]:
    return {
        "parser": "pymupdf",
        "parser_version": pymupdf.VersionBind,
        "python_version": platform.python_version(),
        "extraction_policy": "page.get_text:text:sort-true-v1",
        "normalization_policy": "utf8-lf-trailing-whitespace-v1",
        "input_verification": "sha256-and-declared-page-count",
    }


def select_active_manifest_entries(
    manifest: Mapping[str, Any],
) -> tuple[list[Mapping[str, Any]], list[str]]:
    baseline_state = manifest.get("baseline_state")
    if baseline_state is None or baseline_state == "" or baseline_state == {}:
        raise MaterialBlockContractError("baseline_state is required")
    selections = manifest.get("difficulty_ladder_selections")
    tombstones = manifest.get("retired_tombstones")
    if not isinstance(selections, list) or not isinstance(tombstones, list):
        raise MaterialBlockContractError("manifest selections and tombstones must be lists")

    active_entries: list[Mapping[str, Any]] = []
    for entry in selections:
        if not isinstance(entry, Mapping):
            raise MaterialBlockContractError("manifest selection must be an object")
        if entry.get("status") == "active":
            _require_non_empty_string(entry.get("case_id"), "case_id")
            active_entries.append(entry)
    retired_case_ids: list[str] = []
    for entry in tombstones:
        if not isinstance(entry, Mapping):
            raise MaterialBlockContractError("retired tombstone must be an object")
        retired_case_ids.append(_require_non_empty_string(entry.get("case_id"), "case_id"))

    if len(active_entries) != 3:
        raise MaterialBlockContractError("manifest must contain exactly three active selections")
    active_case_ids = [entry["case_id"] for entry in active_entries]
    if len(active_case_ids) != len(set(active_case_ids)):
        raise MaterialBlockContractError("active case_id values must be unique")
    if len(retired_case_ids) != len(set(retired_case_ids)):
        raise MaterialBlockContractError("retired case_id values must be unique")
    if set(active_case_ids) & set(retired_case_ids):
        raise MaterialBlockContractError("retired case_id must not be reused")
    return sorted(active_entries, key=lambda entry: entry["case_id"]), retired_case_ids


def build_material_blocks(
    active_materials: Sequence[ActiveMaterial],
    retired_case_ids: Sequence[str],
) -> dict[str, Any]:
    _validate_selection(active_materials, retired_case_ids)
    materials = [_build_material(item) for item in sorted(active_materials, key=lambda item: item.case_id)]
    artifact = {
        "schema_version": SCHEMA_VERSION,
        "parser_provenance": parser_provenance(),
        "materials": materials,
    }
    validate_artifact(artifact)
    return artifact


def canonical_json_bytes(artifact: Mapping[str, Any]) -> bytes:
    validate_artifact(artifact)
    content = json.dumps(
        artifact,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return (content + "\n").encode("utf-8")


def write_canonical_artifact(
    artifact: Mapping[str, Any],
    stable_path: Path,
    staging_directory: Path,
    *,
    active_materials: Sequence[ActiveMaterial],
    retired_case_ids: Sequence[str],
) -> None:
    validate_publication(artifact, active_materials, retired_case_ids)
    payload = canonical_json_bytes(artifact)
    stable_path = Path(stable_path)
    staging_directory = Path(staging_directory)
    staging_directory.mkdir(parents=True, exist_ok=False)
    staged_path = staging_directory / stable_path.name

    try:
        with staged_path.open("xb") as staged_file:
            staged_file.write(payload)
            staged_file.flush()
            os.fsync(staged_file.fileno())
        parsed = json.loads(staged_path.read_text(encoding="utf-8"))
        validate_publication(parsed, active_materials, retired_case_ids)
        stable_path.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staged_path, stable_path)
    finally:
        shutil.rmtree(staging_directory, ignore_errors=True)


def validate_artifact(artifact: Mapping[str, Any]) -> None:
    _require_exact_keys(artifact, ROOT_KEYS, "root")
    if artifact["schema_version"] != SCHEMA_VERSION:
        raise MaterialBlockContractError("unsupported schema_version")
    _validate_provenance(artifact["parser_provenance"])
    materials = artifact["materials"]
    if not isinstance(materials, list):
        raise MaterialBlockContractError("materials must be a list")

    material_ids: set[str] = set()
    case_ids: list[str] = []
    block_ids: set[str] = set()
    for material in materials:
        if not isinstance(material, Mapping):
            raise MaterialBlockContractError("material must be an object")
        _require_exact_keys(material, MATERIAL_KEYS, "material")
        material_id = _require_non_empty_string(material["material_id"], "material_id")
        case_id = _require_non_empty_string(material["case_id"], "case_id")
        _require_non_empty_string(material["artifact_ref"], "artifact_ref")
        if material_id != f"{SCHEMA_VERSION}:{case_id}":
            raise MaterialBlockContractError("material_id must be deterministically derived")
        if material_id in material_ids:
            raise MaterialBlockContractError("material_id must be unique")
        material_ids.add(material_id)
        case_ids.append(case_id)

        input_status = material["input_status"]
        _validate_input_status_and_reason(input_status, material["failure_reason"])
        blocks = material["blocks"]
        if not isinstance(blocks, list):
            raise MaterialBlockContractError("blocks must be a list")
        if input_status == "failed" and blocks:
            raise MaterialBlockContractError("failed input must not contain inferred blocks")

        expected_page = 1
        for block in blocks:
            if not isinstance(block, Mapping):
                raise MaterialBlockContractError("block must be an object")
            _require_exact_keys(block, BLOCK_KEYS, "block")
            block_id = _require_non_empty_string(block["block_id"], "block_id")
            if block_id in block_ids:
                raise MaterialBlockContractError("block_id must be unique")
            block_ids.add(block_id)
            if block["content_type"] != CONTENT_TYPE:
                raise MaterialBlockContractError("unsupported content_type")

            locator = block["locator"]
            if not isinstance(locator, Mapping):
                raise MaterialBlockContractError("locator must be an object")
            locator_keys = set(locator)
            if not locator_keys <= {"pdf_page", "source_ref"} or "pdf_page" not in locator_keys:
                raise MaterialBlockContractError("locator must contain pdf_page and optional source_ref only")
            pdf_page = _require_positive_integer(locator["pdf_page"], "pdf_page")
            if pdf_page != expected_page:
                raise MaterialBlockContractError("blocks must cover pages in ascending order")
            if "source_ref" in locator:
                _require_non_empty_string(locator["source_ref"], "source_ref")
            expected_page += 1

            expected_block_id = f"{material_id}:page:{pdf_page:04d}"
            if block_id != expected_block_id:
                raise MaterialBlockContractError("block_id must be deterministically derived")

            status = block["parser_status"]
            _validate_block_status_and_reason(status, block["failure_reason"])
            text = block["text"]
            if status == "success":
                if not isinstance(text, str) or not text:
                    raise MaterialBlockContractError("successful block must contain text")
            elif text is not None:
                raise MaterialBlockContractError("non-success block text must be null")

    if case_ids != sorted(case_ids) or len(case_ids) != len(set(case_ids)):
        raise MaterialBlockContractError("materials must have unique, ascending case_id values")


def validate_publication(
    artifact: Mapping[str, Any],
    active_materials: Sequence[ActiveMaterial],
    retired_case_ids: Sequence[str],
) -> None:
    validate_artifact(artifact)
    _validate_selection(active_materials, retired_case_ids)
    if len(active_materials) != 3:
        raise MaterialBlockContractError("publication requires exactly three active materials")
    if artifact["parser_provenance"] != parser_provenance():
        raise MaterialBlockContractError("publication parser_provenance is not approved")

    expected_by_case = {item.case_id: item for item in active_materials}
    actual_materials = artifact["materials"]
    if {item["case_id"] for item in actual_materials} != set(expected_by_case):
        raise MaterialBlockContractError("publication active case identity mismatch")

    for material in actual_materials:
        expected = expected_by_case[material["case_id"]]
        if material["artifact_ref"] != expected.artifact_ref:
            raise MaterialBlockContractError("publication artifact identity mismatch")
        if material["input_status"] == "failed":
            raise MaterialBlockContractError("publication requires every material to be valid")
        if len(material["blocks"]) != expected.declared_pages:
            raise MaterialBlockContractError("publication declared-page coverage mismatch")
        for page_number, block in enumerate(material["blocks"], start=1):
            expected_locator: dict[str, Any] = {"pdf_page": page_number}
            if page_number in expected.source_refs:
                expected_locator["source_ref"] = expected.source_refs[page_number]
            if block["locator"] != expected_locator:
                raise MaterialBlockContractError("publication source mapping mismatch")


def _build_material(item: ActiveMaterial) -> dict[str, Any]:
    material = {
        "material_id": f"{SCHEMA_VERSION}:{item.case_id}",
        "case_id": item.case_id,
        "artifact_ref": item.artifact_ref,
        "input_status": "valid",
        "failure_reason": None,
        "blocks": [],
    }

    input_failure = _verify_input(item)
    if input_failure is not None:
        material["input_status"] = "failed"
        material["failure_reason"] = input_failure
        return material

    try:
        document = pymupdf.open(item.pdf_path)
    except Exception:
        material["input_status"] = "failed"
        material["failure_reason"] = "document_unreadable"
        return material

    try:
        if document.page_count != item.declared_pages:
            material["input_status"] = "failed"
            material["failure_reason"] = "declared_page_count_mismatch"
            return material

        material["blocks"] = [
            _build_page_block(document, item, page_number)
            for page_number in range(1, item.declared_pages + 1)
        ]
        return material
    finally:
        document.close()


def _verify_input(item: ActiveMaterial) -> str | None:
    if item.declared_pages < 1:
        return "declared_page_count_mismatch"
    try:
        with item.pdf_path.open("rb") as input_file:
            signature = input_file.read(5)
            input_file.seek(0)
            digest = hashlib.file_digest(input_file, "sha256").hexdigest()
    except OSError:
        return "document_unreadable"
    if signature != b"%PDF-":
        return "document_unreadable"
    if digest.lower() != item.expected_sha256.lower():
        return "input_fingerprint_mismatch"
    return None


def _build_page_block(
    document: pymupdf.Document,
    item: ActiveMaterial,
    page_number: int,
) -> dict[str, Any]:
    locator: dict[str, Any] = {"pdf_page": page_number}
    source_ref = item.source_refs.get(page_number)
    if source_ref is not None:
        locator["source_ref"] = source_ref
    block = {
        "block_id": f"{SCHEMA_VERSION}:{item.case_id}:page:{page_number:04d}",
        "content_type": CONTENT_TYPE,
        "text": None,
        "locator": locator,
        "parser_status": "failed",
        "failure_reason": "page_unreadable",
    }
    try:
        page = document.load_page(page_number - 1)
    except Exception:
        return block
    try:
        text = normalize_text(page.get_text("text", sort=True))
    except Exception:
        block["failure_reason"] = "parser_error"
        return block
    if not text:
        block["parser_status"] = "unsupported"
        block["failure_reason"] = "no_extractable_text"
        return block
    block["text"] = text
    block["parser_status"] = "success"
    block["failure_reason"] = None
    return block


def _validate_selection(
    active_materials: Sequence[ActiveMaterial],
    retired_case_ids: Sequence[str],
) -> None:
    active_ids = [item.case_id for item in active_materials]
    if len(active_ids) != len(set(active_ids)):
        raise MaterialBlockContractError("active case_id values must be unique")
    retired_ids = set(retired_case_ids)
    if len(retired_ids) != len(retired_case_ids):
        raise MaterialBlockContractError("retired case_id values must be unique")
    if set(active_ids) & retired_ids:
        raise MaterialBlockContractError("retired case_id must not be reused")
    for item in active_materials:
        _require_non_empty_string(item.case_id, "case_id")
        _require_non_empty_string(item.artifact_ref, "artifact_ref")
        _require_non_empty_string(item.expected_sha256, "expected_sha256")
        declared_pages = _require_positive_integer(item.declared_pages, "declared_pages")
        if not isinstance(item.source_refs, Mapping):
            raise MaterialBlockContractError("source_refs must be a mapping")
        for page_number, source_ref in item.source_refs.items():
            page_number = _require_positive_integer(page_number, "source mapping page")
            if page_number > declared_pages:
                raise MaterialBlockContractError("source mapping page must be declared")
            _require_non_empty_string(source_ref, "source_ref")


def _validate_input_status_and_reason(status: Any, reason: Any) -> None:
    if status not in INPUT_STATUSES:
        raise MaterialBlockContractError("invalid material input_status")
    if status == "valid":
        if reason is not None:
            raise MaterialBlockContractError("valid material failure_reason must be null")
    elif reason not in DOCUMENT_FAILURE_REASONS:
        raise MaterialBlockContractError("failed material requires a supported failure_reason")


def _validate_block_status_and_reason(status: Any, reason: Any) -> None:
    if status not in PARSER_STATUSES:
        raise MaterialBlockContractError("invalid block status")
    if status == "success":
        if reason is not None:
            raise MaterialBlockContractError("successful block failure_reason must be null")
    elif reason not in BLOCK_FAILURE_REASONS:
        raise MaterialBlockContractError("non-success block requires a supported failure_reason")


def _validate_provenance(value: Any) -> None:
    if not isinstance(value, Mapping):
        raise MaterialBlockContractError("parser_provenance must be an object")
    _require_exact_keys(value, PROVENANCE_KEYS, "parser_provenance")
    for field_name, field_value in value.items():
        field_value = _require_non_empty_string(field_value, field_name)
        if any(character in field_value for character in {"\n", "\r", "\x00", "/", "\\"}):
            raise MaterialBlockContractError("parser_provenance contains an unsafe value")


def _require_exact_keys(value: Mapping[str, Any], expected: frozenset[str], owner: str) -> None:
    if set(value) != expected:
        raise MaterialBlockContractError(f"{owner} fields do not match the contract")


def _require_non_empty_string(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise MaterialBlockContractError(f"{field_name} must be a non-empty string")
    return value


def _require_positive_integer(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise MaterialBlockContractError(f"{field_name} must be a positive integer")
    return value
