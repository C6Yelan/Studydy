import hashlib
import json
from pathlib import Path

import pymupdf
import httpx

import pdf_evidence.material_pipeline as pipeline


class Client:
    def post(self, url, **kwargs):
        assert url.endswith("/tokenize")
        return httpx.Response(200, json={"count": 100, "max_model_len": 32768}, request=httpx.Request("POST", url))


def _settings(tmp_path: Path) -> dict:
    lock = json.loads((Path(__file__).parents[2] / "local_ai/runtime-lock.json").read_text())
    return {
        "private_runtime_root": str(tmp_path / "runtime"),
        "runtime_lock": lock,
        "python_executable": str(tmp_path / "ocr/runtime/bin/python3.12"),
        "site_packages": str(tmp_path / "ocr/runtime/lib/python3.12/site-packages"),
        "ocr_model_root": str(tmp_path / "models/unlimited-ocr"),
    }


def _pdf(path: Path, pages: int, *, blank_first: bool = False) -> None:
    document = pymupdf.open()
    for page_number in range(1, pages + 1):
        page = document.new_page(width=612, height=792)
        if not (blank_first and page_number == 1):
            if page_number == 1:
                page.insert_text((72, 72), "Public Algorithms", fontsize=20)
            page.insert_text((72, 120), f"Public lesson {page_number} explains a deterministic learning concept with evidence.", fontsize=12)
    document.save(path)
    document.close()


def _request(path: Path) -> dict:
    return {
        "media_type": "application/pdf",
        "source_path": str(path),
        "expected_source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def _analyze(source: Path, settings: dict, **kwargs):
    request = _request(source)
    digest = request["expected_source_sha256"]
    with pymupdf.open(source) as document:
        page_count = document.page_count
    binding = {
        "source_set_digest": digest,
        "manifest": {"items": [{
            "normalized_sha256": digest,
            "page_count": page_count,
        }]},
        "bundle": {"pages": [
            {"page": page, "source_id": "synthetic-source", "normalized_page": page}
            for page in range(1, page_count + 1)
        ]},
    }
    return pipeline.analyze_material([request], binding, settings, **kwargs)


def _semantic(calls: list[dict]):
    def call(_client, **arguments):
        request = arguments["request"]
        calls.append(request)
        allowed = arguments["response_schema"]["properties"]["concepts"]["items"]["properties"]["c"]["items"]["properties"]["s"]["items"]["enum"]
        assert allowed == [row[0] for section in request["sections"] for row in section["evidence"]]
        first = next(item for section in request["sections"] for item in section["evidence"] if item[2] != "heading")
        return {
            "concepts": [{
                "k": "algorithm", "l": "Algorithm", "a": [],
                "c": [{"m": None, "s": [first[0]]}],
            }],
            "relations": [],
        }
    return call


def test_eight_native_pages_use_one_unified_semantic_call_without_ocr(tmp_path, monkeypatch):
    source = tmp_path / "eight.pdf"
    _pdf(source, 8)
    monkeypatch.setattr(pipeline, "start_ocr_process", lambda _settings: (_ for _ in ()).throw(AssertionError("native PDF must not load OCR")))
    calls: list[dict] = []
    structure = _analyze(
        source, _settings(tmp_path), client=Client(), semantic_call=_semantic(calls)
    )
    assert structure["metrics"]["semantic_calls"] == 1
    assert structure["metrics"]["ocr_calls"] == 0
    assert len(calls) == 1
    assert len({item[1] for section in calls[0]["sections"] for item in section["evidence"]}) == 8
    assert structure["initial_learning_path"][0]["concept_id"] == structure["concepts"][0]["concept_id"]


class FailedOcr:
    def request(self, _request, _timeout):
        raise pipeline.LocalAIError("CHILD_EXITED")

    def close(self):
        pass

    def abort(self):
        pass


def test_multiple_bundles_report_incremental_semantic_progress(tmp_path):
    """後面頁面尚未處理時，不能先把語意進度回報為整份完成。"""
    source = tmp_path / "three.pdf"
    _pdf(source, 3)
    class BudgetClient:
        def post(self, url, **kwargs):
            request = json.loads(kwargs["json"]["messages"][-1]["content"].split("\nINPUT:\n", 1)[1])
            count = 700 * sum(len(section["evidence"]) for section in request["sections"])
            return httpx.Response(200, json={"count": count, "max_model_len": 32768}, request=httpx.Request("POST", url))
    calls = []
    progress = []
    _analyze(source, _settings(tmp_path), client=BudgetClient(),
                              semantic_call=_semantic(calls), progress_callback=lambda stage, done, total: progress.append((stage, done, total)))
    assert len(calls) > 1
    assert {row[1] for call in calls for section in call["sections"] for row in section["evidence"]} == {1, 2, 3}
    completed = [done for stage, done, _total in progress if stage == "semantics"]
    assert completed == sorted(completed)
    assert completed[0] < 3 and completed[-1] == 3


def test_http_batching_carries_concepts_across_all_ninety_pages(tmp_path):
    from runtime.semantic_service import material_request_fits
    class BudgetClient:
        def post(self, url, **kwargs):
            assert url.endswith('/tokenize')
            request = json.loads(kwargs['json']['messages'][-1]['content'].split('\nINPUT:\n', 1)[1])
            count = 700 * sum(len(section['evidence']) for section in request['sections'])
            return httpx.Response(200, json={'count': count, 'max_model_len': 32768}, request=httpx.Request('POST', url))
    source=tmp_path/'ninety.pdf';_pdf(source,90)
    settings=_settings(tmp_path)
    calls=[];progress=[]
    structure=_analyze(source,settings,client=BudgetClient(),semantic_call=_semantic(calls),
        progress_callback=lambda stage,done,total:progress.append((stage,done,total)))
    assert len(calls)>1 and structure['metrics']['semantic_calls']==len(calls)
    rows=[row for call in calls for section in call['sections'] for row in section['evidence']]
    assert {row[1] for row in rows}==set(range(1,91))
    assert len({row[0] for row in rows})==len(rows),'Evidence repeated across batches'
    assert len(rows)==len(structure['evidence']),'Evidence missing from the submitted input'
    assert all(call['existing_concepts'] for call in calls[1:])
    assert len(calls[-1]['existing_concepts'][0]['c'])>len(calls[1]['existing_concepts'][0]['c'])
    assert all(material_request_fits(BudgetClient(),settings['runtime_lock'],call) for call in calls)
    completed=[done for stage,done,_ in progress if stage=='semantics']
    assert completed==sorted(completed) and completed[0]<90 and completed[-1]==90


def test_ocr_failure_excludes_only_scan_and_semantics_still_runs(tmp_path, monkeypatch):
    source = tmp_path / "mixed.pdf"
    _pdf(source, 2, blank_first=True)
    monkeypatch.setattr(pipeline, "start_ocr_process", lambda _settings: FailedOcr())
    calls: list[dict] = []
    structure = _analyze(
        source, _settings(tmp_path), client=Client(), semantic_call=_semantic(calls)
    )
    assert structure["metrics"]["ocr_calls"] == 1
    assert structure["metrics"]["semantic_calls"] == 1
    assert structure["status"]["processing"] == "partial"
    assert structure["excluded_pages"] == [{
        "page_ref": structure["excluded_pages"][0]["page_ref"],
        "page": 1,
        "stage": "evidence",
        "reason_code": "CHILD_EXITED",
    }]
    assert {item[1] for section in calls[0]["sections"] for item in section["evidence"]} == {2}


def test_cancellation_before_semantics_never_opens_a_model_request(tmp_path, monkeypatch):
    import pytest
    source = tmp_path / "cancel.pdf"; _pdf(source, 2)
    class Cancelled(RuntimeError): pass
    requested = False
    def report(stage, done, total):
        nonlocal requested
        if stage == "evidence" and done == total: requested = True
    def check():
        if requested: raise Cancelled()
    monkeypatch.setattr(pipeline, "semantic_client", lambda: (_ for _ in ()).throw(AssertionError("no model client after cancellation")))
    with pytest.raises(Cancelled):
        _analyze(source, _settings(tmp_path), progress_callback=report, cancellation_check=check)


def test_cancellation_stops_semantic_retries_and_next_bundles(tmp_path, monkeypatch):
    import pytest
    source = tmp_path / "cancel.pdf"; _pdf(source, 3)
    class Cancelled(RuntimeError): pass
    for fail_first in (True, False):
        requested = False; calls = []
        def check():
            if requested: raise Cancelled()
        def semantic(client, **arguments):
            nonlocal requested
            requested = True
            if fail_first:
                calls.append(arguments)
                raise ValueError("synthetic invalid model response")
            return _semantic(calls)(client, **arguments)
        original_bundles = pipeline.build_semantic_bundles
        def two_bundles(*args, **kwargs):
            first = next(iter(original_bundles(*args, **kwargs)))
            yield first
            yield first
        with monkeypatch.context() as patch:
            patch.setattr(pipeline, "build_semantic_bundles", two_bundles)
            with pytest.raises(Cancelled):
                _analyze(source, _settings(tmp_path), client=Client(), semantic_call=semantic, cancellation_check=check)
        assert len(calls) == 1


def test_cancellation_at_evidence_checkpoint_does_not_start_the_next_page(tmp_path, monkeypatch):
    import pytest
    source = tmp_path / "cancel.pdf"; _pdf(source, 3)
    class Cancelled(RuntimeError): pass
    pages = []
    original = pipeline.extract_page
    def extract(*args):
        pages.append(args[-1]); return original(*args)
    monkeypatch.setattr(pipeline, "extract_page", extract)
    def report(*_args): raise Cancelled()
    with pytest.raises(Cancelled):
        _analyze(source, _settings(tmp_path), client=Client(), progress_callback=report)
    assert pages == [1]


def test_bundle_input_limit_keeps_reason_without_model_call(tmp_path, monkeypatch):
    import pytest
    source = tmp_path / "limit.pdf"
    _pdf(source, 1)
    monkeypatch.setattr(pipeline, "material_request_fits", lambda *args: False)
    calls = []
    with pytest.raises(pipeline.MaterialAnalysisError, match="SEMANTIC_INPUT_TOO_LARGE"):
        _analyze(source, _settings(tmp_path), client=Client(), semantic_call=_semantic(calls))
    assert calls == []


def test_command_budget_failure_keeps_reason(tmp_path):
    import pytest
    source = tmp_path / "budget.pdf"
    _pdf(source, 1)
    def exhausted(*args, **kwargs):
        raise pipeline.SemanticServiceError("SEMANTIC_BUDGET_EXHAUSTED")
    with pytest.raises(pipeline.MaterialAnalysisError, match="SEMANTIC_BUDGET_EXHAUSTED"):
        _analyze(source, _settings(tmp_path), client=Client(), semantic_call=exhausted)
