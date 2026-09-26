"""錯題補強的真 API／DB 瀏覽器，模型全數由受控 fixture 提供。"""

from copy import deepcopy
import json
from threading import Event, Thread

import httpx
from sqlalchemy import select

import runtime.api.app as api
from learning_adaptation import assessment_sets as sets
from runtime.storage.tables import AssessmentSet, AssessmentSetItem, AnswerEvent, database_session
from browser_e2e_runner import PORT, local_api, main as run_browser
from product_fixtures import closed_loop
from assessment_fixtures import concept_fixture, model_for


# 完整 API／DB 流程只跑一次；桌機／手機互動與版面由既有 study-* mock browser 測試驗證。
def test_direct_remediation_resumes_after_lost_create_response_without_duplicates(closed_loop, monkeypatch):
    fixture = concept_fixture(closed_loop, 3)
    monkeypatch.setattr(api, "runtime_binding", lambda _: {})
    app = api.create_app(api.ApiSettings(
        profile="local",
        public_origin=f"http://127.0.0.1:{PORT}",
        secure_cookie=False,
        local_config=fixture["settings"],
        dsn=fixture["dsn"],
    ))

    stop = Event()
    errors = []
    calls = []
    round_by_set = {}
    original_prompt_by_generated_prompt = {}
    base_model = model_for(fixture, calls=calls)

    def worker():
        while not stop.wait(0.05):
            try:
                work = sets.claim_set_work(dsn=fixture["dsn"])
                if work is None:
                    continue
                if work.set_id not in round_by_set:
                    round_by_set[work.set_id] = len(round_by_set) + 1

                def model(client, **kwargs):
                    if kwargs["task"] == "assessment":
                        response = base_model(client, **kwargs)
                        for candidate in response["candidates"]:
                            original_prompt = candidate["prompt"]
                            # 補強題需要不同題幹，避免與前一輪形成完全相同的題目。
                            candidate["prompt"] = f"Round {round_by_set[work.set_id]}: {original_prompt}"
                            original_prompt_by_generated_prompt[candidate["prompt"]] = original_prompt
                        return response

                    request = deepcopy(kwargs["request"])
                    for question in request["questions"]:
                        question["prompt"] = original_prompt_by_generated_prompt[question["prompt"]]
                    return base_model(client, **{**kwargs, "request": request})

                sets.execute_set_work(work, dsn=fixture["dsn"], semantic_call=model)
            except Exception as error:
                errors.append(type(error).__name__)
                return

    def no_http(*args, **kwargs):
        raise AssertionError("MODEL_HTTP_NOT_ALLOWED")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", no_http)
    monkeypatch.setenv("STUDYDY_E2E_REMEDIATION", "true")
    monkeypatch.setenv("STUDYDY_E2E_REMEDIATION_DATA", json.dumps({
        "material": str(fixture["source"].material_id),
        "run": str(fixture["run"].run_id),
        "revision": fixture["document"]["revision"],
        "session": str(fixture["study"].study_session_id),
        "concept": fixture["concept"]["concept_id"],
    }))

    thread = Thread(target=worker)
    thread.start()
    try:
        with local_api(app):
            result = run_browser("e2e/api/assessment-remediation.spec.ts")
            if result:
                with database_session(fixture["dsn"]) as session:
                    groups = list(session.scalars(select(AssessmentSet).where(
                        AssessmentSet.study_session_id == fixture["study"].study_session_id,
                    )))
                    items = list(session.scalars(select(AssessmentSetItem).where(
                        AssessmentSetItem.study_session_id == fixture["study"].study_session_id,
                    )))
                    print(json.dumps({
                        "worker_errors": errors,
                        "calls": calls,
                        "groups": [group.status for group in groups],
                        "items": [(item.state, item.failure_reason) for item in items],
                    }))
            assert result == 0
    finally:
        stop.set()
        thread.join(timeout=10)

    assert not thread.is_alive()
    assert errors == []
    assert calls == ["assessment", "assessment_check"] * 6

    with database_session(fixture["dsn"]) as session:
        groups = list(session.scalars(select(AssessmentSet).where(
            AssessmentSet.study_session_id == fixture["study"].study_session_id,
        )))
        assert len(groups) == 3
        assert all(group.status == "completed" for group in groups)
        root = next(group for group in groups if group.kind == "diagnostic")
        assert all(
            group.diagnostic_set_id == root.set_id
            for group in groups if group.kind == "remediation"
        )

        answers = list(session.scalars(select(AnswerEvent).where(
            AnswerEvent.study_session_id == fixture["study"].study_session_id,
        )))
        assert len(answers) == 6
        assert sum(answer.is_correct for answer in answers) == 3
