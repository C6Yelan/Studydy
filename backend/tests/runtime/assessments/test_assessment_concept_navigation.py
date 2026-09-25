"""不同觀念可以各自保留題組；切換焦點不改題目範圍或作答授權。"""

from uuid import UUID

from fastapi.testclient import TestClient

from learning_adaptation import assessment_sets as sets
from learning_adaptation.study_sessions import read_study_session
from product_fixtures import HEADERS, ORIGIN, _app, closed_loop
from assessment_fixtures import (
    answer,
    change,
    finish,
    supplement,
    answers_for,
    send,
    concept_fixture,
    create,
    create_other,
    model_for,
    other_concept,
    other_model,
    read,
)


def test_enter_and_start_b_while_a_is_generating_then_submit_a_without_changing_b_focus(
    closed_loop, tmp_path, monkeypatch,
):
    f = concept_fixture(closed_loop, 1)
    first = create(f)
    work_a = sets.claim_set_work(dsn=f["dsn"])
    study_session_id = f["study"].study_session_id
    other = other_concept(f)
    client = TestClient(_app(f["dsn"], tmp_path, monkeypatch), base_url=ORIGIN)
    client.cookies.set("studydy_session", f["token"])

    response = client.post(
        f"/v1/study-sessions/{study_session_id}/focus",
        headers=HEADERS,
        json={"schema": "study-session-focus/v1", "current_concept_id": other["concept_id"]},
    )
    assert response.status_code == 200, response.json()
    assert response.json()["current_concept_id"] == other["concept_id"]
    assert read(f, first)["items"][0]["state"] == "generating"

    base = f"/v1/study-sessions/{study_session_id}/assessment-sets"
    created = client.post(
        base,
        headers={**HEADERS, "Idempotency-Key": "new-concept"},
        json={"schema": "assessment-set-create/v1", "target_concept_id": other["concept_id"]},
    )
    assert created.status_code == 202, created.json()
    second = UUID(created.json()["set_id"])
    listing = client.get(base).json()
    assert listing["schema"] == "assessment-set-list/v1"
    assert set(listing["active_set_ids"]) == {str(first), str(second)}

    duplicate = client.post(
        base,
        headers={**HEADERS, "Idempotency-Key": "same-concept-duplicate"},
        json={"schema": "assessment-set-create/v1", "target_concept_id": f["concept"]["concept_id"]},
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["reason_code"] == "ASSESSMENT_SET_ACTIVE"

    sets.execute_set_work(work_a, dsn=f["dsn"], semantic_call=model_for(f))
    work_b = sets.claim_set_work(dsn=f["dsn"])
    assert work_b.set_id == second
    sets.execute_set_work(work_b, dsn=f["dsn"], semantic_call=other_model)
    assert read(f, first)["status"] == read(f, second)["status"] == "ready"

    send(f, first, answers_for(f, first))
    assert read(f, first)["status"] == "completed"
    assert read(f, second)["answered_count"] == 0
    assert read_study_session(f["learner"], study_session_id, dsn=f["dsn"]).current_concept_id == other["concept_id"]
    send(f, second, answers_for(f, second))
    assert read(f, second)["status"] == "completed"
    assert client.get(base).json()["active_set_ids"] == []


def test_other_concept_work_does_not_block_remediation_or_failed_item_retry(closed_loop):
    f = concept_fixture(closed_loop, 1)
    root = create(f)
    finish(f)
    answer(f, root, wrong={1})
    other = create_other(f)
    assert read(f, root)["cycle"]["can_create_remediation"]

    child = supplement(f, root)
    active = sets.list_sets(f["learner"], f["study"].study_session_id, dsn=f["dsn"])["active_set_ids"]
    assert set(active) == {str(other), str(child)}
    reserved_other = sets.claim_set_work(dsn=f["dsn"])
    assert reserved_other.set_id == other
    reserved_child = sets.claim_set_work(dsn=f["dsn"])
    assert reserved_child.set_id == child

    sets.execute_set_work(reserved_child, dsn=f["dsn"], semantic_call=model_for(f, fail={0}))
    assert read(f, child)["status"] == "failed"
    assert read(f, child)["can_retry"]
    change(f, child, "retry")
    assert read(f, child)["status"] == "preparing"
    assert read(f, other)["items"][0]["state"] == "generating"
