"""隔離 DB 與真 API，模型使用受控替身，不動產品資料。"""
from copy import deepcopy
import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select, func
from assessment_fixtures import concept_fixture
from product_fixtures import ORIGIN, _app, closed_loop
from runtime import material_notes as mn
from runtime.learner_session import register_account
from runtime.storage.database import database_session
from runtime.storage.tables import MaterialNotes, NoteAnnotation, KnowledgeStructure
from runtime.storage import knowledge_reads

HEADERS = {"Origin": ORIGIN}


def answer(request):
    if request.get("phase") == "select_summary":
        chosen = {q["ref"] for q in [q for c in request["concepts"] for q in c["claims"]][:5]}
        return {"concepts": [{"concept": c["ref"], "claim_ids": [q["ref"] for q in c["claims"] if q["ref"] in chosen]}
            for c in request["concepts"]]}
    if request.get("phase") == "review_paragraph":
        return {"checks": [{"part": part, "missing_evidence": [], "supported": True, "reason": ""} for part in request["parts"]],
            "coverage": [{"claim": ref, "explained": True, "note_parts": ["blocks.0"], "reason": ""} for ref in request["coverage_claims"]]}
    return {"paragraphs": [{"heading": c["label"], "level": 1, "blocks": [{"type": "text", "text": q["text"]}],
        "claim_ids": [q["claim_id"]], "evidence_ids": q.get("evidence_ids") or [e["evidence_id"] for e in q["evidence"]]}
        for c in request["concepts"] for q in c["claims"]]}


def setup(closed_loop, tmp_path, monkeypatch):
    f = concept_fixture(closed_loop, 2)
    client = TestClient(_app(f["dsn"], tmp_path, monkeypatch), base_url=ORIGIN)
    client.cookies.set("studydy_session", f["token"])
    return f, client, f"/v1/materials/{f['source'].material_id}/knowledge-structures/{f['document']['revision']}/notes"


def drain(f, call=answer):
    while mn.step(dsn=f["dsn"], call=call): pass


def test_auto_summary_full_on_demand_and_owner_boundary(closed_loop, tmp_path, monkeypatch):
    f, client, path = setup(closed_loop, tmp_path, monkeypatch)
    before = client.get(path + "/summary")
    assert before.status_code == 200, before.json()
    assert before.json()["status"] == "pending"
    assert client.get(path + "/full").json()["status"] == "not_generated"
    drain(f)
    summary = client.get(path + "/summary").json()
    assert summary["status"] == "ready", summary
    assert summary["generation"] == 1 and summary["paragraphs"]
    assert client.get(path + "/full").json()["status"] == "not_generated"
    assert client.post(path + "/full", json={"expected_generation": 0}).status_code == 403
    queued = client.post(path + "/full", headers=HEADERS, json={"expected_generation": 0})
    assert queued.status_code == 202
    assert client.post(path + "/full", headers=HEADERS, json={"expected_generation": 0}).json() == queued.json()
    drain(f)
    full = client.get(path + "/full").json()
    assert full["status"] == "ready" and len(full["paragraphs"]) == 3
    assert client.post(path + "/full", headers=HEADERS, json={"expected_generation": 0}).status_code == 409
    foreign = register_account("note_foreign@example.com", "Synthetic test password 42", dsn=f["dsn"])
    client.cookies.set("studydy_session", foreign.raw_token)
    assert client.get(path + "/summary").status_code == 404
    assert client.post(path + "/full", headers=HEADERS, json={"expected_generation": 1}).status_code == 404


def test_saved_same_page_context_is_published_as_resolvable_evidence_without_changing_source(closed_loop):
    f=concept_fixture(closed_loop,facts=['Numeric value: 0.1.','Saved column label: rate.'])
    key=(f['learner'].learner_id,f['source'].material_id,f['document']['revision'])
    with database_session(f['dsn']) as db:
        before=deepcopy(db.get(KnowledgeStructure,key).document)
    def model(request):
        value=answer(request)
        if request.get('phase') is None:
            headers=[e['evidence_id'] for e in request['evidence'] if e['quotes']==['Saved column label: rate.']]
            for c in request['concepts']:
                for q in c['claims']:
                    if q['text']=='Numeric value: 0.1.':
                        assert headers[0] in q['context_evidence_ids']
                        p=next(p for p in value['paragraphs'] if p['claim_ids']==[q['claim_id']])
                        p['evidence_ids']=[*p['evidence_ids'],headers[0]]
        return value
    drain(f,model)
    result=mn.read(*key,'summary',dsn=f['dsn'])
    assert result['status']=='ready',result['error_code']
    paragraph=next(p for p in result['paragraphs'] if p['blocks'][0]['text']=='Numeric value: 0.1.')
    header=next(e for e in paragraph['evidence'] if e['quote']=='Saved column label: rate.')
    with database_session(f['dsn']) as db:
        _,location=knowledge_reads.evidence_source(db,*key,header['evidence_id'])
        assert location['normalized_page']==1 and location['source_locator']==header['source_locator']
        assert db.get(KnowledgeStructure,key).document==before


def test_annotations_keep_snapshot_and_failed_regeneration_preserves_readable_notes(closed_loop, tmp_path, monkeypatch):
    f, client, path = setup(closed_loop, tmp_path, monkeypatch)
    drain(f)
    first = client.get(path + "/summary").json()
    original = deepcopy(first["paragraphs"][0])
    annotation = str(uuid4())
    endpoint = path + "/summary/annotations/" + annotation
    value = {"expected_generation": 1, "paragraph_id": "0", "body": "我的筆記"}
    saved = client.put(endpoint, headers=HEADERS, json=value)
    assert saved.status_code == 200, saved.json()
    assert saved.json()["annotations"][0]["original_paragraph"] == original
    client.post(path + "/summary", headers=HEADERS, json={"expected_generation": 1})
    assert client.get(path + "/summary").json()["paragraphs"] == first["paragraphs"]
    drain(f, lambda request: {"paragraphs": []})
    failed = client.get(path + "/summary").json()
    assert failed["status"] == "failed" and failed["generation"] == 1
    assert failed["paragraphs"] == first["paragraphs"] and failed["annotations"][0]["body"] == "我的筆記"
    client.post(path + "/summary", headers=HEADERS, json={"expected_generation": 1})
    def changed_prose(request):
        if request.get("phase"):
            return answer(request)
        result = answer(request)
        result["paragraphs"][0]["blocks"][0]["text"] += "（重新整理）"
        return result
    drain(f, changed_prose)
    refreshed = client.get(path + "/summary").json()
    assert refreshed["generation"] == 2 and refreshed["annotations"][0]["paragraph_id"] == "0"
    assert refreshed["annotations"][0]["original_paragraph"] == original
    stale = client.put(path + "/summary/annotations/" + str(uuid4()), headers=HEADERS, json=value)
    assert stale.status_code == 409
    changed = client.put(endpoint, headers=HEADERS, json={**value, "body": "更新想法"})
    assert changed.json()["annotations"][0]["body"] == "更新想法"
    removed = client.put(endpoint, headers=HEADERS, json={**value, "body": ""})
    assert removed.status_code == 200 and removed.json()["annotations"] == []
    assert client.put(endpoint, headers=HEADERS, json={**value, "body": ""}).status_code == 200


def test_v11_paragraphs_stay_readable_with_annotations_until_regenerated(closed_loop, tmp_path, monkeypatch):
    f, client, path = setup(closed_loop, tmp_path, monkeypatch)
    drain(f)
    key = (f['learner'].learner_id, f['source'].material_id, f['document']['revision'], 'summary')
    with database_session(f['dsn']) as db:
        row = db.get(MaterialNotes, key)
        legacy = [{k: v for k, v in p.items() if k not in ('level', 'blocks')} | {
            'text': p['blocks'][0]['text'], 'formula': 'a = b', 'table': {'headers': ['甲'], 'rows': [['乙']]}}
            for p in row.paragraphs]
        row.paragraphs = legacy
        row.published_metadata = None
    saved = client.put(path + '/summary/annotations/' + str(uuid4()), headers=HEADERS,
        json={'expected_generation': 1, 'paragraph_id': '0', 'body': '舊版註記'})
    assert saved.status_code == 200, saved.json()
    view = client.get(path + '/summary').json()
    assert view['schema'] == 'material-notes/v3' and view['outline'] == []
    assert view['paragraphs'] == legacy and view['annotations'][0]['original_paragraph'] == legacy[0]
    for format in ('md', 'pdf', 'docx'):
        assert client.get(path + f'/summary/export/{format}').status_code == 200
    client.post(path + '/summary', headers=HEADERS, json={'expected_generation': 1})
    drain(f)
    regenerated = client.get(path + '/summary').json()
    assert regenerated['generation'] == 2 and all('blocks' in p for p in regenerated['paragraphs'])
    assert [o['number'] for o in regenerated['outline']] == ['一、', '二、', '三、']
    assert regenerated['annotations'][0]['paragraph_id'] == '0'
    assert regenerated['annotations'][0]['original_paragraph'] == legacy[0]


def test_stale_worker_cannot_publish_and_expired_lease_recovers(closed_loop, tmp_path, monkeypatch):
    f, client, path = setup(closed_loop, tmp_path, monkeypatch)
    claim = mn._claim(f["dsn"])
    with database_session(f["dsn"]) as db:
        row = db.get(MaterialNotes, claim["key"], with_for_update=True)
        row.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    replacement = mn._claim(f["dsn"])
    assert replacement["key"] == claim["key"] and replacement["token"] != claim["token"]
    mn._save(claim, f["dsn"], paragraphs=[{"forged": True}])
    with database_session(f["dsn"]) as db:
        row = db.get(MaterialNotes, claim["key"])
        assert row.paragraphs is None and row.lease_token == replacement["token"]
        row.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    assert mn._claim(f["dsn"]) == {"skipped": True}
    with database_session(f["dsn"]) as db:
        row = db.get(MaterialNotes, claim["key"])
        assert row.status == "failed" and row.error_code == "NOTE_JOB_INTERRUPTED"


def test_backfill_is_owner_scoped_and_does_not_regenerate(closed_loop, tmp_path, monkeypatch):
    f, client, path = setup(closed_loop, tmp_path, monkeypatch)
    owner = f["learner"].learner_id
    with database_session(f["dsn"]) as db:
        before = db.scalar(select(func.count()).select_from(MaterialNotes))
    assert mn.queue_existing(owner=owner, dsn=f["dsn"]) == 0
    drain(f)
    assert mn.queue_existing(owner=owner, dsn=f["dsn"]) == 0
    with database_session(f["dsn"]) as db:
        assert db.scalar(select(func.count()).select_from(MaterialNotes)) == before


def test_new_revision_reattaches_exact_sources_and_keeps_changed_paragraph_snapshot(closed_loop, tmp_path, monkeypatch):
    from test_version_moves import new_revision
    f, client, path = setup(closed_loop, tmp_path, monkeypatch)
    drain(f)
    before = client.get(path + "/summary").json()
    for p in before["paragraphs"]:
        assert client.put(path + "/summary/annotations/" + str(uuid4()), headers=HEADERS, json={
            "expected_generation": 1, "paragraph_id": p["id"], "body": p["heading"] + "的註記"}).status_code == 200
    newer = new_revision(f, "rewrite")
    drain(f)
    new_path = path.replace(f["document"]["revision"], newer["revision"])
    after = client.get(new_path + "/summary").json()
    assert after["status"] == "ready", after
    changed = [a for a in after["annotations"] if a["original_paragraph"]["concepts"][0]["label"] == "Signals"]
    unchanged = [a for a in after["annotations"] if a not in changed]
    assert len(changed) == 2 and all(a["paragraph_id"] is None for a in changed)
    assert len(unchanged) == 1 and unchanged[0]["paragraph_id"] is not None
    assert [a["original_paragraph"] for a in after["annotations"]] == before["paragraphs"]
    assert client.get(path + "/summary").status_code == 200


def test_worker_shutdown_returns_job_to_queue_without_consuming_recovery_attempt(closed_loop, tmp_path, monkeypatch):
    from threading import Event
    f, client, path = setup(closed_loop, tmp_path, monkeypatch)
    stop = Event()
    def interrupted(request):
        stop.set()
        return answer(request)
    assert mn.step(dsn=f["dsn"], call=interrupted, stop=stop)
    with database_session(f["dsn"]) as db:
        first = db.scalar(select(MaterialNotes).order_by(MaterialNotes.created_at))
        assert first.status == "pending" and first.attempts == 0
        assert first.lease_token is None and first.paragraphs is None and first.calls == 1
    drain(f)
    assert client.get(path + "/summary").json()["status"] == "ready"


def test_ready_notes_keep_source_quality_notice(closed_loop, tmp_path, monkeypatch):
    f = concept_fixture(closed_loop, 2, source_review_required=True)
    client = TestClient(_app(f["dsn"], tmp_path, monkeypatch), base_url=ORIGIN)
    client.cookies.set("studydy_session", f["token"])
    drain(f)
    path = f"/v1/materials/{f['source'].material_id}/knowledge-structures/{f['document']['revision']}/notes/summary"
    result = client.get(path)
    assert result.status_code == 200, result.json()
    assert result.json()["status"] == "ready"
    assert result.json()["source_status"]["quality"] == "needs_review"


def test_full_sections_report_progress_and_resume_saved_parts(closed_loop, tmp_path, monkeypatch):
    from runtime.storage.tables import KnowledgeChapters
    f, client, path = setup(closed_loop, tmp_path, monkeypatch)
    drain(f)
    owner, material, revision = f['learner'].learner_id, f['source'].material_id, f['document']['revision']
    ids = [c['concept_id'] for c in f['document']['concepts']]
    with database_session(f['dsn']) as db:
        chapters = db.get(KnowledgeChapters, (owner, material, revision))
        chapters.status = 'ready'
        chapters.chapters = [{'title': f'章節 {i+1}', 'concept_ids': [cid], 'fallback': False} for i,cid in enumerate(ids)]
    assert client.post(path+'/full',headers=HEADERS,json={'expected_generation':0}).status_code == 202
    calls=[]
    def stop_second(request):
        if request.get("phase") == "review_paragraph":
            return answer(request)
        calls.append(request)
        if len(calls)==2:
            view=client.get(path+'/full').json()
            assert view['progress']['completed_sections']==1 and view['progress']['total_sections']==2
            assert view['progress']['completed_parts']==1
            assert view['paragraphs']==[]
            raise RuntimeError('synthetic provider failure')
        return answer(request)
    assert mn.step(dsn=f['dsn'],call=stop_second)
    failed=client.get(path+'/full').json()
    assert failed['status']=='failed' and failed['progress']['completed_sections']==1
    assert client.post(path+'/full',headers=HEADERS,json={'expected_generation':0}).status_code==202
    resumed=[]
    def finish_remaining(request):
        if request.get("phase") == "review_paragraph":
            return answer(request)
        resumed.append(request)
        return answer(request)
    assert mn.step(dsn=f['dsn'],call=finish_remaining)
    done=client.get(path+'/full').json()
    assert done['status']=='ready' and done['progress']['completed_sections']==2
    assert len(resumed)==1 and len(done['paragraphs'])==3
    with database_session(f['dsn']) as db:
        assert db.get(MaterialNotes,(owner,material,revision,'full')).section_results is None




def test_changed_contract_discards_unpublished_sections_and_retry_can_finish(closed_loop, tmp_path, monkeypatch):
    f, client, path=setup(closed_loop,tmp_path,monkeypatch)
    drain(f)
    assert client.post(path+'/full',headers=HEADERS,json={'expected_generation':0}).status_code==202
    drain(f)
    first=client.get(path+'/full').json()
    key=(f['learner'].learner_id,f['source'].material_id,f['document']['revision'],'full')
    with database_session(f['dsn']) as db:
        row=db.get(MaterialNotes,key)
        old=deepcopy(row.runtime_lock)
        old['material_notes'].update(version='material-notes/v1',prompt='previous writing policy')
        old['material_notes'].pop('review_prompt')
        row.runtime_lock=old
        row.status,row.error_code='failed','NOTE_CONTRACT_INVALID'
        row.section_results=[deepcopy(row.paragraphs)]
        row.progress_stage='validating'
    queued=client.post(path+'/full',headers=HEADERS,json={'expected_generation':1})
    assert queued.status_code==202
    assert queued.json()['progress'] is None and queued.json()['paragraphs']==first['paragraphs']
    with database_session(f['dsn']) as db:
        row=db.get(MaterialNotes,key)
        assert row.section_plan is None and row.section_results is None
        assert row.runtime_lock==mn.configured_lock()
    calls=[]
    def model(request):
        calls.append(request)
        return answer(request)
    assert mn.step(dsn=f['dsn'],call=model)
    final=client.get(path+'/full').json()
    assert final['status']=='ready' and final['generation']==2
    assert any(r.get('phase')!='review_paragraph' for r in calls)
    assert len([r for r in calls if r.get('phase')=='review_paragraph'])==3




def test_reference_repair_missing_paragraph_preserves_explicit_worker_error(closed_loop,tmp_path,monkeypatch):
    f,client,path=setup(closed_loop,tmp_path,monkeypatch)
    drain(f)
    assert client.post(path+'/full',headers=HEADERS,json={'expected_generation':0}).status_code==202
    calls=[]
    def malformed_repair(request):
        calls.append(request)
        result=answer(request)
        if len(calls)%2==1:
            result['paragraphs'][-1]['evidence_ids']=result['paragraphs'][0]['evidence_ids'][:]
        else:
            result['paragraphs']=result['paragraphs'][:1]
        return result
    assert mn.step(dsn=f['dsn'],call=malformed_repair)
    failed=client.get(path+'/full').json()
    assert failed['status']=='failed' and failed['error_code']=='NOTE_FORMAT_INVALID'
    assert failed['paragraphs']==[] and failed['progress']['completed_parts']==0
    assert len(calls)==6  # 格式錯誤可因重寫改變：同一批共三次嘗試，每次一份稿加一次引用補正。


def test_step_dispatches_content_checks_with_the_saved_review_contract(closed_loop,tmp_path,monkeypatch):
    f,client,path=setup(closed_loop,tmp_path,monkeypatch)
    seen=[]
    def semantics(_client,*,runtime_lock,task,request,response_schema,cancellation_check):
        cancellation_check()
        phase=request.get('phase','generate')
        seen.append(phase)
        assert task=='material_review'
        if phase=='review_paragraph':
            assert runtime_lock['material_review']['prompt']==runtime_lock['material_notes']['review_prompt']
            assert 'checks' in response_schema['properties']
            assert response_schema['properties']['coverage']['maxItems']==len(request['coverage_claims'])
            assert response_schema['$defs']['ContentCheck']['properties']['part']['enum']==request['parts']
            if request['coverage_claims']:
                assert response_schema['$defs']['ClaimCoverage']['properties']['claim']['enum']==request['coverage_claims']
        elif phase=='select_summary':
            assert runtime_lock['material_review']['prompt']==runtime_lock['material_notes']['selection_prompt']
            assert 'concepts' in response_schema['properties']
        else:
            assert runtime_lock['material_review']['prompt']==runtime_lock['material_notes']['prompt']
        return answer(request)
    monkeypatch.setattr(mn,'request_semantics',semantics)
    while mn.step(dsn=f['dsn']):pass
    result=client.get(path+'/summary').json()
    assert result['status']=='ready'
    assert seen.count('review_paragraph')==4  # fixture 的一段教材，加上本教材三段
    assert seen.count('generate')==2  # 兩份短稿各寫一次，沒有頁面空間的補寫輪次。
    assert seen.count('select_summary')==2  # 兩份教材即使很短，仍各挑選一次核心。


def _diagnostics(f, kind):
    with database_session(f['dsn']) as db:
        key = (f['learner'].learner_id, f['source'].material_id, f['document']['revision'], kind)
        return db.get(MaterialNotes, key).generation_diagnostics


def _material_texts(requests):
    return {t for r in requests for c in r.get('concepts', [])
        for t in [c['label'], *(q['text'] for q in c['claims'])]}


def test_dropped_paragraph_and_failure_save_summary_codes_without_material_text(closed_loop, tmp_path, monkeypatch, caplog):
    f, client, path = setup(closed_loop, tmp_path, monkeypatch)
    reason = '原文寫的是另一個條件 SECRET-REASON'
    requests = []

    def drop_first(request):
        requests.append(request)
        if request.get('phase') != 'review_paragraph':
            return answer(request)
        rejected = request['paragraph']['content'][0]['text'] == 'Signal 0 uses code0.'
        return {**answer(request), 'checks': [{'part': p, 'missing_evidence': [], 'supported': not (rejected and p == 'blocks.0'),
            'reason': reason if rejected and p == 'blocks.0' else ''} for p in request['parts']]}
    drain(f, drop_first)
    summary = client.get(path + '/summary').json()
    assert summary['status'] == 'ready' and summary['paragraphs']
    assert 'generation_diagnostics' not in summary and 'diagnostics' not in summary
    saved = _diagnostics(f, 'summary')
    assert set(saved) == {'events'}
    dropped = [e for e in saved['events'] if e['event'] == 'paragraph_dropped']
    assert [e['reason'] for e in dropped] == ['no_supported_content']
    assert len(dropped[0]['claims']) == 1
    rejected = [e for e in saved['events'] if e['event'] == 'review_rejected']
    assert len(rejected) == 1
    assert all(len(i['reason_sha256']) == 12 for e in rejected for i in e['issues'])
    stored = json.dumps(saved, ensure_ascii=False)
    assert 'SECRET-REASON' not in stored
    assert not any(t in stored for t in _material_texts(requests))
    # 日誌與 DB 為同一份摘要。
    logged = [r.getMessage() for r in caplog.records if 'NOTE_GENERATION_ADJUSTED' in r.getMessage()]
    assert json.loads(logged[-1].split('diagnostics=', 1)[1]) == saved

    # 失敗：寫入錯誤碼與事件；不回傳給 API，他人讀不到。
    assert client.post(path + '/full', headers=HEADERS, json={'expected_generation': 0}).status_code == 202
    def malformed(request):
        requests.append(request)
        return {'paragraphs': []}
    drain(f, malformed)
    full = client.get(path + '/full').json()
    assert full['status'] == 'failed' and 'generation_diagnostics' not in full
    failed = _diagnostics(f, 'full')
    assert failed['code'] == full['error_code'] == 'NOTE_FORMAT_INVALID'
    assert failed['error_type'] == 'ValueError'
    assert failed['events'][-1] == {'event': 'part_attempts', 'part': 0, 'attempts': 3,
        'failed': ['NOTE_FORMAT_INVALID'] * 3, 'succeeded': False}
    stored = json.dumps(failed, ensure_ascii=False)
    assert 'SECRET-REASON' not in stored and not any(t in stored for t in _material_texts(requests))
    foreign = register_account('note_diag_foreign@example.com', 'Synthetic test password 42', dsn=f['dsn'])
    client.cookies.set('studydy_session', foreign.raw_token)
    assert client.get(path + '/full').status_code == 404

    # 重新生成覆寫：這次沒有刪減就清為 NULL。
    client.cookies.set('studydy_session', f['token'])
    assert client.post(path + '/full', headers=HEADERS, json={'expected_generation': 0}).status_code == 202
    drain(f)
    assert client.get(path + '/full').json()['status'] == 'ready'
    assert _diagnostics(f, 'full') is None


def test_interrupted_job_saves_same_summary_code_in_db_and_log(closed_loop, tmp_path, monkeypatch, caplog):
    f, client, path = setup(closed_loop, tmp_path, monkeypatch)
    drain(f)
    assert client.post(path + '/full', headers=HEADERS, json={'expected_generation': 0}).status_code == 202
    with database_session(f['dsn']) as db:
        key = (f['learner'].learner_id, f['source'].material_id, f['document']['revision'], 'full')
        db.get(MaterialNotes, key).attempts = 2
    assert mn.step(dsn=f['dsn'], call=answer)
    full = client.get(path + '/full').json()
    assert full['status'] == 'failed' and full['error_code'] == 'NOTE_JOB_INTERRUPTED'
    saved = _diagnostics(f, 'full')
    assert saved == {'code': 'NOTE_JOB_INTERRUPTED', 'events': []}
    logged = [r.getMessage() for r in caplog.records if 'NOTE_GENERATION_FAILED kind=full' in r.getMessage()]
    assert json.loads(logged[-1].split('diagnostics=', 1)[1]) == saved
