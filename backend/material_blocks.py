from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import pymupdf

from material_runtime_files import publish_runtime_json


SCHEMA_VERSION = "material-blocks/v1"
MATERIAL_BLOCKS_STABLE_PATH = (
    ".studydy-runtime/materials/blocks/stable/material-blocks.v1.json"
)


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
    """將單一已授權教材轉換為可重現的 Material Block artifact。"""
    return {
        "schema_version": SCHEMA_VERSION,
        "parser_provenance": parser_provenance(),
        "materials": [_build_material(active_material)],
    }


def persist_material_blocks(
    artifact: Mapping[str, Any],
    *,
    repo_root: str | Path,
) -> None:
    """將通用 JSON 發布器綁定到 Material Blocks 的固定 stable 路徑。"""
    publish_runtime_json(
        artifact,
        repo_root=repo_root,
        stable_path=MATERIAL_BLOCKS_STABLE_PATH,
    )


def _build_material(item: ActiveMaterial) -> dict[str, Any]:
    """驗證單一教材並建立保留失敗狀態的 material record。"""
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
    """驗證 PDF signature 與 SHA-256 fingerprint。"""
    try:
        with item.pdf_path.open("rb") as input_file:
            signature = input_file.read(5)
            input_file.seek(0)
            digest = hashlib.file_digest(input_file, "sha256").hexdigest()
    except OSError:
        return "document_unreadable"
    if signature != b"%PDF-":
        return "document_unreadable"
    if digest != item.expected_sha256:
        return "input_fingerprint_mismatch"
    return None


def _build_page_block(
    document: pymupdf.Document,
    item: ActiveMaterial,
    page_number: int,
) -> dict[str, Any]:
    """建立保留頁面 locator 與解析狀態的 page block。"""
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
