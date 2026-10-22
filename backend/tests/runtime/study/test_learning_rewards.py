"""學習活動 v2 的真 DB：XP 來源、概念首次完成、卡片與收聽、連續天數及今日任務。"""
from copy import deepcopy
from datetime import datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient
from sqlalchemy import text

from assessment_fixtures import answer, concept_fixture, create, finish
from product_fixtures import ORIGIN, _app, closed_loop
from learning_adaptation.learner_progress import derive_learner_progress
from runtime import card_reviews, card_sets, podcast_progress, podcasts
from runtime.learner_session import register_account
from runtime.storage.database import database_session
from runtime.storage.learning_activity import learning_activity
from runtime.storage.tables import CardSet, Podcast

TAIPEI = ZoneInfo("Asia/Taipei")


def activity(fixture, now=None):
    with database_session(fixture["dsn"]) as db:
        return learning_activity(db, fixture["learner"].learner_id, now=now)


def ready_cards(fixture):
    ids = [c["concept_id"] for c in fixture["document"]["concepts"]]
    saved = card_sets.create_card_set(
        fixture["learner"].learner_id, fixture["source"].material_id, fixture["document"]["revision"],
        "複習", ids, "cards", dsn=fixture["dsn"],
    )
    with database_session(fixture["dsn"]) as db:
        db.get(CardSet, saved["card_set_id"]).status = "ready"
    return saved["card_set_id"], ids


def ready_podcast(fixture):
    owner, dsn = fixture["learner"].learner_id, fixture["dsn"]
    identity = podcasts.create_podcast(
        owner, fixture["source"].material_id, fixture["document"]["revision"], "合成教學",
        [c["concept_id"] for c in fixture["document"]["concepts"]], "podcast", delivery="solo", dsn=dsn,
    )["podcast_id"]
    from test_podcasts import complete

    complete(dsn)
    with database_session(dsn) as db:
        row = db.get(Podcast, identity)
        episodes = deepcopy(row.episodes)
        for episode in episodes:
            episode["audio"]["duration_seconds"] = 900
        row.episodes = episodes
    return identity


def test_xp_sources_level_and_quests_follow_real_records(closed_loop, tmp_path, monkeypatch):
    fixture = concept_fixture(closed_loop, 2)
    owner, dsn = fixture["learner"].learner_id, fixture["dsn"]
    empty = activity(fixture)
    assert empty["xp"] == {"total": 0, "today": 0, "level": 1, "level_xp": 0, "level_target": 100,
                           "sources": {"answers": 0, "concepts": 0, "cards": 0, "podcast": 0}}
    assert empty["rule_version"] == "xp-rules/v1" and empty["streak_days"] == 0
    # 已開始學習但今天尚未完成題組：顯示「繼續學習」且未完成。
    assert [(q["kind"], q["done"]) for q in empty["quests"]] == [("continue_study", False)]

    set_id = create(fixture)
    finish(fixture)
    answer(fixture, set_id)
    with database_session(dsn) as db:
        rows = db.execute(text("SELECT is_correct FROM answer_events")).scalars().all()
    answer_xp = sum(10 if correct else 3 for correct in rows)
    progress = derive_learner_progress(fixture["learner"], fixture["study"].study_session_id, dsn=dsn)
    completed = sum(state.status == "completed" for state in progress.concept_states)
    assert completed >= 1

    set_id_cards, ids = ready_cards(fixture)
    card_reviews.submit_review(owner, set_id_cards, ids[0], "forgot", 0, "c1", dsn=dsn)
    # 同一張卡同一天再評一次：複習數加一，XP 不再加。
    card_reviews.submit_review(owner, set_id_cards, ids[0], "remembered", 1, "c2", dsn=dsn)
    card_reviews.submit_review(owner, set_id_cards, ids[1], "remembered", 0, "c3", dsn=dsn)

    podcast = ready_podcast(fixture)
    start = datetime.now(TAIPEI)
    for step in range(6):
        podcast_progress.save_progress(owner, podcast, 0, 60.0 * (step + 1), 60, dsn=dsn,
                                       now=start + timedelta(seconds=60 * step))

    view = activity(fixture, now=start + timedelta(minutes=6))
    assert view["xp"]["sources"] == {"answers": answer_xp, "concepts": 30 * completed, "cards": 4, "podcast": 5}
    total = answer_xp + 30 * completed + 4 + 5
    assert view["xp"]["total"] == view["xp"]["today"] == total
    assert (view["xp"]["level"], view["xp"]["level_xp"]) == ((2, total - 100) if total >= 100 else (1, total))
    today = next(day for day in view["week"] if day["date"] == view["today"])
    assert (today["answers"], today["card_reviews"], today["listened_seconds"], today["active"]) == (len(rows), 3, 360, True)
    assert (view["streak_days"], view["active_today"]) == (1, True)
    quests = {q["kind"]: q for q in view["quests"]}
    # 卡組兩張卡今天第一次複習，沒有更早學過的到期卡 → 新卡任務已完成。
    assert (quests["review_new_cards"]["progress"], quests["review_new_cards"]["target"], quests["review_new_cards"]["done"]) == (2, 2, True)
    assert quests["continue_study"]["done"] is True
    assert str(quests["continue_study"]["study_session_id"]) == str(fixture["study"].study_session_id)
    assert quests["finish_podcast_episode"]["done"] is False
    assert quests["finish_podcast_episode"]["podcast_id"] == podcast and quests["finish_podcast_episode"]["episode_index"] == 0

    podcast_progress.save_progress(owner, podcast, 0, 900, 0, ended=True, dsn=dsn, now=start + timedelta(minutes=7))
    finished = activity(fixture, now=start + timedelta(minutes=8))
    assert {q["kind"]: q["done"] for q in finished["quests"]}["finish_podcast_episode"] is True

    client = TestClient(_app(dsn, tmp_path, monkeypatch), base_url=ORIGIN)
    client.cookies.set("studydy_session", fixture["token"])
    response = client.get("/v1/learning-activity")
    assert response.status_code == 200, response.json()
    assert response.json()["schema"] == "learning-activity/v2" and response.json()["xp"]["total"] == total
    other = register_account(f"other_{uuid4().hex[:8]}@example.com", "Synthetic test password 42", dsn=dsn)
    client.cookies.set("studydy_session", other.raw_token)
    foreign = client.get("/v1/learning-activity").json()
    assert foreign["xp"]["total"] == 0 and foreign["quests"] == [] and foreign["streak_days"] == 0


def test_streak_counts_any_activity_and_listening_needs_a_minute(closed_loop):
    fixture = concept_fixture(closed_loop, 2)
    owner, dsn = fixture["learner"].learner_id, fixture["dsn"]
    set_id_cards, ids = ready_cards(fixture)
    card_reviews.submit_review(owner, set_id_cards, ids[0], "remembered", 0, "c1", dsn=dsn)
    podcast = ready_podcast(fixture)
    podcast_progress.save_progress(owner, podcast, 0, 60, 59, dsn=dsn)
    now = datetime(2026, 10, 22, 12, 0, tzinfo=TAIPEI)
    with database_session(dsn) as db:
        # 卡片複習在 10/21（台灣），收聽 59 秒在 10/20（不滿 1 分鐘不算活躍）。
        db.execute(text("UPDATE card_review_events SET created_at = '2026-10-21T01:00:00+08:00'"))
        db.execute(text("UPDATE podcast_listening_days SET listened_on = '2026-10-20'"))
    view = activity(fixture, now=now)
    assert (view["streak_days"], view["active_today"]) == (1, False)
    assert view["xp"]["sources"]["podcast"] == 0 and view["xp"]["today"] == 0
    with database_session(dsn) as db:
        db.execute(text("UPDATE podcast_listening_days SET listened_seconds = 60"))
    view = activity(fixture, now=now)
    assert view["streak_days"] == 2
    week = {day["date"].isoformat(): day for day in view["week"]}
    assert week["2026-10-20"]["listened_seconds"] == 60 and week["2026-10-20"]["active"]
    assert week["2026-10-21"]["card_reviews"] == 1 and week["2026-10-21"]["active"]
    # 昨天以前學過、今天到期的卡 → 到期卡任務。
    with database_session(dsn) as db:
        db.execute(text("UPDATE card_review_states SET due_at = '2026-10-22T00:00:00+08:00'"))
    quests = {q["kind"]: q for q in activity(fixture, now=now)["quests"]}
    assert "review_new_cards" not in quests
    assert (quests["review_due_cards"]["progress"], quests["review_due_cards"]["target"], quests["review_due_cards"]["done"]) == (0, 1, False)


def test_podcast_quest_reads_episode_lists_saved_with_nul_text(closed_loop):
    """含 U+0000 的集數清單以 {"items": [...]} 保存；今日任務仍要算得出集數，不可讓學習活動失敗。"""
    fixture = concept_fixture(closed_loop, 2)
    podcast = ready_podcast(fixture)
    with database_session(fixture["dsn"]) as db:
        row = db.get(Podcast, podcast)
        episodes = deepcopy(row.episodes)
        episodes[0]["title"] = "含\x00字元"
        row.episodes = episodes
    with database_session(fixture["dsn"]) as db:
        assert db.execute(text("SELECT jsonb_typeof(episodes) FROM podcasts WHERE podcast_id = :p"),
                          {"p": podcast}).scalar_one() == "object"
    quests = {q["kind"]: q for q in activity(fixture)["quests"]}
    assert (quests["finish_podcast_episode"]["podcast_id"], quests["finish_podcast_episode"]["episode_index"]) == (podcast, 0)


def test_repeated_question_counts_only_the_first_answer(closed_loop):
    fixture = concept_fixture(closed_loop, 2)
    dsn = fixture["dsn"]
    set_id = create(fixture)
    finish(fixture)
    answer(fixture, set_id, wrong={1})
    first = activity(fixture)
    before = first["xp"]["sources"]["answers"]
    assert before > 0
    with database_session(dsn) as db:
        rows = db.execute(text("SELECT is_correct FROM answer_events ORDER BY created_at, event_number")).scalars().all()
        assert len(rows) >= 2
        # 讓所有作答成為同一份教材的同一題（題目與正解相同）：只算第一次作答。
        db.execute(text("UPDATE answer_events SET semantic_identity = 'assessment-semantic:sha256:' || repeat('a', 64)"))
    after = activity(fixture)
    assert after["xp"]["sources"]["answers"] == (10 if rows[0] else 3) < before
    # 作答數與連續天數仍逐筆計算。
    assert after["total_answers"] == first["total_answers"] == len(rows)


def test_badges_follow_real_records_and_their_dates(closed_loop):
    fixture = concept_fixture(closed_loop, 2)
    owner, dsn = fixture["learner"].learner_id, fixture["dsn"]
    now = datetime(2026, 10, 22, 12, 0, tzinfo=TAIPEI)
    empty = {b["badge_id"]: b for b in activity(fixture, now=now)["badges"]["items"]}
    assert activity(fixture, now=now)["badges"]["rule_version"] == "badge-rules/v4"
    assert not any(b["earned"] for b in empty.values())

    set_id = create(fixture)
    finish(fixture)
    answer(fixture, set_id)
    set_id_cards, ids = ready_cards(fixture)
    card_reviews.submit_review(owner, set_id_cards, ids[0], "remembered", 0, "c1", dsn=dsn)
    card_reviews.submit_review(owner, set_id_cards, ids[1], "remembered", 0, "c2", dsn=dsn)
    with database_session(dsn) as db:
        answers = db.execute(text("SELECT count(*) FROM answer_events")).scalar_one()
        # 作答在 10/16，兩次卡片複習分別在 10/17、10/18（台灣），學習於 10/19 凌晨（台灣）完成。
        db.execute(text("UPDATE answer_events SET created_at = '2026-10-16T09:00:00+08:00'"))
        db.execute(text(
            """
            UPDATE card_review_events v SET created_at = '2026-10-16T09:00:00+08:00'::timestamptz + n * interval '1 day'
            FROM (SELECT review_event_id, row_number() OVER (ORDER BY created_at) AS n FROM card_review_events) o
            WHERE o.review_event_id = v.review_event_id
            """
        ))
        db.execute(text(
            "UPDATE study_sessions SET status = 'completed', completed_at = '2026-10-18T16:30:00+00:00'"
        ))
    view = activity(fixture, now=now)
    badges = {b["badge_id"]: b for b in view["badges"]["items"]}
    day = lambda value: datetime.fromisoformat(value).date()
    assert (badges["first_answer"]["earned"], badges["first_answer"]["earned_on"]) == (True, day("2026-10-16"))
    assert (badges["answers_100"]["earned"], badges["answers_100"]["progress"]) == (False, answers)
    # 概念首次完成時間由作答重播推導，因此同為 10/16。
    assert (badges["first_concept"]["earned"], badges["first_concept"]["earned_on"]) == (True, day("2026-10-16"))
    assert (badges["first_material"]["earned"], badges["first_material"]["earned_on"]) == (True, day("2026-10-19"))
    assert (badges["cards_100"]["earned"], badges["cards_100"]["progress"]) == (False, 2)
    # 10/16 作答、10/17 與 10/18 複習卡片：連續 3 天於 10/18 達成；之後中斷，streak_7 進度為目前連續天數 0。
    assert (badges["streak_3"]["earned"], badges["streak_3"]["earned_on"]) == (True, day("2026-10-18"))
    assert (badges["streak_7"]["earned"], badges["streak_7"]["progress"]) == (False, view["streak_days"]) == (False, 0)
    assert (badges["listen_30_minutes"]["progress"], badges["level_5"]["progress"]) == (0, view["xp"]["level"])

    # 教材移除後，相關的完成與教材徽章不再成立（作答紀錄仍在，作答徽章保留）。
    with database_session(dsn) as db:
        db.execute(text("UPDATE materials SET discard_requested_at = now()"))
    after = {b["badge_id"]: b for b in activity(fixture, now=now)["badges"]["items"]}
    assert not after["first_material"]["earned"] and not after["first_concept"]["earned"]
    assert after["first_answer"]["earned"]
