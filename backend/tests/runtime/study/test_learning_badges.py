"""badge-rules/v7／xp-rules/v4 的真 DB：交卷記錄分天關卡與準時達標、既有資料回溯推導，以及 0021 升級。"""
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
from runtime.storage.migrations import DEFAULT_MIGRATIONS_DIR, run_migrations
from runtime.storage.tables import Podcast
from test_starting_level import _client, _finish_cross, _preference, _start

TAIPEI = ZoneInfo("Asia/Taipei")
# 本功能的 migration 編號；合併時若改號，這裡與檔名一起改。
BADGE_MIGRATION = 21


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
    # 起始程度診斷（真 API 流程）。
    client = _client(f, tmp_path, monkeypatch)
    _preference(client, f, "familiar")
    launched = _start(client, f)
    assert launched.status_code == 200, launched.json()
    _finish_cross(f)
    answer(f, UUID(launched.json()["selected_set_id"]))
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
        "first_diagnostic", "first_note", "first_annotation", "upload_image", "upload_audio", "upload_video",
        "upload_youtube", "all_media", "first_export", "export_kinds_4", "podcast_series", "night_listener",
    } <= earned
    assert all(badges[badge_id]["earned_on"] == today for badge_id in ("first_diagnostic", "upload_video", "all_media"))
    assert badges["annotations_10"]["progress"] == 2
    assert badges["podcast_hosts_6"]["progress"] == 1 and host
    assert badges["diagnostics_5"]["progress"] == 1
    assert view["xp"]["sources"]["diagnostics"] == 20
    # 有完成題組、筆記註記與聽完 Podcast，但沒有標記概念卡：全能島主未取得。
    assert not badges["all_tools_material"]["earned"]
    # 刪除教材後相關紀錄不再計入（匯出紀錄不屬於教材，保留）。
    with database_session(dsn) as db:
        db.execute(text("UPDATE materials SET discard_requested_at = now() WHERE material_id = :m"), {"m": material})
    view, badges = activity(f)
    assert not badges["first_diagnostic"]["earned"] and not badges["upload_image"]["earned"]
    assert not badges["podcast_series"]["earned"] and badges["first_export"]["earned"]
    assert view["xp"]["sources"]["diagnostics"] == 0


@pytest.fixture
def migrations_dir(request, tmp_path):
    last = getattr(request, "param", None)
    if last is None:
        return DEFAULT_MIGRATIONS_DIR
    target = tmp_path / f"migrations-{last:04d}"
    target.mkdir()
    for source in DEFAULT_MIGRATIONS_DIR.glob("*.sql"):
        if int(source.name[:4]) <= last:
            (target / source.name).write_bytes(source.read_bytes())
    return target


@pytest.fixture
def skip_goal_rebase(monkeypatch):
    """升級前的資料表沒有 created_at，ORM 不能讀目標；合成教材發布時還沒有任何目標，跳過發布時的目標重排。"""
    monkeypatch.setattr(learning_goals, "rebase_goal_for_revision", lambda *args: None)


@pytest.mark.parametrize("migrations_dir", [BADGE_MIGRATION - 1], indirect=True)
def test_migration_backfills_goal_created_at_without_changing_rows(skip_goal_rebase, closed_loop):
    f = concept_fixture(closed_loop, 2)
    baseline = datetime(2026, 10, 20, 9, tzinfo=TAIPEI)
    with database_session(f["dsn"]) as db:
        db.execute(text(
            """
            INSERT INTO learning_goals (material_id, learner_id, target_date, target_percent, baseline_at,
                baseline_revision, baseline_assessable_points, baseline_completed_points,
                baseline_unavailable_claim_ids, notice_revision, notice_points, daily_date,
                daily_unavailable_claim_ids, daily_completed_points, daily_quota_points)
            VALUES (:material, :owner, '2026-12-31', 80, :at, :revision, 3, 0, '{}', :revision, 3,
                    '2026-10-20', '{}', 0, 1)
            """
        ), {"material": f["source"].material_id, "owner": f["learner"].learner_id, "at": baseline,
            "revision": f["document"]["revision"]})
        before = db.execute(text("SELECT row_to_json(g)::jsonb FROM learning_goals g")).scalar()
    assert run_migrations(f["dsn"]) == (BADGE_MIGRATION,)
    with database_session(f["dsn"]) as db:
        after = db.execute(text("SELECT row_to_json(g)::jsonb FROM learning_goals g")).scalar()
        assert {key: value for key, value in after.items() if key != "created_at"} == before
        assert db.execute(text("SELECT created_at = baseline_at FROM learning_goals")).scalar()
        assert db.execute(text("SELECT count(*) FROM goal_milestones")).scalar() == 0
        assert db.execute(text("SELECT count(*) FROM learner_exports")).scalar() == 0
    _, badges = activity(f)
    assert badges["first_goal"]["earned_on"] == baseline.date()
    assert run_migrations(f["dsn"]) == ()
