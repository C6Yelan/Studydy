from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pymupdf
import pytest

import material_blocks
from material_blocks import (
    ActiveMaterial,
    MaterialBlockContractError,
    build_material_blocks,
    canonical_json_bytes,
    select_active_manifest_entries,
    validate_artifact,
    write_canonical_artifact,
)


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
    case_id: str,
    page_texts: list[str],
    source_refs: dict[int, str] | None = None,
) -> ActiveMaterial:
    pdf_path = tmp_path / f"{case_id}.pdf"
    digest = _make_pdf(pdf_path, page_texts)
    return ActiveMaterial(
        case_id=case_id,
        artifact_ref=f"active:{case_id}:compact_pdf",
        pdf_path=pdf_path,
        declared_pages=len(page_texts),
        expected_sha256=digest,
        source_refs=source_refs or {},
    )


def _three_inputs(tmp_path: Path) -> list[ActiveMaterial]:
    return [
        _input(tmp_path, "case-c", ["gamma"]),
        _input(tmp_path, "case-a", ["alpha  ", "beta"]),
        _input(tmp_path, "case-b", ["delta"], {1: "slide:7"}),
    ]


def test_manifest_selection_is_active_only_and_retired_is_non_input() -> None:
    manifest = {
        "baseline_state": "approved",
        "difficulty_ladder_selections": [
            {"case_id": "case-c", "status": "active"},
            {"case_id": "candidate", "status": "candidate"},
            {"case_id": "case-a", "status": "active"},
            {"case_id": "case-b", "status": "active"},
        ],
        "retired_tombstones": [
            {"case_id": "retired-1", "status": "retired"},
            {"case_id": "retired-2", "status": "retired"},
        ],
    }

    active, retired_ids = select_active_manifest_entries(manifest)

    assert [entry["case_id"] for entry in active] == ["case-a", "case-b", "case-c"]
    assert retired_ids == ["retired-1", "retired-2"]
    assert all(entry["status"] == "active" for entry in active)


def test_manifest_selection_rejects_missing_baseline_and_retired_id_reuse() -> None:
    manifest = {
        "difficulty_ladder_selections": [
            {"case_id": "case-a", "status": "active"},
            {"case_id": "case-b", "status": "active"},
            {"case_id": "case-c", "status": "active"},
        ],
        "retired_tombstones": [],
    }
    with pytest.raises(MaterialBlockContractError, match="baseline_state"):
        select_active_manifest_entries(manifest)

    manifest["baseline_state"] = "approved"
    manifest["retired_tombstones"] = [{"case_id": "case-a", "status": "retired"}]
    with pytest.raises(MaterialBlockContractError, match="must not be reused"):
        select_active_manifest_entries(manifest)


def test_contract_is_refined_and_ordered(tmp_path: Path) -> None:
    artifact = build_material_blocks(_three_inputs(tmp_path), ["retired-1"])

    assert set(artifact) == {"schema_version", "parser_provenance", "materials"}
    assert [item["case_id"] for item in artifact["materials"]] == ["case-a", "case-b", "case-c"]
    assert set(artifact["materials"][0]) == {
        "material_id",
        "case_id",
        "artifact_ref",
        "input_status",
        "failure_reason",
        "blocks",
    }
    block = artifact["materials"][0]["blocks"][0]
    assert set(block) == {
        "block_id",
        "content_type",
        "text",
        "locator",
        "parser_status",
        "failure_reason",
    }
    assert "artifact_ref" not in block
    assert "schema_version" not in block
    assert "parser_provenance" not in block
    assert "page_mapping_ref" not in block["locator"]
    assert block["locator"] == {"pdf_page": 1}
    assert artifact["materials"][1]["blocks"][0]["locator"] == {
        "pdf_page": 1,
        "source_ref": "slide:7",
    }
    assert {block["parser_status"] for item in artifact["materials"] for block in item["blocks"]} <= {
        "success",
        "failed",
        "unsupported",
    }


def test_active_coverage_and_same_input_rerun_are_canonical(tmp_path: Path) -> None:
    inputs = _three_inputs(tmp_path)
    first = build_material_blocks(inputs, ["retired-1", "retired-2"])
    second = build_material_blocks(inputs, ["retired-1", "retired-2"])

    assert len(first["materials"]) == 3
    assert [len(item["blocks"]) for item in first["materials"]] == [2, 1, 1]
    assert canonical_json_bytes(first) == canonical_json_bytes(second)


def test_retired_case_id_cannot_be_reused(tmp_path: Path) -> None:
    active = _input(tmp_path, "retired-1", ["text"])

    with pytest.raises(MaterialBlockContractError, match="must not be reused"):
        build_material_blocks([active], ["retired-1"])


@pytest.mark.parametrize(
    ("mutation", "status", "reason"),
    [
        ("fingerprint", "failed", "input_fingerprint_mismatch"),
        ("page_count", "failed", "declared_page_count_mismatch"),
        ("missing", "failed", "document_unreadable"),
        ("signature", "unsupported", "document_unreadable"),
    ],
)
def test_document_input_failures_do_not_infer_blocks(
    tmp_path: Path,
    mutation: str,
    status: str,
    reason: str,
) -> None:
    item = _input(tmp_path, "case-a", ["text"])
    if mutation == "fingerprint":
        item = ActiveMaterial(**{**item.__dict__, "expected_sha256": "0" * 64})
    elif mutation == "page_count":
        item = ActiveMaterial(**{**item.__dict__, "declared_pages": 2})
    elif mutation == "missing":
        item.pdf_path.unlink()
    else:
        item.pdf_path.write_bytes(b"not a pdf")
        item = ActiveMaterial(
            **{
                **item.__dict__,
                "expected_sha256": hashlib.sha256(item.pdf_path.read_bytes()).hexdigest(),
            }
        )

    material = build_material_blocks([item], [])["materials"][0]

    assert material["input_status"] == status
    assert material["failure_reason"] == reason
    assert material["blocks"] == []


def test_empty_page_is_explicit_unsupported_block(tmp_path: Path) -> None:
    item = _input(tmp_path, "case-a", [""])

    block = build_material_blocks([item], [])["materials"][0]["blocks"][0]

    assert block == {
        "block_id": "material-blocks/v1:case-a:page:0001",
        "content_type": "pdf_page_text",
        "text": None,
        "locator": {"pdf_page": 1},
        "parser_status": "unsupported",
        "failure_reason": "no_extractable_text",
    }


def test_page_level_failures_are_not_silently_dropped(tmp_path: Path) -> None:
    item = _input(tmp_path, "case-a", ["first", "second"])

    class UnreadableDocument:
        def load_page(self, page_index: int):
            raise RuntimeError("synthetic unreadable page")

    block = material_blocks._build_page_block(UnreadableDocument(), item, 2)

    assert block["locator"] == {"pdf_page": 2}
    assert block["parser_status"] == "failed"
    assert block["failure_reason"] == "page_unreadable"
    assert block["text"] is None


def test_parser_error_is_explicit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    item = _input(tmp_path, "case-a", ["text"])

    def explode(*args, **kwargs):
        raise RuntimeError("synthetic parser failure")

    monkeypatch.setattr(pymupdf.Page, "get_text", explode)

    block = build_material_blocks([item], [])["materials"][0]["blocks"][0]

    assert block["parser_status"] == "failed"
    assert block["failure_reason"] == "parser_error"
    assert block["text"] is None


def test_validator_rejects_partial_and_extra_block_provenance(tmp_path: Path) -> None:
    artifact = build_material_blocks([_input(tmp_path, "case-a", ["text"])], [])
    partial = copy.deepcopy(artifact)
    partial["materials"][0]["blocks"][0]["parser_status"] = "partial"
    with pytest.raises(MaterialBlockContractError, match="status"):
        validate_artifact(partial)

    duplicated = copy.deepcopy(artifact)
    duplicated["materials"][0]["blocks"][0]["artifact_ref"] = "duplicate"
    with pytest.raises(MaterialBlockContractError, match="fields"):
        validate_artifact(duplicated)


def test_atomic_writer_cleans_staging_and_round_trips(tmp_path: Path) -> None:
    artifact = build_material_blocks([_input(tmp_path, "case-a", ["text"])], [])
    stable_path = tmp_path / "private" / "material_blocks.v1.json"
    staging = tmp_path / "private" / ".material-blocks-staging"

    write_canonical_artifact(artifact, stable_path, staging)

    assert json.loads(stable_path.read_text(encoding="utf-8")) == artifact
    assert not staging.exists()


def test_normalization_removes_only_line_endings_and_trailing_whitespace() -> None:
    assert material_blocks.normalize_text(" first  \r\nsecond\t\rthird\n\n") == " first\nsecond\nthird"
