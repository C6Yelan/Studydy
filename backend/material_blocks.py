from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

import pymupdf


SCHEMA_VERSION = "material-blocks/v1"


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
        "extraction_policy": "page.get_text:text:sort-true-v1",
        "normalization_policy": "utf8-lf-trailing-whitespace-v1",
    }


def select_active_manifest_entries(
    manifest: Mapping[str, Any],
) -> list[Mapping[str, Any]]:
    baseline_state = manifest.get("baseline_state")
    if baseline_state is None or baseline_state == "" or baseline_state == {}:
        raise MaterialBlockContractError("baseline_state is required")
    selections = manifest.get("difficulty_ladder_selections")
    if not isinstance(selections, list):
        raise MaterialBlockContractError("manifest selections must be a list")

    active_entries: list[Mapping[str, Any]] = []
    for entry in selections:
        if not isinstance(entry, Mapping):
            raise MaterialBlockContractError("manifest selection must be an object")
        if entry.get("status") == "active":
            if not isinstance(entry.get("case_id"), str) or not entry["case_id"]:
                raise MaterialBlockContractError("case_id must be a non-empty string")
            active_entries.append(entry)
    if len(active_entries) != 3:
        raise MaterialBlockContractError("manifest must contain exactly three active selections")
    active_case_ids = [entry["case_id"] for entry in active_entries]
    if len(active_case_ids) != len(set(active_case_ids)):
        raise MaterialBlockContractError("active case_id values must be unique")
    return sorted(active_entries, key=lambda entry: entry["case_id"])


def build_material_blocks(
    active_materials: Sequence[ActiveMaterial],
) -> dict[str, Any]:
    materials = [
        _build_material(item) for item in sorted(active_materials, key=lambda item: item.case_id)
    ]
    artifact = {
        "schema_version": SCHEMA_VERSION,
        "parser_provenance": parser_provenance(),
        "materials": materials,
    }
    return artifact


def canonical_json_bytes(artifact: Mapping[str, Any]) -> bytes:
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
) -> None:
    validate_publication(artifact, active_materials)
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
        stable_path.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staged_path, stable_path)
    finally:
        shutil.rmtree(staging_directory, ignore_errors=True)


def validate_publication(
    artifact: Mapping[str, Any],
    active_materials: Sequence[ActiveMaterial],
) -> None:
    if len(active_materials) != 3:
        raise MaterialBlockContractError("publication requires exactly three active materials")
    active_case_ids = [item.case_id for item in active_materials]
    if len(active_case_ids) != len(set(active_case_ids)):
        raise MaterialBlockContractError("publication active case IDs must be unique")
    expected_materials = sorted(active_materials, key=lambda item: item.case_id)
    actual_materials = artifact["materials"]
    if len(actual_materials) != 3 or [item["case_id"] for item in actual_materials] != [
        item.case_id for item in expected_materials
    ]:
        raise MaterialBlockContractError("publication active case identity mismatch")

    for material, expected in zip(actual_materials, expected_materials, strict=True):
        if (
            material["material_id"] != f"{SCHEMA_VERSION}:{expected.case_id}"
            or material["artifact_ref"] != expected.artifact_ref
        ):
            raise MaterialBlockContractError("publication artifact identity mismatch")
        if material["input_status"] != "valid":
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
