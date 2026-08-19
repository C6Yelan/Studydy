import hashlib
from pathlib import Path

import pymupdf
import pytest

from pdf_evidence.ocr_page_evidence import build_page_evidence, extract_page


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
                "type": "code" if ordinal == 0 else "text",
                "text": "  first line\n    second line" if ordinal == 0 else f"Public OCR text {ordinal}",
                "bbox": [100, 100, 900, 300],
            }
            for ordinal in range(65)
        ],
        input_binding={"fixed": True},
        produced_at="2026-08-18T00:00:00Z",
    )
    block = artifact["evidence_blocks"][0]
    assert artifact["schema"] == "page-evidence/v2"
    assert artifact["processing"] == "succeeded"
    assert artifact["decision"] == "review"
    assert block["locator"]["page"] == 1
    assert block["render_region"] == [40.0, 60.0, 360.0, 180.0]
    assert block["source"] == "unlimited_ocr"
    assert block["text"] == "  first line\n    second line"
    assert len(artifact["evidence_blocks"]) == 65
    assert len(artifact["images"]) == 257
    assert "png_bytes" not in artifact
    assert artifact["reason_codes"] == ["PAGE_CONTENT_REVIEW_REQUIRED"]


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
