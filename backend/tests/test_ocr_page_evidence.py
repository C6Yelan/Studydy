import hashlib
from pathlib import Path

import pymupdf
import pytest

from pdf_evidence.document_context import build_document_contexts
from pdf_evidence.ocr_page_evidence import (
    build_native_page_evidence,
    build_page_evidence,
    extract_page,
    route_page,
)


def _pdf(path: Path, *, rotated=False):
    document = pymupdf.open()
    page = document.new_page(width=144, height=216)
    page.insert_text((18, 30), "Public native text")
    if rotated:
        page.set_rotation(90)
    document.save(path)
    document.close()


def _extract(path: Path):
    source_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    with pymupdf.open(path) as document:
        return extract_page(document, source_sha256, 1)


def test_200dpi_rgb_page_identity_and_ocr_locator(tmp_path):
    path = tmp_path / "public.pdf"
    _pdf(path)
    page = _extract(path)
    long_ocr_type = "custom_" + "x" * 64
    page["images"] = [
        {"bbox": [0.0, 0.0, 1.0, 1.0], "digest": f"{ordinal:064x}"}
        for ordinal in range(257)
    ]
    assert page["render"]["dpi"] == 200
    assert page["render"]["colorspace"] == "RGB"
    assert (page["render"]["width"], page["render"]["height"]) == (400, 600)
    artifact = build_page_evidence(
        page,
        [
            {
                "type": long_ocr_type if ordinal == 0 else "text",
                "text": "  first line\n    second line" if ordinal == 0 else f"Public OCR text {ordinal}",
                "bbox": [100, 100, 900, 300],
            }
            for ordinal in range(65)
        ],
        input_binding={"fixed": True},
        produced_at="2026-08-18T00:00:00Z",
    )
    block = artifact["evidence_blocks"][0]
    assert artifact["schema"] == "page-evidence/v3"
    assert artifact["route"] == "OCR_needed"
    assert artifact["processing"] == "succeeded"
    assert artifact["decision"] == "review"
    assert block["locator"]["page"] == 1
    assert block["render_region"] == [40.0, 60.0, 360.0, 180.0]
    assert block["source"] == "unlimited_ocr"
    assert block["ocr_type"] == long_ocr_type
    assert block["text"] == "  first line\n    second line"
    assert len(artifact["evidence_blocks"]) == 65
    assert len(artifact["images"]) == 257
    assert artifact["images"][0]["nearby_evidence_ids"] == [
        evidence["evidence_id"] for evidence in artifact["evidence_blocks"]
    ]
    assert "png_bytes" not in artifact
    assert artifact["reason_codes"] == ["PAGE_CONTENT_REVIEW_REQUIRED"]


def test_native_text_routes_without_ocr_and_keeps_pdf_bbox_order(tmp_path):
    path = tmp_path / "native.pdf"
    _pdf(path)
    page = _extract(path)
    assert route_page(page) == "native_sufficient"
    artifact = build_native_page_evidence(
        page,
        input_binding={"route": "native_sufficient"},
        produced_at="x",
    )
    assert artifact["route"] == "native_sufficient"
    assert artifact["evidence_blocks"][0]["source"] == "native_text"
    assert artifact["evidence_blocks"][0]["reading_order"] == 0
    assert artifact["evidence_blocks"][0]["locator"]["page"] == 1


def test_non_centered_native_heading_starts_stable_following_page_section(
    tmp_path,
):
    path = tmp_path / "native-heading.pdf"
    document = pymupdf.open()
    first = document.new_page(width=612, height=792)
    first.insert_text((72, 72), "Public Learning Objective", fontsize=20)
    first.insert_text(
        (72, 120),
        "This ordinary body sentence provides enough native text for routing.",
        fontsize=12,
    )
    second = document.new_page(width=612, height=792)
    second.insert_text(
        (72, 90),
        "The following page continues the same public learning section.",
        fontsize=12,
    )
    document.save(path)
    document.close()
    source_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()

    with pymupdf.open(path) as document:
        pages = [
            extract_page(document, source_sha256, page_number)
            for page_number in (1, 2)
        ]
    assert [route_page(page) for page in pages] == [
        "native_sufficient",
        "native_sufficient",
    ]
    artifacts = [
        build_native_page_evidence(
            page,
            input_binding={"route": "native_sufficient"},
            produced_at="2026-08-29T00:00:00Z",
        )
        for page in pages
    ]

    assert [
        block["kind"] for block in artifacts[0]["evidence_blocks"]
    ] == ["heading", "paragraph"]
    assert [
        block["reading_order"] for block in artifacts[0]["evidence_blocks"]
    ] == [0, 1]
    assert all(
        block["block_id"] == block["locator"]["block_id"]
        and block["locator"]["page"] == 1
        for block in artifacts[0]["evidence_blocks"]
    )
    replay = build_native_page_evidence(
        pages[0],
        input_binding={"route": "native_sufficient"},
        produced_at="2026-08-29T00:00:00Z",
    )
    assert replay == artifacts[0]

    contexts = build_document_contexts(artifacts)
    heading_block_id = artifacts[0]["evidence_blocks"][0]["block_id"]
    heading_section_id = contexts[0]["current_blocks"][0]["section_id"]
    assert contexts[1]["current_blocks"][0]["heading_ancestry_block_ids"] == [
        heading_block_id
    ]
    assert contexts[1]["current_blocks"][0]["section_id"] == heading_section_id


def test_native_body_and_small_emphasis_do_not_become_headings(tmp_path):
    path = tmp_path / "native-emphasis.pdf"
    document = pymupdf.open()
    page = document.new_page(width=612, height=792)
    page.insert_text(
        (72, 72),
        "Ordinary body text establishes the dominant public font size.",
        fontsize=12,
    )
    page.insert_text(
        (72, 105),
        "Important emphasized phrase",
        fontsize=13,
        fontname="hebo",
    )
    page.insert_text(
        (72, 138),
        "Another ordinary sentence confirms this is body content.",
        fontsize=12,
    )
    document.save(path)
    document.close()

    extracted = _extract(path)
    assert route_page(extracted) == "native_sufficient"
    artifact = build_native_page_evidence(
        extracted,
        input_binding={"route": "native_sufficient"},
        produced_at="x",
    )

    assert all(
        block["kind"] == "paragraph"
        for block in artifact["evidence_blocks"]
    )


def test_empty_and_garbled_native_text_route_to_ocr(tmp_path):
    path = tmp_path / "scan.pdf"
    document = pymupdf.open()
    document.new_page(width=144, height=216)
    document.save(path)
    document.close()
    assert route_page(_extract(path)) == "OCR_needed"

    page = _extract(path)
    page["native_evidence"]["raw_text"] = {
        "blocks": [{"type": 0, "lines": [{"bbox": [1, 1, 20, 20], "spans": [{"text": "��������"}]}]}]
    }
    assert route_page(page) == "OCR_needed"


def test_render_guard_rejects_geometry_before_page_content_or_pixmap_reads():
    class OversizedPage:
        number = 0
        rotation = 0
        rect = pymupdf.Rect(0, 0, 20_000, 20_000)

        def get_text(self, *_args, **_kwargs):
            raise AssertionError("page content must not be read")

        def get_pixmap(self, *_args, **_kwargs):
            raise AssertionError("pixmap must not be rendered")

    class Document:
        def load_page(self, _index):
            return OversizedPage()

    with pytest.raises(ValueError, match="PROTOCOL_LIMIT_EXCEEDED"):
        extract_page(Document(), "0" * 64, 1)


def test_rotated_page_locator_stays_on_same_one_based_page(tmp_path):
    path = tmp_path / "rotated.pdf"
    _pdf(path, rotated=True)
    page = _extract(path)
    artifact = build_page_evidence(
        page,
        [{"type": "title", "text": "Public title", "bbox": [100, 100, 900, 300]}],
        input_binding={},
        produced_at="x",
    )
    region = artifact["evidence_blocks"][0]["locator"]["region"]
    assert artifact["page_number"] == 1
    assert region == pytest.approx([14.4, 21.6, 43.2, 194.4])


def test_blank_and_image_only_blocks_are_rejected_but_text_page_remains(tmp_path):
    path = tmp_path / "public.pdf"
    _pdf(path)
    page = _extract(path)
    page["images"] = [{"bbox": "invalid"}]
    artifact = build_page_evidence(
        page,
        [
            {"type": "text", "text": "Usable public text", "bbox": [10, 10, 900, 200]},
            {"type": "text", "text": " \n ", "bbox": [10, 220, 900, 300]},
            {"type": "image", "text": "", "bbox": [10, 320, 900, 800]},
        ],
        input_binding={},
        produced_at="x",
    )
    assert [block["text"] for block in artifact["evidence_blocks"]] == ["Usable public text"]
    assert artifact["processing"] == "partial"
    assert artifact["quality"] == "needs_review"
    assert artifact["decision"] == "review"
    assert artifact["images"] == []
    assert artifact["reason_codes"] == ["PAGE_CONTENT_REVIEW_REQUIRED", "OCR_OUTPUT_INVALID"]


def test_unsafe_locator_is_rejected_without_publishing_its_evidence(tmp_path):
    path = tmp_path / "public.pdf"
    _pdf(path)
    page = _extract(path)
    artifact = build_page_evidence(
        page,
        [
            {"type": "text", "text": "Usable public text", "bbox": [10, 10, 900, 200]},
            {"type": "text", "text": "Unsafe text", "bbox": [-1, 220, 900, 300]},
        ],
        input_binding={},
        produced_at="x",
    )
    assert [block["text"] for block in artifact["evidence_blocks"]] == ["Usable public text"]
    assert all(block["locator"]["page"] == 1 for block in artifact["evidence_blocks"])
    assert "OCR_OUTPUT_INVALID" in artifact["reason_codes"]


@pytest.mark.parametrize(
    "blocks",
    [
        [{"type": "text", "text": "", "bbox": [0, 0, 10, 10]}],
        [{"type": "text", "text": "x", "bbox": [10, 0, 10, 10]}],
        [
            {"type": "text", "text": "", "bbox": [0, 0, 10, 10]},
            {"type": "image", "text": "", "bbox": [10, 10, 900, 900]},
        ],
    ],
)
def test_all_unusable_blocks_fail_without_page_artifact(tmp_path, blocks):
    path = tmp_path / "public.pdf"
    _pdf(path)
    page = _extract(path)
    with pytest.raises(ValueError, match="NO_USABLE_EVIDENCE"):
        build_page_evidence(page, blocks, input_binding={}, produced_at="x")


def test_wrong_page_identity_and_malformed_child_block_still_fail_hard(tmp_path):
    path = tmp_path / "public.pdf"
    _pdf(path)
    page = _extract(path)
    page["page_number"] = 2
    with pytest.raises(ValueError, match="OCR_LOCATOR_INVALID"):
        build_page_evidence(
            page,
            [{"type": "text", "text": "Public", "bbox": [10, 10, 900, 200]}],
            input_binding={},
            produced_at="x",
        )

    page["page_number"] = 1
    with pytest.raises(ValueError, match="OCR_OUTPUT_INVALID"):
        build_page_evidence(
            page,
            [{"type": "text", "text": "Public", "bbox": [10, 10, 900, 200], "extra": True}],
            input_binding={},
            produced_at="x",
        )
    with pytest.raises(ValueError, match="OCR_OUTPUT_INVALID"):
        build_page_evidence(
            page,
            [{"type": "text", "text": None, "bbox": [10, 10, 900, 200]}],
            input_binding={},
            produced_at="x",
        )
