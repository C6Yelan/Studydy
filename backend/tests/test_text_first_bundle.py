from copy import deepcopy

import pytest

from pdf_evidence.concept_evidence_output import build_terminal
from pdf_evidence.text_first_bundle import validate_bundle_documents, validate_terminal
from test_study_material_output import producer_output


def test_bundle_documents_are_closed_after_all_identities_are_recomputed():
    output = producer_output()
    terminal = build_terminal(
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
    from pdf_evidence.text_first_bundle import _bundle_document

    bundle = _bundle_document(output["run_id"], output, terminal)
    assert validate_bundle_documents(bundle, terminal, output, output["run_id"])
    changed = deepcopy(bundle)
    changed["unexpected_field"] = True
    assert not validate_bundle_documents(changed, terminal, output, output["run_id"])


def test_failed_terminal_allows_observed_over_limit_page_count_but_zero_model_calls():
    terminal = build_terminal(
        run_id="text-first-run:00000000-0000-4000-8000-000000000001",
        produced_at="2026-08-19T00:00:00Z",
        output=None,
        runtime_binding_sha256="a" * 64,
        reasons=["MATERIAL_PAGE_LIMIT_EXCEEDED"],
        duration_ms=1,
        ocr_calls=0,
        concept_calls=0,
        page_count=33,
    )
    assert validate_terminal(terminal, None)


@pytest.mark.parametrize(
    "encoded",
    [b'{"schema":"x","schema":"y"}', b'{"value":NaN}'],
)
def test_bundle_json_rejects_duplicate_and_nonfinite_values(tmp_path, encoded):
    from pdf_evidence.text_first_bundle import _read_json_file

    path = tmp_path / "document.json"
    path.write_bytes(encoded)
    with pytest.raises(ValueError, match="PRODUCER_BUNDLE_INVALID"):
        _read_json_file(path)


def test_bundle_json_has_explicit_depth_and_size_limits(tmp_path):
    from pdf_evidence.text_first_bundle import (
        MAX_BUNDLE_FILE_BYTES,
        _check_depth,
        _read_json_file,
    )

    deep = {}
    cursor = deep
    for _ in range(33):
        cursor["next"] = {}
        cursor = cursor["next"]
    with pytest.raises(ValueError, match="PRODUCER_BUNDLE_INVALID"):
        _check_depth(deep)

    path = tmp_path / "too-large.json"
    with path.open("wb") as output:
        output.seek(MAX_BUNDLE_FILE_BYTES)
        output.write(b"x")
    with pytest.raises(ValueError, match="PRODUCER_BUNDLE_INVALID"):
        _read_json_file(path)
