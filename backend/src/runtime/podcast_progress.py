"""Podcast 收聽進度：每集播放位置、首次聽完時間與每天實際收聽秒數。

播放位置以最後寫入為準，供跨裝置接續。實際收聽秒數由前端回報「距上次回報
實際播放的牆鐘秒數」（暫停時不計），伺服器只採計不超過距離此學生上一筆採計的
實際經過時間（另加少量寬限）、單次上限與集長的部分，重送或多分頁同時播放不會多算。
聽完要播到片尾，且這一集累積採計的收聽秒數達門檻；只拖到片尾不算。
"""

from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, select, text

from .errors import PodcastError
from .podcasts import _locked
from .storage.database import database_session
from .storage.tables import PodcastEpisodeProgress, PodcastListeningDay

TIMEZONE = ZoneInfo("Asia/Taipei")
# 前端每 15 秒回報一次；離線累積的部分超過上限即不採計，避免一次補報大量秒數。
REPORT_LIMIT_SECONDS = 60
# 計時器與網路延遲造成的誤差寬限。
CLOCK_GRACE_SECONDS = 2
# 播放到距片尾 1 秒內視為播到片尾。
END_TOLERANCE_SECONDS = 1
# 聽完需累積的實際收聽秒數占集長比例；最快 2 倍速從頭聽到尾約為集長一半，留少量誤差。
COMPLETION_LISTENED_RATIO = 0.45


def _durations(row):
    """只有生成完成的節目才有可播放的分集與集長。"""
    if row.status != "ready" or not all(e.get("audio") for e in row.episodes):
        raise PodcastError("RESOURCE_NOT_FOUND")
    return [float(e["audio"]["duration_seconds"]) for e in row.episodes]


def _view(db, podcast_id, credited=0):
    rows = list(db.scalars(
        select(PodcastEpisodeProgress)
        .where(PodcastEpisodeProgress.podcast_id == podcast_id)
        .order_by(PodcastEpisodeProgress.episode_index)
    ))
    current = max(rows, key=lambda r: (r.updated_at, r.episode_index), default=None)
    return {
        "schema": "podcast-progress/v1",
        "podcast_id": podcast_id,
        "current": {
            "episode_index": current.episode_index,
            "position_seconds": current.position_seconds,
            "updated_at": current.updated_at,
        } if current else None,
        "episodes": [
            {
                "episode_index": r.episode_index,
                "position_seconds": r.position_seconds,
                "completed": r.completed_at is not None,
            }
            for r in rows
        ],
        "credited_seconds": credited,
    }


def read_progress(owner, podcast_id, *, dsn=None):
    with database_session(dsn) as db:
        row, _ = _locked(db, owner, podcast_id, read=True)
        _durations(row)
        return _view(db, podcast_id)


def credit_limit(reported, duration, last_credit_at, now):
    """可採計的收聽秒數：不超過回報值、單次上限、集長（0.5 倍速為兩倍）與實際經過時間。"""
    limit = min(reported, REPORT_LIMIT_SECONDS, int(duration * 2) + CLOCK_GRACE_SECONDS)
    if last_credit_at is not None:
        elapsed = (now - last_credit_at).total_seconds() + CLOCK_GRACE_SECONDS
        limit = min(limit, max(0, int(elapsed)))
    return max(0, limit)


def save_progress(owner, podcast_id, episode_index, position_seconds, listened_seconds,
                  ended=False, legacy=False, *, dsn=None, now=None):
    now = now or datetime.now(TIMEZONE)
    with database_session(dsn) as db:
        row, _ = _locked(db, owner, podcast_id, read=True)
        durations = _durations(row)
        if not 0 <= episode_index < len(durations):
            raise PodcastError("REQUEST_INVALID")
        duration = durations[episode_index]
        if position_seconds > duration + END_TOLERANCE_SECONDS:
            raise PodcastError("REQUEST_INVALID")
        # 同一學生的回報依序處理，才能正確比對距上一筆採計的經過時間。
        db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                   {"key": f"podcast-listening:{owner}"})
        if legacy:
            # 舊版只在瀏覽器保存位置；伺服器已有紀錄時以伺服器為準，且不採計收聽秒數。
            if listened_seconds or ended:
                raise PodcastError("REQUEST_INVALID")
            exists = db.scalar(select(func.count()).select_from(PodcastEpisodeProgress)
                               .where(PodcastEpisodeProgress.podcast_id == podcast_id))
            if not exists:
                db.add(PodcastEpisodeProgress(
                    podcast_id=podcast_id, episode_index=episode_index, learner_id=owner,
                    position_seconds=position_seconds, completed_at=None, updated_at=now,
                ))
                db.flush()
            return _view(db, podcast_id)
        progress = db.get(PodcastEpisodeProgress, (podcast_id, episode_index), with_for_update=True)
        if progress is None:
            progress = PodcastEpisodeProgress(
                podcast_id=podcast_id, episode_index=episode_index, learner_id=owner, completed_at=None,
            )
            db.add(progress)
        progress.position_seconds = min(position_seconds, duration)
        progress.updated_at = now
        credited = 0
        if listened_seconds > 0:
            last = db.scalar(select(func.max(PodcastListeningDay.updated_at))
                             .where(PodcastListeningDay.learner_id == owner))
            credited = credit_limit(listened_seconds, duration, last, now)
        if credited:
            day = now.astimezone(TIMEZONE).date()
            record = db.get(PodcastListeningDay, (podcast_id, episode_index, day), with_for_update=True)
            if record is None:
                record = PodcastListeningDay(
                    podcast_id=podcast_id, episode_index=episode_index, listened_on=day,
                    learner_id=owner, listened_seconds=0,
                )
                db.add(record)
            record.listened_seconds = min(86400, record.listened_seconds + credited)
            record.updated_at = now
        db.flush()
        if progress.completed_at is None and (ended or position_seconds >= duration - END_TOLERANCE_SECONDS):
            listened = db.scalar(
                select(func.coalesce(func.sum(PodcastListeningDay.listened_seconds), 0))
                .where(PodcastListeningDay.podcast_id == podcast_id,
                       PodcastListeningDay.episode_index == episode_index)
            )
            if listened >= duration * COMPLETION_LISTENED_RATIO:
                progress.completed_at = now
                db.flush()
        return _view(db, podcast_id, credited)


def forget_positions(db, podcast_id):
    """Podcast 軟刪除時移除播放位置；每日收聽紀錄是學習活動，保留到實際刪除。"""
    db.execute(delete(PodcastEpisodeProgress).where(PodcastEpisodeProgress.podcast_id == podcast_id))

