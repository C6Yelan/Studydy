"""進度只驗證一次結構，且並行交卷不會混入同次讀取。"""

from concurrent.futures import ThreadPoolExecutor

from learning_adaptation.assessment_sets import _list_sets
from learning_adaptation.learner_progress import derive_learner_progress, progress_snapshot
from product_fixtures import ORIGIN, _app, closed_loop
from assessment_fixtures import answer, finish, concept_fixture, create, read


def test_progress_validates_structure_once_and_keeps_a_consistent_snapshot(closed_loop, monkeypatch):
    import runtime.storage.knowledge_structures as storage
    import knowledge_map.structure as structure
    import learning_adaptation.map_context as context

    fixture = concept_fixture(closed_loop, 2)
    root = create(fixture)
    finish(fixture)
    checks = []
    validate = structure.validate_knowledge_structure

    def counted(document):
        checks.append(document["revision"])
        return validate(document)

    for module in (storage, structure, context):
        monkeypatch.setattr(module, "validate_knowledge_structure", counted)
    with progress_snapshot(
        fixture["learner"], fixture["study"].study_session_id, dsn=fixture["dsn"],
    ) as (db, study, document, before):
        assert len(checks) == 1
        assert before.event_watermark == 0
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(answer, fixture, root, {1}).result(timeout=15)
        # 新答案已 commit，舊讀取仍維持相同時點，不再靠完整重算兩次來猜是否一致。
        assert _list_sets(db, study)["sets"][0]["answered_count"] == 0
        assert before.assessment_cycles[0]["outcome"] == "in_progress"

    after = derive_learner_progress(
        fixture["learner"], fixture["study"].study_session_id, dsn=fixture["dsn"],
    )
    assert after.event_watermark == 2
    assert after.assessment_cycles[0]["pending_count"] == 1
    assert after.guidance_revision != before.guidance_revision
    assert read(fixture, root)["answered_count"] == 2


def test_resume_projects_from_one_verified_document(closed_loop, monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    import runtime.storage.knowledge_structures as storage

    fixture = concept_fixture(closed_loop, 2)
    checks = []
    validate = storage.validate_knowledge_structure

    def counted(document):
        checks.append(document["revision"])
        return validate(document)

    monkeypatch.setattr(storage, "validate_knowledge_structure", counted)
    client = TestClient(_app(fixture["dsn"], tmp_path, monkeypatch), base_url=ORIGIN)
    client.cookies.set("studydy_session", fixture["token"])
    path = (
        f"/v1/materials/{fixture['source'].material_id}"
        f"/knowledge-structures/{fixture['document']['revision']}"
        f"/study-sessions/{fixture['study'].study_session_id}/resume"
    )
    response = client.get(path, params={"run_id": fixture["document"]["run_id"]})
    assert response.status_code == 200, response.json()
    assert checks == [fixture["document"]["revision"]]
    assert response.json()["progress"]["event_watermark"] == 0
