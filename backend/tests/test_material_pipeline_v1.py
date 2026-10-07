import hashlib
import json
from pathlib import Path

import pymupdf
import httpx
import pytest

import pdf_evidence.material_pipeline as pipeline


class Client:
    def post(self, url, **kwargs):
        assert url.endswith("/tokenize")
        return httpx.Response(200, json={"count": 100, "max_model_len": 65536}, request=httpx.Request("POST", url))


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
            page.insert_text(
                (72, 120),
                f"Public lesson {page_number} explains a deterministic learning concept with evidence.",
                fontsize=12,
            )
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
        concept_schema = arguments["response_schema"]["properties"]["concepts"]["items"]
        claim_schema = concept_schema["properties"]["c"]["items"]
        allowed = claim_schema["properties"]["s"]["items"]["enum"]
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

    def reject_ocr(_settings):
        raise AssertionError("native PDF must not load OCR")

    monkeypatch.setattr(pipeline, "start_ocr_process", reject_ocr)
    calls: list[dict] = []
    structure = _analyze(
        source, _settings(tmp_path), client=Client(), semantic_call=_semantic(calls)
    )
    assert structure["metrics"]["semantic_calls"] == 1
    assert structure["metrics"]["ocr_calls"] == 0
    assert len(calls) == 1
    assert len({item[1] for section in calls[0]["sections"] for item in section["evidence"]}) == 8
    assert structure["initial_learning_path"][0]["concept_id"] == structure["concepts"][0]["concept_id"]


def test_native_analysis_can_finish_while_another_process_owns_ocr_lock(tmp_path):
    source = tmp_path / 'native.pdf'
    _pdf(source, 1)
    settings = _settings(tmp_path)
    with pipeline.material_analysis_lock(Path(settings['private_runtime_root'])):
        structure = _analyze(source, settings, client=Client(), semantic_call=_semantic([]))
    assert structure['metrics']['ocr_calls'] == 0
    assert structure['status']['processing'] == 'succeeded'


def test_ocr_lock_covers_child_lifetime_and_releases_before_semantics(tmp_path, monkeypatch):
    source = tmp_path / 'mixed.pdf'
    _pdf(source, 2, blank_first=True)
    settings = _settings(tmp_path)
    root = Path(settings['private_runtime_root'])
    events = []

    def assert_locked():
        with pytest.raises(pipeline.MaterialAnalysisError, match='RUNTIME_BUSY'):
            with pipeline.material_analysis_lock(root, wait_seconds=0):
                pass

    class Ocr(FailedOcr):
        def request(self, request, timeout, **kwargs):
            assert_locked()
            events.append('request')
            return super().request(request, timeout)

        def close(self):
            assert_locked()
            events.append('close')

    def start(_):
        assert_locked()
        events.append('start')
        return Ocr()

    def semantic(client, **kwargs):
        with pipeline.material_analysis_lock(root, wait_seconds=0):
            events.append('semantic')
        return _semantic([])(client, **kwargs)

    monkeypatch.setattr(pipeline, 'start_ocr_process', start)
    result = _analyze(source, settings, client=Client(), semantic_call=semantic)
    assert result['status']['processing'] == 'partial'
    assert events == ['start', 'request', 'close', 'semantic']


def test_failed_ocr_start_does_not_keep_lock_for_next_page(tmp_path, monkeypatch):
    source = tmp_path / 'scans.pdf'
    with pymupdf.open() as document:
        document.new_page()
        document.new_page()
        document.save(source)
    starts = []

    def fail(_):
        starts.append(1)
        raise pipeline.LocalAIError('CHILD_EXITED')

    monkeypatch.setattr(pipeline, 'start_ocr_process', fail)
    settings = _settings(tmp_path)
    with pytest.raises(pipeline.MaterialAnalysisError, match='NO_USABLE_EVIDENCE'):
        _analyze(source, settings, client=Client(), semantic_call=_semantic([]))
    assert len(starts) == 2
    with pipeline.material_analysis_lock(Path(settings['private_runtime_root']), wait_seconds=0):
        pass


class FailedOcr:
    def request(self, _request, _timeout, **kwargs):
        raise pipeline.LocalAIError("CHILD_EXITED")

    def close(self):
        pass

    def abort(self):
        pass



def test_http_batching_carries_concepts_across_all_ninety_pages(tmp_path):
    from runtime.semantic_service import material_request_fits

    class BudgetClient:
        def post(self, url, **kwargs):
            assert url.endswith("/tokenize")
            request = json.loads(kwargs["json"]["messages"][-1]["content"].split("\nINPUT:\n", 1)[1])
            count = 700 * sum(len(section["evidence"]) for section in request["sections"])
            return httpx.Response(
                200, json={"count": count, "max_model_len": 65536},
                request=httpx.Request("POST", url),
            )

    source = tmp_path / "ninety.pdf"
    _pdf(source, 90)
    settings = _settings(tmp_path)
    calls = []
    progress = []
    structure = _analyze(
        source, settings, client=BudgetClient(), semantic_call=_semantic(calls),
        progress_callback=lambda stage, done, total: progress.append((stage, done, total)),
    )
    assert len(calls) > 1
    assert structure["metrics"]["semantic_calls"] == len(calls)
    rows = [
        row for call in calls for section in call["sections"] for row in section["evidence"]
    ]
    assert {row[1] for row in rows} == set(range(1, 91))
    assert len({row[0] for row in rows}) == len(rows), "Evidence repeated across batches"
    assert len(rows) == len(structure["evidence"]), "Evidence missing from the submitted input"
    assert all(call["existing_concepts"] for call in calls[1:])
    assert len(calls[-1]["existing_concepts"][0]["c"]) > len(calls[1]["existing_concepts"][0]["c"])
    assert all(material_request_fits(BudgetClient(), settings["runtime_lock"], call) for call in calls)
    completed = [done for stage, done, _ in progress if stage == "semantics"]
    assert completed == sorted(completed)
    assert completed[0] < 90
    assert completed[-1] == len(structure["evidence"])
    assert all(total == len(structure["evidence"]) for stage, _, total in progress if stage == "semantics")


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
    source = tmp_path / "cancel.pdf"
    _pdf(source, 2)

    class Cancelled(RuntimeError):
        pass

    requested = False

    def report(stage, done, total):
        nonlocal requested
        if stage == "evidence" and done == total:
            requested = True

    def check():
        if requested:
            raise Cancelled()

    def reject_model_client():
        raise AssertionError("no model client after cancellation")

    monkeypatch.setattr(pipeline, "semantic_client", reject_model_client)
    with pytest.raises(Cancelled):
        _analyze(source, _settings(tmp_path), progress_callback=report, cancellation_check=check)


def test_cancellation_stops_semantic_retries_and_next_bundles(tmp_path, monkeypatch):
    source = tmp_path / "cancel.pdf"
    _pdf(source, 3)

    class Cancelled(RuntimeError):
        pass

    for fail_first in (True, False):
        requested = False
        calls = []

        def check():
            if requested:
                raise Cancelled()

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
    source = tmp_path / "cancel.pdf"
    _pdf(source, 3)

    class Cancelled(RuntimeError):
        pass

    pages = []
    original = pipeline.extract_page

    def extract(*args):
        pages.append(args[-1])
        return original(*args)

    monkeypatch.setattr(pipeline, "extract_page", extract)

    def report(*_args):
        raise Cancelled()

    with pytest.raises(Cancelled):
        _analyze(source, _settings(tmp_path), client=Client(), progress_callback=report)
    assert pages == [1]


def test_bundle_input_limit_keeps_reason_without_model_call(tmp_path, monkeypatch):
    source = tmp_path / "limit.pdf"
    _pdf(source, 1)
    monkeypatch.setattr(pipeline, "material_request_fits", lambda *args, **kwargs: False)
    calls = []
    with pytest.raises(pipeline.MaterialAnalysisError, match="SEMANTIC_INPUT_TOO_LARGE"):
        _analyze(source, _settings(tmp_path), client=Client(), semantic_call=_semantic(calls))
    assert calls == []


def test_page_checkpoints_resume_without_extracting_completed_pages(tmp_path,monkeypatch):
    from copy import deepcopy
    source=tmp_path/'resume.pdf';_pdf(source,4)
    settings=_settings(tmp_path);cache={};visited=[];actual=pipeline.extract_page
    def extract(*args):visited.append(args[-1]);return actual(*args)
    monkeypatch.setattr(pipeline,'extract_page',extract)
    def save(number,page):cache[number]=deepcopy(page)
    def stop(stage,completed,total):
        if completed==2:raise RuntimeError('synthetic interruption')
    with pytest.raises(RuntimeError,match='synthetic interruption'):
        pipeline._page_evidence(source,_request(source)['expected_source_sha256'],[1,2,3,4],settings,'2026-10-07T00:00:00Z',stop,lambda:None,save_page=save)
    assert visited==[1,2] and set(cache)=={1,2}
    before=deepcopy(cache);visited.clear();progress=[]
    pages,excluded,ocr=pipeline._page_evidence(source,_request(source)['expected_source_sha256'],[1,2,3,4],settings,'2026-10-07T00:01:00Z',lambda *args:progress.append(args),lambda:None,load_page=cache.get,save_page=save)
    assert visited==[3,4] and not excluded and ocr==0
    assert pages[:2]==[before[1],before[2]]
    assert [p['page_number'] for p in pages]==[1,2,3,4]
    assert [event[1] for event in progress]==[1,2,3,4]
    with pytest.raises(pipeline.MaterialAnalysisError,match='ANALYSIS_CHECKPOINT_INVALID'):
        pipeline._page_evidence(source,_request(source)['expected_source_sha256'],[1],settings,'2026-10-07T00:02:00Z',lambda *args:None,lambda:None,load_page=lambda number:cache[2])


def test_page_checkpoint_write_failure_stops_before_next_page(tmp_path,monkeypatch):
    source=tmp_path/'write-failed.pdf';_pdf(source,3);visited=[];reported=[];actual=pipeline.extract_page
    def extract(*args):visited.append(args[-1]);return actual(*args)
    monkeypatch.setattr(pipeline,'extract_page',extract)
    def save(*args):raise RuntimeError('synthetic disk failure')
    with pytest.raises(RuntimeError,match='synthetic disk failure'):
        pipeline._page_evidence(source,_request(source)['expected_source_sha256'],[1,2,3],_settings(tmp_path),'2026-10-07T00:00:00Z',lambda *args:reported.append(args),lambda:None,save_page=save)
    assert visited==[1] and reported==[]


def test_saved_ocr_page_is_not_sent_to_ocr_again_after_interruption(tmp_path,monkeypatch):
    from copy import deepcopy
    source=tmp_path/'scanned.pdf'
    with pymupdf.open() as doc:
        doc.new_page();doc.new_page();doc.save(source)
    requests=[];starts=[];cache={}
    class Ocr:
        def request(self,request,*args,**kwargs):
            requests.append(request['request_id'])
            return {'schema':'local-ocr-response/v1','request_id':request['request_id'],'blocks':[{'type':'text','text':'Synthetic scanned page contains a complete source statement.','bbox':[10,10,400,100]}]}
        def close(self):pass
        def abort(self):pass
    monkeypatch.setattr(pipeline,'start_ocr_process',lambda settings:(starts.append(1),Ocr())[1])
    settings=_settings(tmp_path);digest=_request(source)['expected_source_sha256']
    def save(number,page):cache[number]=deepcopy(page)
    def stop(stage,completed,total):
        if completed==1:raise RuntimeError('synthetic shutdown')
    with pytest.raises(RuntimeError,match='synthetic shutdown'):
        pipeline._page_evidence(source,digest,[1,2],settings,'2026-10-07T00:00:00Z',stop,lambda:None,save_page=save)
    assert requests==['page-1'] and set(cache)=={1}
    pages,excluded,calls=pipeline._page_evidence(source,digest,[1,2],settings,'2026-10-07T00:01:00Z',lambda *a:None,lambda:None,load_page=cache.get,save_page=save)
    assert requests==['page-1','page-2'] and len(starts)==2 and calls==2 and not excluded
    assert pages[0]==cache[1]
    monkeypatch.setattr(pipeline,'start_ocr_process',lambda *args:pytest.fail('saved OCR pages must not load the model'))
    monkeypatch.setattr(pipeline,'extract_page',lambda *args:pytest.fail('saved OCR pages must not be rendered again'))
    assert pipeline._page_evidence(source,digest,[1,2],settings,'2026-10-07T00:02:00Z',lambda *a:None,lambda:None,load_page=cache.get)[0]==pages


def test_19_sources_368_pages_resume_from_disk_without_repeating_first_250(tmp_path,monkeypatch):
    from pdf_evidence.source_set import collect_source_set
    from knowledge_map.source_context import build_document_context
    inputs=[];items=[];source_pages=[]
    for index,count in enumerate([19]*18+[26]):
        path=tmp_path/f'input-{index}.pdf'
        with pymupdf.open() as doc:
            for number in range(1,count+1):
                doc.new_page().insert_text((72,72),f'Source {index} page {number}: synthetic evidence remains traceable across retries.')
            doc.save(path)
        request=_request(path);inputs.append(request)
        items.append({'normalized_sha256':request['expected_source_sha256'],'page_count':count})
        offset=len(source_pages)
        source_pages.extend({'page':offset+n,'source_id':f'source-{index}','normalized_page':n} for n in range(1,count+1))
    binding={'source_set_digest':'a'*64,'manifest':{'items':items},'bundle':{'pages':source_pages}}
    directory=tmp_path/'snapshots';directory.mkdir();cache=tmp_path/'cache';cache.mkdir()
    class DiskPages:
        def save_evidence_page(self,number,page):(cache/f'{number}.json').write_text(json.dumps(page))
        def load_evidence_page(self,number):
            path=cache/f'{number}.json';return json.loads(path.read_text()) if path.exists() else None
    visited=[];actual=pipeline.extract_page
    monkeypatch.setattr(pipeline,'extract_page',lambda *args:(visited.append((args[1],args[2])),actual(*args))[1])
    monkeypatch.setattr(pipeline,'start_ocr_process',lambda *args:pytest.fail('native-only fixture must not call OCR'))
    def interrupted(stage,completed,total):
        assert total==368
        if completed==250:raise RuntimeError('synthetic restart')
    settings=_settings(tmp_path)
    with pytest.raises(RuntimeError,match='synthetic restart'):
        collect_source_set(inputs,binding,None,directory,settings,'2026-10-07T00:00:00Z',interrupted,lambda:None,pipeline._page_evidence,analysis_archive=DiskPages())
    assert len(visited)==250 and len(list(cache.glob('*.json')))==250
    first_pages=set(visited);visited.clear()
    retry_directory=tmp_path/'retry-snapshots';retry_directory.mkdir()
    pages,excluded,ocr=collect_source_set(inputs,binding,None,retry_directory,settings,'2026-10-07T00:01:00Z',lambda *args:None,lambda:None,pipeline._page_evidence,analysis_archive=DiskPages())
    assert len(visited)==118 and not first_pages.intersection(visited)
    assert not excluded and ocr==0 and [p['page_number'] for p in pages]==list(range(1,369))
    context=build_document_context(pages,page_count=368,source_pages=source_pages)
    assert len(context['evidence'])==368 and len(context['sections'])==19
    assert [p['page'] for p in context['source_pages']]==list(range(1,369))
    assert all(e['source_locator']['page']==e['page'] for e in context['evidence'])
