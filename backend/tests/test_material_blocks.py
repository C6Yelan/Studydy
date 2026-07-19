from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path

import pymupdf
import pytest

import material_blocks
from material_blocks import ActiveMaterial, build_material_blocks


def _make_pdf(path: Path, page_texts: list[str]) -> str:
    document = pymupdf.open()
    for text in page_texts:
        page = document.new_page()
        if text:
            page.insert_text((72, 72), text)
    document.save(path)
    document.close()
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _input(
    tmp_path: Path,
    page_texts: list[str],
    source_refs: dict[int, str] | None = None,
) -> ActiveMaterial:
    pdf_path = tmp_path / "case-a.pdf"
    return ActiveMaterial(
        case_id="case-a",
        artifact_ref="active:case-a:compact_pdf",
        pdf_path=pdf_path,
        declared_pages=len(page_texts),
        expected_sha256=_make_pdf(pdf_path, page_texts),
        source_refs=source_refs or {},
    )


def test_successful_material_has_reproducible_page_blocks_and_mapping(tmp_path: Path) -> None:
    item = _input(tmp_path, ["alpha  ", "beta"], {2: "slide:7"})

    artifact = build_material_blocks(item)

    assert set(artifact) == {"schema_version", "parser_provenance", "materials"}
    assert artifact["schema_version"] == "material-blocks/v1"
    assert artifact["parser_provenance"] == {
        "parser": "pymupdf",
        "parser_version": pymupdf.VersionBind,
        "extraction_policy": "page.get_text:text:sort-true-v1",
        "normalization_policy": "utf8-lf-trailing-whitespace-v1",
    }
    assert artifact["materials"] == [
        {
            "material_id": "material-blocks/v1:case-a",
            "case_id": "case-a",
            "artifact_ref": "active:case-a:compact_pdf",
            "input_status": "valid",
            "failure_reason": None,
            "blocks": [
                {
                    "block_id": "material-blocks/v1:case-a:page:0001",
                    "text": "alpha",
                    "locator": {"pdf_page": 1},
                    "parser_status": "success",
                    "failure_reason": None,
                },
                {
                    "block_id": "material-blocks/v1:case-a:page:0002",
                    "text": "beta",
                    "locator": {"pdf_page": 2, "source_ref": "slide:7"},
                    "parser_status": "success",
                    "failure_reason": None,
                },
            ],
        }
    ]


def test_same_input_rerun_has_identical_domain_records(tmp_path: Path) -> None:
    item = _input(tmp_path, ["alpha", "beta"], {2: "slide:7"})

    assert build_material_blocks(item) == build_material_blocks(item)


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ("fingerprint", "input_fingerprint_mismatch"),
        ("page_count", "declared_page_count_mismatch"),
        ("missing", "document_unreadable"),
        ("signature", "document_unreadable"),
        ("document", "document_unreadable"),
    ],
)
def test_document_failures_are_explicit_without_inferred_blocks(
    tmp_path: Path,
    mutation: str,
    reason: str,
) -> None:
    item = _input(tmp_path, ["text"])
    if mutation == "fingerprint":
        item = replace(item, expected_sha256="0" * 64)
    elif mutation == "page_count":
        item = replace(item, declared_pages=2)
    elif mutation == "missing":
        item.pdf_path.unlink()
    else:
        content = b"not a pdf" if mutation == "signature" else b"%PDF-not valid"
        item.pdf_path.write_bytes(content)
        item = replace(item, expected_sha256=hashlib.sha256(content).hexdigest())

    material = build_material_blocks(item)["materials"][0]

    assert material["input_status"] == "failed"
    assert material["failure_reason"] == reason
    assert material["blocks"] == []


def test_empty_page_is_explicit_unsupported_block(tmp_path: Path) -> None:
    item = _input(tmp_path, [""])

    block = build_material_blocks(item)["materials"][0]["blocks"][0]

    assert block == {
        "block_id": "material-blocks/v1:case-a:page:0001",
        "text": None,
        "locator": {"pdf_page": 1},
        "parser_status": "unsupported",
        "failure_reason": "no_extractable_text",
    }


def test_unreadable_page_is_not_silently_dropped(tmp_path: Path) -> None:
    item = _input(tmp_path, ["first", "second"], {2: "slide:7"})

    class UnreadableDocument:
        def load_page(self, page_index: int):
            raise RuntimeError("synthetic unreadable page")

    block = material_blocks._build_page_block(UnreadableDocument(), item, 2)

    assert block == {
        "block_id": "material-blocks/v1:case-a:page:0002",
        "text": None,
        "locator": {"pdf_page": 2, "source_ref": "slide:7"},
        "parser_status": "failed",
        "failure_reason": "page_unreadable",
    }


def test_parser_error_is_explicit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    item = _input(tmp_path, ["text"])

    def explode(*args, **kwargs):
        raise RuntimeError("synthetic parser failure")

    monkeypatch.setattr(pymupdf.Page, "get_text", explode)

    block = build_material_blocks(item)["materials"][0]["blocks"][0]

    assert block["locator"] == {"pdf_page": 1}
    assert block["parser_status"] == "failed"
    assert block["failure_reason"] == "parser_error"
    assert block["text"] is None


def test_normalization_preserves_content_while_normalizing_line_endings() -> None:
    assert material_blocks.normalize_text(" first  \r\nsecond\t\rthird\n\n") == " first\nsecond\nthird"
