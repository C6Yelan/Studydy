"""由實際工作列與 provider 鎖狀態投影分集步驟，不估計排隊順序。"""
from datetime import UTC, datetime
import json
from urllib.request import urlopen

from .errors import SourceError
from .provider_http import provider_request


def live_progress(rows):
    jobs = [{"job_id": str(identity), "job_token": str(token)} for identity, token in rows if token]
    if not jobs:
        return {}
    try:
        request = provider_request('/jobs/progress', {"jobs": jobs}, unavailable=SourceError('PODCAST_PROVIDER_UNAVAILABLE'))
        with urlopen(request, timeout=1) as response:
            result = json.load(response)
        return {(item['job_id'], item['job_token']): item for item in result['jobs']}
    except (OSError, ValueError, KeyError, TypeError, SourceError):
        # 不可觀測 provider 時保留排隊狀態，不猜正在使用 GPU 或排隊人數。
        return {}


def steps(row, children):
    live = children['live']
    scenes = {child.episode_index: child for child in children['scene_rows']}
    videos = {child.episode_index: child for child in children['video_rows']}
    current = next((i for i, episode in enumerate(row.episodes) if not episode.get('audio')), None)
    result = []
    now = datetime.now(UTC)
    for index, episode in enumerate(row.episodes):
        for step in ('script', 'audio', 'alignment', 'video'):
            if step == 'video' and not row.video_enabled and index not in videos:
                continue
            started = finished = None
            status = 'waiting'
            job_id = token = None
            if step in ('script', 'audio'):
                timing = episode.get('step_times', {}).get(step, {})
                started, finished = timing.get('started_at'), timing.get('finished_at')
                active_step = 'audio' if episode.get('script') else 'script'
                if episode.get(step):
                    status = 'completed'
                elif index == current and step == active_step:
                    # 講稿在 backend 依 runtime lock 生成；配音在 provider 排隊，實際狀態由 live progress 覆寫。
                    status = {'pending': 'queued', 'running': 'running' if step == 'script' else 'queued',
                              'failed': 'failed', 'cancelled': 'skipped'}.get(row.status, 'waiting')
                    if row.status == 'running':
                        job_id, token = row.podcast_id, row.lease_token
            else:
                child = (scenes if step == 'alignment' else videos).get(index)
                if child is not None:
                    started, finished = child.started_at, child.finished_at
                    if step == 'alignment' and child.alignment:
                        status = 'completed'
                    elif step == 'alignment' and row.status != 'ready' and child.status == 'pending':
                        status = 'waiting'
                    else:
                        status = {'pending': 'queued', 'running': 'queued', 'ready': 'completed',
                                  'failed': 'failed', 'cancelled': 'skipped', 'removed': 'skipped'}.get(child.status, 'waiting')
                    if child.status == 'running' and status != 'completed':
                        job_id = child.scene_id if step == 'alignment' else child.video_id
                        token = child.lease_token
            if job_id is not None and token is not None:
                observed = live.get((str(job_id), str(token)))
                if observed:
                    status = observed['status']
            if isinstance(started, str):
                started = datetime.fromisoformat(started)
            if isinstance(finished, str):
                finished = datetime.fromisoformat(finished)
            elapsed = ((finished or now) - started).total_seconds() if started and (finished or status in ('queued', 'running')) else None
            result.append({'episode_index': index, 'step': step, 'status': status,
                           'started_at': started, 'elapsed_seconds': max(0, elapsed) if elapsed is not None else None,
                           'queue_ahead': None})
    return result
