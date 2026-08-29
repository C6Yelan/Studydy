from copy import deepcopy
import hashlib
import json
from pathlib import Path
import threading

import pymupdf
import pytest

import pdf_evidence.text_first_run as run_module
from pdf_evidence.source_pdf import snapshot_whole_document_request
from pdf_evidence.text_first_bundle import read_producer_bundle


def _settings(tmp_path):
    runtime_lock = json.loads(
        (Path(__file__).parents[2] / "local_ai" / "runtime-lock.json").read_text(encoding="utf-8")
    )
    root = tmp_path
    return {
        "private_runtime_root": str(root / "runtime"),
        "runtime_lock": runtime_lock,
        "python_executable": str(root / "ocr/runtime/bin/python3.12"),
        "site_packages": str(root / "ocr/runtime/lib/python3.12/site-packages"),
        "concept_site_packages": str(root / "vllm/lib/python3.12/site-packages"),
        "ocr_model_root": str(root / "models/unlimited-ocr"),
        "relation_model_root": str(root / "models/mdeberta-v3-base-mnli-xnli"),
        "concept_api_base_url": "http://127.0.0.1:8101",
        "concept_model": runtime_lock["semantic"]["model_id"],
        "concept_server_executable": str(root / "vllm/bin/vllm"),
        "concept_model_root": str(root / "models/qwen3-14b-awq"),
        "concept_kv_cache_bytes": 2_147_483_648,
        "concept_max_concurrency": 1,
        "concept_max_model_len": 8_192,
    }


def _pdf(path, page_count=1):
    document = pymupdf.open()
    for page_number in range(1, page_count + 1):
        page = document.new_page(width=612, height=792)
        page.insert_text((72, 100), f"Public science note {page_number}")
    document.save(path)
    document.close()


def _title_and_content_pdf(path):
    document = pymupdf.open()
    title_page = document.new_page(width=720, height=540)
    title_page.insert_text((280, 330), "Public title", fontsize=32)
    title_page.insert_text((295, 400), "Public author", fontsize=20)
    title_page.insert_text((560, 520), "Public footer", fontsize=12)
    content_page = document.new_page(width=720, height=540)
    content_page.insert_text((43, 65), "Public topic", fontsize=32)
    content_page.insert_text(
        (43, 150),
        "Public grounded content explains a concrete learning rule and example.",
        fontsize=18,
    )
    content_page.insert_text((560, 520), "Public footer", fontsize=12)
    document.save(path)
    document.close()


def _request(path):
    return {
        "media_type": "application/pdf",
        "source_path": str(path),
        "expected_source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "page_numbers": [1],
    }


def _whole_request(path):
    request = _request(path)
    request.pop("page_numbers")
    return request


def test_source_pdf_snapshot_and_whole_document_page_count(tmp_path):
    source_path = tmp_path / "source.pdf"
    snapshot_path = tmp_path / "snapshot.pdf"
    _pdf(source_path, page_count=2)
    source_sha256 = hashlib.sha256(source_path.read_bytes()).hexdigest()

    checked = snapshot_whole_document_request(
        {
            "media_type": "application/pdf",
            "source_path": str(source_path),
            "expected_source_sha256": source_sha256,
        },
        snapshot_path,
    )
    assert snapshot_path.read_bytes() == source_path.read_bytes()
    assert checked["page_numbers"] == [1, 2]

    symlink_path = tmp_path / "source-link.pdf"
    symlink_path.symlink_to(source_path)
    with pytest.raises(ValueError, match="SOURCE_READ_FAILED"):
        snapshot_whole_document_request(
            {
                "media_type": "application/pdf",
                "source_path": str(symlink_path),
                "expected_source_sha256": source_sha256,
            },
            tmp_path / "ignored.pdf",
        )


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


class MultipleEvidenceChild(FakeChild):
    def request(self, request, timeout):
        self.state[self.kind] += 1
        return {
            "schema": "local-ocr-response/v1",
            "request_id": request["request_id"],
            "blocks": [
                {"type": "text", "text": "First public detail", "bbox": [100, 100, 900, 300]},
                {"type": "text", "text": "Second public detail", "bbox": [100, 350, 900, 550]},
            ],
        }


class FakeConceptAPI:
    def __init__(self, state, *, invalid_first=False, always_invalid=False):
        self.state = state
        self.invalid_first = invalid_first
        self.always_invalid = always_invalid

    def __call__(self, client, **arguments):
        self.state["concept"] += 1
        self.state.setdefault("semantic_requests", []).append(
            deepcopy(arguments["semantic_request"])
        )
        if self.always_invalid or (self.invalid_first and self.state["concept"] == 1):
            return '{"concepts":'
        evidence_id = arguments["semantic_request"]["evidence"][0]["id"]
        return json.dumps(
            {
                "concepts": [
                    {
                        "label": "Public concept",
                        "definition": {"text": "Public definition", "evidence_ids": [evidence_id]},
                        "key_points": [{"text": "Public point", "evidence_ids": [evidence_id]}],
                    }
                ]
            },
            separators=(",", ":"),
        )


class FailingOcr(FakeChild):
    def __init__(self, kind, state, reason_code):
        super().__init__(kind, state)
        self.reason_code = reason_code

    def request(self, request, timeout):
        self.state[self.kind] += 1
        raise run_module.LocalAIError(self.reason_code)


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


class FakeConceptServer:
    def __init__(self, state):
        self.state = state
        self.is_closed = False
        assert state["resident"] == []
        state["resident"].append("concept_server")

    def close(self):
        self.state["resident"].remove("concept_server")
        self.is_closed = True


@pytest.fixture(autouse=True)
def no_real_concept_server(monkeypatch):
    class FakeServer:
        def close(self):
            return None

    monkeypatch.setattr(run_module, "start_concept_server", lambda _: FakeServer())
    monkeypatch.setattr(
        run_module,
        "fit_concept_request",
        lambda _client, **arguments: deepcopy(arguments["semantic_request"]),
    )


def test_sequential_product_path_and_exact_replay_zero_model_calls(tmp_path, monkeypatch):
    path = tmp_path / "public.pdf"
    _pdf(path)
    state = _state()
    monkeypatch.setattr(run_module, "start_ocr_process", lambda settings: FakeChild("ocr", state))
    monkeypatch.setattr(run_module, "request_concept_text", FakeConceptAPI(state))
    first = run_module.run_full_text_first_pdf(_whole_request(path), _settings(tmp_path))
    second = run_module.run_full_text_first_pdf(_whole_request(path), _settings(tmp_path))
    assert first["processing"] == second["processing"] == "succeeded"
    assert state["ocr"] == 0
    assert state["concept"] == 1
    assert state["ocr_loads"] == 0
    assert first["concept_loads"] == 1
    assert second["concept_loads"] == 0
    assert second["ocr_calls"] == second["concept_calls"] == 0
    assert list((tmp_path / "runtime").rglob("*.png")) == []
    saved_json = "".join(
        path.read_text(encoding="utf-8")
        for path in (tmp_path / "runtime").rglob("*.json")
    )
    semantic_cache = json.loads(
        next((tmp_path / "runtime" / "cache" / "semantic").glob("*.json")).read_text(
            encoding="utf-8"
        )
    )
    output = read_producer_bundle(
        tmp_path / "runtime", first["run_id"]
    )["output"]
    batch_binding = semantic_cache["input_binding"]["batch_bindings"][0]
    assert semantic_cache["cache_key"] == run_module.canonical_sha256(
        semantic_cache["input_binding"]
    )
    assert semantic_cache["artifact"]["input_binding"] == (
        semantic_cache["input_binding"]
    )
    assert batch_binding["semantic_request_sha256"]
    assert batch_binding["semantic_request_sha256"] == (
        run_module.canonical_sha256(state["semantic_requests"][0])
    )
    assert batch_binding["semantic_request"] == state["semantic_requests"][0]
    assert batch_binding["semantic_request"]["document_context"][
        "document_context_id"
    ].startswith(
        "concept-context:sha256:"
    )
    assert output["document_contexts"][0]["page_evidence_id"] != (
        output["pages"][0]["page_evidence_id"]
    )
    assert output["semantic_batches"][0]["semantic_request"][
        "document_context"
    ]["source_context_id"] == (
        output["document_contexts"][0]["context_id"]
    )
    assert "png_base64" not in saved_json
    assert "model_text" not in saved_json
    assert state["resident"] == []


def test_progress_callback_reports_each_page_and_stage_monotonically(
    tmp_path,
    monkeypatch,
):
    path = tmp_path / "progress.pdf"
    _title_and_content_pdf(path)
    state = _state()
    monkeypatch.setattr(run_module, "request_concept_text", FakeConceptAPI(state))
    updates = []

    bundle = run_module.run_full_text_first_pdf(
        _whole_request(path),
        _settings(tmp_path),
        progress_callback=lambda stage, completed, total: updates.append(
            (stage, completed, total)
        ),
    )

    assert bundle["processing"] == "succeeded"
    assert updates == [
        ("page_evidence", 0, 2),
        ("page_evidence", 1, 2),
        ("page_evidence", 2, 2),
        ("concept_generation", 0, 2),
        ("concept_generation", 1, 2),
        ("concept_generation", 2, 2),
    ]


def test_progress_callback_failure_produces_failed_bundle(tmp_path):
    path = tmp_path / "progress-failure.pdf"
    _pdf(path)

    def fail_progress(_stage, _completed, _total):
        raise RuntimeError("progress storage unavailable")

    bundle = run_module.run_full_text_first_pdf(
        _whole_request(path),
        _settings(tmp_path),
        progress_callback=fail_progress,
    )

    assert bundle["processing"] == "failed"
    assert bundle["reason_codes"] == ["INTERNAL_FAILURE"]


def test_heading_only_page_keeps_evidence_without_suppressing_adjacent_content(
    tmp_path, monkeypatch
):
    path = tmp_path / "title-and-content.pdf"
    _title_and_content_pdf(path)
    state = _state()
    monkeypatch.setattr(run_module, "request_concept_text", FakeConceptAPI(state))

    bundle = run_module.run_full_text_first_pdf(
        _whole_request(path), _settings(tmp_path)
    )
    output = read_producer_bundle(tmp_path / "runtime", bundle["run_id"])["output"]

    assert bundle["processing"] == "succeeded"
    assert bundle["included_page_count"] == 2
    assert bundle["concept_calls"] == state["concept"] == 1
    assert all(
        block["kind"] == "heading"
        for block in output["pages"][0]["evidence_blocks"]
    )
    assert [
        block["kind"] for block in output["pages"][1]["evidence_blocks"]
    ][:2] == ["heading", "paragraph"]
    second_context = next(
        context
        for context in output["document_contexts"]
        if context["page_ref"] == output["pages"][1]["page_ref"]
    )
    assert second_context["current_blocks"][1][
        "heading_ancestry_block_ids"
    ] == [output["pages"][1]["evidence_blocks"][0]["block_id"]]
    assert [concept["page_ref"] for concept in output["concepts"]] == [
        output["pages"][1]["page_ref"]
    ]


def test_oversized_page_is_split_and_all_batches_remain_grounded(tmp_path, monkeypatch):
    path = tmp_path / "public.pdf"
    _pdf(path)
    state = _state()
    seen_requests = []

    def split_when_needed(client, **arguments):
        semantic_request = arguments["semantic_request"]
        seen_requests.append(deepcopy(semantic_request))
        if len(semantic_request["evidence"]) > 1:
            raise run_module.ConceptAPIError("MODEL_INPUT_TOO_LARGE")
        return FakeConceptAPI(state)(client, **arguments)

    monkeypatch.setattr(
        run_module,
        "start_ocr_process",
        lambda settings: MultipleEvidenceChild("ocr", state),
    )
    monkeypatch.setattr(run_module, "route_page", lambda page: "OCR_needed")
    monkeypatch.setattr(run_module, "request_concept_text", split_when_needed)

    bundle = run_module.run_full_text_first_pdf(_whole_request(path), _settings(tmp_path))
    output = read_producer_bundle(
        tmp_path / "runtime", bundle["run_id"]
    )["output"]

    assert bundle["processing"] == "succeeded"
    assert bundle["excluded_page_count"] == 0
    assert bundle["concept_calls"] == len(output["concepts"])
    assert len(seen_requests) > bundle["concept_calls"]
    assert [
        batch["batch_index"] for batch in output["semantic_batches"]
    ] == list(range(len(output["semantic_batches"])))
    assert len(output["semantic_batches"]) == bundle["concept_calls"]
    assert len({
        batch["semantic_request_sha256"]
        for batch in output["semantic_batches"]
    }) == len(output["semantic_batches"])
    assert len({
        batch["semantic_request"]["document_context"]["document_context_id"]
        for batch in output["semantic_batches"]
    }) == len(output["semantic_batches"])
    assert {
        batch["semantic_request"]["document_context"]["source_context_id"]
        for batch in output["semantic_batches"]
    } == {output["document_contexts"][0]["context_id"]}
    page_evidence_ids = {
        block["evidence_id"] for block in output["pages"][0]["evidence_blocks"]
    }
    assert {
        evidence_id
        for concept in output["concepts"]
        for claim in [concept["definition"], *concept["key_points"]]
        for evidence_id in claim["evidence_ids"]
    } == page_evidence_ids


def test_malformed_concept_output_is_one_call_and_failed(tmp_path, monkeypatch):
    path = tmp_path / "public.pdf"
    _pdf(path)
    state = _state()
    monkeypatch.setattr(run_module, "start_ocr_process", lambda settings: FakeChild("ocr", state))
    monkeypatch.setattr(
        run_module,
        "request_concept_text",
        FakeConceptAPI(state, invalid_first=True),
    )
    bundle = run_module.run_full_text_first_pdf(_whole_request(path), _settings(tmp_path))
    assert bundle["processing"] == "failed"
    assert bundle["output_id"] is None
    assert bundle["concept_calls"] == 1
    assert bundle["reason_codes"] == ["MODEL_OUTPUT_INVALID"]
    run_root = tmp_path / "runtime" / "runs" / bundle["run_id"]
    assert not (run_root / "concept-evidence-output.json").exists()


def test_dispatched_ocr_failure_keeps_reason_and_counts_call(
    tmp_path, monkeypatch
):
    path = tmp_path / "public.pdf"
    _pdf(path)
    state = _state()
    monkeypatch.setattr(
        run_module,
        "start_ocr_process",
        lambda settings: FailingOcr("ocr", state, "CHILD_EXITED"),
    )
    monkeypatch.setattr(run_module, "route_page", lambda page: "OCR_needed")
    bundle = run_module.run_full_text_first_pdf(_whole_request(path), _settings(tmp_path))
    assert bundle["processing"] == "failed"
    assert bundle["reason_codes"] == ["PROCESS_FAILED"]
    assert bundle["ocr_calls"] == 1
    assert bundle["concept_calls"] == 0
    assert state["resident"] == []


def test_invalid_semantic_cache_is_recomputed_and_reported(tmp_path, monkeypatch):
    path = tmp_path / "public.pdf"
    _pdf(path)
    state = _state()
    monkeypatch.setattr(run_module, "start_ocr_process", lambda settings: FakeChild("ocr", state))
    monkeypatch.setattr(run_module, "request_concept_text", FakeConceptAPI(state))
    first = run_module.run_full_text_first_pdf(_whole_request(path), _settings(tmp_path))
    assert first["processing"] == "succeeded"
    semantic_cache = next((tmp_path / "runtime" / "cache" / "semantic").glob("*.json"))
    semantic_cache.write_text('{"invalid":NaN}', encoding="utf-8")
    replay = run_module.run_full_text_first_pdf(_whole_request(path), _settings(tmp_path))
    assert replay["processing"] == "succeeded"
    assert replay["ocr_calls"] == 0
    assert replay["concept_calls"] == 1
    assert "CACHE_RECOVERED" in replay["reason_codes"]


@pytest.mark.parametrize("mutation", ["evidence_text", "context_kind"])
def test_recomputed_exact_request_or_context_cache_tamper_is_rejected(
    tmp_path, monkeypatch, mutation
):
    path = tmp_path / "public.pdf"
    _pdf(path)
    state = _state()
    monkeypatch.setattr(
        run_module, "request_concept_text", FakeConceptAPI(state)
    )
    first = run_module.run_full_text_first_pdf(
        _whole_request(path), _settings(tmp_path)
    )
    assert first["processing"] == "succeeded"
    cache_path = next((tmp_path / "runtime" / "cache" / "semantic").glob("*.json"))
    record = json.loads(cache_path.read_text(encoding="utf-8"))
    binding = record["input_binding"]
    batch = binding["batch_bindings"][0]
    request = batch["semantic_request"]
    if mutation == "evidence_text":
        request["evidence"][0]["text"] = "Changed public Evidence"
    else:
        request["document_context"]["current_blocks"][0]["kind"] = "list"
        context_identity = {
            key: value
            for key, value in request["document_context"].items()
            if key != "document_context_id"
        }
        request["document_context"]["document_context_id"] = (
            "concept-context:sha256:"
            + run_module.canonical_sha256(context_identity)
        )
    batch["semantic_request_sha256"] = run_module.canonical_sha256(request)
    record["cache_key"] = run_module.canonical_sha256(binding)
    record["artifact"]["input_binding"] = deepcopy(binding)
    record["artifact_sha256"] = run_module.canonical_sha256(record["artifact"])
    cache_path.write_bytes(run_module.canonical_bytes(record))

    replay = run_module.run_full_text_first_pdf(
        _whole_request(path), _settings(tmp_path)
    )

    assert replay["processing"] == "succeeded"
    assert state["concept"] == 2
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
    first = run_module.run_full_text_first_pdf(_whole_request(path), _settings(tmp_path))
    assert first["processing"] == "succeeded"
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

    replay = run_module.run_full_text_first_pdf(_whole_request(path), _settings(tmp_path))

    assert replay["processing"] == "succeeded"
    assert replay["ocr_calls"] == 0
    assert replay["concept_calls"] == 1
    assert "CACHE_RECOVERED" in replay["reason_codes"]


def test_all_rejected_blocks_publish_only_no_evidence_bundle(tmp_path, monkeypatch):
    path = tmp_path / "public.pdf"
    _pdf(path)
    state = _state()
    monkeypatch.setattr(run_module, "start_ocr_process", lambda settings: AllInvalidOcr("ocr", state))
    monkeypatch.setattr(run_module, "route_page", lambda page: "OCR_needed")
    bundle = run_module.run_full_text_first_pdf(_whole_request(path), _settings(tmp_path))
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
    monkeypatch.setattr(run_module, "route_page", lambda page: "OCR_needed")
    bundle = run_module.run_full_text_first_pdf(_whole_request(path), _settings(tmp_path))
    assert bundle["processing"] == "failed"
    assert bundle["reason_codes"] == ["PROCESS_FAILED"]
    assert bundle["ocr_calls"] == 1
    assert bundle["concept_calls"] == 0
    assert list((tmp_path / "runtime" / "cache" / "page").glob("*.json")) == []


def test_runtime_drift_rejected_before_private_path_access(tmp_path):
    settings = _settings(tmp_path)
    settings = deepcopy(settings)
    settings["runtime_lock"]["semantic"]["prompt_sha256"] = "0" * 64
    bundle = run_module.run_full_text_first_pdf(
        {
            "media_type": "application/pdf",
            "source_path": "private-sentinel.pdf",
            "expected_source_sha256": "0" * 64,
        },
        settings,
    )
    assert bundle["reason_codes"] == ["RUNTIME_INVALID"]
    assert "private-sentinel" not in json.dumps(bundle)
def test_external_concept_api_is_rejected_before_source_access(tmp_path):
    settings = deepcopy(_settings(tmp_path))
    settings["concept_api_base_url"] = "http://example.test:8101"
    bundle = run_module.run_full_text_first_pdf(
        {
            "media_type": "application/pdf",
            "source_path": "private-sentinel.pdf",
            "expected_source_sha256": "0" * 64,
        },
        settings,
    )
    assert bundle["reason_codes"] == ["RUNTIME_INVALID"]
    assert "private-sentinel" not in json.dumps(bundle)
def test_invalid_media_type_is_truthful_and_sanitized(tmp_path):
    bundle = run_module.run_full_text_first_pdf(
        {
            "media_type": "text/plain",
            "source_path": "private-sentinel.pdf",
            "expected_source_sha256": "0" * 64,
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
    source_documents = []

    def tracked_extract_page(*arguments):
        if previous_page:
            assert "png_bytes" not in previous_page[0]
            assert "native_evidence" not in previous_page[0]
        source_documents.append(arguments[0])
        page = original_extract_page(*arguments)
        previous_page[:] = [page]
        return page

    monkeypatch.setattr(run_module, "extract_page", tracked_extract_page)
    monkeypatch.setattr(
        run_module,
        "start_ocr_process",
        lambda settings: SecondPageInvalidOcr("ocr", state),
    )
    monkeypatch.setattr(run_module, "route_page", lambda page: "OCR_needed")
    monkeypatch.setattr(
        run_module,
        "request_concept_text",
        FakeConceptAPI(state),
    )
    run_id = "text-first-run:00000000-0000-4000-8000-000000000002"
    bundle = run_module.run_full_text_first_pdf(
        _whole_request(path),
        _settings(tmp_path),
        run_id=run_id,
    )
    assert bundle["processing"] == "partial"
    assert bundle["page_count"] == 2
    assert bundle["included_page_count"] == bundle["excluded_page_count"] == 1
    assert bundle["ocr_calls"] == 2
    assert bundle["concept_calls"] == 1
    assert bundle["ocr_loads"] == 1
    assert bundle["concept_loads"] == 1
    published = read_producer_bundle(tmp_path / "runtime", run_id)
    assert published["output"]["concepts"][0]["processing"] == "succeeded"
    assert published["output"]["excluded_pages"][0]["page_number"] == 2
    assert published["output"]["excluded_pages"][0]["decision"] == "reject"
    assert "png_bytes" not in previous_page[0]
    assert "native_evidence" not in previous_page[0]
    assert len({id(document) for document in source_documents}) == 1
    assert state["resident"] == []
    assert list((tmp_path / "runtime").rglob("*.png")) == []
    assert not (tmp_path / "runtime" / "artifacts" / "native").exists()
    for artifact_path in (tmp_path / "runtime").rglob("*.json"):
        assert '"raw_text"' not in artifact_path.read_text(encoding="utf-8")


def test_formal_long_pdf_processes_every_page_without_truncation(
    tmp_path, monkeypatch
):
    page_count = 33
    max_concurrency = 1
    path = tmp_path / f"long-{page_count}-pages.pdf"
    _pdf(path, page_count=page_count)
    state = _state()
    servers = []
    active = 0
    maximum_active = 0
    active_lock = threading.Lock()

    def concurrent_concept(client, **arguments):
        nonlocal active, maximum_active
        with active_lock:
            active += 1
            maximum_active = max(maximum_active, active)
        try:
            threading.Event().wait(0.005)
            return FakeConceptAPI(state)(client, **arguments)
        finally:
            with active_lock:
                active -= 1

    monkeypatch.setattr(
        run_module, "start_ocr_process", lambda settings: FakeChild("ocr", state)
    )
    monkeypatch.setattr(
        run_module,
        "start_concept_server",
        lambda _: servers.append(FakeConceptServer(state)) or servers[-1],
    )
    monkeypatch.setattr(run_module, "request_concept_text", concurrent_concept)
    run_id = f"text-first-run:00000000-0000-4000-8000-{page_count:012d}"
    settings = _settings(tmp_path)
    settings["concept_max_concurrency"] = max_concurrency
    bundle = run_module.run_full_text_first_pdf(
        _whole_request(path),
        settings,
        run_id=run_id,
    )
    assert bundle["processing"] == "succeeded"
    assert bundle["page_count"] == page_count
    assert bundle["included_page_count"] == page_count
    assert bundle["excluded_page_count"] == 0
    assert bundle["ocr_calls"] == 0
    assert bundle["concept_calls"] == page_count
    assert bundle["ocr_loads"] == 0
    assert bundle["concept_loads"] == 1
    assert maximum_active == max_concurrency
    assert len(servers) == 1 and servers[0].is_closed
    published = read_producer_bundle(tmp_path / "runtime", run_id)
    pages = published["output"]["pages"]
    assert [page["page_number"] for page in pages] == list(range(1, page_count + 1))
    assert [concept["page_ref"] for concept in published["output"]["concepts"]] == [
        page["page_ref"] for page in pages
    ]
    assert pages[-1]["evidence_blocks"][0]["locator"]["page"] == page_count
    assert state["resident"] == []
    assert list((tmp_path / "runtime").rglob("*.png")) == []


def test_formal_concept_failure_closes_owned_server(tmp_path, monkeypatch):
    path = tmp_path / "public.pdf"
    _pdf(path)
    state = _state()
    servers = []

    monkeypatch.setattr(
        run_module, "start_ocr_process", lambda _: FakeChild("ocr", state)
    )
    monkeypatch.setattr(
        run_module,
        "start_concept_server",
        lambda _: servers.append(FakeConceptServer(state)) or servers[-1],
    )
    monkeypatch.setattr(
        run_module,
        "request_concept_text",
        lambda *_, **__: (_ for _ in ()).throw(
            run_module.ConceptAPIError("CONCEPT_API_UNAVAILABLE")
        ),
    )

    bundle = run_module.run_full_text_first_pdf(
        _whole_request(path),
        _settings(tmp_path),
    )

    assert bundle["processing"] == "failed"
    assert bundle["reason_codes"] == ["PROCESS_FAILED"]
    assert bundle["concept_calls"] == 2
    assert len(servers) == 1 and servers[0].is_closed
    assert state["resident"] == []


def test_formal_lock_has_bounded_busy_failure(tmp_path, monkeypatch):
    runtime_root = tmp_path / "runtime"
    with run_module.material_analysis_lock(runtime_root):
        moments = iter((0.0, 6.0))
        monkeypatch.setattr(run_module.time, "monotonic", lambda: next(moments))
        monkeypatch.setattr(run_module.time, "sleep", lambda _: None)
        with pytest.raises(ValueError, match="RUNTIME_BUSY"):
            with run_module.material_analysis_lock(runtime_root):
                pass


def test_formal_run_reuses_worker_lock_ownership(tmp_path, monkeypatch):
    observed = []
    monkeypatch.setattr(
        run_module,
        "_process_pdf",
        lambda request, settings, **arguments: observed.append(arguments) or {"ok": True},
    )

    with run_module.material_analysis_lock(tmp_path / "runtime"):
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
    assert len(observed) == 1


def test_formal_busy_lock_publishes_truthful_failed_bundle(tmp_path, monkeypatch):
    class BusyLock:
        def __enter__(self):
            raise ValueError("RUNTIME_BUSY")

        def __exit__(self, *_arguments):
            return None

    monkeypatch.setattr(
        run_module, "material_analysis_lock", lambda _: BusyLock()
    )
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
