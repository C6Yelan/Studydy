from pathlib import Path

import pytest

import pdf_evidence.text_first_bundle as output_module
from pdf_evidence.concept_evidence_output import build_terminal, validate_output_document
from pdf_evidence.ocr_page_evidence import canonical_sha256
from test_study_material_output import producer_output


def terminal_for(output):
    return build_terminal(
        run_id=output["run_id"],
        produced_at=output["produced_at"],
        output=output,
        runtime_binding_sha256="a" * 64,
        reasons=output["reason_codes"],
        duration_ms=1,
        ocr_calls=1,
        concept_calls=1,
        ocr_loads=1,
        concept_loads=1,
        page_count=1,
    )


def test_atomic_publish_writes_output_before_terminal(tmp_path):
    output = producer_output()
    terminal = terminal_for(output)
    destination = output_module.publish_run(tmp_path, output["run_id"], output, terminal)
    assert (destination / "concept-evidence-output.json").is_file()
    assert (destination / "terminal.json").is_file()
    with pytest.raises(FileExistsError):
        output_module.publish_run(tmp_path, output["run_id"], output, terminal)


def test_output_write_failure_never_leaves_published_terminal(tmp_path, monkeypatch):
    output = producer_output()
    terminal = terminal_for(output)
    monkeypatch.setattr(
        output_module,
        "_write_new",
        lambda path, encoded: (_ for _ in ()).throw(OSError()),
    )
    with pytest.raises(OSError, match="FINAL_OUTPUT_WRITE_FAILED"):
        output_module.publish_run(
            tmp_path, output["run_id"], output, terminal,
        )
    assert not (tmp_path / "runs" / output["run_id"]).exists()


def test_verified_reader_rejects_tamper_symlink_and_traversal(tmp_path):
    output = producer_output()
    terminal = build_terminal(
        run_id=output["run_id"], produced_at=output["produced_at"], output=None,
        runtime_binding_sha256="a" * 64, reasons=["INTERNAL_FAILURE"], duration_ms=1,
        ocr_calls=0, concept_calls=0, page_count=1,
    )
    destination = output_module.publish_run(tmp_path, output["run_id"], None, terminal)
    assert output_module.read_producer_bundle(tmp_path, output["run_id"])["terminal"] == terminal
    (destination / "terminal.json").write_text('{"tampered":true}', encoding="utf-8")
    with pytest.raises(ValueError, match="PRODUCER_BUNDLE_INVALID"):
        output_module.read_producer_bundle(tmp_path, output["run_id"])
    with pytest.raises(ValueError, match="PRODUCER_BUNDLE_INVALID"):
        output_module.read_producer_bundle(tmp_path, "../run-one")


def test_recomputed_page_and_output_identity_cannot_hide_unexpected_field():
    output = producer_output()
    output["pages"][0]["evidence_blocks"][0]["unexpected_field"] = True
    page_identity = dict(output["pages"][0])
    page_identity.pop("page_evidence_id")
    output["pages"][0]["page_evidence_id"] = (
        "page-evidence:sha256:" + canonical_sha256(page_identity)
    )
    output_identity = dict(output)
    output_identity.pop("output_id")
    output["output_id"] = "concept-evidence-output:sha256:" + canonical_sha256(
        output_identity
    )
    assert validate_output_document(output) is False


def test_terminal_is_closed_and_rejects_type_count_and_nonfinite_values():
    output = producer_output()
    terminal = terminal_for(output)
    terminal["unexpected_field"] = True
    assert output_module.validate_terminal(terminal, output) is False
    terminal.pop("unexpected_field")
    terminal["ocr_calls"] = True
    assert output_module.validate_terminal(terminal, output) is False
    terminal["ocr_calls"] = 33
    assert output_module.validate_terminal(terminal, output) is False
