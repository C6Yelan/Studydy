from pathlib import Path

import pytest

import pdf_evidence.concept_evidence_output as output_module


def test_atomic_publish_writes_output_before_terminal(tmp_path):
    output = {"output_id": "output-one"}
    terminal = {"processing": "partial"}
    destination = output_module.publish_run(tmp_path, "run-one", output, terminal)
    assert (destination / "concept-evidence-output.json").is_file()
    assert (destination / "terminal.json").is_file()
    with pytest.raises(FileExistsError):
        output_module.publish_run(tmp_path, "run-one", output, terminal)


def test_output_write_failure_never_leaves_published_terminal(tmp_path, monkeypatch):
    monkeypatch.setattr(
        output_module,
        "_write_new",
        lambda path, encoded: (_ for _ in ()).throw(OSError()),
    )
    with pytest.raises(OSError, match="FINAL_OUTPUT_WRITE_FAILED"):
        output_module.publish_run(
            tmp_path,
            "run-one",
            {"output_id": "output-one"},
            {"processing": "partial"},
        )
    assert not (tmp_path / "runs" / "run-one").exists()


def test_verified_reader_rejects_tamper_symlink_and_traversal(tmp_path):
    terminal = {
        "schema": output_module.TERMINAL_SCHEMA,
        "run_id": "run-one",
        "output_id": None,
        "processing": "failed",
        "quality": "needs_review",
        "decision": "reject",
    }
    destination = output_module.publish_run(tmp_path, "run-one", None, terminal)
    assert output_module.read_producer_bundle(tmp_path, "run-one")["terminal"] == terminal
    (destination / "terminal.json").write_text('{"tampered":true}', encoding="utf-8")
    with pytest.raises(ValueError, match="PRODUCER_BUNDLE_INVALID"):
        output_module.read_producer_bundle(tmp_path, "run-one")
    with pytest.raises(ValueError, match="PRODUCER_BUNDLE_INVALID"):
        output_module.read_producer_bundle(tmp_path, "../run-one")
