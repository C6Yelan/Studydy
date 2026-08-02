from __future__ import annotations

import hashlib
import json
import math
from typing import Any

from .page_structure import validate_page_structure


ALIGNMENT_SCHEMA = "s1-page-alignment/v1"
NATIVE_SCHEMA = "s1-page-native/v1"
NATIVE_FIELDS = {
    "schema",
    "material_ref",
    "page_ref",
    "page_number",
    "geometry",
    "spans",
    "images",
    "drawings",
}
SIMPLE_TEXT_TYPES = {"heading", "paragraph", "code"}


def _canonical_sha256(value: Any) -> str | None:
    """以固定 JSON 表示計算 SHA-256，無法序列化時回傳 None。"""
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


def _valid_sha256(value: Any) -> bool:
    """判斷值是否為小寫十六進位的標準 SHA-256。"""
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _valid_bbox(value: Any) -> bool:
    """判斷 bbox 是否由四個有限數字組成有效矩形。"""
    if not isinstance(value, list) or len(value) != 4:
        return False
    if any(
        isinstance(number, bool)
        or not isinstance(number, (int, float))
        or not math.isfinite(number)
        for number in value
    ):
        return False
    return value[2] > value[0] and value[3] > value[1]


def _result(
    page_structure: Any,
    page_evidence: Any,
    page_structure_sha256: str | None,
    native_sha256: str | None,
    *,
    processing: str,
    quality: str,
    decision: str,
    reason_code: str,
    findings: list[dict[str, str]],
    trusted_identity: bool,
) -> dict[str, Any]:
    """建立只含識別、綁定狀態與無教材內容 findings 的結果。"""
    if trusted_identity:
        identity = {
            "material_ref": page_structure["material_ref"],
            "page_ref": page_structure["page_ref"],
            "page_number": page_structure["page_number"],
        }
        evidence_ref = page_evidence["evidence_ref"]
    else:
        identity = {"material_ref": None, "page_ref": None, "page_number": None}
        evidence_ref = None
    return {
        "schema": ALIGNMENT_SCHEMA,
        "identity": identity,
        "input_binding": {
            "evidence_ref": evidence_ref,
            "page_structure_sha256": page_structure_sha256,
            "native_sha256": native_sha256,
        },
        "processing": processing,
        "quality": quality,
        "decision": decision,
        "reason_code": reason_code,
        "findings": findings,
    }


def _validate_native_binding(
    page_structure: dict[str, Any],
    page_evidence: dict[str, Any],
    native_page: Any,
    native_sha256: str | None,
) -> str | None:
    """驗證 native 頁面形狀、identity 與 Page Evidence 的 canonical hash 綁定。"""
    if not isinstance(native_page, dict) or set(native_page) != NATIVE_FIELDS:
        return "NATIVE_PAGE_INVALID"
    if native_page["schema"] != NATIVE_SCHEMA:
        return "NATIVE_PAGE_INVALID"
    page_number = native_page["page_number"]
    if isinstance(page_number, bool) or not isinstance(page_number, int) or page_number < 1:
        return "NATIVE_PAGE_INVALID"
    geometry = native_page["geometry"]
    spans = native_page["spans"]
    if (
        not isinstance(geometry, dict)
        or not isinstance(spans, list)
        or not isinstance(native_page["images"], list)
        or not isinstance(native_page["drawings"], list)
    ):
        return "NATIVE_PAGE_INVALID"
    for span in spans:
        if (
            not isinstance(span, dict)
            or not isinstance(span.get("text"), str)
            or not _valid_bbox(span.get("bbox"))
        ):
            return "NATIVE_PAGE_INVALID"

    if (
        native_page["material_ref"] != page_structure["material_ref"]
        or native_page["page_ref"] != page_structure["page_ref"]
        or native_page["page_number"] != page_structure["page_number"]
        or native_page["geometry"] != page_evidence.get("geometry")
    ):
        return "NATIVE_PAGE_BINDING_INVALID"

    hashes = page_evidence.get("hashes")
    if not isinstance(hashes, dict) or set(hashes) != {
        "source_sha256",
        "native_sha256",
        "render_sha256",
    }:
        return "PAGE_EVIDENCE_HASH_BINDING_INVALID"
    source_sha256 = hashes["source_sha256"]
    expected_native_sha256 = hashes["native_sha256"]
    render_sha256 = hashes["render_sha256"]
    if not all(
        _valid_sha256(value)
        for value in (source_sha256, expected_native_sha256, render_sha256)
    ):
        return "PAGE_EVIDENCE_HASH_BINDING_INVALID"
    if page_structure["material_ref"] != f"material:sha256:{source_sha256}":
        return "PAGE_EVIDENCE_HASH_BINDING_INVALID"
    page_ref_sha256 = hashlib.sha256(
        f"{source_sha256}:{page_structure['page_number']}".encode("ascii")
    ).hexdigest()
    if page_structure["page_ref"] != f"page:sha256:{page_ref_sha256}":
        return "PAGE_EVIDENCE_HASH_BINDING_INVALID"
    if native_sha256 is None or native_sha256 != expected_native_sha256:
        return "NATIVE_HASH_MISMATCH"
    evidence_sha256 = hashlib.sha256(
        (
            f"{source_sha256}:{page_structure['page_number']}:"
            f"{expected_native_sha256}:{render_sha256}"
        ).encode("ascii")
    ).hexdigest()
    if page_evidence["evidence_ref"] != f"evidence:sha256:{evidence_sha256}":
        return "PAGE_EVIDENCE_HASH_BINDING_INVALID"
    return None


def _alignment_findings(
    page_structure: dict[str, Any], native_page: dict[str, Any]
) -> tuple[str | None, list[dict[str, str]]]:
    """以原生 spans 判斷簡單文字、bbox 與 reading order 是否可確定對回。"""
    if native_page["images"] or native_page["drawings"]:
        return "VISION_CONTENT_NEEDS_REVIEW", [
            {"reason_code": "VISION_CONTENT_PRESENT"}
        ]

    complex_findings = []
    for element in page_structure["elements"]:
        if element["type"] not in SIMPLE_TEXT_TYPES:
            finding_reason_code = (
                "UNCERTAIN_REGION_PRESENT"
                if element["type"] == "other_visible_region"
                else "COMPLEX_ELEMENT_PRESENT"
            )
            complex_findings.append(
                {
                    "element_id": element["id"],
                    "reason_code": finding_reason_code,
                }
            )
    if complex_findings:
        return "COMPLEX_CONTENT_NEEDS_REVIEW", complex_findings
    if page_structure["spatial_relations"]:
        return "SPATIAL_RELATION_NEEDS_REVIEW", [
            {"reason_code": "SPATIAL_RELATION_NOT_NATIVE_GROUNDED"}
        ]

    spans = native_page["spans"]
    if not page_structure["elements"] or not spans:
        return "TEXT_ALIGNMENT_NEEDS_REVIEW", [
            {"reason_code": "TEXT_CONTENT_EMPTY"}
        ]

    elements_by_id = {
        element["id"]: element for element in page_structure["elements"]
    }
    matched_span_indexes = []
    findings = []
    for element_id in page_structure["reading_order"]:
        element = elements_by_id[element_id]
        candidates = [
            index
            for index, span in enumerate(spans)
            if span["text"] == element["text"] and span["bbox"] == element["bbox"]
        ]
        if len(candidates) != 1:
            findings.append(
                {
                    "element_id": element_id,
                    "reason_code": "NATIVE_SPAN_MATCH_UNCERTAIN",
                }
            )
        else:
            matched_span_indexes.append(candidates[0])
    if findings:
        return "TEXT_ALIGNMENT_NEEDS_REVIEW", findings
    if (
        len(set(matched_span_indexes)) != len(matched_span_indexes)
        or set(matched_span_indexes) != set(range(len(spans)))
    ):
        return "TEXT_ALIGNMENT_NEEDS_REVIEW", [
            {"reason_code": "NATIVE_SPAN_COVERAGE_UNCERTAIN"}
        ]
    if matched_span_indexes != sorted(matched_span_indexes):
        return "READING_ORDER_NEEDS_REVIEW", [
            {"reason_code": "NATIVE_READING_ORDER_CONFLICT"}
        ]
    return None, []


def assess_page_structure_alignment(
    page_structure: Any, page_evidence: Any, native_page: Any
) -> dict[str, Any]:
    """純函式評估 Page Structure 是否由同頁 native evidence 充分支持。"""
    page_structure_sha256 = _canonical_sha256(page_structure)
    native_sha256 = _canonical_sha256(native_page)
    failure_reason = validate_page_structure(page_structure, page_evidence)
    if failure_reason is None:
        failure_reason = _validate_native_binding(
            page_structure, page_evidence, native_page, native_sha256
        )
    if failure_reason is not None:
        return _result(
            page_structure,
            page_evidence,
            page_structure_sha256,
            native_sha256,
            processing="failed",
            quality="unsupported",
            decision="reject",
            reason_code=failure_reason,
            findings=[],
            trusted_identity=False,
        )

    reason_code, findings = _alignment_findings(page_structure, native_page)
    if reason_code is None:
        quality = "accepted"
        decision = "retain"
        reason_code = "ALIGNMENT_ACCEPTED"
    else:
        quality = "needs_review"
        decision = "review"
    return _result(
        page_structure,
        page_evidence,
        page_structure_sha256,
        native_sha256,
        processing="succeeded",
        quality=quality,
        decision=decision,
        reason_code=reason_code,
        findings=findings,
        trusted_identity=True,
    )
