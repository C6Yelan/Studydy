from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any
import unicodedata

import pymupdf


PAGE_SCHEMA = "page-evidence/v2"
NATIVE_SCHEMA = "page-native/v2"
RENDER_POLICY = "pymupdf-rgb-200dpi/v1"
PROCESSING_POLICY = "unlimited-ocr-page-evidence/v1"
NORMALIZER_POLICY = "ocr-text-nfc-line-preserving/v1"
_OCR_TYPE = re.compile(r"[A-Za-z_][\w-]{0,63}")


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _ref(kind: str, value: Any) -> str:
    return f"{kind}:sha256:{canonical_sha256(value)}"


def _box(value: Any) -> list[float]:
    return [float(value.x0), float(value.y0), float(value.x1), float(value.y1)]


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("OCR_LOCATOR_INVALID")
        return value
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, (pymupdf.Rect, pymupdf.IRect)):
        return _box(value)
    if isinstance(value, pymupdf.Point):
        return [float(value.x), float(value.y)]
    if isinstance(value, pymupdf.Quad):
        return [_json_value(point) for point in value]
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    raise ValueError("OCR_LOCATOR_INVALID")


def extract_page(
    document: pymupdf.Document, source_sha256: str, page_number: int
) -> dict[str, Any]:
    """在記憶體建立 200-DPI RGB PNG 與 native evidence。"""
    page = document.load_page(page_number - 1)
    if page.number + 1 != page_number:
        raise ValueError("OCR_LOCATOR_INVALID")
    visible = page.rect
    if visible.width <= 0 or visible.height <= 0 or page.rotation not in (0, 90, 180, 270):
        raise ValueError("OCR_LOCATOR_INVALID")
    estimated_width = math.ceil(visible.width * 200 / 72)
    estimated_height = math.ceil(visible.height * 200 / 72)
    estimated_rgb_bytes = estimated_width * estimated_height * 3
    if (
        estimated_rgb_bytes > 150_000_000
        or max(estimated_width, estimated_height) > 32_768
    ):
        raise ValueError("PROTOCOL_LIMIT_EXCEEDED")
    raw_text = _json_value(page.get_text("rawdict", sort=False))
    images = _json_value(page.get_image_info(hashes=True, xrefs=True))
    drawings = _json_value(page.get_drawings())
    pixmap = page.get_pixmap(dpi=200, colorspace=pymupdf.csRGB, alpha=False)
    png_bytes = pixmap.tobytes("png")
    if (
        pixmap.width * pixmap.height > 50_000_000
        or max(pixmap.width, pixmap.height) > 32_768
        or len(png_bytes) > 64 * 1024 * 1024
        or not png_bytes.startswith(b"\x89PNG\r\n\x1a\n")
    ):
        raise ValueError("PROTOCOL_LIMIT_EXCEEDED")
    material_id = f"material:sha256:{source_sha256}"
    material_revision = _ref("material-revision", {"source_sha256": source_sha256})
    page_ref = _ref("page", {"source_sha256": source_sha256, "page_number": page_number})
    section_id = _ref("section", {"page_ref": page_ref})
    native_evidence = {
        "schema": NATIVE_SCHEMA,
        "material_id": material_id,
        "material_revision": material_revision,
        "section_id": section_id,
        "page_ref": page_ref,
        "page_number": page_number,
        "raw_text": raw_text,
        "images": images,
        "drawings": drawings,
    }
    return {
        "material_id": material_id,
        "material_revision": material_revision,
        "section_id": section_id,
        "page_ref": page_ref,
        "page_number": page_number,
        "geometry": {
            "visible_points": _box(visible),
            "unrotated_points": _box(visible * page.derotation_matrix),
            "rotation_degrees": page.rotation,
            "derotation_matrix": [float(number) for number in page.derotation_matrix],
        },
        "native_evidence_ref": _ref("native-evidence", native_evidence),
        "native_evidence": native_evidence,
        "images": images,
        "png_bytes": png_bytes,
        "render": {
            "schema": "page-render/v1",
            "policy": RENDER_POLICY,
            "dpi": 200,
            "colorspace": "RGB",
            "format": "PNG",
            "coverage": "full_visible_page",
            "pymupdf_version": pymupdf.__version__,
            "width": pixmap.width,
            "height": pixmap.height,
            "sha256": hashlib.sha256(png_bytes).hexdigest(),
        },
    }


def _normalized_text(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("OCR_OUTPUT_INVALID")
    normalized = unicodedata.normalize(
        "NFC", value.replace("\r\n", "\n").replace("\r", "\n")
    )
    normalized = "\n".join(line.rstrip() for line in normalized.split("\n"))
    if not normalized.strip():
        raise ValueError("OCR_OUTPUT_INVALID")
    return normalized


def _kind(ocr_type: str) -> str:
    normalized = ocr_type.casefold().replace("-", "_")
    if normalized in {"title", "header", "heading", "section_header"}:
        return "heading"
    if normalized in {"list", "list_item"}:
        return "list"
    if normalized in {"code", "code_block"}:
        return "code"
    if normalized in {"caption", "figure_caption", "table_caption"}:
        return "caption"
    if normalized in {"image", "figure", "image_text"}:
        return "image_text"
    if normalized in {"text", "paragraph", "body"}:
        return "paragraph"
    return "other"


def _locator(normalized_bbox: Any, page: dict[str, Any]) -> tuple[list[float], list[float]]:
    if (
        not isinstance(normalized_bbox, list)
        or len(normalized_bbox) != 4
        or any(type(number) not in {int, float} or not math.isfinite(number) for number in normalized_bbox)
        or not (
            0 <= normalized_bbox[0] < normalized_bbox[2] <= 1000
            and 0 <= normalized_bbox[1] < normalized_bbox[3] <= 1000
        )
    ):
        raise ValueError("OCR_LOCATOR_INVALID")
    width, height = page["render"]["width"], page["render"]["height"]
    render_region = [
        normalized_bbox[0] * width / 1000,
        normalized_bbox[1] * height / 1000,
        normalized_bbox[2] * width / 1000,
        normalized_bbox[3] * height / 1000,
    ]
    visible = pymupdf.Rect(page["geometry"]["visible_points"])
    rotated = pymupdf.Rect(
        render_region[0] * visible.width / width,
        render_region[1] * visible.height / height,
        render_region[2] * visible.width / width,
        render_region[3] * visible.height / height,
    )
    unrotated = rotated * pymupdf.Matrix(*page["geometry"]["derotation_matrix"])
    boundary = pymupdf.Rect(page["geometry"]["unrotated_points"])
    clipped = unrotated & boundary
    if clipped.width <= 0 or clipped.height <= 0:
        raise ValueError("OCR_LOCATOR_INVALID")
    return render_region, _box(clipped)


def _distance(first: list[float], second: list[float]) -> float:
    dx = max(first[0] - second[2], second[0] - first[2], 0)
    dy = max(first[1] - second[3], second[1] - first[3], 0)
    return math.hypot(dx, dy)


def build_page_evidence(
    page: dict[str, Any],
    ocr_blocks: Any,
    *,
    input_binding: dict[str, Any],
    produced_at: str,
) -> dict[str, Any]:
    """只保留可建立同頁文字 Evidence 的 blocks；child contract 仍整體 fail closed。"""
    if not isinstance(ocr_blocks, list) or not ocr_blocks:
        raise ValueError("OCR_OUTPUT_INVALID")
    native_evidence = page.get("native_evidence")
    if (
        not isinstance(native_evidence, dict)
        or native_evidence.get("page_number") != page.get("page_number")
        or native_evidence.get("page_ref") != page.get("page_ref")
        or native_evidence.get("material_id") != page.get("material_id")
        or native_evidence.get("material_revision") != page.get("material_revision")
        or native_evidence.get("section_id") != page.get("section_id")
    ):
        raise ValueError("OCR_LOCATOR_INVALID")
    evidence_blocks: list[dict[str, Any]] = []
    has_rejected_block = False
    for reading_order, block in enumerate(ocr_blocks):
        if not isinstance(block, dict) or set(block) != {"type", "text", "bbox"}:
            raise ValueError("OCR_OUTPUT_INVALID")
        ocr_type = block["type"]
        if not isinstance(ocr_type, str) or _OCR_TYPE.fullmatch(ocr_type) is None:
            raise ValueError("OCR_OUTPUT_INVALID")
        if not isinstance(block["text"], str):
            raise ValueError("OCR_OUTPUT_INVALID")
        try:
            text = _normalized_text(block["text"])
            render_region, region = _locator(block["bbox"], page)
        except ValueError:
            has_rejected_block = True
            continue
        block_id = _ref(
            "block",
            {"page_ref": page["page_ref"], "reading_order": reading_order, "region": region},
        )
        identity = {
            "page_ref": page["page_ref"],
            "block_id": block_id,
            "ocr_type": ocr_type,
            "text": text,
            "reading_order": reading_order,
            "region": region,
        }
        evidence_blocks.append(
            {
                "evidence_id": _ref("evidence", identity),
                "block_id": block_id,
                "ocr_type": ocr_type,
                "kind": _kind(ocr_type),
                "text": text,
                "reading_order": reading_order,
                "locator": {
                    "page": page["page_number"],
                    "block_id": block_id,
                    "region": region,
                },
                "render_region": render_region,
                "source": "unlimited_ocr",
            }
        )
    if not evidence_blocks:
        raise ValueError("NO_USABLE_EVIDENCE")
    image_artifacts: list[dict[str, Any]] = []
    has_rejected_image = False
    for ordinal, image in enumerate(page["images"]):
        bbox = image.get("bbox") if isinstance(image, dict) else None
        if not isinstance(bbox, list) or len(bbox) != 4:
            has_rejected_image = True
            continue
        try:
            region = [float(number) for number in bbox]
        except (TypeError, ValueError):
            has_rejected_image = True
            continue
        if any(not math.isfinite(number) for number in region) or not (
            region[0] < region[2] and region[1] < region[3]
        ):
            has_rejected_image = True
            continue
        captions = [
            block["evidence_id"]
            for block in evidence_blocks
            if block["kind"] == "caption" and _distance(region, block["locator"]["region"]) <= 36
        ]
        nearby = sorted(
            (
                (_distance(region, block["locator"]["region"]), block["reading_order"], block["evidence_id"])
                for block in evidence_blocks
                if block["kind"] != "caption" and _distance(region, block["locator"]["region"]) <= 72
            )
        )[:4]
        image_artifacts.append(
            {
                "image_id": _ref("image", {"page_ref": page["page_ref"], "ordinal": ordinal, "region": region}),
                "image_hash": image.get("digest"),
                "region": region,
                "caption_evidence_ids": sorted(set(captions)),
                "nearby_evidence_ids": [item[2] for item in nearby],
            }
        )
    reasons = ["PAGE_CONTENT_REVIEW_REQUIRED"]
    if has_rejected_block or has_rejected_image:
        reasons.append("OCR_OUTPUT_INVALID")
    artifact = {
        "schema": PAGE_SCHEMA,
        "material_id": page["material_id"],
        "material_revision": page["material_revision"],
        "section_id": page["section_id"],
        "page_ref": page["page_ref"],
        "page_number": page["page_number"],
        "geometry": page["geometry"],
        "coordinate_space": "unrotated_pdf_points",
        "native_evidence_ref": page["native_evidence_ref"],
        "render": page["render"],
        "evidence_blocks": evidence_blocks,
        "images": image_artifacts,
        "input_binding": input_binding,
        "processing_policy": PROCESSING_POLICY,
        "normalizer_policy": NORMALIZER_POLICY,
        "produced_at": produced_at,
        "processing": "partial" if has_rejected_block or has_rejected_image else "succeeded",
        "quality": "needs_review",
        "decision": "review",
        "reason_codes": reasons,
    }
    artifact["page_evidence_id"] = _ref("page-evidence", artifact)
    if len(canonical_bytes(artifact)) > 4 * 1024 * 1024:
        raise ValueError("PROTOCOL_LIMIT_EXCEEDED")
    return artifact
