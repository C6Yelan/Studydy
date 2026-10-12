"""慢工作持續續租，取消仍可終止；全部使用隔離 DB 與合成資料。"""
from datetime import UTC, datetime, timedelta
from uuid import uuid4
import pytest
from product_fixtures import closed_loop
from runtime import voice, topics, research, podcast_process
from runtime.generation_jobs import cancellable_job
from runtime.storage.tables import VoiceTurn, TopicScope, MaterialResearch, database_session
from test_podcast_process import provider_server, wait_until


@pytest.mark.parametrize('kind', ['voice', 'topic', 'research'])
def test_live_job_outlasts_initial_lease_without_reclaim_and_remains_cancellable(closed_loop, provider_server, kind):
    owner, material, _, _, dsn, _ = closed_loop
    if kind == 'voice':
        conversation = voice.create(owner.learner_id, material.material_id, 'slow', dsn=dsn)
        voice.add_turn(owner.learner_id, conversation['conversation_id'], 'slow', '合成問題', dsn=dsn)
        state = voice.claim(dsn=dsn)
        model, identity, status, token = VoiceTurn, state['turn_id'], state['status'], state['lease_token']
        reclaim = lambda: voice.claim(dsn=dsn)
    elif kind == 'topic':
        state = topics.create(owner.learner_id, '合成主題', 'slow', dsn=dsn)
        model, identity, status, token = TopicScope, state['topic_id'], 'pending', uuid4()
        reclaim = lambda: topics.step(dsn=dsn)
    else:
        research.create(owner.learner_id, material.material_id, '合成搜尋', 'review', 'slow', dsn=dsn)
        state = research.claim(dsn=dsn)
        model, identity, status, token = MaterialResearch, state['research_id'], state['status'], state['lease_token']
        reclaim = lambda: research.claim(dsn=dsn)
    original_expiry = datetime.now(UTC) + timedelta(seconds=2)
    with database_session(dsn) as db:
        row = db.get(model, identity)
        row.lease_token = token
        row.lease_expires_at = original_expiry
    def expiry():
        with database_session(dsn) as db: return db.get(model, identity).lease_expires_at
    with cancellable_job(model, identity, token, dsn=dsn, statuses=(status,)) as metadata:
        with podcast_process.job(metadata) as cancelled:
            wait_until(lambda: expiry() > original_expiry + timedelta(minutes=10))
            wait_until(lambda: datetime.now(UTC) > original_expiry)
            assert not reclaim() and not cancelled.is_set()
            with database_session(dsn) as db:
                row = db.get(model, identity)
                row.status = 'cancelled'
                row.lease_token = row.lease_expires_at = None
            wait_until(cancelled.is_set)
