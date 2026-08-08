from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
from typing import Any

import pymupdf

from .catalog import (
    _artifact_path,
    _canonical_sha256,
    _nonempty_string,
    _normalized_text,
    _valid_timestamp,
    validate_controlled_resource_catalog,
)


RESOURCE_EVIDENCE_SCHEMA = "resource-evidence/v1"
EXTRACTION_POLICY_VERSION = "pymupdf-native-text-blocks/v1"
MAX_BLOCKS = 6
MAX_CHARACTERS = 6000
MIN_NATIVE_CHARACTERS = 200
APPROVED_SOURCE_LOCATORS = frozenset(
    {
        "https://opendatastructures.org/ods-cpp.pdf",
        "https://opentextbc.ca/electroniccommerce/open/download?type=pdf",
    }
)

_CATALOG_COPY_FIELDS = (
    "title",
    "source_locator",
    "license_status",
    "use_boundary",
    "artifact_ref",
    "artifact_sha256",
)
_EVIDENCE_FIELDS = {
    "schema",
    "evidence_set_id",
    "source_s2_revision",
    "catalog_revision",
    "concept_id",
    "resource_key",
    *_CATALOG_COPY_FIELDS,
    "pymupdf_version",
    "extraction_policy_version",
    "blocks",
    "processing_status",
    "quality_status",
    "decision_status",
    "reason_code",
    "produced_at",
    "run_id",
}
_BLOCK_FIELDS = {
    "evidence_id",
    "page_number",
    "block_index",
    "text",
    "text_sha256",
}
_PROMPT_INJECTION_MARKERS = (
    "ignore previous instructions",
    "ignore all previous instructions",
    "system prompt",
    "developer message",
    "[inst]",
)


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _resource(catalog: Any, resource_key: Any) -> dict[str, Any] | None:
    if not isinstance(catalog, dict) or not isinstance(catalog.get("resources"), list):
        return None
    return next(
        (
            item
            for item in catalog["resources"]
            if isinstance(item, dict) and item.get("resource_key") == resource_key
        ),
        None,
    )


def _valid_concept_terms(concept_terms: Any) -> bool:
    return (
        isinstance(concept_terms, list)
        and bool(concept_terms)
        and all(_nonempty_string(term) and len(term) <= 100 for term in concept_terms)
        and len(concept_terms)
        == len({_normalized_text(term) for term in concept_terms})
    )


def _term_occurs(term: str, text: str) -> bool:
    normalized_term = _normalized_text(term)
    normalized_text = _normalized_text(text)
    return normalized_term == normalized_text or (
        len(normalized_term) >= 2 and normalized_term in normalized_text
    )


def _native_blocks(pdf_path: Path) -> tuple[list[dict[str, Any]], int] | None:
    """依頁面與 PyMuPDF block 順序讀取原生文字，不執行 OCR。"""
    try:
        document = pymupdf.open(pdf_path)
    except (OSError, RuntimeError, ValueError):
        return None
    try:
        if not document.is_pdf or document.page_count < 1:
            return None
        blocks = []
        for page_number, page in enumerate(document, start=1):
            for block_index, raw_block in enumerate(page.get_text("blocks", sort=True)):
                if len(raw_block) < 7 or raw_block[6] != 0:
                    continue
                text = raw_block[4].strip()
                if text:
                    blocks.append(
                        {
                            "page_number": page_number,
                            "block_index": block_index,
                            "text": text,
                        }
                    )
        return blocks, document.page_count
    except (OSError, RuntimeError, ValueError):
        return None
    finally:
        document.close()


def _has_prompt_injection(blocks: list[dict[str, Any]]) -> bool:
    for block in blocks:
        normalized_text = _normalized_text(block["text"])
        if any(marker in normalized_text for marker in _PROMPT_INJECTION_MARKERS):
            return True
    return False


def _evidence_block(
    artifact_sha256: str,
    native_block: dict[str, Any],
) -> dict[str, Any] | None:
    text_sha256 = _sha256_text(native_block["text"])
    identity = {
        "artifact_sha256": artifact_sha256,
        "page_number": native_block["page_number"],
        "block_index": native_block["block_index"],
        "text_sha256": text_sha256,
    }
    identity_sha256 = _canonical_sha256(identity)
    if identity_sha256 is None:
        return None
    return {
        "evidence_id": f"resource-evidence-block:sha256:{identity_sha256}",
        "page_number": native_block["page_number"],
        "block_index": native_block["block_index"],
        "text": native_block["text"],
        "text_sha256": text_sha256,
    }


def _evidence_set_id(
    artifact_sha256: Any,
    concept_id: Any,
    blocks: Any,
) -> str | None:
    identity = {
        "artifact_sha256": artifact_sha256,
        "concept_id": concept_id,
        "extraction_policy_version": EXTRACTION_POLICY_VERSION,
        "blocks": blocks,
    }
    identity_sha256 = _canonical_sha256(identity)
    return (
        f"resource-evidence:sha256:{identity_sha256}"
        if identity_sha256 is not None
        else None
    )


def _evidence_result(
    catalog: Any,
    resource: dict[str, Any],
    source_s2_revision: Any,
    concept_id: Any,
    blocks: list[dict[str, Any]],
    produced_at: Any,
    run_id: Any,
    status: tuple[str, str, str, str],
) -> dict[str, Any]:
    processing_status, quality_status, decision_status, reason_code = status
    artifact_sha256 = resource.get("artifact_sha256", "")
    return {
        "schema": RESOURCE_EVIDENCE_SCHEMA,
        "evidence_set_id": _evidence_set_id(
            artifact_sha256,
            concept_id,
            blocks,
        ),
        "source_s2_revision": source_s2_revision,
        "catalog_revision": (
            catalog.get("catalog_revision", "") if isinstance(catalog, dict) else ""
        ),
        "concept_id": concept_id,
        "resource_key": resource.get("resource_key", ""),
        **{
            field: deepcopy(resource.get(field, ""))
            for field in _CATALOG_COPY_FIELDS
        },
        "pymupdf_version": pymupdf.VersionBind,
        "extraction_policy_version": EXTRACTION_POLICY_VERSION,
        "blocks": deepcopy(blocks),
        "processing_status": processing_status,
        "quality_status": quality_status,
        "decision_status": decision_status,
        "reason_code": reason_code,
        "produced_at": produced_at,
        "run_id": run_id,
    }


def _write_json(path: Path, value: dict[str, Any]) -> None:
    """以 exclusive create 保存 Evidence，避免重跑覆寫既有 baseline。"""
    content = (
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        ).encode("utf-8")
        + b"\n"
    )
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )
    with os.fdopen(descriptor, "wb") as file:
        file.write(content)
        file.flush()
        os.fsync(file.fileno())


def _preserve_result(
    output_path: Any,
    evidence: dict[str, Any],
) -> dict[str, Any]:
    """寫入失敗時回報 failed/reject，不把未保存內容視為成功。"""
    try:
        checked_output_path = Path(output_path)
        _write_json(checked_output_path, evidence)
    except (OSError, TypeError, ValueError):
        failed = deepcopy(evidence)
        failed["processing_status"] = "failed"
        failed["quality_status"] = "unsupported"
        failed["decision_status"] = "reject"
        failed["reason_code"] = "RESOURCE_EVIDENCE_WRITE_FAILED"
        return failed
    return evidence


def _failed_result(
    catalog: Any,
    resource: dict[str, Any],
    source_s2_revision: Any,
    concept_id: Any,
    produced_at: Any,
    run_id: Any,
    reason_code: str,
    output_path: Any,
) -> dict[str, Any]:
    evidence = _evidence_result(
        catalog,
        resource,
        source_s2_revision,
        concept_id,
        [],
        produced_at,
        run_id,
        ("failed", "unsupported", "reject", reason_code),
    )
    return _preserve_result(output_path, evidence)


def build_resource_evidence(
    catalog: Any,
    resource_key: Any,
    artifact_root: str | Path,
    content_type: Any,
    source_s2_revision: Any,
    concept_id: Any,
    concept_terms: Any,
    produced_at: Any,
    run_id: Any,
    output_path: str | Path,
) -> dict[str, Any]:
    """從受控 PDF 選出可回查的 Concept 相關原生文字 blocks。"""
    resource = _resource(catalog, resource_key) or {"resource_key": resource_key}
    if (
        not _nonempty_string(source_s2_revision)
        or not _nonempty_string(concept_id)
        or not _valid_concept_terms(concept_terms)
        or not _valid_timestamp(produced_at)
        or not _nonempty_string(run_id)
    ):
        return _failed_result(
            catalog,
            resource,
            source_s2_revision,
            concept_id,
            produced_at,
            run_id,
            "RESOURCE_EVIDENCE_REQUEST_INVALID",
            output_path,
        )
    if not isinstance(content_type, str) or (
        content_type.split(";", 1)[0].strip().casefold() != "application/pdf"
    ):
        return _failed_result(
            catalog,
            resource,
            source_s2_revision,
            concept_id,
            produced_at,
            run_id,
            "RESOURCE_CONTENT_TYPE_INVALID",
            output_path,
        )
    catalog_reason = validate_controlled_resource_catalog(catalog, artifact_root)
    if catalog_reason is not None:
        return _failed_result(
            catalog,
            resource,
            source_s2_revision,
            concept_id,
            produced_at,
            run_id,
            catalog_reason,
            output_path,
        )
    resource = _resource(catalog, resource_key)
    if resource is None:
        return _failed_result(
            catalog,
            {"resource_key": resource_key},
            source_s2_revision,
            concept_id,
            produced_at,
            run_id,
            "RESOURCE_EVIDENCE_RESOURCE_NOT_FOUND",
            output_path,
        )
    if resource["source_locator"] not in APPROVED_SOURCE_LOCATORS:
        return _failed_result(
            catalog,
            resource,
            source_s2_revision,
            concept_id,
            produced_at,
            run_id,
            "RESOURCE_SOURCE_NOT_APPROVED",
            output_path,
        )
    try:
        checked_artifact_root = Path(artifact_root).resolve()
    except (OSError, TypeError):
        checked_artifact_root = Path()
    pdf_path = _artifact_path(checked_artifact_root, resource["artifact_ref"])
    native_output = _native_blocks(pdf_path) if pdf_path is not None else None
    if native_output is None:
        return _failed_result(
            catalog,
            resource,
            source_s2_revision,
            concept_id,
            produced_at,
            run_id,
            "RESOURCE_PDF_INVALID",
            output_path,
        )
    native_blocks, _ = native_output
    if sum(len(block["text"]) for block in native_blocks) < MIN_NATIVE_CHARACTERS:
        return _failed_result(
            catalog,
            resource,
            source_s2_revision,
            concept_id,
            produced_at,
            run_id,
            "RESOURCE_NATIVE_TEXT_INSUFFICIENT",
            output_path,
        )
    if _has_prompt_injection(native_blocks):
        return _failed_result(
            catalog,
            resource,
            source_s2_revision,
            concept_id,
            produced_at,
            run_id,
            "RESOURCE_PROMPT_INJECTION_SUSPECTED",
            output_path,
        )

    selected_blocks = []
    character_count = 0
    for native_block in native_blocks:
        if not any(
            _term_occurs(term, native_block["text"])
            for term in concept_terms
        ):
            continue
        if character_count + len(native_block["text"]) > MAX_CHARACTERS:
            continue
        block = _evidence_block(resource["artifact_sha256"], native_block)
        if block is None:
            return _failed_result(
                catalog,
                resource,
                source_s2_revision,
                concept_id,
                produced_at,
                run_id,
                "RESOURCE_EVIDENCE_IDENTITY_INVALID",
                output_path,
            )
        selected_blocks.append(block)
        character_count += len(block["text"])
        if len(selected_blocks) == MAX_BLOCKS:
            break
    if not selected_blocks:
        return _failed_result(
            catalog,
            resource,
            source_s2_revision,
            concept_id,
            produced_at,
            run_id,
            "RESOURCE_EVIDENCE_NOT_FOUND",
            output_path,
        )

    evidence = _evidence_result(
        catalog,
        resource,
        source_s2_revision,
        concept_id,
        selected_blocks,
        produced_at,
        run_id,
        ("succeeded", "accepted", "retain", "RESOURCE_EVIDENCE_ACCEPTED"),
    )
    validation_reason = validate_resource_evidence(
        evidence,
        catalog,
        artifact_root,
        content_type,
        source_s2_revision,
        concept_id,
        concept_terms,
    )
    if validation_reason is not None:
        evidence["processing_status"] = "failed"
        evidence["quality_status"] = "unsupported"
        evidence["decision_status"] = "reject"
        evidence["reason_code"] = validation_reason
    return _preserve_result(output_path, evidence)


def validate_resource_evidence(
    evidence: Any,
    catalog: Any,
    artifact_root: str | Path,
    content_type: Any,
    source_s2_revision: Any,
    concept_id: Any,
    concept_terms: Any,
) -> str | None:
    """重驗 catalog copy、PDF locator、文字 hash、bounds 與 Evidence identity。"""
    if not isinstance(evidence, dict) or set(evidence) != _EVIDENCE_FIELDS:
        return "RESOURCE_EVIDENCE_ROOT_INVALID"
    if evidence["schema"] != RESOURCE_EVIDENCE_SCHEMA:
        return "RESOURCE_EVIDENCE_ROOT_INVALID"
    if (
        evidence["source_s2_revision"] != source_s2_revision
        or evidence["concept_id"] != concept_id
        or evidence["extraction_policy_version"] != EXTRACTION_POLICY_VERSION
        or evidence["pymupdf_version"] != pymupdf.VersionBind
        or not _valid_concept_terms(concept_terms)
    ):
        return "RESOURCE_EVIDENCE_BINDING_INVALID"
    resource = _resource(catalog, evidence["resource_key"])
    if resource is None or evidence["catalog_revision"] != catalog.get(
        "catalog_revision"
    ):
        return "RESOURCE_EVIDENCE_BINDING_INVALID"
    if resource["source_locator"] not in APPROVED_SOURCE_LOCATORS:
        return "RESOURCE_SOURCE_NOT_APPROVED"
    if any(evidence[field] != resource[field] for field in _CATALOG_COPY_FIELDS):
        return "RESOURCE_EVIDENCE_BINDING_INVALID"
    expected_set_id = _evidence_set_id(
        evidence["artifact_sha256"],
        evidence["concept_id"],
        evidence["blocks"],
    )
    if evidence["evidence_set_id"] != expected_set_id:
        return "RESOURCE_EVIDENCE_IDENTITY_INVALID"
    if not _valid_timestamp(evidence["produced_at"]) or not _nonempty_string(
        evidence["run_id"]
    ):
        return "RESOURCE_EVIDENCE_ROOT_INVALID"

    status = (
        evidence["processing_status"],
        evidence["quality_status"],
        evidence["decision_status"],
    )
    if status != ("succeeded", "accepted", "retain"):
        return (
            None
            if status == ("failed", "unsupported", "reject")
            and _nonempty_string(evidence["reason_code"])
            else "RESOURCE_EVIDENCE_STATUS_INVALID"
        )
    if evidence["reason_code"] != "RESOURCE_EVIDENCE_ACCEPTED":
        return "RESOURCE_EVIDENCE_STATUS_INVALID"
    if not isinstance(content_type, str) or (
        content_type.split(";", 1)[0].strip().casefold() != "application/pdf"
    ):
        return "RESOURCE_CONTENT_TYPE_INVALID"
    catalog_reason = validate_controlled_resource_catalog(catalog, artifact_root)
    if catalog_reason is not None:
        return catalog_reason
    try:
        checked_artifact_root = Path(artifact_root).resolve()
    except (OSError, TypeError):
        return "RESOURCE_EVIDENCE_ARTIFACT_ROOT_INVALID"
    pdf_path = _artifact_path(checked_artifact_root, resource["artifact_ref"])
    native_output = _native_blocks(pdf_path) if pdf_path is not None else None
    if native_output is None:
        return "RESOURCE_PDF_INVALID"
    native_blocks, page_count = native_output
    if sum(len(block["text"]) for block in native_blocks) < MIN_NATIVE_CHARACTERS:
        return "RESOURCE_NATIVE_TEXT_INSUFFICIENT"
    if _has_prompt_injection(native_blocks):
        return "RESOURCE_PROMPT_INJECTION_SUSPECTED"

    blocks = evidence["blocks"]
    if (
        not isinstance(blocks, list)
        or not blocks
        or len(blocks) > MAX_BLOCKS
        or sum(
            len(block.get("text", ""))
            for block in blocks
            if isinstance(block, dict)
        )
        > MAX_CHARACTERS
    ):
        return "RESOURCE_EVIDENCE_BOUNDS_INVALID"
    native_by_locator = {
        (block["page_number"], block["block_index"]): block["text"]
        for block in native_blocks
    }
    previous_locator = (0, -1)
    evidence_ids = set()
    for block in blocks:
        if not isinstance(block, dict) or set(block) != _BLOCK_FIELDS:
            return "RESOURCE_EVIDENCE_BLOCK_INVALID"
        page_number = block["page_number"]
        block_index = block["block_index"]
        locator = (page_number, block_index)
        if (
            isinstance(page_number, bool)
            or not isinstance(page_number, int)
            or page_number < 1
            or page_number > page_count
            or isinstance(block_index, bool)
            or not isinstance(block_index, int)
            or block_index < 0
            or locator <= previous_locator
        ):
            return "RESOURCE_EVIDENCE_BLOCK_INVALID"
        previous_locator = locator
        if native_by_locator.get(locator) != block["text"]:
            return "RESOURCE_EVIDENCE_BLOCK_INVALID"
        if block["text_sha256"] != _sha256_text(block["text"]):
            return "RESOURCE_EVIDENCE_BLOCK_HASH_INVALID"
        expected_block = _evidence_block(
            evidence["artifact_sha256"],
            {
                "page_number": page_number,
                "block_index": block_index,
                "text": block["text"],
            },
        )
        if expected_block is None or block["evidence_id"] != expected_block[
            "evidence_id"
        ]:
            return "RESOURCE_EVIDENCE_BLOCK_IDENTITY_INVALID"
        if block["evidence_id"] in evidence_ids:
            return "RESOURCE_EVIDENCE_BLOCK_IDENTITY_INVALID"
        evidence_ids.add(block["evidence_id"])
        if not any(_term_occurs(term, block["text"]) for term in concept_terms):
            return "RESOURCE_EVIDENCE_SELECTION_INVALID"
    return None
