"""badge-rules/v8／xp-rules/v5 的真 DB：交卷記錄分天關卡與準時達標、既有資料回溯推導，以及 0019 升級。"""
import json
from copy import deepcopy
from datetime import datetime, timedelta
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import text

from assessment_fixtures import answer, concept_fixture, create, create_other, finish, other_model
from learning_adaptation import assessment_sets, learning_goals
from product_fixtures import closed_loop
from runtime import podcasts
from runtime.learning_activity import learning_activity, record_export
from runtime.source_normalization import upload_source
from runtime.storage.database import database_session
from runtime.storage.tables import Podcast

TAIPEI = ZoneInfo("Asia/Taipei")


def activity(f):
    with database_session(f["dsn"]) as db:
        view = learning_activity(db, f["learner"].learner_id)
    return view, {item["badge_id"]: item for item in view["badges"]["items"]}


def milestones(f):
    with database_session(f["dsn"]) as db:
        return sorted(db.execute(text("SELECT kind, day, target_date FROM goal_milestones")).all())


def finish_other(f):
    while work := assessment_sets.claim_set_work(dsn=f["dsn"]):
        assessment_sets.execute_set_work(work, dsn=f["dsn"], semantic_call=other_model)


def test_submit_records_stage_and_on_time_goal_once(closed_loop):
    f = concept_fixture(closed_loop, 2)
    today = datetime.now(TAIPEI).date()
    learning_goals.set_goal(f["learner"], f["source"].material_id, today, 60, dsn=f["dsn"])
    _, before = activity(f)
    assert before["first_goal"]["earned_on"] == today
    assert not before["first_stage"]["earned"] and not before["goal_on_time"]["earned"]
    # 3 個可檢測重點的 60% 是 2 點；今天就是目標日，今日配額 2。
    root = create(f)
    finish(f)
    answer(f, root)
    assert milestones(f) == [("achieved", today, today), ("stage", today, None)]
    view, badges = activity(f)
    for badge_id in ("first_goal", "first_stage", "goal_on_time", "last_minute"):
        assert badges[badge_id]["earned_on"] == today, badge_id
    assert view["xp"]["sources"]["goals"] == 15 + 50
    # 之後再交卷（同一天、已達標）不重複記錄。
    other = create_other(f)
    finish_other(f)
    answer(f, other)
    assert milestones(f) == [("achieved", today, today), ("stage", today, None)]
    assert activity(f)[0]["xp"]["sources"]["goals"] == 65


def test_goal_already_met_when_set_is_not_an_on_time_achievement(closed_loop):
    f = concept_fixture(closed_loop, 2)
    today = datetime.now(TAIPEI).date()
    root = create(f)
    finish(f)
    answer(f, root)
    # 先完成 2/3 點再設定 60% 目標：設定那一刻已達標。
    learning_goals.set_goal(f["learner"], f["source"].material_id, today + timedelta(days=3), 60, dsn=f["dsn"])
    other = create_other(f)
    finish_other(f)
    answer(f, other)
    kinds = [kind for kind, _, _ in milestones(f)]
    assert "achieved" not in kinds
    _, badges = activity(f)
    assert not badges["goal_on_time"]["earned"] and not badges["last_minute"]["earned"]


def test_existing_records_are_derived_retroactively(closed_loop, tmp_path, monkeypatch):
    f = concept_fixture(closed_loop, 2)
    owner, dsn, material = f["learner"].learner_id, f["dsn"], f["source"].material_id
    revision = f["document"]["revision"]
    root = create(f)
    finish(f)
    answer(f, root)
    # 媒體來源：圖片組、音訊、影片（只存音軌，來源仍記為影片）、YouTube。
    for name, media, data in (
        ("photos.zip", "application/zip", b"PK synthetic"), ("talk.mp3", "audio/mpeg", b"ID3 synthetic"),
        ("clip.mp4", "video/mp4", b"m4a synthetic"),
        ("lecture.youtube", "application/vnd.studydy.youtube+json", json.dumps({"video_id": "abcdefghijk"}).encode()),
    ):
        upload_source(owner, material, data, name, media, str(uuid4()), dsn=dsn)
    now = datetime.now(TAIPEI)
    with database_session(dsn) as db:
        lock = db.execute(text("SELECT runtime_lock_document FROM assessment_sets LIMIT 1")).scalar()
        db.execute(text(
            """
            INSERT INTO material_notes (learner_id, material_id, structure_revision, kind, status, paragraphs,
                                        runtime_lock, created_at, updated_at)
            VALUES (:owner, :material, :revision, 'full', 'ready', '[]', CAST(:lock AS jsonb), :now, :now)
            """
        ), {"owner": owner, "material": material, "revision": revision, "lock": json.dumps(lock), "now": now})
        # 同一段落兩則註記只算一段。
        for anchor in ("p1", "p1", "p2"):
            db.execute(text(
                """
                INSERT INTO note_annotations (annotation_id, learner_id, material_id, kind, structure_revision, anchor,
                                              paragraph_snapshot, body, created_at, updated_at)
                VALUES (:id, :owner, :material, 'full', :revision, :anchor, '{}', '心得', :now, :now)
                """
            ), {"id": uuid4(), "owner": owner, "material": material, "revision": revision, "anchor": anchor, "now": now})
    # 兩集節目（單人主持）都在凌晨 3 點聽完。
    identity = podcasts.create_podcast(owner, material, revision, "合成教學",
        [c["concept_id"] for c in f["document"]["concepts"]], "podcast", delivery="solo", dsn=dsn)["podcast_id"]
    from test_podcasts import complete

    complete(dsn)
    night = datetime.combine(now.date(), datetime.min.time(), TAIPEI) + timedelta(hours=3)
    with database_session(dsn) as db:
        row = db.get(Podcast, identity)
        episodes = deepcopy(row.episodes)
        if len(episodes) == 1:
            episodes.append(deepcopy(episodes[0]))
        row.episodes = episodes
        for index in range(len(episodes)):
            db.execute(text(
                """
                INSERT INTO podcast_episode_progress (podcast_id, episode_index, learner_id, position_seconds,
                                                      completed_at, updated_at)
                VALUES (:podcast, :index, :owner, 1, :at, :at)
                """
            ), {"podcast": identity, "index": index, "owner": owner, "at": night})
        host = episodes[0]["hosts"]["host"]
    for kind in ("notes", "cards", "podcast", "map"):
        record_export(owner, kind, dsn=dsn)
    record_export(owner, "notes", dsn=dsn)

    view, badges = activity(f)
    today = now.date()
    earned = {badge_id for badge_id, item in badges.items() if item["earned"]}
    assert {
        "first_note", "first_annotation", "upload_image", "upload_audio", "upload_video",
        "upload_youtube", "all_media", "first_export", "export_kinds_4", "podcast_series", "night_listener",
    } <= earned
    assert all(badges[badge_id]["earned_on"] == today for badge_id in ("upload_video", "all_media"))
    assert badges["annotations_10"]["progress"] == 2
    assert badges["podcast_hosts_all"]["progress"] == 1 and host
    # 有完成題組、筆記註記與聽完 Podcast，但沒有標記概念卡：全能島主未取得。
    assert not badges["all_tools_material"]["earned"]
    # 模擬內容不可讀：當下計量可減少，但已取得徽章及日期永久保存。
    with database_session(dsn) as db:
        db.execute(text("UPDATE materials SET discard_requested_at = now() WHERE material_id = :m"), {"m": material})
    view, badges = activity(f)
    assert badges["upload_image"]["earned"]
    assert badges["podcast_series"]["earned"] and badges["first_export"]["earned"]


def test_milestone_failure_rolls_back_only_its_savepoint_and_submit_succeeds(closed_loop, monkeypatch, caplog):
    f = concept_fixture(closed_loop, 2)
    learning_goals.set_goal(f["learner"], f["source"].material_id, datetime.now(TAIPEI).date(), 60, dsn=f["dsn"])

    def broken(db, owner, material_id):
        # 先寫入一筆再失敗：savepoint 必須把這筆一起回滾。
        db.execute(text(
            "INSERT INTO goal_milestones (learner_id, material_id, kind, day, created_at) "
            "VALUES (:owner, :material, 'stage', current_date, now())"
        ), {"owner": owner, "material": material_id})
        raise RuntimeError("synthetic failure")

    monkeypatch.setattr(learning_goals, "record_goal_milestones", broken)
    root = create(f)
    finish(f)
    answer(f, root)
    with database_session(f["dsn"]) as db:
        assert db.execute(text("SELECT status FROM assessment_sets WHERE set_id = :id"), {"id": root}).scalar() == "completed"
        assert db.execute(text("SELECT count(*) FROM answer_events")).scalar() > 0
    assert milestones(f) == []
    assert "GOAL_MILESTONE_RECORD_FAILED" in caplog.text and "synthetic failure" not in caplog.text


def test_export_record_failure_does_not_raise(closed_loop, caplog):
    owner, dsn = closed_loop[0].learner_id, closed_loop[4]
    # 不合法的種類違反 CHECK：寫入失敗只記錄錯誤類型，不往外丟。
    record_export(owner, "unknown", dsn=dsn)
    with database_session(dsn) as db:
        assert db.execute(text("SELECT count(*) FROM learner_exports")).scalar() == 0
    assert "EXPORT_RECORD_FAILED kind=unknown" in caplog.text
