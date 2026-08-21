from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pymupdf
import pytest

from learning_resources import resource_intake
from learning_resources.map_resources import (
    build_resource_library,
    validate_resource_library,
)
from pdf_evidence.ocr_page_evidence import canonical_bytes, canonical_sha256
from pdf_evidence.text_first_bundle import build_producer_bundle, publish_run
from test_study_material_output import producer_output


def _environment(runtime_root: Path) -> dict[str, str]:
    return {
        "STUDYDY_PRIVATE_RUNTIME_ROOT": str(runtime_root),
        "STUDYDY_LOCAL_AI_PYTHON": "/opt/studydy/ocr/bin/python3.12",
        "STUDYDY_LOCAL_AI_SITE_PACKAGES": "/opt/studydy/ocr/site-packages",
        "STUDYDY_CONCEPT_SITE_PACKAGES": "/opt/studydy/vllm/site-packages",
        "STUDYDY_OCR_MODEL_ROOT": "/opt/studydy/models/ocr",
        "STUDYDY_CONCEPT_API_BASE_URL": "http://127.0.0.1:8101",
        "STUDYDY_CONCEPT_MODEL": "Qwen/Qwen3-4B-Instruct-2507",
        "STUDYDY_CONCEPT_SERVER_EXECUTABLE": "/opt/studydy/vllm/bin/vllm",
        "STUDYDY_CONCEPT_MODEL_ROOT": "/opt/studydy/models/qwen",
    }


def _metadata(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "schema": "resource-source-metadata/v1",
                "title": "Public Algorithms",
                "authors": ["Ada Student"],
                "source_url": "https://example.edu/algorithms.pdf",
                "citation": "Ada Student. Public Algorithms.",
                "license": "CC BY 4.0",
                "license_url": "https://creativecommons.org/licenses/by/4.0/",
                "use_boundary": "Attribution required.",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _source(source_sha256: str, title: str) -> dict:
    return {
        "source_sha256": source_sha256,
        "page_count": 1,
        "title": title,
        "authors": ["Ada Student"],
        "source_url": f"https://example.edu/{title.casefold().replace(' ', '-')}.pdf",
        "citation": f"Ada Student. {title}.",
        "license": "CC BY 4.0",
        "license_url": "https://creativecommons.org/licenses/by/4.0/",
        "use_boundary": "Attribution required.",
    }


def _entry(source_sha256: str, label: str = "Base Concept") -> dict:
    return {
        "source_sha256": source_sha256,
        "page_number": 1,
        "label": label,
        "quote": f"{label} has public evidence.",
        "region": {
            "coordinate_space": "unrotated_pdf_points",
            "bbox": [20.0, 30.0, 300.0, 60.0],
        },
    }


def _write_library(path: Path, extra: tuple[str, str] | None = None) -> None:
    sources = [_source("b" * 64, "Base Source")]
    entries = [_entry("b" * 64)]
    if extra is not None:
        source_sha256, label = extra
        sources.append(_source(source_sha256, "Extra Source"))
        entries.append(_entry(source_sha256, label))
    library = build_resource_library(sources, entries)
    path.write_bytes(canonical_bytes(library))


def _install_producer(monkeypatch, runtime_root: Path, calls: list[dict]) -> None:
    monkeypatch.setattr(
        resource_intake,
        "formal_runtime_preflight",
        lambda _: {"runtime_binding_sha256": "d" * 64},
    )

    def run(request, settings):
        calls.append(deepcopy(request))
        output = producer_output()
        output["concepts"][0]["processing"] = "succeeded"
        identity = dict(output)
        identity.pop("output_id")
        output["output_id"] = (
            "concept-evidence-output:sha256:" + canonical_sha256(identity)
        )
        bundle = build_producer_bundle(
            run_id=output["run_id"],
            produced_at=output["produced_at"],
            output=output,
            runtime_binding_sha256=canonical_sha256(settings["runtime_lock"]),
            reasons=output["reason_codes"],
            duration_ms=120,
            ocr_calls=1,
            concept_calls=1,
            ocr_loads=1,
            concept_loads=1,
            page_count=1,
        )
        publish_run(runtime_root, bundle, output)
        return bundle

    monkeypatch.setattr(resource_intake, "run_full_text_first_pdf", run)
    monkeypatch.setattr(resource_intake, "_inspect_pdf", lambda _: ("a" * 64, 1))


def _analyze(tmp_path, monkeypatch, capsys):
    candidates = tmp_path / "candidates"
    runtime_root = tmp_path / "runtime"
    metadata_path = tmp_path / "source-secret-name.json"
    pdf_path = tmp_path / "source-secret-name.pdf"
    library_path = tmp_path / "library.json"
    _metadata(metadata_path)
    pdf_path.write_bytes(b"unused-by-stub")
    _write_library(library_path)
    calls: list[dict] = []
    monkeypatch.setattr(resource_intake, "CANDIDATE_ROOT", candidates)
    _install_producer(monkeypatch, runtime_root, calls)
    arguments = [
        "analyze", str(pdf_path), "--metadata", str(metadata_path),
        "--page-ceiling", "1", "--latency-ceiling-seconds", "2",
        "--library-file", str(library_path),
    ]
    assert resource_intake.main(arguments, _environment(runtime_root)) == 0
    first = json.loads(capsys.readouterr().out)
    return first, arguments, pdf_path, library_path, runtime_root, candidates, calls


def _publish_arguments(
    analyzed: dict,
    candidate_sha256: str,
    pdf_path: Path,
    library_path: Path,
) -> list[str]:
    return [
        "publish", analyzed["candidate_id"],
        "--candidate-sha256", candidate_sha256,
        "--confirm", analyzed["candidate_id"], "--source-pdf", str(pdf_path),
        "--library-file", str(library_path),
    ]


def test_pdf_inspection_computes_exact_hash_and_physical_page_count(tmp_path):
    pdf_path = tmp_path / "two-pages.pdf"
    document = pymupdf.open()
    document.new_page()
    document.new_page()
    document.save(pdf_path)
    document.close()

    source_sha256, page_count = resource_intake._inspect_pdf(pdf_path)

    assert source_sha256 == hashlib.sha256(pdf_path.read_bytes()).hexdigest()
    assert page_count == 2


def test_analyze_writes_immutable_bundle_and_exact_replay_uses_no_model(
    tmp_path, monkeypatch, capsys
):
    first, arguments, _, _, runtime_root, candidates, calls = _analyze(
        tmp_path, monkeypatch, capsys
    )

    assert first["status"] == "analyzed"
    assert first["ocr_calls"] == first["qwen_attempts"] == 1
    assert "source-secret-name" not in json.dumps(first)
    directory = candidates / first["candidate_id"]
    encoded = (directory / "candidate.json").read_bytes()
    candidate = json.loads(encoded)
    assert encoded.startswith(b"{\n") and encoded.endswith(b"\n")
    assert hashlib.sha256(encoded).hexdigest() == first["candidate_sha256"]
    assert candidate["candidate_content_sha256"] == (
        resource_intake._candidate_content_sha256(candidate)
    )
    assert candidate["publishable_proposals"][0]["label"] == "Public concept"
    assert candidate["processing"] == "partial"
    assert candidate["critical_blockers"] == []
    assert (directory.stat().st_mode & 0o777) == 0o700
    assert ((directory / "candidate.json").stat().st_mode & 0o777) == 0o600
    assert ((directory / "review.md").stat().st_mode & 0o777) == 0o600
    review = (directory / "review.md").read_text(encoding="utf-8")
    assert "Public evidence" in review
    assert "Runtime binding" in review
    assert "Calls: OCR 1, Qwen 1" in review
    assert "python -m learning_resources.resource_intake publish" in review

    assert resource_intake.main(arguments, _environment(runtime_root)) == 0
    replay = json.loads(capsys.readouterr().out)
    assert replay["status"] == "replay"
    assert replay["ocr_calls"] == replay["qwen_attempts"] == 0
    assert len(calls) == 1


@pytest.mark.parametrize(
    "telemetry_field",
    ["external_network_calls", "peak_rss_kib"],
)
def test_telemetry_tamper_fails_replay_and_publish_without_overwriting(
    tmp_path, monkeypatch, capsys, telemetry_field
):
    (
        analyzed,
        analyze_arguments,
        pdf_path,
        library_path,
        runtime_root,
        candidates,
        calls,
    ) = _analyze(tmp_path, monkeypatch, capsys)
    candidate_path = candidates / analyzed["candidate_id"] / "candidate.json"
    candidate = json.loads(candidate_path.read_bytes())
    if telemetry_field == "external_network_calls":
        candidate["telemetry"][telemetry_field] = 999
    else:
        candidate["telemetry"][telemetry_field] += 1
    corrupted_bytes = resource_intake._candidate_bytes(candidate)
    candidate_path.write_bytes(corrupted_bytes)
    corrupted_sha256 = hashlib.sha256(corrupted_bytes).hexdigest()
    original_library = library_path.read_bytes()

    assert resource_intake.main(
        analyze_arguments, _environment(runtime_root)
    ) == 1
    replay_failure = json.loads(capsys.readouterr().out)
    assert replay_failure == {
        "reason_code": "RESOURCE_CANDIDATE_INVALID",
        "status": "failed",
    }
    assert corrupted_sha256 not in json.dumps(replay_failure)
    assert candidate_path.read_bytes() == corrupted_bytes
    assert len(calls) == 1

    publish_arguments = _publish_arguments(
        analyzed, corrupted_sha256, pdf_path, library_path
    )
    assert resource_intake.main(
        publish_arguments, _environment(runtime_root)
    ) == 1
    assert json.loads(capsys.readouterr().out)["reason_code"] == (
        "RESOURCE_CANDIDATE_INVALID"
    )
    assert candidate_path.read_bytes() == corrupted_bytes
    assert library_path.read_bytes() == original_library
    assert len(calls) == 1


def test_publish_is_idempotent_and_never_parses_review(
    tmp_path, monkeypatch, capsys
):
    analyzed, _, pdf_path, library_path, runtime_root, candidates, _ = _analyze(
        tmp_path, monkeypatch, capsys
    )
    review_path = candidates / analyzed["candidate_id"] / "review.md"
    review_path.write_text("human notes are not machine authority", encoding="utf-8")
    arguments = _publish_arguments(
        analyzed, analyzed["candidate_sha256"], pdf_path, library_path
    )

    assert resource_intake.main(arguments, _environment(runtime_root)) == 0
    published = json.loads(capsys.readouterr().out)
    first_bytes = library_path.read_bytes()
    assert published["status"] == "published"
    assert published["source_count"] == 2
    assert validate_resource_library(json.loads(first_bytes)) is None

    assert resource_intake.main(arguments, _environment(runtime_root)) == 0
    replay = json.loads(capsys.readouterr().out)
    assert replay["status"] == "already_published"
    assert replay["written_bytes"] == 0
    assert library_path.read_bytes() == first_bytes


def test_publish_confirmation_stale_and_partial_conflict_leave_target_exact(
    tmp_path, monkeypatch, capsys
):
    analyzed, _, pdf_path, library_path, runtime_root, _, _ = _analyze(
        tmp_path, monkeypatch, capsys
    )
    common = [
        "publish", analyzed["candidate_id"],
        "--candidate-sha256", analyzed["candidate_sha256"],
    ]
    original = library_path.read_bytes()
    assert resource_intake.main(
        common + ["--confirm", "wrong", "--source-pdf", "private-name.pdf", "--library-file", "missing.json"],
        _environment(runtime_root),
    ) == 1
    assert json.loads(capsys.readouterr().out)["reason_code"] == "RESOURCE_CONFIRMATION_MISMATCH"
    assert library_path.read_bytes() == original

    _write_library(library_path, ("c" * 64, "Other Concept"))
    stale_bytes = library_path.read_bytes()
    assert resource_intake.main(
        common + ["--confirm", analyzed["candidate_id"], "--source-pdf", str(pdf_path), "--library-file", str(library_path)],
        _environment(runtime_root),
    ) == 1
    assert json.loads(capsys.readouterr().out)["reason_code"] == "RESOURCE_BASE_LIBRARY_STALE"
    assert library_path.read_bytes() == stale_bytes

    _write_library(library_path, ("a" * 64, "Different Proposal"))
    conflict_bytes = library_path.read_bytes()
    assert resource_intake.main(
        common + ["--confirm", analyzed["candidate_id"], "--source-pdf", str(pdf_path), "--library-file", str(library_path)],
        _environment(runtime_root),
    ) == 1
    assert json.loads(capsys.readouterr().out)["reason_code"] == "RESOURCE_LIBRARY_CONFLICT"
    assert library_path.read_bytes() == conflict_bytes


def test_replace_failure_and_candidate_tamper_never_overwrite_library(
    tmp_path, monkeypatch, capsys
):
    analyzed, _, pdf_path, library_path, runtime_root, candidates, _ = _analyze(
        tmp_path, monkeypatch, capsys
    )
    arguments = _publish_arguments(
        analyzed, analyzed["candidate_sha256"], pdf_path, library_path
    )
    original = library_path.read_bytes()

    with monkeypatch.context() as source_failure:
        source_failure.setattr(
            resource_intake, "_inspect_pdf", lambda _: ("c" * 64, 1)
        )
        assert resource_intake.main(arguments, _environment(runtime_root)) == 1
        assert json.loads(capsys.readouterr().out)["reason_code"] == (
            "RESOURCE_SOURCE_BINDING_MISMATCH"
        )
        assert library_path.read_bytes() == original

    with monkeypatch.context() as temporary_file_failure:
        temporary_file_failure.setattr(
            resource_intake.tempfile,
            "NamedTemporaryFile",
            lambda **_: (_ for _ in ()).throw(OSError()),
        )
        assert resource_intake.main(arguments, _environment(runtime_root)) == 1
        assert json.loads(capsys.readouterr().out)["reason_code"] == (
            "RESOURCE_LIBRARY_WRITE_FAILED"
        )
        assert library_path.read_bytes() == original

    with monkeypatch.context() as replace_failure:
        replace_failure.setattr(
            resource_intake.os,
            "replace",
            lambda *_: (_ for _ in ()).throw(OSError()),
        )
        assert resource_intake.main(arguments, _environment(runtime_root)) == 1
        assert json.loads(capsys.readouterr().out)["reason_code"] == (
            "RESOURCE_LIBRARY_WRITE_FAILED"
        )
        assert library_path.read_bytes() == original

    candidate_path = candidates / analyzed["candidate_id"] / "candidate.json"
    candidate_path.write_bytes(candidate_path.read_bytes() + b" ")
    assert resource_intake.main(arguments, _environment(runtime_root)) == 1
    assert json.loads(capsys.readouterr().out)["reason_code"] == "RESOURCE_CANDIDATE_SHA_MISMATCH"
    assert library_path.read_bytes() == original


def test_projection_omits_unsupported_and_marks_grounding_blockers():
    output = producer_output()
    uncertain = deepcopy(output)
    uncertain["concepts"][0]["processing"] = "partial"
    proposals, omitted, blockers = resource_intake._project_output(uncertain)
    assert proposals == []
    assert omitted[0]["reason_code"] == "RESOURCE_PROPOSAL_UNCERTAIN"
    assert blockers == []

    missing = deepcopy(output)
    missing["concepts"][0]["evidence_ids"] = []
    proposals, omitted, blockers = resource_intake._project_output(missing)
    assert proposals == []
    assert omitted[0]["reason_code"] == "RESOURCE_EVIDENCE_MISSING"
    assert blockers == ["RESOURCE_EVIDENCE_MISSING"]

    multiple = deepcopy(output)
    multiple["concepts"][0]["evidence_ids"] *= 2
    proposals, omitted, blockers = resource_intake._project_output(multiple)
    assert proposals == []
    assert omitted[0]["reason_code"] == "RESOURCE_MULTIPLE_EVIDENCE_NOT_SUPPORTED"
    assert blockers == []

    rejected = deepcopy(output)
    rejected["rejected_candidates"] = [{
        "page_ref": rejected["pages"][0]["page_ref"],
        "candidate_index": 3,
        "processing": "failed",
        "quality": "needs_review",
        "decision": "reject",
        "reason_codes": ["MODEL_OUTPUT_INVALID"],
    }]
    _, omitted, _ = resource_intake._project_output(rejected)
    assert omitted[0]["reason_code"] == "RESOURCE_PROPOSAL_UNCERTAIN"


@pytest.mark.parametrize(
    "document",
    [
        {"schema": "resource-source-metadata/v1"},
        {
            "schema": "resource-source-metadata/v1", "title": "X", "authors": ["A", "A"],
            "source_url": "https://example.test/x", "citation": "X", "license": "CC0",
            "license_url": "https://example.test/license", "use_boundary": "Allowed",
        },
    ],
)
def test_metadata_is_closed_and_rejects_duplicate_authors(document):
    with pytest.raises(resource_intake.ResourceIntakeError, match="RESOURCE_METADATA_INVALID"):
        resource_intake._validate_metadata(document)


@pytest.mark.parametrize(
    "encoded",
    [b'{"schema":"first","schema":"second"}', b'{"value":NaN}'],
)
def test_json_file_boundary_rejects_duplicate_and_nonfinite(tmp_path, encoded):
    path = tmp_path / "source.json"
    path.write_bytes(encoded)
    with pytest.raises(resource_intake.ResourceIntakeError, match="RESOURCE_METADATA_INVALID"):
        resource_intake._read_json(path, "RESOURCE_METADATA_INVALID")


def test_cli_exposes_exactly_analyze_and_publish():
    subcommands = next(
        action for action in resource_intake._parser()._actions if hasattr(action, "choices") and action.choices
    )
    assert set(subcommands.choices) == {"analyze", "publish"}
