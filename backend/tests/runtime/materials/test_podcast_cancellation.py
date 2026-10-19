"""隔離 DB → 真 worker／HTTP → 子程序：取消後下一筆工作必須能繼續。"""
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
import sys

import pytest
from sqlalchemy import select

from product_fixtures import closed_loop
from materials.test_podcasts import create, script, wav
from runtime import podcasts, podcast_process
from functools import partial
from runtime.podcast_worker import step
from runtime.workers import BackgroundWorker
from runtime.storage.tables import Podcast, PodcastScenes, PodcastVideo
from runtime.storage.database import database_session
from test_podcast_process import provider_server, provider, wait_until, alive
from runtime.generation_jobs import cancellable_job


@pytest.mark.parametrize('action', ['cancel', 'delete'])
@pytest.mark.parametrize('stage', ['script', 'audio'])
def test_cancel_or_delete_running_job_kills_child_and_unblocks_next(closed_loop, provider_server, monkeypatch, tmp_path, action, stage):
    owner, _, _, _, dsn, _ = closed_loop
    identity = create(closed_loop)['podcast_id']
    if stage == 'audio':
        claimed = podcasts.claim_step(dsn=dsn)
        podcasts.finish_step(claimed, script=script(claimed), dsn=dsn)
    next_id = create(closed_loop, key='next-job')['podcast_id']
    marker = tmp_path / 'started'
    blocked = False

    def wait_first(kind, cancelled):
        nonlocal blocked
        if kind == stage and not blocked:
            blocked = True
            podcast_process.run([sys.executable, '-c',
                'import os,time,pathlib,sys;pathlib.Path(sys.argv[1]).write_text(str(os.getpid()));time.sleep(60)', str(marker)],
                timeout=65, cancelled=cancelled)

    def generate_script(body, *, cancelled=None):
        wait_first('script', cancelled)
        return script({'episode': {'claims': body['claims']}})

    def generate_audio(body, *, cancelled=None, **kwargs):
        wait_first('audio', cancelled)
        return wav(), None

    monkeypatch.setattr(provider, 'script', generate_script)
    monkeypatch.setattr(provider, 'audio', generate_audio)
    worker = BackgroundWorker(
        partial(step, dsn=dsn), name="test-podcast", error_code="PODCAST_WORKER_FAILED"
    ).start()
    try:
        wait_until(lambda: marker.exists() and marker.stat().st_size)
        pid = int(marker.read_text())
        view = podcasts.read_podcast(owner.learner_id, identity, dsn=dsn)
        if action == 'cancel':
            podcasts.change_podcast(owner.learner_id, identity, 'cancel', view['version'], dsn=dsn)
        else:
            podcasts.delete_podcast(owner.learner_id, identity, dsn=dsn)
        wait_until(lambda: not alive(pid), timeout=4)
        wait_until(lambda: podcasts.read_podcast(owner.learner_id, next_id, dsn=dsn)['status'] == 'ready', timeout=8)
        with database_session(dsn) as db:
            row = db.get(Podcast, identity)
            assert row.status == 'deleted'
            assert row.lease_token is None
            assert not any(episode.get('audio') for episode in row.episodes)
    finally:
        with database_session(dsn) as db:
            for row in db.scalars(select(Podcast).where(Podcast.status.in_(['pending', 'running']))):
                row.status = 'cancelled'
                row.lease_token = row.lease_expires_at = None
        worker.stop()
        worker.thread.join(timeout=7)
        assert not worker.thread.is_alive()


@pytest.mark.parametrize('all_audio_ready', [False, True])
def test_cancel_deletes_completed_media_and_all_child_jobs_but_keeps_material(closed_loop, all_audio_ready):
    owner, _, _, _, dsn, _ = closed_loop
    identity = create(closed_loop)['podcast_id']
    with database_session(dsn) as db:
        row = db.get(Podcast, identity)
        row.episodes = [deepcopy(row.episodes[0]), deepcopy(row.episodes[0])]
    claimed = podcasts.claim_step(dsn=dsn)
    segments = []
    for index, claim in enumerate(claimed['episode']['claims']):
        text = claim['text']
        ref = {'source_index': index, 'evidence_ids': [e['evidence_id'] for e in claim['evidence']]}
        segments.append({'beat_id': f'beat-{index}', 'title': '合成重點', 'turns': [
            {'speaker': 'host', 'text': text, 'parts': [{'text': text, 'source_refs': [ref]}]}]})
    podcasts.finish_step(claimed, script={'schema':'podcast-script/v2','provider':'synthetic','segments':segments}, dsn=dsn)
    claimed = podcasts.claim_step(dsn=dsn)
    podcasts.finish_step(claimed, audio=wav(), audio_provider='synthetic', dsn=dsn)
    if all_audio_ready:
        from materials.test_podcasts import complete
        complete(dsn)
    view = podcasts.read_podcast(owner.learner_id, identity, dsn=dsn)
    assert view['generation']['active'] is True
    assert view['generation']['completed_videos'] == 0
    assert (view['status'] == 'ready') is all_audio_ready
    material_id = view['material_id']
    deleted = podcasts.change_podcast(owner.learner_id, identity, 'cancel', view['version'], dsn=dsn)
    assert deleted == {'schema':'podcast-deleted/v1','podcast_id':identity}
    with database_session(dsn) as db:
        from runtime.storage.tables import Material, Artifact
        for child in (PodcastScenes, PodcastVideo):
            assert db.scalar(select(child).where(child.podcast_id == identity)) is None
        assert db.get(Material, material_id).discard_requested_at is None
        assert not list(db.scalars(select(Artifact).where(Artifact.material_id == material_id, Artifact.kind == 'podcast_audio')))
    assert podcasts.list_podcasts(owner.learner_id, dsn=dsn)['podcasts'] == []


@pytest.mark.parametrize('expired', [False, True])
def test_audio_lease_tracks_worker_liveness_not_generation_duration(closed_loop, provider_server, expired):
    owner, _, _, _, dsn, _ = closed_loop
    identity = create(closed_loop)['podcast_id']
    claimed = podcasts.claim_step(dsn=dsn)
    podcasts.finish_step(claimed, script=script(claimed), dsn=dsn)
    claimed = podcasts.claim_step(dsn=dsn)
    old_expiry = datetime.now(UTC) + timedelta(seconds=-1 if expired else 4)
    with database_session(dsn) as db:
        db.get(Podcast, identity).lease_expires_at = old_expiry

    def read_lease():
        with database_session(dsn) as db:
            row = db.get(Podcast, identity)
            return row.status, row.lease_token, row.lease_expires_at

    with cancellable_job(Podcast, identity, claimed['token'], dsn=dsn) as metadata:
        with podcast_process.job(metadata) as cancelled:
            if expired:
                wait_until(cancelled.is_set)
                assert read_lease()[2] == old_expiry  # 不復活已失去執行權的 worker。
            else:
                wait_until(lambda: read_lease()[2] > old_expiry + timedelta(minutes=10))
                wait_until(lambda: datetime.now(UTC) > old_expiry)
                status, token, expiry = read_lease()
                assert status == 'running' and token == claimed['token'] and expiry > datetime.now(UTC)
                assert not cancelled.is_set()
                podcasts.delete_podcast(owner.learner_id, identity, dsn=dsn)
                wait_until(cancelled.is_set)
                assert read_lease() == ('deleted', None, None)


@pytest.mark.parametrize('model', [PodcastVideo, PodcastScenes])
def test_child_lease_renews_and_delete_still_cancels(closed_loop, provider_server, model):
    from materials.test_podcasts import complete
    from uuid import UUID, uuid4
    owner, _, _, _, dsn, _ = closed_loop
    identity = create(closed_loop)['podcast_id']
    complete(dsn)
    token = uuid4()
    old_expiry = datetime.now(UTC) + timedelta(seconds=3)
    with database_session(dsn) as db:
        row = db.scalar(select(model).where(model.podcast_id == identity))
        if row is None:
            row = PodcastScenes(scene_id=uuid4(),podcast_id=identity,episode_index=0,input_sha256='a'*64,status='pending',version=1,created_at=datetime.now(UTC))
            db.add(row)
        row.status = 'running'
        row.lease_token = token
        row.lease_expires_at = old_expiry
        child_id = getattr(row, model.__mapper__.primary_key[0].name)
    def expiry():
        with database_session(dsn) as db:
            return db.get(model, child_id).lease_expires_at
    with cancellable_job(model, child_id, token, dsn=dsn) as metadata:
        with podcast_process.job(metadata) as cancelled:
            wait_until(lambda: expiry() > old_expiry + timedelta(minutes=10))
            wait_until(lambda: datetime.now(UTC) > old_expiry)
            assert not cancelled.is_set()
            podcasts.delete_podcast(owner.learner_id, identity, dsn=dsn)
            wait_until(cancelled.is_set)


def test_preview_requires_all_media_and_retry_preserves_ready_episodes(closed_loop):
    from materials.test_podcasts import complete
    owner, _, _, _, dsn, _ = closed_loop
    identity = create(closed_loop)['podcast_id']
    with database_session(dsn) as db:
        row = db.get(Podcast, identity)
        row.episodes = [deepcopy(row.episodes[0]) for _ in range(3)]
    for _ in range(3): complete(dsn)
    view = podcasts.read_podcast(owner.learner_id, identity, dsn=dsn)
    original_audio = [e['audio'] for e in view['episodes']]
    assert view['status'] == 'ready' and not view['generation']['preview_ready']
    with database_session(dsn) as db:
        rows = list(db.scalars(select(PodcastVideo).where(PodcastVideo.podcast_id == identity).order_by(PodcastVideo.episode_index)))
        rows[0].status = 'ready'
        from uuid import UUID
        rows[0].artifact_id = UUID(original_audio[0]['artifact_id'])
        rows[0].manifest = {'synthetic': True}
        rows[1].status = 'failed'; rows[1].error_code = 'LUNA_GENERATION_TIMEOUT'
        db.delete(rows[2])  # 既有音訊缺少影片工作的情況。
        ready_version = rows[0].version
    view = podcasts.read_podcast(owner.learner_id, identity, dsn=dsn)
    assert view['generation']['completed_videos'] == 1
    assert view['generation']['retryable_videos'] and not view['generation']['preview_ready']
    podcasts.change_podcast(owner.learner_id, identity, 'retry', view['version'], dsn=dsn)
    with database_session(dsn) as db:
        rows = list(db.scalars(select(PodcastVideo).where(PodcastVideo.podcast_id == identity).order_by(PodcastVideo.episode_index)))
        assert [r.status for r in rows] == ['ready', 'pending', 'pending']
        assert rows[0].version == ready_version
        assert [e['audio'] for e in db.get(Podcast, identity).episodes] == original_audio
        for row in rows:
            row.status = 'ready'
            row.artifact_id = UUID(original_audio[row.episode_index]['artifact_id'])
            row.manifest = {'synthetic': True}
    assert podcasts.read_podcast(owner.learner_id, identity, dsn=dsn)['generation']['preview_ready']
