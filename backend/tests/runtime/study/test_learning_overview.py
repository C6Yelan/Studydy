"""學習統計與地圖區域：實際 DB 的彙總、台灣日期邊界與帳號隔離。"""
from datetime import datetime, timezone
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import text

from assessment_fixtures import answer, concept_fixture, create, finish
from product_fixtures import ORIGIN, _app, closed_loop
from runtime.learner_session import register_account
from runtime.storage.database import database_session
from runtime.learning_activity import learning_activity, learning_rhythm


def _client(fixture, tmp_path, monkeypatch, token=None):
    client = TestClient(_app(fixture["dsn"], tmp_path, monkeypatch), base_url=ORIGIN)
    client.cookies.set("studydy_session", token or fixture["token"])
    return client


def test_learning_activity_counts_answers_by_taipei_day_for_the_owner_only(closed_loop, tmp_path, monkeypatch):
    fixture = concept_fixture(closed_loop, 3)
    set_id = create(fixture)
    finish(fixture)
    answer(fixture, set_id, wrong={1})
    owner = fixture["learner"].learner_id
    with database_session(fixture["dsn"]) as db:
        ids = db.execute(text(
            "SELECT answer_event_id FROM answer_events ORDER BY event_number"
        )).scalars().all()
        assert len(ids) == 3
        # 台灣 10/21 00:30、10/20 23:30（UTC 前一天）與 10/18；10/19 沒有作答。
        for identity, moment in zip(ids, [
            "2026-10-20T16:30:00+00:00", "2026-10-20T15:30:00+00:00", "2026-10-18T03:00:00+00:00",
        ]):
            db.execute(text("UPDATE answer_events SET created_at = :at WHERE answer_event_id = :id"),
                       {"at": moment, "id": identity})
        correct = dict(db.execute(text(
            "SELECT answer_event_id, is_correct FROM answer_events"
        )).all())

    def at(moment):
        with database_session(fixture["dsn"]) as db:
            return learning_activity(db, owner, now=datetime.fromisoformat(moment))

    def rhythm(moment, period="week", offset=0):
        with database_session(fixture["dsn"]) as db:
            return learning_rhythm(db, owner, period=period, offset=offset, now=datetime.fromisoformat(moment))

    today = at("2026-10-21T12:00:00+08:00")
    assert today["today"].isoformat() == "2026-10-21"
    assert (today["streak_days"], today["active_today"]) == (2, True)
    week = rhythm("2026-10-21T12:00:00+08:00")
    assert (week["start"].isoformat(), week["end"].isoformat(), week["first_day"].isoformat()) == ("2026-10-19", "2026-10-25", "2026-10-18")
    days = {day["date"].isoformat(): (day["answers"], day["correct"]) for day in week["days"]}
    assert list(days) == [f"2026-10-{day}" for day in range(19, 26)]
    assert days["2026-10-21"] == (1, int(correct[ids[0]]))
    assert days["2026-10-20"] == (1, int(correct[ids[1]]))
    assert days["2026-10-19"] == (0, 0)
    # 往前翻一週可看到 10/18（週日）的作答；整月與長區間彙總同一份每日紀錄。
    earlier = {day["date"].isoformat(): day["answers"] for day in rhythm("2026-10-21T12:00:00+08:00", offset=1)["days"]}
    assert list(earlier)[0] == "2026-10-12" and earlier["2026-10-18"] == 1
    month = rhythm("2026-10-21T12:00:00+08:00", period="month")
    assert len(month["days"]) == 31 and sum(day["answers"] for day in month["days"]) == 3
    year = rhythm("2026-10-21T12:00:00+08:00", period="year")
    assert len(year["days"]) == 364 and sum(day["active"] for day in year["days"]) == 3
    assert (today["total_answers"], today["total_correct"]) == (3, sum(correct.values()))
    # 今天尚未作答時，連續天數保留到今天結束；隔一天沒作答才歸零。
    assert (at("2026-10-22T09:00:00+08:00")["streak_days"], at("2026-10-22T09:00:00+08:00")["active_today"]) == (2, False)
    assert at("2026-10-23T00:00:00+08:00")["streak_days"] == 0
    # 以日期為單位，晚於指定「今天」的作答日不計入。
    assert at("2026-10-20T08:00:00+08:00")["total_answers"] == 2
    assert at("2026-10-17T23:59:00+08:00")["total_answers"] == 0

    client = _client(fixture, tmp_path, monkeypatch)
    response = client.get("/v1/learning-activity")
    assert response.status_code == 200, response.json()
    body = response.json()
    assert body["schema"] == "learning-activity/v4" and body["timezone"] == "Asia/Taipei"
    assert body["total_answers"] == 3
    assert client.get("/v1/learning-activity", params={"learner_id": str(uuid4())}).status_code == 400
    response = client.get("/v1/learning-activity/rhythm", params={"period": "month", "offset": 0})
    assert response.status_code == 200, response.json()
    assert response.json()["schema"] == "learning-rhythm/v1" and response.json()["period"] == "month"
    # 各區間都約十年可翻：週 520、月 120、季 40、年 10 頁。
    for period, limit in (("week", 520), ("month", 120), ("quarter", 40), ("year", 10)):
        assert client.get("/v1/learning-activity/rhythm", params={"period": period, "offset": limit}).status_code == 200
        assert client.get("/v1/learning-activity/rhythm", params={"period": period, "offset": limit + 1}).status_code in (400, 422)
    for params in ({"period": "decade"}, {"period": "week", "offset": -1},
                   {"period": "week", "learner_id": str(uuid4())}, {}):
        assert client.get("/v1/learning-activity/rhythm", params=params).status_code in (400, 422), params

    other = register_account(f"other_{uuid4().hex[:8]}@example.com", "Synthetic test password 42", dsn=fixture["dsn"])
    foreign = _client(fixture, tmp_path, monkeypatch, token=other.raw_token).get("/v1/learning-activity").json()
    assert (foreign["total_answers"], foreign["streak_days"]) == (0, 0)
    foreign_rhythm = _client(fixture, tmp_path, monkeypatch, token=other.raw_token).get(
        "/v1/learning-activity/rhythm", params={"period": "year"}).json()
    assert foreign_rhythm["first_day"] is None and all(day["answers"] == 0 for day in foreign_rhythm["days"])
    client.cookies.clear()
    assert client.get("/v1/learning-activity").status_code == 401
    assert client.get("/v1/learning-activity/rhythm", params={"period": "week"}).status_code == 401


def test_regions_cover_every_concept_once_and_stay_owner_scoped(closed_loop, tmp_path, monkeypatch):
    fixture = concept_fixture(closed_loop, 3)
    mid, rev = fixture["source"].material_id, fixture["document"]["revision"]
    base = f"/v1/materials/{mid}/knowledge-structures/{rev}"
    client = _client(fixture, tmp_path, monkeypatch)
    response = client.get(base + "/regions")
    assert response.status_code == 200, response.json()
    body = response.json()
    outline = client.get(base + "/outline").json()
    path = [s["concept_id"] for s in sorted(outline["initial_learning_path"], key=lambda s: s["position"])]
    # 單一來源教材依學習順序分段，並標示為推導分段。
    assert body["schema"] == "knowledge-regions/v1" and body["basis"] == "learning_order"
    assert [c for region in body["regions"] for c in region["concept_ids"]] == path
    assert all(region["source_id"] is None for region in body["regions"])

    other = register_account(f"other_{uuid4().hex[:8]}@example.com", "Synthetic test password 42", dsn=fixture["dsn"])
    assert _client(fixture, tmp_path, monkeypatch, token=other.raw_token).get(base + "/regions").status_code == 404
    client.cookies.clear()
    assert client.get(base + "/regions").status_code == 401
