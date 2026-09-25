"""單一觀念題組的真 API／DB browser：受控模型，沒有外部模型請求。"""

import json
from threading import Event, Thread
from uuid import UUID

import httpx
import pytest
from sqlalchemy import select

import runtime.api.app as api
from learning_adaptation import assessment_sets as sets
from runtime.storage.tables import AssessmentSet, AnswerEvent, database_session
from browser_e2e_runner import PORT, local_api, main as run_browser
from product_fixtures import closed_loop
from assessment_fixtures import concept_fixture, model_for


# 1536 覆蓋桌機雙欄與 409 版本衝突；390 覆蓋手機單欄及直接交卷回應遺失。
@pytest.mark.parametrize("width", [1536, 390])
def test_whole_set_browser_submits_once_and_restores_all_answers(closed_loop, monkeypatch, width):
    fixture = concept_fixture(closed_loop, 3)
    monkeypatch.setattr(api, "runtime_binding", lambda _: {})
    app = api.create_app(api.ApiSettings(
        profile="local",
        public_origin=f"http://127.0.0.1:{PORT}",
        secure_cookie=False,
        local_config=fixture["settings"],
        dsn=fixture["dsn"],
    ))

    release = Event()
    stop = Event()
    errors = []
    calls = []
    base_model = model_for(fixture, calls=calls)

    def model(*args, **kwargs):
        # 先讓瀏覽器確認準備中與重整畫面，再允許模型完成。
        while not release.wait(0.05):
            if stop.is_set():
                raise RuntimeError("Fixture stopped")
        return base_model(*args, **kwargs)

    @app.post("/v1/__test/sets/{set_id}/version")
    def advance_version(set_id: UUID):
        # 模擬讀取後的並行狀態更新，驗證交卷遇到明確 409 後不重用舊版本。
        with database_session(fixture["dsn"]) as session:
            group = session.get(AssessmentSet, set_id)
            assert group.study_session_id == fixture["study"].study_session_id
            group.set_version += 1
        return {"updated": True}

    @app.post("/v1/__test/sets/release")
    def release_generation():
        release.set()
        return {"released": True}

    def worker():
        while not stop.wait(0.05):
            try:
                work = sets.claim_set_work(dsn=fixture["dsn"])
                if work:
                    sets.execute_set_work(work, dsn=fixture["dsn"], semantic_call=model)
            except Exception as error:
                errors.append(type(error).__name__)
                return

    def no_model_http(*args, **kwargs):
        raise AssertionError("MODEL_HTTP_NOT_ALLOWED")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", no_model_http)
    monkeypatch.setenv("STUDYDY_E2E_ASSESSMENT_SET", "true")
    monkeypatch.setenv("STUDYDY_E2E_SET_DATA", json.dumps({
        "material": str(fixture["source"].material_id),
        "run": str(fixture["run"].run_id),
        "revision": fixture["document"]["revision"],
        "session": str(fixture["study"].study_session_id),
        "concept": fixture["concept"]["concept_id"],
        "width": width,
    }))

    thread = Thread(target=worker)
    thread.start()
    try:
        with local_api(app):
            assert run_browser("e2e/assessment-sets.spec.ts") == 0
    finally:
        stop.set()
        release.set()
        thread.join(timeout=10)

    assert not thread.is_alive()
    assert errors == []
    assert calls == ["assessment", "assessment_check"] * 3

    with database_session(fixture["dsn"]) as session:
        groups = list(session.scalars(select(AssessmentSet).where(
            AssessmentSet.study_session_id == fixture["study"].study_session_id,
        )))
        answers = list(session.scalars(select(AnswerEvent).where(
            AnswerEvent.study_session_id == fixture["study"].study_session_id,
        )))
        assert len(groups) == 1
        assert groups[0].status == "completed"
        assert len(answers) == 3
        assert sum(answer.is_correct for answer in answers) == 2
