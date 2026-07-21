from __future__ import annotations

import contextlib
import io
import math
import re
from pathlib import Path
from typing import Any, Mapping, Sequence

import pymupdf

from material_blocks import normalize_text


SCHEMA_VERSION = "material-native-analysis/v1"
BBOX_TOLERANCE_POINTS = 0.5
SCAN_IMAGE_AREA_RATIO = 0.5
COMPLEX_IMAGE_AREA_RATIO = 0.10
COMPLEX_VECTOR_AREA_RATIO = 0.10
COMPLEX_VECTOR_COUNT = 8
BACKGROUND_IMAGE_AREA_RATIO = 0.90


def analyze_material_native(
    material_blocks: Mapping[str, Any],
    pdf_paths: Mapping[str, str | Path],
) -> dict[str, Any]:
    """Summarize native PDF structure without retaining page content."""
    if material_blocks.get("schema_version") != "material-blocks/v1":
        raise ValueError("material_blocks_schema_mismatch")
    materials = material_blocks.get("materials")
    if not isinstance(materials, list):
        raise ValueError("materials_invalid")

    rows: list[dict[str, Any]] = []
    for material in materials:
        if not isinstance(material, Mapping):
            raise ValueError("material_invalid")
        rows.extend(_analyze_material(material, pdf_paths))

    rows.sort(key=lambda row: (row["material_id"], row["pdf_page"]))
    return {
        "schema_version": SCHEMA_VERSION,
        "page_count": len(rows),
        "pages": rows,
    }


def _analyze_material(
    material: Mapping[str, Any],
    pdf_paths: Mapping[str, str | Path],
) -> list[dict[str, Any]]:
    material_id = material.get("material_id")
    case_id = material.get("case_id")
    artifact_ref = material.get("artifact_ref")
    blocks = material.get("blocks")
    if not isinstance(material_id, str) or not material_id:
        raise ValueError("material_id_missing")
    if not isinstance(case_id, str) or not case_id:
        raise ValueError("case_id_missing")
    if not isinstance(blocks, list):
        raise ValueError("blocks_invalid")

    pdf_path = pdf_paths.get(case_id)
    if pdf_path is None:
        return [
            _failed_row(
                material_id,
                case_id,
                artifact_ref,
                block,
                "document_path_missing",
            )
            for block in blocks
        ]

    try:
        document = pymupdf.open(Path(pdf_path))
    except Exception:
        return [
            _failed_row(
                material_id,
                case_id,
                artifact_ref,
                block,
                "document_unreadable",
            )
            for block in blocks
        ]

    try:
        page_count_mismatch = document.page_count != len(blocks)
        rows = []
        for block in blocks:
            if not isinstance(block, Mapping):
                raise ValueError("block_invalid")
            locator = block.get("locator")
            if not isinstance(locator, Mapping):
                raise ValueError("locator_missing")
            pdf_page = locator.get("pdf_page")
            if not isinstance(pdf_page, int) or pdf_page < 1:
                raise ValueError("page_locator_missing")
            try:
                page = document.load_page(pdf_page - 1)
            except Exception:
                rows.append(
                    _failed_row(
                        material_id,
                        case_id,
                        artifact_ref,
                        block,
                        "page_unreadable",
                    )
                )
                continue
            row = _analyze_page(material_id, case_id, artifact_ref, block, page)
            if page_count_mismatch:
                row["status"] = "partial"
                row["reasons"] = sorted(
                    {*row["reasons"], "document_page_count_mismatch"}
                )
            rows.append(row)
        return rows
    finally:
        document.close()


def _provenance() -> dict[str, Any]:
    return {
        "library": "PyMuPDF",
        "library_version": pymupdf.VersionBind,
        "baseline_policy": "page.get_text:text:sort-true-v1",
        "native_policy": (
            "rawdict+words+blocks+displayed-images+drawings+find-tables-v1"
        ),
        "bbox_tolerance_points": BBOX_TOLERANCE_POINTS,
        "geometry_area_policy": "summed-clipped-area-capped-v1",
    }


def _identity(
    material_id: str,
    case_id: str,
    artifact_ref: Any,
    block: Mapping[str, Any],
) -> dict[str, Any]:
    locator = block.get("locator")
    if not isinstance(locator, Mapping):
        locator = {}
    return {
        "material_id": material_id,
        "case_id": case_id,
        "artifact_ref": artifact_ref if isinstance(artifact_ref, str) else None,
        "block_id": block.get("block_id")
        if isinstance(block.get("block_id"), str)
        else None,
        "pdf_page": locator.get("pdf_page")
        if isinstance(locator.get("pdf_page"), int)
        else None,
        "source_ref": locator.get("source_ref")
        if isinstance(locator.get("source_ref"), str)
        else None,
    }


def _empty_bbox_summary() -> dict[str, int]:
    return {
        "total": 0,
        "valid": 0,
        "inside_page_tolerance": 0,
        "outside_page_tolerance": 0,
        "invalid": 0,
    }


def _empty_native_summary() -> dict[str, Any]:
    return {
        "rawdict": {
            "available": False,
            "text_blocks": 0,
            "image_blocks": 0,
            "block_bboxes": _empty_bbox_summary(),
            "span_bboxes": _empty_bbox_summary(),
            "character_bboxes": _empty_bbox_summary(),
            "character_count": 0,
        },
        "words": {
            "available": False,
            "count": 0,
            "bboxes": _empty_bbox_summary(),
        },
        "blocks": {
            "available": False,
            "count": 0,
            "text_blocks": 0,
            "image_blocks": 0,
            "bboxes": _empty_bbox_summary(),
        },
        "displayed_images": {
            "available": False,
            "count": 0,
            "bboxes": _empty_bbox_summary(),
            "page_area_ratio": 0.0,
            "background_like_count": 0,
            "non_background_count": 0,
            "non_background_page_area_ratio": 0.0,
        },
        "vectors": {
            "available": False,
            "count": 0,
            "bboxes": _empty_bbox_summary(),
            "page_area_ratio": 0.0,
        },
        "tables": {
            "available": False,
            "count": 0,
            "bboxes": _empty_bbox_summary(),
        },
    }


def _empty_comparability(block: Mapping[str, Any]) -> dict[str, Any]:
    baseline_text = block.get("text")
    return {
        "baseline_status": block.get("parser_status"),
        "baseline_nonempty": isinstance(baseline_text, str) and bool(baseline_text),
        "rawdict_nonempty": False,
        "blocks_nonempty": False,
        "rawdict_exact_match": None,
        "blocks_exact_match": None,
        "words_non_whitespace_match": None,
    }


def _empty_signals() -> dict[str, dict[str, Any]]:
    return {
        "ordinary_layout": {"detected": False, "evidence": []},
        "scan_image_text": {"detected": False, "evidence": []},
        "complex_structure": {"detected": False, "evidence": []},
    }


def _failed_row(
    material_id: str,
    case_id: str,
    artifact_ref: Any,
    block: Mapping[str, Any],
    reason: str,
) -> dict[str, Any]:
    signals = _empty_signals()
    return {
        **_identity(material_id, case_id, artifact_ref, block),
        "page_bbox": None,
        "provenance": _provenance(),
        "native_summary": _empty_native_summary(),
        "comparability": _empty_comparability(block),
        "reading_order": {"assessment": "unavailable", "reasons": [reason]},
        "gap_signals": signals,
        "gap_class": "analysis_unavailable",
        "next_task_eligibility": _eligibility(signals),
        "status": "failed",
        "reasons": [reason],
    }


def _analyze_page(
    material_id: str,
    case_id: str,
    artifact_ref: Any,
    block: Mapping[str, Any],
    page: pymupdf.Page,
) -> dict[str, Any]:
    page_bbox = _rect_values(page.rect)
    if page_bbox is None:
        return _failed_row(
            material_id,
            case_id,
            artifact_ref,
            block,
            "page_bbox_invalid",
        )

    reasons: list[str] = []
    summary = _empty_native_summary()
    rawdict_text: str | None = None
    blocks_text: str | None = None
    word_text: str | None = None

    try:
        rawdict = page.get_text("rawdict", sort=True)
        rawdict_text = _summarize_rawdict(rawdict, summary["rawdict"], page_bbox)
    except AttributeError:
        reasons.append("rawdict_api_unsupported")
    except Exception:
        reasons.append("rawdict_analysis_failed")

    try:
        words = page.get_text("words", sort=True)
        word_text = _summarize_words(words, summary["words"], page_bbox)
    except AttributeError:
        reasons.append("words_api_unsupported")
    except Exception:
        reasons.append("words_analysis_failed")

    try:
        blocks = page.get_text("blocks", sort=True)
        blocks_text = _summarize_blocks(blocks, summary["blocks"], page_bbox)
    except AttributeError:
        reasons.append("blocks_api_unsupported")
    except Exception:
        reasons.append("blocks_analysis_failed")

    try:
        images = page.get_image_info(hashes=False, xrefs=False)
        image_bboxes = [image.get("bbox") for image in images]
        summary["displayed_images"] = _displayed_image_summary(
            image_bboxes,
            page_bbox,
        )
    except AttributeError:
        reasons.append("displayed_image_api_unsupported")
    except Exception:
        reasons.append("displayed_image_analysis_failed")

    try:
        drawings = page.get_drawings()
        drawing_bboxes = [drawing.get("rect") for drawing in drawings]
        summary["vectors"] = {
            "available": True,
            "count": len(drawings),
            "bboxes": _bbox_summary(drawing_bboxes, page_bbox),
            "page_area_ratio": _page_area_ratio(drawing_bboxes, page_bbox),
        }
    except AttributeError:
        reasons.append("drawing_api_unsupported")
    except Exception:
        reasons.append("drawing_analysis_failed")

    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(
            io.StringIO()
        ):
            table_finder = page.find_tables()
        tables = list(table_finder.tables)
        table_bboxes = [table.bbox for table in tables]
        summary["tables"] = {
            "available": True,
            "count": len(tables),
            "bboxes": _bbox_summary(table_bboxes, page_bbox),
        }
    except AttributeError:
        reasons.append("table_api_unsupported")
    except Exception:
        reasons.append("table_analysis_failed")

    reasons.extend(_bbox_reason_codes(summary))

    comparability = _compare_with_baseline(
        block,
        rawdict_text,
        blocks_text,
        word_text,
    )
    reading_order = _reading_order(comparability)
    signals = _gap_signals(summary, comparability, reading_order)
    if _identity(material_id, case_id, artifact_ref, block)["source_ref"] is None:
        reasons.append("source_ref_missing")

    return {
        **_identity(material_id, case_id, artifact_ref, block),
        "page_bbox": page_bbox,
        "provenance": _provenance(),
        "native_summary": summary,
        "comparability": comparability,
        "reading_order": reading_order,
        "gap_signals": signals,
        "gap_class": _gap_class(signals),
        "next_task_eligibility": _eligibility(signals),
        "status": _status_for_reasons(reasons),
        "reasons": sorted(set(reasons)),
    }


def _summarize_rawdict(
    rawdict: Any,
    output: dict[str, Any],
    page_bbox: list[float],
) -> str:
    if not isinstance(rawdict, Mapping) or not isinstance(rawdict.get("blocks"), list):
        raise ValueError("rawdict_invalid")
    blocks = rawdict["blocks"]
    block_bboxes = []
    span_bboxes = []
    character_bboxes = []
    text_blocks = 0
    image_blocks = 0
    block_texts = []
    character_count = 0
    for block in blocks:
        if not isinstance(block, Mapping):
            continue
        block_bboxes.append(block.get("bbox"))
        if block.get("type") == 1:
            image_blocks += 1
            continue
        if block.get("type") != 0:
            continue
        text_blocks += 1
        line_texts = []
        for line in block.get("lines", []):
            span_texts = []
            for span in line.get("spans", []):
                span_bboxes.append(span.get("bbox"))
                characters = span.get("chars", [])
                text = "".join(
                    character.get("c", "")
                    for character in characters
                    if isinstance(character, Mapping)
                )
                character_count += len(text)
                character_bboxes.extend(
                    character.get("bbox")
                    for character in characters
                    if isinstance(character, Mapping)
                )
                span_texts.append(text)
            line_texts.append("".join(span_texts))
        block_texts.append("\n".join(line_texts))
    output.update(
        {
            "available": True,
            "text_blocks": text_blocks,
            "image_blocks": image_blocks,
            "block_bboxes": _bbox_summary(block_bboxes, page_bbox),
            "span_bboxes": _bbox_summary(span_bboxes, page_bbox),
            "character_bboxes": _bbox_summary(character_bboxes, page_bbox),
            "character_count": character_count,
        }
    )
    return normalize_text("\n".join(block_texts))


def _summarize_words(
    words: Any,
    output: dict[str, Any],
    page_bbox: list[float],
) -> str:
    if not isinstance(words, list):
        raise ValueError("words_invalid")
    bboxes = []
    text_parts = []
    for word in words:
        if not isinstance(word, Sequence) or len(word) < 5:
            continue
        bboxes.append(word[:4])
        if isinstance(word[4], str):
            text_parts.append(word[4])
    output.update(
        {
            "available": True,
            "count": len(words),
            "bboxes": _bbox_summary(bboxes, page_bbox),
        }
    )
    return "".join(text_parts)


def _summarize_blocks(
    blocks: Any,
    output: dict[str, Any],
    page_bbox: list[float],
) -> str:
    if not isinstance(blocks, list):
        raise ValueError("blocks_invalid")
    bboxes = []
    text_parts = []
    text_blocks = 0
    image_blocks = 0
    for block in blocks:
        if not isinstance(block, Sequence) or len(block) < 7:
            continue
        bboxes.append(block[:4])
        if block[6] == 0:
            text_blocks += 1
            if isinstance(block[4], str):
                text_parts.append(block[4])
        elif block[6] == 1:
            image_blocks += 1
    output.update(
        {
            "available": True,
            "count": len(blocks),
            "text_blocks": text_blocks,
            "image_blocks": image_blocks,
            "bboxes": _bbox_summary(bboxes, page_bbox),
        }
    )
    return normalize_text("\n".join(text_parts))


def _compare_with_baseline(
    block: Mapping[str, Any],
    rawdict_text: str | None,
    blocks_text: str | None,
    word_text: str | None,
) -> dict[str, Any]:
    baseline_text = block.get("text")
    baseline_comparable = (
        block.get("parser_status") == "success" and isinstance(baseline_text, str)
    )
    result = {
        "baseline_status": block.get("parser_status"),
        "baseline_nonempty": baseline_comparable and bool(baseline_text),
        "rawdict_nonempty": isinstance(rawdict_text, str) and bool(rawdict_text),
        "blocks_nonempty": isinstance(blocks_text, str) and bool(blocks_text),
        "rawdict_exact_match": None,
        "blocks_exact_match": None,
        "words_non_whitespace_match": None,
    }
    if not baseline_comparable:
        return result
    if rawdict_text is not None:
        result["rawdict_exact_match"] = rawdict_text == baseline_text
    if blocks_text is not None:
        result["blocks_exact_match"] = blocks_text == baseline_text
    if word_text is not None:
        baseline_without_space = re.sub(r"\s+", "", baseline_text)
        result["words_non_whitespace_match"] = word_text == baseline_without_space
    return result


def _reading_order(comparability: Mapping[str, Any]) -> dict[str, Any]:
    if comparability.get("baseline_status") != "success":
        return {
            "assessment": "not_comparable",
            "reasons": ["baseline_not_successful"],
        }
    if not comparability.get("baseline_nonempty"):
        return {
            "assessment": "not_comparable",
            "reasons": ["baseline_empty"],
        }
    word_match = comparability.get("words_non_whitespace_match")
    comparisons = {
        "rawdict_order_differs": comparability.get("rawdict_exact_match"),
        "blocks_order_differs": comparability.get("blocks_exact_match"),
    }
    if word_match is True:
        return {"assessment": "match", "reasons": []}
    if word_match is False:
        return {
            "assessment": "divergent",
            "reasons": ["words_order_differs"],
        }
    available = [value for value in comparisons.values() if value is not None]
    if not available:
        return {
            "assessment": "unavailable",
            "reasons": ["native_text_comparison_unavailable"],
        }
    reasons = [reason for reason, matches in comparisons.items() if matches is False]
    return {
        "assessment": "divergent" if reasons else "match",
        "reasons": sorted(reasons),
    }


def _gap_signals(
    summary: Mapping[str, Any],
    comparability: Mapping[str, Any],
    reading_order: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    baseline_nonempty = bool(comparability.get("baseline_nonempty"))
    native_characters = summary["rawdict"]["character_count"]
    image_ratio = summary["displayed_images"]["page_area_ratio"]
    non_background_image_ratio = summary["displayed_images"][
        "non_background_page_area_ratio"
    ]
    vector_ratio = summary["vectors"]["page_area_ratio"]
    table_count = summary["tables"]["count"]
    vector_count = summary["vectors"]["count"]

    ordinary_evidence = []
    if baseline_nonempty and reading_order.get("assessment") == "divergent":
        ordinary_evidence.append("reading_order_divergent")

    scan_evidence = []
    if (
        not baseline_nonempty
        and native_characters == 0
        and image_ratio >= SCAN_IMAGE_AREA_RATIO
    ):
        scan_evidence.extend(
            ["baseline_and_native_text_empty", "large_displayed_image_area"]
        )

    complex_evidence = []
    if table_count > 0:
        complex_evidence.append("native_table_geometry")
    if non_background_image_ratio >= COMPLEX_IMAGE_AREA_RATIO:
        complex_evidence.append("non_background_displayed_image_geometry")
    if (
        vector_count >= COMPLEX_VECTOR_COUNT
        and vector_ratio >= COMPLEX_VECTOR_AREA_RATIO
        and reading_order.get("assessment") == "divergent"
    ):
        complex_evidence.append("dense_vector_geometry_with_order_divergence")

    return {
        "ordinary_layout": {
            "detected": bool(ordinary_evidence),
            "evidence": ordinary_evidence,
        },
        "scan_image_text": {
            "detected": bool(scan_evidence),
            "evidence": scan_evidence,
        },
        "complex_structure": {
            "detected": bool(complex_evidence),
            "evidence": complex_evidence,
        },
    }


def _gap_class(signals: Mapping[str, Mapping[str, Any]]) -> str:
    if signals["scan_image_text"]["detected"]:
        return "scan_image_text_gap"
    if signals["complex_structure"]["detected"]:
        return "complex_structure_gap"
    if signals["ordinary_layout"]["detected"]:
        return "ordinary_layout_gap"
    return "no_detected_native_gap"


def _eligibility(signals: Mapping[str, Mapping[str, Any]]) -> dict[str, bool]:
    return {
        "task5_ordinary_layout": bool(signals["ordinary_layout"]["detected"]),
        "task6_scan_image_text": bool(signals["scan_image_text"]["detected"]),
        "task7_complex_structure": bool(signals["complex_structure"]["detected"]),
    }


def _rect_values(value: Any) -> list[float] | None:
    try:
        rect = pymupdf.Rect(value)
    except Exception:
        return None
    coordinates = [float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1)]
    if not all(math.isfinite(coordinate) for coordinate in coordinates):
        return None
    if rect.width <= 0 or rect.height <= 0:
        return None
    return coordinates


def _bbox_summary(
    values: Sequence[Any],
    page_bbox: list[float],
) -> dict[str, int]:
    result = _empty_bbox_summary()
    result["total"] = len(values)
    page = pymupdf.Rect(page_bbox)
    tolerance = BBOX_TOLERANCE_POINTS
    for value in values:
        bbox = _rect_values(value)
        if bbox is None:
            result["invalid"] += 1
            continue
        result["valid"] += 1
        rect = pymupdf.Rect(bbox)
        if (
            rect.x0 >= page.x0 - tolerance
            and rect.y0 >= page.y0 - tolerance
            and rect.x1 <= page.x1 + tolerance
            and rect.y1 <= page.y1 + tolerance
        ):
            result["inside_page_tolerance"] += 1
        else:
            result["outside_page_tolerance"] += 1
    return result


def _page_area_ratio(values: Sequence[Any], page_bbox: list[float]) -> float:
    page = pymupdf.Rect(page_bbox)
    area = 0.0
    for value in values:
        bbox = _rect_values(value)
        if bbox is None:
            continue
        clipped = pymupdf.Rect(bbox) & page
        if clipped.width > 0 and clipped.height > 0:
            area += clipped.width * clipped.height
    return round(min(area / (page.width * page.height), 1.0), 6)


def _displayed_image_summary(
    values: Sequence[Any],
    page_bbox: list[float],
) -> dict[str, Any]:
    page = pymupdf.Rect(page_bbox)
    non_background = []
    background_like_count = 0
    for value in values:
        bbox = _rect_values(value)
        if bbox is None:
            continue
        clipped = pymupdf.Rect(bbox) & page
        ratio = (clipped.width * clipped.height) / (page.width * page.height)
        if ratio >= BACKGROUND_IMAGE_AREA_RATIO:
            background_like_count += 1
        else:
            non_background.append(value)
    return {
        "available": True,
        "count": len(values),
        "bboxes": _bbox_summary(values, page_bbox),
        "page_area_ratio": _page_area_ratio(values, page_bbox),
        "background_like_count": background_like_count,
        "non_background_count": len(non_background),
        "non_background_page_area_ratio": _page_area_ratio(
            non_background,
            page_bbox,
        ),
    }


def _status_for_reasons(reasons: Sequence[str]) -> str:
    non_blocking = {"native_bbox_outside_page_tolerance"}
    return "partial" if any(reason not in non_blocking for reason in reasons) else "success"


def _bbox_reason_codes(summary: Mapping[str, Any]) -> list[str]:
    bbox_summaries = [
        summary["rawdict"]["block_bboxes"],
        summary["rawdict"]["span_bboxes"],
        summary["rawdict"]["character_bboxes"],
        summary["words"]["bboxes"],
        summary["blocks"]["bboxes"],
        summary["displayed_images"]["bboxes"],
        summary["vectors"]["bboxes"],
        summary["tables"]["bboxes"],
    ]
    reasons = []
    if any(item["invalid"] for item in bbox_summaries):
        reasons.append("native_bbox_invalid")
    if any(item["outside_page_tolerance"] for item in bbox_summaries):
        reasons.append("native_bbox_outside_page_tolerance")
    return reasons
