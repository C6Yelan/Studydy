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
        "concept_model_root": "fixed-qwen",
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
    def __init__(self, kind, state, *, invalid_first=False, always_invalid=False):
        self.kind = kind
        self.state = state
        self.invalid_first = invalid_first
        self.always_invalid = always_invalid
        state[f"{kind}_loads"] += 1
        state["resident"].append(kind)
        assert len(state["resident"]) == 1

    def request(self, request, timeout):
        self.state[self.kind] += 1
        if self.kind == "ocr":
            return {
                "schema": "local-ocr-response/v1",
                "request_id": request["request_id"],
                "blocks": [
                    {"type": "text", "text": "Public evidence", "bbox": [100, 100, 900, 300]}
                ],
            }
        if self.always_invalid or (self.invalid_first and request["attempt"] == 1):
            model_text = '{"concepts":[]}'
        else:
            evidence_id = request["semantic_request"]["evidence"][0]["evidence_id"]
            model_text = json.dumps(
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
        return {
            "schema": "local-concept-response/v1",
            "request_id": request["request_id"],
            "attempt": request["attempt"],
            "model_text": model_text,
        }

    def close(self):
        self.state["resident"].remove(self.kind)

    def abort(self):
        self.state["resident"].remove(self.kind)


class TimeoutConcept(FakeChild):
    def request(self, request, timeout):
        self.state[self.kind] += 1
        if request["attempt"] == 1:
            raise run_module.LocalAIError("CHILD_TIMEOUT")
        return super().request(request, timeout)


class FailingOcr(FakeChild):
    def __init__(self, kind, state, reason_code):
        super().__init__(kind, state)
        self.reason_code = reason_code

    def request(self, request, timeout):
        self.state[self.kind] += 1
        raise run_module.LocalAIError(self.reason_code)


class TruncatedConcept(FakeChild):
    def request(self, request, timeout):
        self.state[self.kind] += 1
        return {
            "schema": "local-concept-failure/v1",
            "request_id": request["request_id"],
            "attempt": request["attempt"],
            "reason_code": "MODEL_OUTPUT_TRUNCATED",
        }


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
    return {"resident": [], "ocr": 0, "concept": 0, "ocr_loads": 0, "concept_loads": 0}


def test_sequential_product_path_and_exact_replay_zero_model_calls(tmp_path, monkeypatch):
    path = tmp_path / "public.pdf"
    _pdf(path)
    state = _state()
    monkeypatch.setattr(run_module, "start_ocr_process", lambda settings: FakeChild("ocr", state))
    monkeypatch.setattr(run_module, "start_concept_process", lambda settings: FakeChild("concept", state))
    first = run_module.run_text_first_pdf(_request(path), _settings(tmp_path))
    second = run_module.run_text_first_pdf(_request(path), _settings(tmp_path))
    assert first["processing"] == second["processing"] == "partial"
    assert state["ocr"] == state["concept"] == 1
    assert state["ocr_loads"] == state["concept_loads"] == 1
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
        "start_concept_process",
        lambda settings: FakeChild("concept", state, invalid_first=True),
    )
    terminal = run_module.run_text_first_pdf(_request(path), _settings(tmp_path))
    assert terminal["processing"] == "partial"
    assert terminal["concept_calls"] == 2
    semantic_cache = next((tmp_path / "runtime" / "cache" / "semantic").glob("*.json"))
    artifact = json.loads(semantic_cache.read_text(encoding="utf-8"))["artifact"]
    assert artifact["attempt"] == 2


def test_semantic_timeout_restarts_child_for_second_and_final_attempt(tmp_path, monkeypatch):
    path = tmp_path / "public.pdf"
    _pdf(path)
    state = _state()
    monkeypatch.setattr(run_module, "start_ocr_process", lambda settings: FakeChild("ocr", state))
    monkeypatch.setattr(
        run_module,
        "start_concept_process",
        lambda settings: TimeoutConcept("concept", state),
    )
    terminal = run_module.run_text_first_pdf(_request(path), _settings(tmp_path))
    assert terminal["processing"] == "partial"
    assert terminal["concept_calls"] == 2
    assert state["concept_loads"] == 2
    assert state["resident"] == []


def test_two_invalid_semantic_attempts_publish_only_failed_terminal(tmp_path, monkeypatch):
    path = tmp_path / "public.pdf"
    _pdf(path)
    state = _state()
    monkeypatch.setattr(run_module, "start_ocr_process", lambda settings: FakeChild("ocr", state))
    monkeypatch.setattr(
        run_module,
        "start_concept_process",
        lambda settings: FakeChild("concept", state, always_invalid=True),
    )
    terminal = run_module.run_text_first_pdf(_request(path), _settings(tmp_path))
    assert terminal["processing"] == "failed"
    assert terminal["output_id"] is None
    assert terminal["reason_codes"] == ["INVALID_CONCEPT_COUNT"]
    run_root = tmp_path / "runtime" / "runs" / terminal["run_id"]
    assert not (run_root / "concept-evidence-output.json").exists()


def test_two_non_eos_attempts_fail_without_semantic_cache_or_output(tmp_path, monkeypatch):
    path = tmp_path / "public.pdf"
    _pdf(path)
    state = _state()
    monkeypatch.setattr(run_module, "start_ocr_process", lambda settings: FakeChild("ocr", state))
    monkeypatch.setattr(
        run_module,
        "start_concept_process",
        lambda settings: TruncatedConcept("concept", state),
    )
    terminal = run_module.run_text_first_pdf(_request(path), _settings(tmp_path))
    assert terminal["processing"] == "failed"
    assert terminal["reason_codes"] == ["MODEL_OUTPUT_TRUNCATED"]
    assert terminal["concept_calls"] == 2
    assert list((tmp_path / "runtime" / "cache" / "semantic").glob("*.json")) == []
    run_root = tmp_path / "runtime" / "runs" / terminal["run_id"]
    assert not (run_root / "concept-evidence-output.json").exists()
    assert state["resident"] == []


@pytest.mark.parametrize("reason_code", ["CHILD_TIMEOUT", "CHILD_EXITED"])
def test_dispatched_ocr_failure_keeps_reason_and_counts_call(
    tmp_path, monkeypatch, reason_code
):
    path = tmp_path / "public.pdf"
    _pdf(path)
    state = _state()
    monkeypatch.setattr(
        run_module,
        "start_ocr_process",
        lambda settings: FailingOcr("ocr", state, reason_code),
    )
    terminal = run_module.run_text_first_pdf(_request(path), _settings(tmp_path))
    assert terminal["processing"] == "failed"
    assert terminal["reason_codes"] == [reason_code]
    assert terminal["ocr_calls"] == 1
    assert terminal["concept_calls"] == 0
    assert state["resident"] == []


def test_invalid_semantic_cache_is_recomputed_and_reported(tmp_path, monkeypatch):
    path = tmp_path / "public.pdf"
    _pdf(path)
    state = _state()
    monkeypatch.setattr(run_module, "start_ocr_process", lambda settings: FakeChild("ocr", state))
    monkeypatch.setattr(run_module, "start_concept_process", lambda settings: FakeChild("concept", state))
    first = run_module.run_text_first_pdf(_request(path), _settings(tmp_path))
    assert first["processing"] == "partial"
    semantic_cache = next((tmp_path / "runtime" / "cache" / "semantic").glob("*.json"))
    semantic_cache.write_text('{"invalid":NaN}', encoding="utf-8")
    replay = run_module.run_text_first_pdf(_request(path), _settings(tmp_path))
    assert replay["processing"] == "partial"
    assert replay["ocr_calls"] == 0
    assert replay["concept_calls"] == 1
    assert "CACHE_INVALID" in replay["reason_codes"]


def test_all_rejected_blocks_publish_only_no_evidence_terminal(tmp_path, monkeypatch):
    path = tmp_path / "public.pdf"
    _pdf(path)
    state = _state()
    monkeypatch.setattr(run_module, "start_ocr_process", lambda settings: AllInvalidOcr("ocr", state))
    terminal = run_module.run_text_first_pdf(_request(path), _settings(tmp_path))
    assert terminal["processing"] == "failed"
    assert terminal["reason_codes"] == ["NO_USABLE_EVIDENCE"]
    assert terminal["ocr_calls"] == 1
    assert terminal["concept_calls"] == 0
    assert state["concept_loads"] == 0
    assert list((tmp_path / "runtime" / "cache" / "page").glob("*.json")) == []
    run_root = tmp_path / "runtime" / "runs" / terminal["run_id"]
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
    terminal = run_module.run_text_first_pdf(_request(path), _settings(tmp_path))
    assert terminal["processing"] == "failed"
    assert terminal["reason_codes"] == ["CHILD_RESPONSE_INVALID"]
    assert terminal["ocr_calls"] == 1
    assert terminal["concept_calls"] == 0
    assert list((tmp_path / "runtime" / "cache" / "page").glob("*.json")) == []


def test_runtime_drift_rejected_before_private_path_access(tmp_path):
    settings = _settings(tmp_path)
    settings = deepcopy(settings)
    settings["runtime_lock"]["semantic"]["prompt_sha256"] = "0" * 64
    terminal = run_module.run_text_first_pdf(
        {
            "media_type": "application/pdf",
            "source_path": "private-sentinel.pdf",
            "expected_source_sha256": "0" * 64,
            "page_numbers": [1],
        },
        settings,
    )
    assert terminal["reason_codes"] == ["RUNTIME_BINDING_INVALID"]
    assert "private-sentinel" not in json.dumps(terminal)


def test_source_hash_drift_in_runtime_lock_is_rejected(tmp_path):
    settings = deepcopy(_settings(tmp_path))
    settings["runtime_lock"]["semantic"]["code_hashes"]["local_ai_concept"] = (
        "c1cea5dd3331dc4481a9ee4d337c7fe65f835bf5654f4956d27e715c4bbc11c4"
    )
    terminal = run_module.run_text_first_pdf(
        {
            "media_type": "application/pdf",
            "source_path": "private-sentinel.pdf",
            "expected_source_sha256": "0" * 64,
            "page_numbers": [1],
        },
        settings,
    )
    assert terminal["reason_codes"] == ["RUNTIME_BINDING_INVALID"]
    assert "private-sentinel" not in json.dumps(terminal)


def test_old_wheel_binding_is_rejected_before_source_access(tmp_path):
    settings = deepcopy(_settings(tmp_path))
    settings["runtime_lock"]["packages"]["studydy_local_ai_wheel_sha256"] = (
        "9b6c195e390fde91b94ed19e24c6c80f2f4e5e3d787f2fc96cb03834eb8bc8e0"
    )
    terminal = run_module.run_text_first_pdf(
        {
            "media_type": "application/pdf",
            "source_path": "private-sentinel.pdf",
            "expected_source_sha256": "0" * 64,
            "page_numbers": [1],
        },
        settings,
    )
    assert terminal["reason_codes"] == ["RUNTIME_BINDING_INVALID"]
    assert "private-sentinel" not in json.dumps(terminal)


def test_missing_eos_termination_policy_is_rejected_before_source_access(tmp_path):
    settings = deepcopy(_settings(tmp_path))
    settings["runtime_lock"]["semantic"]["policy"].pop("generation_termination")
    terminal = run_module.run_text_first_pdf(
        {
            "media_type": "application/pdf",
            "source_path": "private-sentinel.pdf",
            "expected_source_sha256": "0" * 64,
            "page_numbers": [1],
        },
        settings,
    )
    assert terminal["reason_codes"] == ["RUNTIME_BINDING_INVALID"]
    assert "private-sentinel" not in json.dumps(terminal)


def test_generation_cap_drift_in_runtime_lock_is_rejected(tmp_path):
    settings = deepcopy(_settings(tmp_path))
    settings["runtime_lock"]["semantic"]["generation"]["max_new_tokens"] = 1_024
    terminal = run_module.run_text_first_pdf(
        {
            "media_type": "application/pdf",
            "source_path": "private-sentinel.pdf",
            "expected_source_sha256": "0" * 64,
            "page_numbers": [1],
        },
        settings,
    )
    assert terminal["reason_codes"] == ["RUNTIME_BINDING_INVALID"]
    assert "private-sentinel" not in json.dumps(terminal)


def test_invalid_media_type_is_truthful_and_sanitized(tmp_path):
    terminal = run_module.run_text_first_pdf(
        {
            "media_type": "text/plain",
            "source_path": "private-sentinel.pdf",
            "expected_source_sha256": "0" * 64,
            "page_numbers": [1],
        },
        _settings(tmp_path),
    )
    assert terminal["processing"] == "failed"
    assert terminal["reason_codes"] == ["MEDIA_TYPE_INVALID"]
    assert "private-sentinel" not in json.dumps(terminal)


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
        "start_concept_process",
        lambda settings: FakeChild("concept", state),
    )
    run_id = "text-first-run:00000000-0000-4000-8000-000000000002"
    terminal = run_module.run_full_text_first_pdf(
        {
            "media_type": "application/pdf",
            "source_path": str(path),
            "expected_source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        },
        _settings(tmp_path),
        run_id=run_id,
    )
    assert terminal["processing"] == "partial"
    assert terminal["page_count"] == 2
    assert terminal["included_page_count"] == terminal["excluded_page_count"] == 1
    assert terminal["ocr_calls"] == 2
    assert terminal["concept_calls"] == 1
    assert terminal["ocr_loads"] == terminal["concept_loads"] == 1
    bundle = read_producer_bundle(tmp_path / "runtime", run_id)
    assert bundle["output"]["concepts"][0]["processing"] == "succeeded"
    assert bundle["output"]["excluded_pages"][0]["page_number"] == 2
    assert bundle["output"]["excluded_pages"][0]["decision"] == "reject"
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
        "start_concept_process",
        lambda settings: FakeChild("concept", state),
    )
    run_id = f"text-first-run:00000000-0000-4000-8000-{page_count:012d}"
    terminal = run_module.run_full_text_first_pdf(
        {
            "media_type": "application/pdf",
            "source_path": str(path),
            "expected_source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        },
        _settings(tmp_path),
        run_id=run_id,
    )
    assert terminal["processing"] == "succeeded"
    assert terminal["page_count"] == page_count
    assert terminal["included_page_count"] == page_count
    assert terminal["excluded_page_count"] == 0
    assert terminal["ocr_calls"] == terminal["concept_calls"] == page_count
    assert terminal["ocr_loads"] == terminal["concept_loads"] == 1
    bundle = read_producer_bundle(tmp_path / "runtime", run_id)
    pages = bundle["output"]["pages"]
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


def test_formal_busy_lock_publishes_truthful_failed_terminal(tmp_path, monkeypatch):
    class BusyLock:
        def __enter__(self):
            raise ValueError("RUNTIME_BUSY")

        def __exit__(self, *_arguments):
            return None

    monkeypatch.setattr(run_module, "_agent1_lock", lambda _: BusyLock())
    run_id = "text-first-run:00000000-0000-4000-8000-000000000003"
    terminal = run_module.run_full_text_first_pdf(
        {
            "media_type": "application/pdf",
            "source_path": "not-read-while-busy.pdf",
            "expected_source_sha256": "0" * 64,
        },
        _settings(tmp_path),
        run_id=run_id,
    )
    assert terminal["processing"] == "failed"
    assert terminal["reason_codes"] == ["RUNTIME_BUSY"]
    assert terminal["ocr_calls"] == terminal["concept_calls"] == 0
    assert read_producer_bundle(tmp_path / "runtime", run_id)["terminal"] == terminal


def test_formal_runtime_rejects_caller_page_subset_before_model(tmp_path, monkeypatch):
    path = tmp_path / "public.pdf"
    _pdf(path)
    monkeypatch.setattr(
        run_module,
        "start_ocr_process",
        lambda settings: (_ for _ in ()).throw(AssertionError("OCR must not start")),
    )
    terminal = run_module.run_full_text_first_pdf(
        _request(path),
        _settings(tmp_path),
    )
    assert terminal["processing"] == "failed"
    assert terminal["reason_codes"] == ["SOURCE_READ_FAILED"]
    assert terminal["ocr_calls"] == 0
