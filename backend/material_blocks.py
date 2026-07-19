from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import pymupdf


SCHEMA_VERSION = "material-blocks/v1"


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


def build_material_blocks(active_material: ActiveMaterial) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "parser_provenance": parser_provenance(),
        "materials": [_build_material(active_material)],
    }


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
