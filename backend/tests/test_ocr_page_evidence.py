import hashlib
from pathlib import Path

import pymupdf
import pytest

from knowledge_map.structure import (
    SemanticState, apply_semantic_response, build_document_context,
    build_semantic_bundles, semantic_request, semantic_response_schema,
)
from pdf_evidence.ocr_page_evidence import (
    _native_text_blocks,
    build_native_page_evidence,
    build_page_evidence,
    extract_page,
    route_page,
)


def test_wrapped_statement_crosses_pdf_blocks_but_not_columns_or_new_items():
    """PDF 物件邊界不等於句界；跨欄與新條列不能被拼成同一陳述。"""
    def block(text, x, y):
        return {"type": 0, "lines": [{"bbox": [x, y, x + 180, y + 15],
                                     "spans": [{"text": text, "size": 12}]}]}
    page = {"geometry": {"unrotated_points": [0, 0, 612, 792]},
            "native_evidence": {"raw_text": {"blocks": [
                block("The sensor returns", 72, 100),
                block("three samples.", 84, 119),
                block("• The interval is 17 ms.", 72, 138),
                block("Left column", 72, 180),
                block("Right column", 330, 180),
            ]}}}
    texts = [b["text"] for b in _native_text_blocks(page)]
    assert texts == ["The sensor returns\nthree samples.", "• The interval is 17 ms.",
                     "Left column", "Right column"]


def test_formal_definition_keeps_indented_body_and_separates_next_definition():
    """定義分隔符與縮排共同定界，不依函式名稱或 PDF 物件邊界切句。"""
    def line(text, x, y):
        return {"bbox": [x, y, x + 220, y + 15], "spans": [{"text": text, "size": 12}]}
    page = {"geometry": {"unrotated_points": [0, 0, 612, 792]},
            "native_evidence": {"raw_text": {"blocks": [
                {"type": 0, "lines": [line("For any buffer", 72, 100), line("Buffer Allocate(limit) ::=", 72, 119)]},
                {"type": 0, "lines": [
                    line("create an empty buffer", 144, 138),
                    line("Boolean Available(buffer) ::=", 72, 157),
                ]},
                {"type": 0, "lines": [
                    line("if buffer has room", 144, 176),
                    line("return TRUE", 144, 195),
                    line("Boolean Ready(buffer) ::= TRUE", 72, 214),
                ]},
            ]}}}
    assert [b["text"] for b in _native_text_blocks(page)] == [
        "For any buffer",
        "Buffer Allocate(limit) ::=\ncreate an empty buffer",
        "Boolean Available(buffer) ::=\nif buffer has room\nreturn TRUE",
        "Boolean Ready(buffer) ::= TRUE",
    ]


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
    assert artifact["schema"] == "page-evidence/v1"
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

    context = build_document_context(artifacts, page_count=2)
    heading_evidence_id = artifacts[0]["evidence_blocks"][0]["evidence_id"]
    assert len(context["sections"]) == 1
    assert context["sections"][0]["heading_evidence_id"] == heading_evidence_id
    assert {item["section_id"] for item in context["evidence"]} == {
        context["sections"][0]["section_id"]
    }


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


def test_running_metadata_is_preserved_but_not_a_claim_source(tmp_path):
    """頁尾先寫入 PDF 也不能成為 Claim；正文、頁底程式及固定數值保留。"""
    path = tmp_path / "public-layout.pdf"
    document = pymupdf.open()
    for number in range(1, 4):
        page = document.new_page(width=612, height=792)
        page.insert_text((300, 782), str(number), fontsize=8)
        page.insert_text((400, 782), "Publisher Copyright 2025", fontsize=8)
        page.insert_text((72, 70), "Sensors", fontsize=22)
        page.insert_text((72, 120), "A sensor sends readings\nto the controller.", fontsize=12)
        page.insert_text((72, 210), "Copyright protects this text.", fontsize=12)
        page.insert_text((72, 745), "int capacity[17];", fontsize=12)
        page.insert_text((300, 745), 'char label[] = "Copyright 2025";', fontsize=8)
        page.insert_text((72, 765), "42", fontsize=12)
    document.save(path)
    document.close()
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    with pymupdf.open(path) as document:
        pages = [build_native_page_evidence(
            extract_page(document, sha, n), input_binding={}, produced_at="x",
        ) for n in range(1, 4)]
    context = build_document_context(pages, page_count=3)
    state = SemanticState()
    bundle = next(build_semantic_bundles(context, state=state, fits=lambda _: True))
    request = semantic_request(context, bundle, state)
    rows = [row for section in request["sections"] for row in section["evidence"]]
    assert any("Publisher Copyright" in e["exact_text"] for e in context["evidence"])
    assert all("Publisher Copyright" not in row[3] for row in rows)
    assert not any(row[3] in {"1", "2", "3"} for row in rows)
    assert any(row[3] == "A sensor sends readings\nto the controller." for row in rows)
    assert any(row[3] == "Copyright protects this text." for row in rows)
    assert any(row[3] == "int capacity[17];" for row in rows)
    assert any(row[3] == 'char label[] = "Copyright 2025";' for row in rows)
    assert any(row[3] == "42" for row in rows)
    concept_schema = semantic_response_schema([row[0] for row in rows])["properties"]["concepts"]["items"]
    claim_schema = concept_schema["properties"]["c"]["items"]
    allowed = claim_schema["properties"]["s"]["items"]["enum"]
    footer = next(i for i, e in enumerate(context["evidence"]) if "Publisher Copyright" in e["exact_text"])
    body = next(row[0] for row in rows if row[3].startswith("A sensor sends"))
    assert footer not in allowed
    assert context["evidence"][body]["exact_text"].startswith("A sensor sends")
    apply_semantic_response({"concepts": [{"k": "sensor", "l": "Sensor", "a": [], "c": [
        {"m": "A sensor sends readings to a controller.", "s": [footer]},
        {"m": None, "s": [footer]},
        {"m": None, "s": [body]},
    ]}], "relations": []}, context=context, bundle=bundle, state=state)
    assert state.rejected_claims == 2
    assert len(state.concepts["sensor"]["claims"]) == 1
    assert "controller" in state.concepts["sensor"]["claims"][0]["text"]


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


def test_native_title_does_not_hide_uncovered_image_and_ocr_keeps_native(tmp_path):
    path = tmp_path / "mixed.pdf"
    _pdf(path)
    page = _extract(path)
    page["images"] = [{"bbox": [20, 65, 130, 190]}]
    assert route_page(page) == "OCR_needed"
    artifact = build_page_evidence(
        page,
        [
            {"type": "text", "text": "Public native text", "bbox": [100, 50, 950, 180]},
            {"type": "code", "text": "if (left > right) swap(left, right);", "bbox": [150, 310, 900, 850]},
        ],
        input_binding={}, produced_at="x",
    )
    blocks = artifact["evidence_blocks"]
    assert [(b["source"], b["text"]) for b in blocks] == [
        ("native_text", "Public native text"),
        ("unlimited_ocr", "if (left > right) swap(left, right);"),
    ]
    context = build_document_context([artifact], page_count=1)
    assert len(context["evidence"]) == 2
    assert all(b["locator"]["page"] == 1 for b in blocks)


def test_small_logo_does_not_trigger_ocr(tmp_path):
    path = tmp_path / "logo.pdf"
    _pdf(path)
    page = _extract(path)
    page["images"] = [{"bbox": [130, 0, 140, 10]}]
    assert route_page(page) == "native_sufficient"


def test_wrapped_native_unit_and_image_ocr_both_reach_whole_evidence_claim(tmp_path):
    path = tmp_path / "wrapped-with-image.pdf"
    with pymupdf.open() as document:
        pdf_page = document.new_page(width=300, height=300)
        pdf_page.insert_text((20, 35), "The buffer holds", fontsize=10)
        pdf_page.insert_text((20, 48), "four values.", fontsize=10)
        document.save(path)
    page = _extract(path)
    page["images"] = [{"bbox": [20, 90, 250, 250]}]
    assert route_page(page) == "OCR_needed"
    artifact = build_page_evidence(
        page,
        [{"type": "code", "text": "int values[4];", "bbox": [100, 330, 800, 730]}],
        input_binding={}, produced_at="x",
    )
    texts = ["The buffer holds\nfour values.", "int values[4];"]
    assert [(block["source"], block["text"]) for block in artifact["evidence_blocks"]] == [
        ("native_text", texts[0]), ("unlimited_ocr", texts[1]),
    ]
    context = build_document_context([artifact], page_count=1)
    state = SemanticState()
    apply_semantic_response(
        {"concepts": [{"k": "buffer", "l": "Buffer", "a": [],
                       "c": [{"m": None, "s": [0, 1]}]}], "relations": []},
        context=context,
        bundle={"sections": context["sections"], "evidence": context["evidence"]},
        state=state,
    )
    claim = state.concepts["buffer"]["claims"][0]
    assert [span["quote"] for span in claim["source_spans"]] == texts


def test_unrecovered_image_retains_native_with_review_status(tmp_path):
    path = tmp_path / "missing-image-text.pdf"
    _pdf(path)
    page = _extract(path)
    page["images"] = [{"bbox": [20, 65, 130, 190]}]
    artifact = build_page_evidence(page, [], input_binding={}, produced_at="x")
    assert artifact["processing"] == "partial"
    assert "IMAGE_TEXT_NOT_RECOVERED" in artifact["reason_codes"]
    assert artifact["evidence_blocks"][0]["text"] == "Public native text"
