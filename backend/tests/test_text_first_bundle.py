from copy import deepcopy

import pytest

import pdf_evidence.text_first_bundle as bundle_module
from pdf_evidence.ocr_page_evidence import canonical_sha256
from pdf_evidence.text_first_bundle import (
    build_producer_bundle,
    validate_bundle_documents,
)
from test_study_material_output import producer_output


def _success_bundle(output):
    return build_producer_bundle(
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


def _failed_bundle(output):
    return build_producer_bundle(
        run_id=output["run_id"],
        produced_at=output["produced_at"],
        output=None,
        runtime_binding_sha256="a" * 64,
        reasons=["INTERNAL_FAILURE"],
        duration_ms=1,
        ocr_calls=0,
        concept_calls=0,
        page_count=1,
    )


def test_bundle_documents_are_closed_after_all_identities_are_recomputed():
    output = producer_output()
    bundle = _success_bundle(output)
    assert bundle["schema"] == "text-first-producer-bundle/v2"
    assert validate_bundle_documents(bundle, output, output["run_id"])
    changed = deepcopy(bundle)
    changed["unexpected_field"] = True
    assert not validate_bundle_documents(changed, output, output["run_id"])


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("output_id", "concept-evidence-output:sha256:" + "f" * 64),
        ("output_sha256", "f" * 64),
        ("output_size_bytes", 0),
    ],
)
def test_bundle_binds_output_identity_hash_and_size(field, value):
    output = producer_output()
    bundle = _success_bundle(output)
    bundle[field] = value
    identity = dict(bundle)
    identity.pop("bundle_id")
    bundle["bundle_id"] = (
        "text-first-producer-bundle:sha256:" + canonical_sha256(identity)
    )

    assert not validate_bundle_documents(bundle, output, output["run_id"])


def test_atomic_publish_writes_exact_files_and_rejects_collision(tmp_path):
    output = producer_output()
    bundle = _success_bundle(output)
    destination = bundle_module.publish_run(tmp_path, bundle, output)

    assert {item.name for item in destination.iterdir()} == {
        "concept-evidence-output.json",
        "producer-bundle.json",
    }
    with pytest.raises(FileExistsError):
        bundle_module.publish_run(tmp_path, bundle, output)


def test_publish_write_failures_never_leave_a_run(tmp_path, monkeypatch):
    output = producer_output()
    cases = [
        (_success_bundle(output), output, "FINAL_OUTPUT_WRITE_FAILED"),
        (_failed_bundle(output), None, "PRODUCER_BUNDLE_WRITE_FAILED"),
    ]
    monkeypatch.setattr(
        bundle_module,
        "_write_new",
        lambda path, encoded: (_ for _ in ()).throw(OSError()),
    )

    for index, (bundle, published_output, reason) in enumerate(cases):
        runtime_root = tmp_path / str(index)
        with pytest.raises(OSError, match=reason):
            bundle_module.publish_run(runtime_root, bundle, published_output)
        assert not (runtime_root / "runs" / output["run_id"]).exists()


def test_verified_reader_rejects_tamper_symlink_and_traversal(tmp_path):
    output = producer_output()
    bundle = _failed_bundle(output)
    destination = bundle_module.publish_run(tmp_path, bundle, None)
    assert bundle_module.read_producer_bundle(tmp_path, output["run_id"])[
        "bundle"
    ] == bundle

    bundle_path = destination / "producer-bundle.json"
    bundle_path.write_text('{"tampered":true}', encoding="utf-8")
    with pytest.raises(ValueError, match="PRODUCER_BUNDLE_INVALID"):
        bundle_module.read_producer_bundle(tmp_path, output["run_id"])

    bundle_path.unlink()
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    bundle_path.symlink_to(outside)
    with pytest.raises(ValueError, match="PRODUCER_BUNDLE_INVALID"):
        bundle_module.read_producer_bundle(tmp_path, output["run_id"])
    with pytest.raises(ValueError, match="PRODUCER_BUNDLE_INVALID"):
        bundle_module.read_producer_bundle(tmp_path, "../run-one")


def test_bundle_rejects_detailed_reason_code():
    output = producer_output()
    bundle = _success_bundle(output)
    bundle["reason_codes"] = ["SEMANTIC_REVIEW_REQUIRED"]
    identity = dict(bundle)
    identity.pop("bundle_id")
    bundle["bundle_id"] = (
        "text-first-producer-bundle:sha256:" + canonical_sha256(identity)
    )

    assert not validate_bundle_documents(bundle, output, output["run_id"])


def test_failed_bundle_keeps_observed_page_count_without_product_ceiling():
    bundle = build_producer_bundle(
        run_id="text-first-run:00000000-0000-4000-8000-000000000001",
        produced_at="2026-08-19T00:00:00Z",
        output=None,
        runtime_binding_sha256="a" * 64,
        reasons=["INTERNAL_FAILURE"],
        duration_ms=1,
        ocr_calls=0,
        concept_calls=0,
        page_count=100_001,
    )
    assert validate_bundle_documents(bundle, None, bundle["run_id"])


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
        MAX_ARTIFACT_FILE_BYTES,
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
        output.seek(MAX_ARTIFACT_FILE_BYTES)
        output.write(b"x")
    with pytest.raises(ValueError, match="PRODUCER_BUNDLE_INVALID"):
        _read_json_file(path)
