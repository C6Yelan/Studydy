"""讀時計算的診斷、歷史題組、結構難度與個人難點。"""

from fastapi.testclient import TestClient

from assessment_fixtures import answer, concept_fixture, create, finish, supplement
from product_fixtures import ORIGIN, _app, closed_loop
from runtime.learner_session import register_account


def _base(fixture):
    return (f"/v1/materials/{fixture['source'].material_id}/knowledge-structures/"
            f"{fixture['document']['revision']}")


def _client(fixture, tmp_path, monkeypatch):
    client = TestClient(_app(fixture["dsn"], tmp_path, monkeypatch), base_url=ORIGIN)
    client.cookies.set("studydy_session", fixture["token"])
    return client


def _diagnosis(client, fixture, group=None):
    response = client.get(_base(fixture) + "/learning-diagnosis",
                          params={"set_id": str(group)} if group else {})
    assert response.status_code == 200, response.json()
    return response.json()


def test_read_diagnosis_projects_scope_counts_and_resolvable_sources(closed_loop, monkeypatch, tmp_path):
    fixture = concept_fixture(closed_loop, 2)
    client = _client(fixture, tmp_path, monkeypatch)
    empty = _diagnosis(client, fixture)
    assert empty["strengths"] == empty["weaknesses"] == empty["recommendations"] == []
    group = create(fixture)
    finish(fixture)
    answer(fixture, group, wrong={1})
    for scope in (None, group):
        report = _diagnosis(client, fixture, scope)
        assert report == _diagnosis(client, fixture, scope)
        points = report["strengths"][0]["points"] + report["weaknesses"][0]["points"]
        assert sum(point["answer_count"] for point in points) == 2
        assert sum(point["correct_count"] for point in points) == 1
        point = report["weaknesses"][0]["points"][0]
        assert point["answer_count"] == 1 and point["correct_count"] == 0
        assert point["latest_is_correct"] is False
        assert len(point["title"]) <= 81
        reference = point["evidence"][0]
        resolved = client.get(report["source_resolver"] + f"/{reference['evidence_id']}/source")
        assert resolved.status_code == 200, resolved.json()
        assert resolved.json()["normalized_page"] == reference["normalized_page"]
        assert report["recommendations"][0]["concept_id"] == fixture["concept"]["concept_id"]


def test_completed_set_uses_its_cutoff_after_later_remediation(closed_loop, monkeypatch, tmp_path):
    fixture = concept_fixture(closed_loop, 1)
    original = create(fixture)
    finish(fixture)
    answer(fixture, original, wrong={1})
    first_retry = supplement(fixture, original)
    finish(fixture, variant="first-correct")
    answer(fixture, first_retry)
    client = _client(fixture, tmp_path, monkeypatch)
    first = _diagnosis(client, fixture, first_retry)
    assert first["strengths"] == []
    assert first["weaknesses"][0]["points"]
    point = first["weaknesses"][0]["points"][0]
    assert point["answer_count"] == point["correct_count"] == 1
    second_retry = supplement(fixture, original, key="second-correct")
    finish(fixture, variant="second-correct")
    answer(fixture, second_retry)
    second = _diagnosis(client, fixture, second_retry)
    assert second["strengths"][0]["points"]
    assert second["weaknesses"] == []
    assert _diagnosis(client, fixture, first_retry) == first
    assert _diagnosis(client, fixture, original)["strengths"] == []
    material = _diagnosis(client, fixture)
    assert material["weaknesses"] == []
    point = material["strengths"][0]["points"][0]
    assert point["answer_count"] == 3 and point["correct_count"] == 2


def test_structure_difficulty_covers_unanswered_concepts_and_owner_scope(closed_loop, monkeypatch, tmp_path):
    fixture = concept_fixture(closed_loop, 5)
    client = _client(fixture, tmp_path, monkeypatch)
    response = client.get(_base(fixture) + "/concept-difficulty")
    assert response.status_code == 200, response.json()
    report = response.json()
    assert report["mode"] == "structure"
    assert {item["concept_id"] for item in report["concepts"]} == {
        item["concept_id"] for item in fixture["document"]["concepts"]}
    assert all(item["answer_count"] == 0 for item in report["concepts"])
    many = next(item for item in report["concepts"] if item["concept_id"] == fixture["concept"]["concept_id"])
    assert many["level"] == "hard" and "5" in many["reason"]
    other = next(item for item in report["concepts"] if item["concept_id"] != many["concept_id"])
    assert other["level"] == "standard"
    foreign = register_account("insights_other@example.com", "Synthetic test password 42", dsn=fixture["dsn"])
    client.cookies.set("studydy_session", foreign.raw_token)
    for endpoint in ("concept-difficulty", "learning-diagnosis"):
        denied = client.get(_base(fixture) + "/" + endpoint)
        assert denied.status_code == 404, denied.json()


def test_personal_difficulty_requires_three_answers_and_survives_remediation(closed_loop, monkeypatch, tmp_path):
    fixture = concept_fixture(closed_loop, 1)
    original = create(fixture)
    finish(fixture)
    answer(fixture, original, wrong={1})
    first = supplement(fixture, original)
    finish(fixture, variant="second-error")
    answer(fixture, first, wrong={1})
    client = _client(fixture, tmp_path, monkeypatch)

    def personal():
        response = client.get(_base(fixture) + "/concept-difficulty")
        assert response.status_code == 200, response.json()
        return next(item for item in response.json()["concepts"]
                    if item["concept_id"] == fixture["concept"]["concept_id"])

    two = personal()
    assert (two["answer_count"], two["incorrect_count"], two["personal_difficulty"]) == (2, 2, False)
    second = supplement(fixture, original, key="first-success")
    finish(fixture, variant="first-success")
    answer(fixture, second)
    three = personal()
    assert (three["answer_count"], three["incorrect_count"], three["personal_difficulty"]) == (3, 2, True)
    third = supplement(fixture, original, key="second-success")
    finish(fixture, variant="second-success")
    answer(fixture, third)
    report = _diagnosis(client, fixture)
    assert report["weaknesses"] == [] and report["strengths"]
    assert report["recommendations"][0]["concept_id"] == fixture["concept"]["concept_id"]
    four = personal()
    assert (four["answer_count"], four["incorrect_count"], four["personal_difficulty"]) == (4, 2, True)
    practice = create(fixture, key="practice-after-completed")
    finish(fixture, variant="practice-after-completed")
    answer(fixture, practice)
    five = personal()
    assert (five["answer_count"], five["incorrect_count"], five["personal_difficulty"]) == (5, 2, False)
