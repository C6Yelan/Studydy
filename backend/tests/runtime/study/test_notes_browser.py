"""真筆記 API 與隔離 DB 的 UI 保存／來源／按需生成流程。"""
import json
from threading import Event, Thread
import httpx
from sqlalchemy import select
from assessment_fixtures import concept_fixture
from product_fixtures import closed_loop
from browser_e2e_runner import PORT, local_api, main as run_browser
from runtime import material_notes as mn
from runtime.api import app as api
from runtime.storage.database import database_session
from runtime.storage.tables import NoteAnnotation
from test_note_queue import answer


def test_notes_browser_persists_annotations_and_generates_full_notes(closed_loop, monkeypatch):
    f = concept_fixture(closed_loop, 2)
    monkeypatch.setattr(api, "runtime_binding", lambda _: {})
    app = api.create_app(api.ApiSettings(profile="local", public_origin=f"http://127.0.0.1:{PORT}",
        secure_cookie=False, local_config=f["settings"], dsn=f["dsn"]))
    while mn.step(dsn=f["dsn"], call=answer): pass
    stop, errors = Event(), []
    def worker():
        while not stop.wait(0.05):
            try: mn.step(dsn=f["dsn"], call=answer, stop=stop)
            except Exception as e:
                errors.append(type(e).__name__)
                return
    def no_model_http(*args, **kwargs): raise AssertionError("MODEL_HTTP_NOT_ALLOWED")
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", no_model_http)
    monkeypatch.setenv("STUDYDY_E2E_NOTES", "true")
    monkeypatch.setenv("STUDYDY_E2E_NOTES_DATA", json.dumps({"material": str(f["source"].material_id)}))
    thread = Thread(target=worker)
    thread.start()
    try:
        with local_api(app): assert run_browser("e2e/api/notes.spec.ts") == 0
    finally:
        stop.set(); thread.join(timeout=10)
    assert not thread.is_alive() and not errors
    with database_session(f["dsn"]) as db:
        values = db.scalars(select(NoteAnnotation).where(NoteAnnotation.material_id == f["source"].material_id)).all()
        assert sorted(value.body for value in values) == ["完整筆記的複習想法", "真 API 保存我的複習想法"]
