"""Podcast 收聽進度的真 API／DB：接續位置、舊進度上傳、防重送灌水、刪除與帳號隔離。"""
from copy import deepcopy
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import psycopg
import pytest
from fastapi.testclient import TestClient

from product_fixtures import HEADERS, ORIGIN, _app, closed_loop
from runtime import podcast_progress, podcasts
from runtime.errors import PodcastError
from runtime.learner_session import register_account
from runtime.material_discard import request_material_discard
from runtime.storage.database import database_session
from runtime.storage.tables import Podcast
from test_podcasts import complete, create

TAIPEI = ZoneInfo("Asia/Taipei")


def ready_podcast(fixture, seconds=600):
    """合成音訊只有 1 秒；把集長改成 seconds，才能測試位置與收聽上限。"""
    identity = create(fixture)["podcast_id"]
    complete(fixture[4])
    with database_session(fixture[4]) as db:
        row = db.get(Podcast, identity)
        episodes = deepcopy(row.episodes)
        for episode in episodes:
            episode["audio"]["duration_seconds"] = seconds
        row.episodes = episodes
    return identity


def days(dsn):
    with psycopg.connect(dsn) as db:
        return db.execute(
            "SELECT listened_on, listened_seconds FROM podcast_listening_days ORDER BY listened_on"
        ).fetchall()


def test_positions_resume_across_devices_and_mark_finished_episodes(closed_loop):
    owner, _, _, _, dsn, _ = closed_loop
    learner = owner.learner_id
    identity = ready_podcast(closed_loop)
    empty = podcast_progress.read_progress(learner, identity, dsn=dsn)
    assert (empty["current"], empty["episodes"]) == (None, [])
    now = datetime(2026, 10, 22, 9, 0, tzinfo=TAIPEI)
    saved = podcast_progress.save_progress(learner, identity, 0, 42.5, 15, dsn=dsn, now=now)
    assert saved["credited_seconds"] == 15
    assert saved["current"]["episode_index"] == 0 and saved["current"]["position_seconds"] == 42.5
    # 另一台裝置開啟時讀到同一個位置。
    assert podcast_progress.read_progress(learner, identity, dsn=dsn)["current"]["position_seconds"] == 42.5
    # 直接拖到片尾：這集只實際聽了 25 秒，不算聽完。
    skipped = podcast_progress.save_progress(learner, identity, 0, 599.5, 10, dsn=dsn, now=now + timedelta(seconds=10))
    assert skipped["episodes"] == [{"episode_index": 0, "position_seconds": 599.5, "completed": False}]
    # 實際播放累積到集長 45%（270 秒）後再播到片尾才算聽完。
    for step in range(1, 5):
        podcast_progress.save_progress(learner, identity, 0, 60.0 * step, 60, dsn=dsn,
                                       now=now + timedelta(seconds=10 + 60 * step))
    finished = podcast_progress.save_progress(learner, identity, 0, 599.5, 5, ended=True, dsn=dsn,
                                              now=now + timedelta(seconds=300))
    assert finished["episodes"] == [{"episode_index": 0, "position_seconds": 599.5, "completed": True}]
    # 聽完後重新從頭聽，聽完紀錄保留。
    again = podcast_progress.save_progress(learner, identity, 0, 3, 0, dsn=dsn, now=now + timedelta(seconds=310))
    assert again["episodes"][0]["completed"] is True and again["current"]["position_seconds"] == 3
    with pytest.raises(PodcastError, match="^REQUEST_INVALID$"):
        podcast_progress.save_progress(learner, identity, 0, 700, 0, dsn=dsn)
    with pytest.raises(PodcastError, match="^REQUEST_INVALID$"):
        podcast_progress.save_progress(learner, identity, 99, 1, 0, dsn=dsn)


def test_replayed_and_parallel_reports_cannot_inflate_listening(closed_loop):
    owner, _, _, _, dsn, _ = closed_loop
    learner = owner.learner_id
    identity = ready_podcast(closed_loop)
    now = datetime(2026, 10, 22, 9, 0, tzinfo=TAIPEI)
    assert podcast_progress.save_progress(learner, identity, 0, 15, 15, dsn=dsn, now=now)["credited_seconds"] == 15
    # 同一筆回報立刻重送：只剩 2 秒寬限。
    assert podcast_progress.save_progress(learner, identity, 0, 15, 15, dsn=dsn, now=now)["credited_seconds"] == 2
    # 15 秒後正常回報。
    assert podcast_progress.save_progress(learner, identity, 0, 30, 15, dsn=dsn, now=now + timedelta(seconds=15))["credited_seconds"] == 15
    # 離線很久後一次補報大量秒數：只採計單次上限。
    later = now + timedelta(hours=2)
    assert podcast_progress.save_progress(learner, identity, 0, 90, 1800, dsn=dsn, now=later)["credited_seconds"] == 60
    assert days(dsn) == [(now.date(), 92)]
    # 台灣日期跨日分開累計。
    midnight = datetime(2026, 10, 23, 0, 0, 10, tzinfo=TAIPEI)
    podcast_progress.save_progress(learner, identity, 0, 100, 10, dsn=dsn, now=midnight)
    assert days(dsn) == [(now.date(), 92), (midnight.date(), 10)]


def test_legacy_position_only_fills_an_empty_record(closed_loop):
    owner, _, _, _, dsn, _ = closed_loop
    learner = owner.learner_id
    identity = ready_podcast(closed_loop)
    uploaded = podcast_progress.save_progress(learner, identity, 0, 120, 0, legacy=True, dsn=dsn)
    assert uploaded["current"]["position_seconds"] == 120 and uploaded["credited_seconds"] == 0
    assert days(dsn) == []
    podcast_progress.save_progress(learner, identity, 0, 200, 0, dsn=dsn)
    # 伺服器已有紀錄時，另一個瀏覽器的舊進度不覆蓋。
    assert podcast_progress.save_progress(learner, identity, 0, 10, 0, legacy=True, dsn=dsn)["current"]["position_seconds"] == 200
    with pytest.raises(PodcastError, match="^REQUEST_INVALID$"):
        podcast_progress.save_progress(learner, identity, 0, 10, 5, legacy=True, dsn=dsn)


def test_delete_removes_positions_and_discard_removes_listening(closed_loop):
    owner, source, _, _, dsn, _ = closed_loop
    learner = owner.learner_id
    identity = ready_podcast(closed_loop)
    podcast_progress.save_progress(learner, identity, 0, 30, 15, dsn=dsn)
    podcasts.delete_podcast(learner, identity, dsn=dsn)
    with pytest.raises(PodcastError, match="^RESOURCE_NOT_FOUND$"):
        podcast_progress.read_progress(learner, identity, dsn=dsn)
    with psycopg.connect(dsn) as db:
        assert db.execute("SELECT count(*) FROM podcast_episode_progress").fetchone() == (0,)
        # 收聽紀錄是學習活動，軟刪除後仍保留。
        assert db.execute("SELECT count(*) FROM podcast_listening_days").fetchone() == (1,)
    assert request_material_discard(learner, source.material_id, dsn=dsn) == "removed"
    with psycopg.connect(dsn) as db:
        assert db.execute("SELECT count(*) FROM podcast_listening_days").fetchone() == (0,)


def test_api_boundaries_and_owner_isolation(closed_loop, tmp_path, monkeypatch):
    _, _, _, _, dsn, token = closed_loop
    identity = ready_podcast(closed_loop)
    client = TestClient(_app(dsn, tmp_path, monkeypatch), base_url=ORIGIN)
    url = f"/v1/podcasts/{identity}/progress"
    body = {"schema": "podcast-progress-update/v1", "episode_index": 0, "position_seconds": 12.5, "listened_seconds": 10}
    assert client.get(url).status_code == 401
    assert client.post(url, headers=HEADERS, json=body).status_code == 401
    client.cookies.set("studydy_session", token)
    assert client.post(url, json=body).status_code == 403
    for change in [{"episode_index": -1}, {"position_seconds": -1}, {"listened_seconds": 1.5},
                   {"listened_seconds": 99999}, {"schema": "x"}, {"extra": 1}]:
        assert client.post(url, headers=HEADERS, json={**body, **change}).status_code == 400, change
    saved = client.post(url, headers=HEADERS, json=body)
    assert saved.status_code == 200, saved.json()
    assert saved.json()["schema"] == "podcast-progress/v1" and saved.json()["credited_seconds"] == 10
    read = client.get(url)
    assert read.status_code == 200 and read.json()["current"]["position_seconds"] == 12.5
    assert client.get(url, params={"x": "1"}).status_code == 400

    other = register_account("progress-other@example.com", "Synthetic other password 42", dsn=dsn)
    client.cookies.set("studydy_session", other.raw_token)
    assert client.get(url).status_code == 404
    assert client.post(url, headers=HEADERS, json=body).status_code == 404
