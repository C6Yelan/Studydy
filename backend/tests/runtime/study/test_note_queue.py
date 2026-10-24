"""隔離 DB 與真 API，模型使用受控替身，不動產品資料。"""
from copy import deepcopy
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

HEADERS = {"Origin": ORIGIN}


def answer(request):
    return {"paragraphs": [{"heading": c["label"], "text": q["text"], "table": None, "formula": None,
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
        result = answer(request)
        result["paragraphs"][0]["text"] += "（重新整理）"
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
        resumed.append(request)
        return answer(request)
    assert mn.step(dsn=f['dsn'],call=finish_remaining)
    done=client.get(path+'/full').json()
    assert done['status']=='ready' and done['progress']['completed_sections']==2
    assert len(resumed)==1 and len(done['paragraphs'])==3
    with database_session(f['dsn']) as db:
        assert db.get(MaterialNotes,(owner,material,revision,'full')).section_results is None
