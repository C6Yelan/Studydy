from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pymupdf
import pytest

import pdf_evidence.text_first_run as run_module
from pdf_evidence.source_pdf import build_whole_document_request, copy_source_snapshot
from pdf_evidence.text_first_bundle import read_producer_bundle


def _settings(tmp_path):
    runtime_lock = json.loads(
        (Path(__file__).parents[2] / "local_ai" / "runtime-lock.json").read_text(encoding="utf-8")
    )
    return {
        "private_runtime_root": str(tmp_path / "runtime"),
        "runtime_lock": runtime_lock,
        "python_executable": "fixed-python",
        "site_packages": "fixed-site-packages",
        "ocr_model_root": "fixed-ocr",
        "concept_api_base_url": "http://127.0.0.1:8101",
        "concept_model": runtime_lock["semantic"]["model_id"],
    }


def _pdf(path, page_count=1):
    document = pymupdf.open()
    for page_number in range(1, page_count + 1):
        page = document.new_page(width=612, height=792)
        page.insert_text((72, 100), f"Public science note {page_number}")
    document.save(path)
    document.close()


def _request(path):
    return {
        "media_type": "application/pdf",
        "source_path": str(path),
        "expected_source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "page_numbers": [1],
    }


def test_source_pdf_snapshot_and_whole_document_page_count(tmp_path):
    source_path = tmp_path / "source.pdf"
    snapshot_path = tmp_path / "snapshot.pdf"
    _pdf(source_path, page_count=2)
    source_sha256 = hashlib.sha256(source_path.read_bytes()).hexdigest()

    assert copy_source_snapshot(source_path, snapshot_path) is None
    assert snapshot_path.read_bytes() == source_path.read_bytes()
    assert build_whole_document_request(
        {
            "media_type": "application/pdf",
            "source_path": str(snapshot_path),
            "expected_source_sha256": source_sha256,
        }
    )["page_numbers"] == [1, 2]

    symlink_path = tmp_path / "source-link.pdf"
    symlink_path.symlink_to(source_path)
    assert copy_source_snapshot(symlink_path, tmp_path / "ignored.pdf") == "MATERIAL_MISSING"


class FakeChild:
    def __init__(self, kind, state):
        self.kind = kind
        self.state = state
        state[f"{kind}_loads"] += 1
        state["resident"].append(kind)
        assert len(state["resident"]) == 1

    def request(self, request, timeout):
        self.state[self.kind] += 1
        return {
            "schema": "local-ocr-response/v1",
            "request_id": request["request_id"],
            "blocks": [
                {"type": "text", "text": "Public evidence", "bbox": [100, 100, 900, 300]}
            ],
        }

    def close(self):
        self.state["resident"].remove(self.kind)

    def abort(self):
        self.state["resident"].remove(self.kind)


class FakeConceptAPI:
    def __init__(self, state, *, invalid_first=False, always_invalid=False):
        self.state = state
        self.invalid_first = invalid_first
        self.always_invalid = always_invalid

    def __call__(self, client, **arguments):
        self.state["concept"] += 1
        if self.always_invalid or (self.invalid_first and self.state["concept"] == 1):
            return '{"concepts":[]}'
        evidence_id = arguments["semantic_request"]["evidence"][0]["evidence_id"]
        return json.dumps(
            {
                "concepts": [
                    {
                        "label": "Public concept",
                        "definition": "Public definition",
                        "key_points": ["Public point"],
                        "evidence_ids": [evidence_id],
                    }
                ]
            },
            separators=(",", ":"),
        )


class TimeoutConceptAPI(FakeConceptAPI):
    def __call__(self, client, **arguments):
        if self.state["concept"] == 0:
            self.state["concept"] += 1
            raise run_module.ConceptAPIError("CONCEPT_API_TIMEOUT")
        return super().__call__(client, **arguments)


class FailingOcr(FakeChild):
    def __init__(self, kind, state, reason_code):
        super().__init__(kind, state)
        self.reason_code = reason_code

    def request(self, request, timeout):
        self.state[self.kind] += 1
        raise run_module.LocalAIError(self.reason_code)


class TruncatedConceptAPI(FakeConceptAPI):
    def __call__(self, client, **arguments):
        self.state["concept"] += 1
        raise run_module.ConceptAPIError("MODEL_OUTPUT_TRUNCATED")


class AllInvalidOcr(FakeChild):
    def request(self, request, timeout):
        self.state[self.kind] += 1
        return {
            "schema": "local-ocr-response/v1",
            "request_id": request["request_id"],
            "blocks": [{"type": "text", "text": "", "bbox": [100, 100, 900, 300]}],
        }


class MalformedOcrResponse(FakeChild):
    def request(self, request, timeout):
        self.state[self.kind] += 1
        return {
            "schema": "local-ocr-response/v1",
            "request_id": request["request_id"],
            "blocks": [],
            "extra": True,
        }


class SecondPageInvalidOcr(FakeChild):
    def request(self, request, timeout):
        self.state[self.kind] += 1
        if request["request_id"] == "page-2":
            return {
                "schema": "local-ocr-response/v1",
                "request_id": request["request_id"],
                "blocks": [
                    {"type": "text", "text": "", "bbox": [100, 100, 900, 300]}
                ],
            }
        return {
            "schema": "local-ocr-response/v1",
            "request_id": request["request_id"],
            "blocks": [
                {
                    "type": "text",
                    "text": "Public evidence",
                    "bbox": [100, 100, 900, 300],
                }
            ],
        }


def _state():
    return {"resident": [], "ocr": 0, "concept": 0, "ocr_loads": 0}


def test_sequential_product_path_and_exact_replay_zero_model_calls(tmp_path, monkeypatch):
    path = tmp_path / "public.pdf"
    _pdf(path)
    state = _state()
    monkeypatch.setattr(run_module, "start_ocr_process", lambda settings: FakeChild("ocr", state))
    monkeypatch.setattr(run_module, "request_concept_text", FakeConceptAPI(state))
    first = run_module.run_text_first_pdf(_request(path), _settings(tmp_path))
    second = run_module.run_text_first_pdf(_request(path), _settings(tmp_path))
    assert first["processing"] == second["processing"] == "partial"
    assert state["ocr"] == state["concept"] == 1
    assert state["ocr_loads"] == 1
    assert first["concept_loads"] == second["concept_loads"] == 0
    assert second["ocr_calls"] == second["concept_calls"] == 0
    assert list((tmp_path / "runtime").rglob("*.png")) == []
    saved_json = "".join(
        path.read_text(encoding="utf-8")
        for path in (tmp_path / "runtime").rglob("*.json")
    )
    assert "png_base64" not in saved_json
    assert "model_text" not in saved_json
    assert state["resident"] == []


def test_semantic_fixed_retry_uses_same_binding_and_first_success(tmp_path, monkeypatch):
    path = tmp_path / "public.pdf"
    _pdf(path)
    state = _state()
    monkeypatch.setattr(run_module, "start_ocr_process", lambda settings: FakeChild("ocr", state))
    monkeypatch.setattr(
        run_module,
        "request_concept_text",
        FakeConceptAPI(state, invalid_first=True),
    )
    bundle = run_module.run_text_first_pdf(_request(path), _settings(tmp_path))
    assert bundle["processing"] == "partial"
    assert bundle["concept_calls"] == 2
    semantic_cache = next((tmp_path / "runtime" / "cache" / "semantic").glob("*.json"))
    artifact = json.loads(semantic_cache.read_text(encoding="utf-8"))["artifact"]
    assert artifact["attempt"] == 2


def test_semantic_timeout_retries_api_for_second_and_final_attempt(tmp_path, monkeypatch):
    path = tmp_path / "public.pdf"
    _pdf(path)
    state = _state()
    monkeypatch.setattr(run_module, "start_ocr_process", lambda settings: FakeChild("ocr", state))
    monkeypatch.setattr(
        run_module,
        "request_concept_text",
        TimeoutConceptAPI(state),
    )
    bundle = run_module.run_text_first_pdf(_request(path), _settings(tmp_path))
    assert bundle["processing"] == "partial"
    assert bundle["concept_calls"] == 2
    assert bundle["concept_loads"] == 0
    assert state["resident"] == []


def test_two_invalid_semantic_attempts_publish_only_failed_bundle(tmp_path, monkeypatch):
    path = tmp_path / "public.pdf"
    _pdf(path)
    state = _state()
    monkeypatch.setattr(run_module, "start_ocr_process", lambda settings: FakeChild("ocr", state))
    monkeypatch.setattr(
        run_module,
        "request_concept_text",
        FakeConceptAPI(state, always_invalid=True),
    )
    bundle = run_module.run_text_first_pdf(_request(path), _settings(tmp_path))
    assert bundle["processing"] == "failed"
    assert bundle["output_id"] is None
    assert bundle["reason_codes"] == ["MODEL_OUTPUT_INVALID"]
    run_root = tmp_path / "runtime" / "runs" / bundle["run_id"]
    assert not (run_root / "concept-evidence-output.json").exists()


def test_two_non_eos_attempts_fail_without_semantic_cache_or_output(tmp_path, monkeypatch):
    path = tmp_path / "public.pdf"
    _pdf(path)
    state = _state()
    monkeypatch.setattr(run_module, "start_ocr_process", lambda settings: FakeChild("ocr", state))
    monkeypatch.setattr(
        run_module,
        "request_concept_text",
        TruncatedConceptAPI(state),
    )
    bundle = run_module.run_text_first_pdf(_request(path), _settings(tmp_path))
    assert bundle["processing"] == "failed"
    assert bundle["reason_codes"] == ["MODEL_OUTPUT_INVALID"]
    assert bundle["concept_calls"] == 2
    assert list((tmp_path / "runtime" / "cache" / "semantic").glob("*.json")) == []
    run_root = tmp_path / "runtime" / "runs" / bundle["run_id"]
    assert not (run_root / "concept-evidence-output.json").exists()
    assert state["resident"] == []


@pytest.mark.parametrize(
    ("reason_code", "formal_reason"),
    [("CHILD_TIMEOUT", "PROCESS_TIMEOUT"), ("CHILD_EXITED", "PROCESS_FAILED")],
)
def test_dispatched_ocr_failure_keeps_reason_and_counts_call(
    tmp_path, monkeypatch, reason_code, formal_reason
):
    path = tmp_path / "public.pdf"
    _pdf(path)
    state = _state()
    monkeypatch.setattr(
        run_module,
        "start_ocr_process",
        lambda settings: FailingOcr("ocr", state, reason_code),
    )
    bundle = run_module.run_text_first_pdf(_request(path), _settings(tmp_path))
    assert bundle["processing"] == "failed"
    assert bundle["reason_codes"] == [formal_reason]
    assert bundle["ocr_calls"] == 1
    assert bundle["concept_calls"] == 0
    assert state["resident"] == []


def test_invalid_semantic_cache_is_recomputed_and_reported(tmp_path, monkeypatch):
    path = tmp_path / "public.pdf"
    _pdf(path)
    state = _state()
    monkeypatch.setattr(run_module, "start_ocr_process", lambda settings: FakeChild("ocr", state))
    monkeypatch.setattr(run_module, "request_concept_text", FakeConceptAPI(state))
    first = run_module.run_text_first_pdf(_request(path), _settings(tmp_path))
    assert first["processing"] == "partial"
    semantic_cache = next((tmp_path / "runtime" / "cache" / "semantic").glob("*.json"))
    semantic_cache.write_text('{"invalid":NaN}', encoding="utf-8")
    replay = run_module.run_text_first_pdf(_request(path), _settings(tmp_path))
    assert replay["processing"] == "partial"
    assert replay["ocr_calls"] == 0
    assert replay["concept_calls"] == 1
    assert "CACHE_RECOVERED" in replay["reason_codes"]


def test_page_cache_uses_full_geometry_validation_before_replay(tmp_path, monkeypatch):
    path = tmp_path / "public.pdf"
    _pdf(path)
    state = _state()
    monkeypatch.setattr(
        run_module, "start_ocr_process", lambda settings: FakeChild("ocr", state)
    )
    monkeypatch.setattr(
        run_module,
        "request_concept_text",
        FakeConceptAPI(state),
    )
    first = run_module.run_text_first_pdf(_request(path), _settings(tmp_path))
    assert first["processing"] == "partial"
    page_cache = next((tmp_path / "runtime" / "cache" / "page").glob("*.json"))
    record = json.loads(page_cache.read_text(encoding="utf-8"))
    artifact = record["artifact"]
    artifact["geometry"]["visible_points"] = [0.0, 0.0, 0.0, 1.0]
    artifact.pop("page_evidence_id")
    artifact["page_evidence_id"] = (
        "page-evidence:sha256:" + run_module.canonical_sha256(artifact)
    )
    record["artifact_sha256"] = run_module.canonical_sha256(artifact)
    page_cache.write_bytes(run_module.canonical_bytes(record))

    replay = run_module.run_text_first_pdf(_request(path), _settings(tmp_path))

    assert replay["processing"] == "partial"
    assert replay["ocr_calls"] == 1
    assert replay["concept_calls"] == 1
    assert "CACHE_RECOVERED" in replay["reason_codes"]


def test_all_rejected_blocks_publish_only_no_evidence_bundle(tmp_path, monkeypatch):
    path = tmp_path / "public.pdf"
    _pdf(path)
    state = _state()
    monkeypatch.setattr(run_module, "start_ocr_process", lambda settings: AllInvalidOcr("ocr", state))
    bundle = run_module.run_text_first_pdf(_request(path), _settings(tmp_path))
    assert bundle["processing"] == "failed"
    assert bundle["reason_codes"] == ["PAGE_CONTENT_UNUSABLE"]
    assert bundle["ocr_calls"] == 1
    assert bundle["concept_calls"] == 0
    assert bundle["concept_loads"] == 0
    assert list((tmp_path / "runtime" / "cache" / "page").glob("*.json")) == []
    run_root = tmp_path / "runtime" / "runs" / bundle["run_id"]
    assert not (run_root / "concept-evidence-output.json").exists()


def test_malformed_child_response_remains_hard_failure(tmp_path, monkeypatch):
    path = tmp_path / "public.pdf"
    _pdf(path)
    state = _state()
    monkeypatch.setattr(
        run_module,
        "start_ocr_process",
        lambda settings: MalformedOcrResponse("ocr", state),
    )
    bundle = run_module.run_text_first_pdf(_request(path), _settings(tmp_path))
    assert bundle["processing"] == "failed"
    assert bundle["reason_codes"] == ["PROCESS_FAILED"]
    assert bundle["ocr_calls"] == 1
    assert bundle["concept_calls"] == 0
    assert list((tmp_path / "runtime" / "cache" / "page").glob("*.json")) == []


def test_runtime_drift_rejected_before_private_path_access(tmp_path):
    settings = _settings(tmp_path)
    settings = deepcopy(settings)
    settings["runtime_lock"]["semantic"]["prompt_sha256"] = "0" * 64
    bundle = run_module.run_text_first_pdf(
        {
            "media_type": "application/pdf",
            "source_path": "private-sentinel.pdf",
            "expected_source_sha256": "0" * 64,
            "page_numbers": [1],
        },
        settings,
    )
    assert bundle["reason_codes"] == ["RUNTIME_INVALID"]
    assert "private-sentinel" not in json.dumps(bundle)


def test_source_hash_drift_in_runtime_lock_is_rejected(tmp_path):
    settings = deepcopy(_settings(tmp_path))
    settings["runtime_lock"]["semantic"]["code_hashes"]["backend_concept_api"] = (
        "c1cea5dd3331dc4481a9ee4d337c7fe65f835bf5654f4956d27e715c4bbc11c4"
    )
    bundle = run_module.run_text_first_pdf(
        {
            "media_type": "application/pdf",
            "source_path": "private-sentinel.pdf",
            "expected_source_sha256": "0" * 64,
            "page_numbers": [1],
        },
        settings,
    )
    assert bundle["reason_codes"] == ["RUNTIME_INVALID"]
    assert "private-sentinel" not in json.dumps(bundle)


def test_external_concept_api_is_rejected_before_source_access(tmp_path):
    settings = deepcopy(_settings(tmp_path))
    settings["concept_api_base_url"] = "http://example.test:8101"
    bundle = run_module.run_text_first_pdf(
        {
            "media_type": "application/pdf",
            "source_path": "private-sentinel.pdf",
            "expected_source_sha256": "0" * 64,
            "page_numbers": [1],
        },
        settings,
    )
    assert bundle["reason_codes"] == ["RUNTIME_INVALID"]
    assert "private-sentinel" not in json.dumps(bundle)


def test_missing_finish_reason_policy_is_rejected_before_source_access(tmp_path):
    settings = deepcopy(_settings(tmp_path))
    settings["runtime_lock"]["semantic"]["policy"].pop("generation_termination")
    bundle = run_module.run_text_first_pdf(
        {
            "media_type": "application/pdf",
            "source_path": "private-sentinel.pdf",
            "expected_source_sha256": "0" * 64,
            "page_numbers": [1],
        },
        settings,
    )
    assert bundle["reason_codes"] == ["RUNTIME_INVALID"]
    assert "private-sentinel" not in json.dumps(bundle)


def test_generation_cap_drift_in_runtime_lock_is_rejected(tmp_path):
    settings = deepcopy(_settings(tmp_path))
    settings["runtime_lock"]["semantic"]["generation"]["max_tokens"] = 1_024
    bundle = run_module.run_text_first_pdf(
        {
            "media_type": "application/pdf",
            "source_path": "private-sentinel.pdf",
            "expected_source_sha256": "0" * 64,
            "page_numbers": [1],
        },
        settings,
    )
    assert bundle["reason_codes"] == ["RUNTIME_INVALID"]
    assert "private-sentinel" not in json.dumps(bundle)


def test_invalid_media_type_is_truthful_and_sanitized(tmp_path):
    bundle = run_module.run_text_first_pdf(
        {
            "media_type": "text/plain",
            "source_path": "private-sentinel.pdf",
            "expected_source_sha256": "0" * 64,
            "page_numbers": [1],
        },
        _settings(tmp_path),
    )
    assert bundle["processing"] == "failed"
    assert bundle["reason_codes"] == ["SOURCE_INVALID"]
    assert "private-sentinel" not in json.dumps(bundle)


def test_formal_whole_document_excludes_one_page_and_keeps_grounded_core(
    tmp_path, monkeypatch
):
    path = tmp_path / "public-two-pages.pdf"
    _pdf(path, page_count=2)
    state = _state()
    original_extract_page = run_module.extract_page
    previous_page = []

    def tracked_extract_page(*arguments):
        if previous_page:
            assert "png_bytes" not in previous_page[0]
        page = original_extract_page(*arguments)
        previous_page[:] = [page]
        return page

    monkeypatch.setattr(run_module, "extract_page", tracked_extract_page)
    monkeypatch.setattr(
        run_module,
        "start_ocr_process",
        lambda settings: SecondPageInvalidOcr("ocr", state),
    )
    monkeypatch.setattr(
        run_module,
        "request_concept_text",
        FakeConceptAPI(state),
    )
    run_id = "text-first-run:00000000-0000-4000-8000-000000000002"
    bundle = run_module.run_full_text_first_pdf(
        {
            "media_type": "application/pdf",
            "source_path": str(path),
            "expected_source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        },
        _settings(tmp_path),
        run_id=run_id,
    )
    assert bundle["processing"] == "partial"
    assert bundle["page_count"] == 2
    assert bundle["included_page_count"] == bundle["excluded_page_count"] == 1
    assert bundle["ocr_calls"] == 2
    assert bundle["concept_calls"] == 1
    assert bundle["ocr_loads"] == 1
    assert bundle["concept_loads"] == 0
    published = read_producer_bundle(tmp_path / "runtime", run_id)
    assert published["output"]["concepts"][0]["processing"] == "succeeded"
    assert published["output"]["excluded_pages"][0]["page_number"] == 2
    assert published["output"]["excluded_pages"][0]["decision"] == "reject"
    assert "png_bytes" not in previous_page[0]
    assert state["resident"] == []
    assert list((tmp_path / "runtime").rglob("*.png")) == []


@pytest.mark.parametrize("page_count", [33, 40])
def test_formal_long_pdf_processes_every_page_without_truncation(
    tmp_path, monkeypatch, page_count
):
    path = tmp_path / f"long-{page_count}-pages.pdf"
    _pdf(path, page_count=page_count)
    state = _state()
    monkeypatch.setattr(
        run_module, "start_ocr_process", lambda settings: FakeChild("ocr", state)
    )
    monkeypatch.setattr(
        run_module,
        "request_concept_text",
        FakeConceptAPI(state),
    )
    run_id = f"text-first-run:00000000-0000-4000-8000-{page_count:012d}"
    bundle = run_module.run_full_text_first_pdf(
        {
            "media_type": "application/pdf",
            "source_path": str(path),
            "expected_source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        },
        _settings(tmp_path),
        run_id=run_id,
    )
    assert bundle["processing"] == "succeeded"
    assert bundle["page_count"] == page_count
    assert bundle["included_page_count"] == page_count
    assert bundle["excluded_page_count"] == 0
    assert bundle["ocr_calls"] == bundle["concept_calls"] == page_count
    assert bundle["ocr_loads"] == 1
    assert bundle["concept_loads"] == 0
    published = read_producer_bundle(tmp_path / "runtime", run_id)
    pages = published["output"]["pages"]
    assert [page["page_number"] for page in pages] == list(range(1, page_count + 1))
    assert pages[-1]["evidence_blocks"][0]["locator"]["page"] == page_count
    assert state["resident"] == []
    assert list((tmp_path / "runtime").rglob("*.png")) == []


def test_formal_lock_has_bounded_busy_failure(tmp_path, monkeypatch):
    runtime_root = tmp_path / "runtime"
    with run_module._agent1_lock(runtime_root):
        moments = iter((0.0, 6.0))
        monkeypatch.setattr(run_module.time, "monotonic", lambda: next(moments))
        monkeypatch.setattr(run_module.time, "sleep", lambda _: None)
        with pytest.raises(ValueError, match="RUNTIME_BUSY"):
            with run_module._agent1_lock(runtime_root):
                pass


def test_formal_run_reuses_worker_lock_ownership(tmp_path, monkeypatch):
    observed = []
    monkeypatch.setattr(
        run_module,
        "_run_text_first_pdf",
        lambda request, settings, **arguments: observed.append(arguments) or {"ok": True},
    )

    with run_module._agent1_lock(tmp_path / "runtime"):
        bundle = run_module.run_full_text_first_pdf(
            {
                "media_type": "application/pdf",
                "source_path": "owned-by-worker.pdf",
                "expected_source_sha256": "0" * 64,
            },
            _settings(tmp_path),
            run_id="text-first-run:00000000-0000-4000-8000-000000000004",
        )

    assert bundle == {"ok": True}
    assert observed[0]["whole_document"] is True


def test_formal_busy_lock_publishes_truthful_failed_bundle(tmp_path, monkeypatch):
    class BusyLock:
        def __enter__(self):
            raise ValueError("RUNTIME_BUSY")

        def __exit__(self, *_arguments):
            return None

    monkeypatch.setattr(run_module, "_agent1_lock", lambda _: BusyLock())
    run_id = "text-first-run:00000000-0000-4000-8000-000000000003"
    bundle = run_module.run_full_text_first_pdf(
        {
            "media_type": "application/pdf",
            "source_path": "not-read-while-busy.pdf",
            "expected_source_sha256": "0" * 64,
        },
        _settings(tmp_path),
        run_id=run_id,
    )
    assert bundle["processing"] == "failed"
    assert bundle["reason_codes"] == ["RUNTIME_BUSY"]
    assert bundle["ocr_calls"] == bundle["concept_calls"] == 0
    assert read_producer_bundle(tmp_path / "runtime", run_id)["bundle"] == bundle


def test_formal_runtime_rejects_caller_page_subset_before_model(tmp_path, monkeypatch):
    path = tmp_path / "public.pdf"
    _pdf(path)
    monkeypatch.setattr(
        run_module,
        "start_ocr_process",
        lambda settings: (_ for _ in ()).throw(AssertionError("OCR must not start")),
    )
    bundle = run_module.run_full_text_first_pdf(
        _request(path),
        _settings(tmp_path),
    )
    assert bundle["processing"] == "failed"
    assert bundle["reason_codes"] == ["SOURCE_READ_FAILED"]
    assert bundle["ocr_calls"] == 0
