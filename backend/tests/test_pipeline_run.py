from __future__ import annotations

import base64
from copy import deepcopy
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import stat
import threading

import pymupdf
import pytest

import pdf_evidence
from knowledge_map.artifacts import build_artifacts
from pdf_evidence.pipeline import run as pipeline_run
from pdf_evidence.pipeline.run import (
    development_pipeline_binding,
    run_development_pdf,
)
from pdf_evidence.page_evidence import _build_page_evidence
from pdf_evidence.study_material_output import validate_study_material_output


def _canonical_sha256(value):
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _simple_page_body(page_number):
    return {
        "elements": [
            {
                "id": f"heading-{page_number}",
                "type": "heading",
                "bbox": [80, 100, 850, 220],
                "text": f"Topic {page_number}",
            },
            {
                "id": f"paragraph-{page_number}",
                "type": "paragraph",
                "bbox": [80, 280, 850, 500],
                "text": f"Grounded explanation {page_number}.",
            },
        ],
        "reading_order": [f"heading-{page_number}", f"paragraph-{page_number}"],
        "spatial_relations": [],
    }


def _complex_page_bodies():
    return {
        1: {
            "elements": [
                {
                    "id": "heading-1",
                    "type": "heading",
                    "bbox": [70, 80, 800, 180],
                    "text": "Formula topic",
                },
                {
                    "id": "paragraph-1",
                    "type": "paragraph",
                    "bbox": [70, 220, 800, 360],
                    "text": "A grounded formula explanation.",
                },
                {
                    "id": "formula-1",
                    "type": "formula",
                    "bbox": [200, 430, 700, 560],
                    "latex": "x^2 + y^2",
                },
            ],
            "reading_order": ["heading-1", "paragraph-1", "formula-1"],
            "spatial_relations": [],
        },
        2: {
            "elements": [
                {
                    "id": "node-2",
                    "type": "diagram_node",
                    "bbox": [100, 150, 400, 500],
                },
                {
                    "id": "label-2",
                    "type": "diagram_label",
                    "bbox": [150, 250, 350, 330],
                    "text": "Diagram only",
                    "node_id": "node-2",
                },
                {
                    "id": "uncertain-2",
                    "type": "other_visible_region",
                    "bbox": [500, 200, 850, 600],
                    "uncertainty_kind": "unreadable",
                },
            ],
            "reading_order": ["label-2", "uncertain-2"],
            "spatial_relations": [],
        },
    }


class _Loopback:
    """提供 frozen structured-generation endpoint 的真實本機 HTTP fixture。"""

    def __init__(
        self,
        page_bodies,
        *,
        visual_decision="retain",
        invalid_concept_evidence=False,
        invalid_content_evidence=False,
    ):
        self.page_bodies = page_bodies
        self.visual_decision = visual_decision
        self.invalid_concept_evidence = invalid_concept_evidence
        self.invalid_content_evidence = invalid_content_evidence
        self.requests = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                if self.path != "/v1/structured-generation":
                    self.send_error(404)
                    return
                length = int(self.headers["Content-Length"])
                request_body = json.loads(self.rfile.read(length))
                owner.requests.append(request_body)
                operation = request_body["operation"]
                if operation == "page_structure":
                    page_number = request_body["payload"]["target_page_evidence"][
                        "page_number"
                    ]
                    output = deepcopy(owner.page_bodies[page_number])
                elif operation == "visual_alignment_adjudication":
                    page_number = request_body["payload"]["page_evidence"][
                        "page_number"
                    ]
                    decision = (
                        owner.visual_decision.get(page_number, "retain")
                        if isinstance(owner.visual_decision, dict)
                        else owner.visual_decision
                    )
                    output = {"decision": decision}
                elif operation == "concept_candidate":
                    context = request_body["payload"]["concept_context"]
                    evidence_ids = [
                        reference["evidence_id"] for reference in context["evidence"]
                    ]
                    if owner.invalid_concept_evidence:
                        evidence_ids = ["evidence-reference:sha256:" + "f" * 64]
                    output = {
                        "name": context["elements"][0]["text"],
                        "definition": context["elements"][1]["text"],
                        "scope": "This source page.",
                        "evidence_ids": evidence_ids,
                    }
                else:
                    context = request_body["payload"]["summary_context"]
                    evidence_id = context["groups"][0]["members"][0][
                        "evidence_ids"
                    ][0]
                    if owner.invalid_content_evidence:
                        evidence_id = "evidence-reference:sha256:" + "e" * 64
                    output = {
                        "summary": "Grounded summary for the supplied concepts.",
                        "summary_evidence_ids": [evidence_id],
                        "relation_clues": [],
                    }
                response = {
                    "schema": "structured-generation-response/v1",
                    "request_id": request_body["request_id"],
                    "operation": operation,
                    "runtime_binding_sha256": _canonical_sha256(
                        request_body["runtime_binding"]
                    ),
                    "output": output,
                }
                encoded = json.dumps(response).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

            def log_message(self, format, *args):
                return

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def endpoint(self):
        return f"http://127.0.0.1:{self.server.server_port}"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, exception_type, exception, traceback):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


def _config(cache_dir, endpoint):
    return {
        "endpoint_url": endpoint,
        "cache_dir": str(cache_dir),
        "deadline_seconds": 10,
        "max_attempts": 2,
        "retry_backoff_seconds": 0,
        "model_id": "local-development-model",
        "model_revision": "revision-1",
        "model_artifact_sha256": "c" * 64,
        "projector_sha256": "d" * 64,
        "runtime_id": "local-runtime-1",
        "processing_policy_version": "development-generation-policy/v1",
    }


def _make_pdf(path, *, landscape=False, page_count=2):
    document = pymupdf.open()
    for page_number in range(1, page_count + 1):
        width, height = ((640, 360) if landscape else (420, 600))
        page = document.new_page(width=width, height=height)
        page.insert_text((40, 60), f"Native topic {page_number}", fontsize=16)
        page.insert_text(
            (40, 110), f"Native grounded explanation {page_number}.", fontsize=11
        )
        page.draw_rect(pymupdf.Rect(35, 140, width - 35, height - 40))
    document.save(path)
    document.close()
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run(pdf_path, source_sha256, output_root, config, run_id="run-1"):
    return run_development_pdf(
        pdf_path,
        source_sha256,
        output_root,
        config,
        run_id=run_id,
        produced_at="2026-08-11T12:00:00+08:00",
        page_limit=10,
    )


def test_public_pipeline_binding_is_hash_only_and_matches_run(tmp_path):
    config = _config(tmp_path / "private-cache", "http://127.0.0.1:8080")
    binding = development_pipeline_binding(config)
    assert binding["schema"] == "s1-development-pipeline-binding/v1"
    assert len(binding["runtime_binding_sha256"]) == 64
    assert str(tmp_path) not in repr(binding)
    assert config["endpoint_url"] not in repr(binding)
    relocated = {
        **config,
        "endpoint_url": "http://127.0.0.1:9090",
        "cache_dir": str(tmp_path / "another-private-cache"),
    }
    assert development_pipeline_binding(relocated) == binding
    changed = {**config, "model_revision": "revision-2"}
    assert development_pipeline_binding(changed) != binding
    assert development_pipeline_binding({}) is None


def test_run_rejects_non_loopback_endpoint_before_generation(
    tmp_path, monkeypatch
):
    """正式 PDF run 只接受沒有 DNS、credentials 或額外路徑的 loopback。"""
    pdf_path = tmp_path / "source.pdf"
    source_sha256 = _make_pdf(pdf_path)
    monkeypatch.setattr(
        pipeline_run,
        "generate_development_page_structure",
        lambda *args, **kwargs: pytest.fail("invalid endpoint reached generation"),
    )
    config = _config(tmp_path / "cache", "http://192.0.2.1:8080")
    run = _run(pdf_path, source_sha256, tmp_path / "output", config)
    assert run["reason_code"] == "LOCAL_ENDPOINT_NOT_LOOPBACK"
    assert run["provider_call_counts"]["total"] == 0


def test_run_rejects_invalid_config_before_generation(
    tmp_path, monkeypatch
):
    """正式 PDF run 在任何 generation 前完成 immutable config preflight。"""
    pdf_path = tmp_path / "source.pdf"
    source_sha256 = _make_pdf(pdf_path)
    monkeypatch.setattr(
        pipeline_run,
        "generate_development_page_structure",
        lambda *args, **kwargs: pytest.fail("invalid config reached generation"),
    )
    config = _config(tmp_path / "cache", "http://127.0.0.1:8080")
    config["max_attempts"] = 0
    run = _run(pdf_path, source_sha256, tmp_path / "output", config)
    assert run["reason_code"] == "LOCAL_CONFIG_INVALID"
    assert run["provider_call_counts"]["total"] == 0


def test_fresh_pdf_full_pipeline_and_zero_call_replay(tmp_path):
    """全新 PDF 走真 loopback HTTP，replay 使用同一組 validated cache。"""
    pdf_path = tmp_path / "fresh-simple.pdf"
    source_sha256 = _make_pdf(pdf_path)
    page_bodies = {1: _simple_page_body(1), 2: _simple_page_body(2)}
    with _Loopback(page_bodies) as loopback:
        config = _config(tmp_path / "cache", loopback.endpoint)
        first = _run(pdf_path, source_sha256, tmp_path / "output", config)
        assert first["processing"] == "succeeded"
        assert first["quality"] == "needs_review"
        assert first["decision"] == "review"
        assert first["reason_code"] == "DEVELOPMENT_OUTPUT_NEEDS_REVIEW"
        assert first["provider_call_counts"] == {
            "page_structure": 2,
            "visual_alignment_adjudication": 2,
            "concept_candidate": 2,
            "concept_content": 1,
            "total": 7,
        }
        output = first["study_material_output"]
        assert validate_study_material_output(output) is None
        assert output["development_only"] is True
        assert output["material_ref"] == f"material:sha256:{source_sha256}"
        assert output["known_limitations"] == [
            {
                "reason_code": "FORMAL_PROVIDER_DEFERRED",
                "affected_page_refs": sorted(
                    page["page_ref"] for page in output["pages"]
                ),
            }
        ]
        assert len(loopback.requests) == 7
        page_requests = [
            request
            for request in loopback.requests
            if request["operation"] == "page_structure"
        ]
        assert len(page_requests) == 2
        for request in page_requests:
            render_bytes = base64.b64decode(
                request["payload"]["target_render_base64"], validate=True
            )
            assert hashlib.sha256(render_bytes).hexdigest() == request[
                "input_binding"
            ]["artifact_sha256s"]["target_render"]

        first_request_count = len(loopback.requests)
        replay = _run(pdf_path, source_sha256, tmp_path / "output", config)
        assert len(loopback.requests) == first_request_count
        assert replay["provider_call_counts"]["total"] == 0
        assert replay["cache_hits"] == {
            "page_structure": 2,
            "visual_alignment_adjudication": 2,
            "concept_candidate": 2,
            "concept_content": 1,
            "total": 7,
        }
        assert replay["study_material_output"]["output_id"] == output["output_id"]

        public_text = json.dumps(replay, ensure_ascii=False, sort_keys=True)
        assert str(pdf_path) not in public_text
        assert str(tmp_path / "output") not in public_text
        assert str(config["cache_dir"]) not in public_text
        assert loopback.endpoint not in public_text
        assert "target_render_base64" not in public_text


def test_different_layout_preserves_page_as_legal_partial(tmp_path):
    """沒有 heading context 的 complex 頁仍保留 locator，且不得出現半頁 Concept。"""
    pdf_path = tmp_path / "fresh-landscape.pdf"
    source_sha256 = _make_pdf(pdf_path, landscape=True)
    with _Loopback(_complex_page_bodies()) as loopback:
        result = _run(
            pdf_path,
            source_sha256,
            tmp_path / "output",
            _config(tmp_path / "cache", loopback.endpoint),
            run_id="run-complex",
        )
    assert result["processing"] == "partial"
    assert result["reason_code"] == "DEVELOPMENT_FULL_DOCUMENT_PARTIAL"
    assert result["provider_call_counts"] == {
        "page_structure": 2,
        "visual_alignment_adjudication": 2,
        "concept_candidate": 1,
        "concept_content": 1,
        "total": 6,
    }
    output = result["study_material_output"]
    assert validate_study_material_output(output) is None
    assert len(output["pages"]) == 2
    unavailable = next(
        item
        for item in output["known_limitations"]
        if item["reason_code"] == "CONCEPT_CONTEXT_UNAVAILABLE"
    )
    second_page_ref = output["pages"][1]["page_ref"]
    assert unavailable["affected_page_refs"] == [second_page_ref]
    assert all(
        member["page_ref"] != second_page_ref
        for concept in output["concepts"]
        for member in concept["members"]
    )
    assert result["page_statuses"][1]["reason_code"] == (
        "CONCEPT_CONTEXT_UNAVAILABLE"
    )


@pytest.mark.parametrize(
    ("page_two_body", "visual_decision", "stage", "reason_code", "total_calls"),
    [
        ({}, "retain", "page_structure", "PAGE_STRUCTURE_INVALID", 5),
        (
            _simple_page_body(2),
            {1: "retain", 2: "reject"},
            "visual_alignment",
            "VISUAL_ALIGNMENT_REVIEW_REJECTED",
            6,
        ),
    ],
)
def test_one_rejected_page_is_excluded_from_truthful_partial_output(
    tmp_path,
    page_two_body,
    visual_decision,
    stage,
    reason_code,
    total_calls,
):
    """單頁品質失敗保留 Evidence/reason，其餘頁可繼續產生下游 artifact。"""
    pdf_path = tmp_path / "two-pages.pdf"
    source_sha256 = _make_pdf(pdf_path)
    with _Loopback(
        {1: _simple_page_body(1), 2: page_two_body},
        visual_decision=visual_decision,
    ) as loopback:
        result = _run(
            pdf_path,
            source_sha256,
            tmp_path / "output",
            _config(tmp_path / "cache", loopback.endpoint),
        )

    assert (
        result["processing"],
        result["quality"],
        result["decision"],
        result["reason_code"],
    ) == (
        "partial",
        "needs_review",
        "review",
        "DEVELOPMENT_FULL_DOCUMENT_PARTIAL",
    )
    assert result["provider_call_counts"]["total"] == total_calls
    output = result["study_material_output"]
    assert validate_study_material_output(output) is None
    assert [page["page_number"] for page in output["pages"]] == [1]
    exclusion = next(
        item
        for item in output["known_limitations"]
        if item["reason_code"] == "PAGE_CONTENT_EXCLUDED"
    )
    assert len(exclusion["affected_pages"]) == 1
    excluded_page = exclusion["affected_pages"][0]
    assert {
        key: value
        for key, value in excluded_page.items()
        if key != "page_evidence_ref"
    } == {
        "page_ref": result["page_statuses"][1]["page_ref"],
        "page_number": 2,
        "last_stage": stage,
        "processing": "failed",
        "quality": "unsupported",
        "decision": "reject",
        "reason_code": reason_code,
    }
    assert excluded_page["page_evidence_ref"].startswith("evidence:sha256:")
    assert all(
        member["page_number"] == 1
        for concept in output["concepts"]
        for member in concept["members"]
    )
    knowledge_map, _, view = build_artifacts(output)
    excluded_view = next(
        item
        for item in view["limitations"]
        if item["reason_code"] == "PAGE_CONTENT_EXCLUDED"
    )
    assert excluded_view == {
        "reason_code": "PAGE_CONTENT_EXCLUDED",
        "page_numbers": [2],
        "affected_page_count": 1,
    }

def test_more_than_eight_concepts_share_content_and_keyword_batches(tmp_path):
    """超過八組時 content 與 keywords 使用相同分批並維持完整 coverage。"""
    pdf_path = tmp_path / "nine-topics.pdf"
    source_sha256 = _make_pdf(pdf_path, page_count=9)
    page_bodies = {
        page_number: _simple_page_body(page_number)
        for page_number in range(1, 10)
    }
    with _Loopback(page_bodies) as loopback:
        result = _run(
            pdf_path,
            source_sha256,
            tmp_path / "output",
            _config(tmp_path / "cache", loopback.endpoint),
            run_id="run-nine-groups",
        )

    assert result["processing"] == "succeeded"
    assert result["provider_call_counts"] == {
        "page_structure": 9,
        "visual_alignment_adjudication": 9,
        "concept_candidate": 9,
        "concept_content": 2,
        "total": 29,
    }
    output = result["study_material_output"]
    assert validate_study_material_output(output) is None
    assert len(output["concepts"]) == 9
    assert len(output["keywords"]) == 9
    assert len(output["summaries"]) == 2


def test_generation_timeout_is_never_downgraded_to_partial(tmp_path, monkeypatch):
    """transport 耗盡時整個 run 失敗，並保留精確呼叫數。"""
    pdf_path = tmp_path / "source.pdf"
    source_sha256 = _make_pdf(pdf_path)

    def timeout(*args, **kwargs):
        return {
            "processing": "failed",
            "reason_code": "LOCAL_PROVIDER_TIMEOUT",
            "provider_call_count": 2,
            "cache_hit": False,
            "artifact": None,
        }

    monkeypatch.setattr(
        pipeline_run,
        "generate_development_page_structure",
        timeout,
    )
    result = _run(
        pdf_path,
        source_sha256,
        tmp_path / "output",
        _config(tmp_path / "cache", "http://127.0.0.1:8080"),
    )

    assert result["processing"] == "failed"
    assert result["reason_code"] == "LOCAL_PROVIDER_TIMEOUT"
    assert result["provider_call_counts"]["total"] == 2
    assert result["study_material_output"] is None


def test_preflight_failures_make_zero_calls(tmp_path, monkeypatch):
    """hash、media、加密與頁數問題都在 generation 前終止。"""
    pdf_path = tmp_path / "source.pdf"
    source_sha256 = _make_pdf(pdf_path)
    encrypted_path = tmp_path / "encrypted.pdf"
    document = pymupdf.open()
    document.new_page()
    document.save(
        encrypted_path,
        encryption=pymupdf.PDF_ENCRYPT_AES_256,
        owner_pw="owner",
        user_pw="reader",
    )
    document.close()
    encrypted_sha256 = hashlib.sha256(encrypted_path.read_bytes()).hexdigest()
    not_pdf = tmp_path / "not.pdf"
    not_pdf.write_bytes(b"not a pdf")
    with _Loopback({1: _simple_page_body(1), 2: _simple_page_body(2)}) as loopback:
        config = _config(tmp_path / "cache", loopback.endpoint)
        wrong_hash = _run(
            pdf_path, "0" * 64, tmp_path / "wrong-hash", config
        )
        invalid_media = _run(
            not_pdf,
            hashlib.sha256(not_pdf.read_bytes()).hexdigest(),
            tmp_path / "invalid-media",
            config,
        )
        encrypted = _run(
            encrypted_path,
            encrypted_sha256,
            tmp_path / "encrypted-output",
            config,
        )
        limited = run_development_pdf(
            pdf_path,
            source_sha256,
            tmp_path / "limited",
            config,
            run_id="limited",
            produced_at="2026-08-11T12:00:00+08:00",
            page_limit=1,
        )
        with monkeypatch.context() as context:

            def corrupt_snapshot(source, snapshot_path):
                snapshot_path.write_bytes(b"%PDF-1.7\ncorrupt snapshot")
                return None

            context.setattr(
                pipeline_run,
                "_copy_source_snapshot",
                corrupt_snapshot,
            )
            corrupted_snapshot = _run(
                pdf_path,
                source_sha256,
                tmp_path / "corrupted-snapshot",
                config,
            )
        assert loopback.requests == []
    assert wrong_hash["reason_code"] == "SOURCE_HASH_MISMATCH"
    assert invalid_media["reason_code"] == "MATERIAL_NOT_PDF"
    assert encrypted["reason_code"] == "PDF_ENCRYPTED"
    assert limited["reason_code"] == "PAGE_LIMIT_EXCEEDED"
    assert corrupted_snapshot["reason_code"] == "SOURCE_HASH_MISMATCH"
    assert all(
        result["study_material_output"] is None
        for result in (
            wrong_hash,
            invalid_media,
            encrypted,
            limited,
            corrupted_snapshot,
        )
    )


def test_invalid_content_evidence_fails_entire_run(tmp_path):
    """content Evidence 斷鏈不得被降級為 partial。"""
    pdf_path = tmp_path / "source.pdf"
    source_sha256 = _make_pdf(pdf_path)
    page_bodies = {1: _simple_page_body(1), 2: _simple_page_body(2)}
    with _Loopback(page_bodies, invalid_content_evidence=True) as loopback:
        result = _run(
            pdf_path,
            source_sha256,
            tmp_path / "output",
            _config(tmp_path / "cache", loopback.endpoint),
        )
    assert result["processing"] == "failed"
    assert result["decision"] == "reject"
    assert result["reason_code"] == "CONCEPT_CONTENT_EVIDENCE_INVALID"
    assert result["study_material_output"] is None


def test_study_material_builder_failure_keeps_terminal_envelope(
    tmp_path, monkeypatch
):
    """同鏈 builder 失敗仍不得把 failure dict 當成正式 SMO。"""
    pdf_path = tmp_path / "source.pdf"
    source_sha256 = _make_pdf(pdf_path)
    monkeypatch.setattr(
        pipeline_run,
        "build_study_material_output",
        lambda *args, **kwargs: {
            "schema": "study-material-output/v2",
            "development_only": True,
            "processing": "failed",
            "quality": "unsupported",
            "decision": "reject",
            "reason_code": "STUDY_MATERIAL_OUTPUT_CONTENT_INPUT_INVALID",
        },
    )
    with _Loopback({1: _simple_page_body(1), 2: _simple_page_body(2)}) as loopback:
        result = _run(
            pdf_path,
            source_sha256,
            tmp_path / "output",
            _config(tmp_path / "cache", loopback.endpoint),
        )

    assert result["processing"] == "failed"
    assert result["decision"] == "reject"
    assert result["reason_code"] == "STUDY_MATERIAL_OUTPUT_ROOT_INVALID"
    assert result["study_material_output"] is None


def test_all_page_evidence_precedes_generation_and_artifact_tamper_fails(
    tmp_path, monkeypatch
):
    """後頁 Evidence/file gap 不得在前頁已送 model 後才被發現。"""
    pdf_path = tmp_path / "source.pdf"
    source_sha256 = _make_pdf(pdf_path)
    original_build = (
        pipeline_run.page_evidence._build_page_evidence
    )

    def fail_second_page(pdf, source_hash, page_number, output_root):
        if page_number == 2:
            return {
                "schema": "page-evidence/v1",
                "status": "failed",
                "reason": "INJECTED_PAGE_EVIDENCE_FAILURE",
                "page_number": 2,
            }
        return original_build(pdf, source_hash, page_number, output_root)

    monkeypatch.setattr(
        pipeline_run.page_evidence,
        "_build_page_evidence",
        fail_second_page,
    )
    with _Loopback({1: _simple_page_body(1), 2: _simple_page_body(2)}) as loopback:
        result = _run(
            pdf_path,
            source_sha256,
            tmp_path / "output",
            _config(tmp_path / "cache", loopback.endpoint),
        )
        assert loopback.requests == []
    assert result["reason_code"] == "INJECTED_PAGE_EVIDENCE_FAILURE"

    monkeypatch.setattr(
        pipeline_run.page_evidence,
        "_build_page_evidence",
        original_build,
    )
    evidence_root = tmp_path / "evidence"
    evidence = _build_page_evidence(pdf_path, source_sha256, 1, evidence_root)
    native, render, reason = pipeline_run.page_evidence._load_page_artifacts(
        evidence_root, evidence
    )
    assert reason is None and native is not None and render is not None
    digest = evidence["evidence_ref"].removeprefix("evidence:sha256:")
    render_path = evidence_root / "output" / digest / "render.png"
    render_path.write_bytes(render + b"tampered")
    assert pipeline_run.page_evidence._load_page_artifacts(
        evidence_root, evidence
    )[2] == "PAGE_EVIDENCE_ARTIFACT_INVALID"


def test_product_exports_only_the_current_pdf_entry():
    """package root 只公開目前正式的 PDF processing entry。"""
    assert pdf_evidence.__all__ == ["run_development_pdf"]
    assert pdf_evidence.run_development_pdf is run_development_pdf


def test_page_evidence_root_symlink_fails_before_write_or_call(tmp_path):
    """controlled Page Evidence root 不得把教材產物導出指定 root。"""
    pdf_path = tmp_path / "source.pdf"
    source_sha256 = _make_pdf(pdf_path)
    output_root = tmp_path / "output"
    output_root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    symlink_path = output_root / "page_evidence"
    symlink_path.parent.mkdir(parents=True, exist_ok=True)
    symlink_path.symlink_to(outside, target_is_directory=True)

    result = _run(
        pdf_path,
        source_sha256,
        output_root,
        _config(tmp_path / "cache", "http://127.0.0.1:8080"),
    )

    assert result["processing"] == "failed"
    assert result["reason_code"] == "OUTPUT_ROOT_INVALID"
    assert result["provider_call_counts"]["total"] == 0
    assert result["study_material_output"] is None
    assert list(outside.iterdir()) == []


def test_invalid_output_root_is_terminal_before_write_or_call(tmp_path):
    """OS 不可用的 output path 必須回固定 failure envelope。"""
    pdf_path = tmp_path / "source.pdf"
    source_sha256 = _make_pdf(pdf_path)
    result = _run(
        pdf_path,
        source_sha256,
        "\x00",
        _config(tmp_path / "cache", "http://127.0.0.1:8080"),
    )
    assert result["processing"] == "failed"
    assert result["reason_code"] == "OUTPUT_ROOT_INVALID"
    assert result["provider_call_counts"]["total"] == 0
    assert result["study_material_output"] is None


def test_source_swap_after_preflight_cannot_change_page_evidence(
    tmp_path, monkeypatch
):
    """所有 Page Evidence 都只讀同一個已驗 hash 的短生命週期 snapshot。"""
    pdf_path = tmp_path / "source-a.pdf"
    source_sha256 = _make_pdf(pdf_path)
    replacement = tmp_path / "source-b.pdf"
    replacement_sha256 = _make_pdf(replacement, landscape=True)
    original_build = (
        pipeline_run.page_evidence._build_page_evidence
    )
    snapshot_hashes = []
    snapshot_paths = []
    snapshot_modes = []

    def swap_then_build(source, expected_hash, page_number, output_root):
        if not snapshot_hashes:
            os.replace(replacement, pdf_path)
        snapshot = Path(source)
        snapshot_paths.append(snapshot)
        snapshot_hashes.append(hashlib.sha256(snapshot.read_bytes()).hexdigest())
        snapshot_modes.append(
            (
                stat.S_IMODE(snapshot.stat().st_mode),
                stat.S_IMODE(snapshot.parent.stat().st_mode),
            )
        )
        return original_build(source, expected_hash, page_number, output_root)

    monkeypatch.setattr(
        pipeline_run.page_evidence,
        "_build_page_evidence",
        swap_then_build,
    )
    page_bodies = {1: _simple_page_body(1), 2: _simple_page_body(2)}
    with _Loopback(page_bodies) as loopback:
        result = _run(
            pdf_path,
            source_sha256,
            tmp_path / "output",
            _config(tmp_path / "cache", loopback.endpoint),
        )

    assert result["processing"] == "succeeded"
    assert result["study_material_output"]["material_ref"] == (
        f"material:sha256:{source_sha256}"
    )
    assert snapshot_hashes == [source_sha256, source_sha256]
    assert snapshot_modes == [(0o600, 0o700), (0o600, 0o700)]
    assert hashlib.sha256(pdf_path.read_bytes()).hexdigest() == replacement_sha256
    assert all(path != pdf_path and not path.exists() for path in snapshot_paths)


def test_snapshot_cleanup_failure_preserves_exact_run_metrics(tmp_path, monkeypatch):
    """cleanup gap 必須終止 run，但不可抹掉已發生的呼叫與頁面狀態。"""
    pdf_path = tmp_path / "source.pdf"
    source_sha256 = _make_pdf(pdf_path)
    temporary_root = tmp_path / "snapshot"
    temporary_root.mkdir()

    class CleanupFailure:
        def __init__(self, *, prefix):
            self.name = str(temporary_root)

        def cleanup(self):
            raise OSError("injected cleanup failure")

    expected_status = {
        "page_number": 1,
        "page_ref": "page:sha256:" + "a" * 64,
        "last_stage": "page_structure",
        "processing": "failed",
        "quality": "unsupported",
        "decision": "reject",
        "reason_code": "LOCAL_PROVIDER_TIMEOUT",
    }

    def completed_inner(*args, **kwargs):
        return {
            "schema": "s1-development-run/v1",
            "development_only": True,
            "run_id": "run-1",
            "input_binding": {
                "material_ref": f"material:sha256:{source_sha256}",
                "source_sha256": source_sha256,
                "page_count": 2,
                "runtime_binding_sha256": "b" * 64,
            },
            "processing": "failed",
            "quality": "unsupported",
            "decision": "reject",
            "reason_code": "LOCAL_PROVIDER_TIMEOUT",
            "provider_call_counts": {
                "page_structure": 2,
                "visual_alignment_adjudication": 0,
                "concept_candidate": 0,
                "concept_content": 0,
                "total": 2,
            },
            "cache_hits": {
                "page_structure": 0,
                "visual_alignment_adjudication": 0,
                "concept_candidate": 0,
                "concept_content": 0,
                "total": 0,
            },
            "page_statuses": [expected_status],
            "study_material_output": None,
        }

    monkeypatch.setattr(
        pipeline_run.tempfile,
        "TemporaryDirectory",
        CleanupFailure,
    )
    monkeypatch.setattr(
        pipeline_run,
        "_run_development_pdf_snapshot",
        completed_inner,
    )
    result = _run(
        pdf_path,
        source_sha256,
        tmp_path / "output",
        _config(tmp_path / "cache", "http://127.0.0.1:8080"),
    )

    assert result["reason_code"] == "MATERIAL_SNAPSHOT_CLEANUP_FAILED"
    assert result["processing"] == "failed"
    assert result["provider_call_counts"]["total"] == 2
    assert result["page_statuses"] == [expected_status]
    assert result["study_material_output"] is None
    assert not (temporary_root / "source.pdf").exists()


def test_non_regular_source_is_rejected_without_blocking_or_call(tmp_path):
    """FIFO 等非 regular source 不得在 open/read 階段卡住 worker。"""
    fifo_path = tmp_path / "source.pdf"
    os.mkfifo(fifo_path)
    result = _run(
        fifo_path,
        "a" * 64,
        tmp_path / "output",
        _config(tmp_path / "cache", "http://127.0.0.1:8080"),
    )
    assert result["processing"] == "failed"
    assert result["reason_code"] == "MATERIAL_MISSING"
    assert result["provider_call_counts"]["total"] == 0
    assert result["study_material_output"] is None
