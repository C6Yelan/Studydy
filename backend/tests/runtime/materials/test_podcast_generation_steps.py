from uuid import uuid4
from datetime import UTC, datetime
from threading import Lock

from product_fixtures import closed_loop
from materials.test_podcasts import create, script, wav
from materials.test_podcast_scenes import alignment
from runtime import podcasts, podcast_scenes, podcast_process
from runtime.storage.database import database_session
from runtime.storage.tables import PodcastVideo


def test_generation_steps_follow_queue_lock_and_published_results(closed_loop, monkeypatch):
    from runtime import podcast_generation
    owner, _, _, _, dsn, _ = closed_loop
    identity=create(closed_loop, video=True)['podcast_id']
    def view():
        return podcasts.read_podcast(owner.learner_id, identity, dsn=dsn)['generation']['steps']
    assert [s['status'] for s in view()]==['queued','waiting','waiting','waiting']
    state=podcasts.claim_step(dsn=dsn)
    assert view()[0]['status']=='queued' and view()[0]['started_at']
    metadata={'job_id':str(identity),'job_token':str(state['token'])}
    # 使用真 provider 工作註冊表，僅替換 HTTP 傳輸。
    def local_progress(jobs):
        values=podcast_process.progress({'jobs':[{'job_id':str(i),'job_token':str(t)} for i,t in jobs if t]})
        return {(j['job_id'],j['job_token']):j for j in values['jobs']}
    monkeypatch.setattr(podcast_generation,'live_progress',local_progress)
    with podcast_process.tracked_lock(Lock(), None, metadata, '/script'):
        assert view()[0]['status']=='running'
    claims=state['episode']['claims']
    verified={'schema':'podcast-script/v2','provider':'synthetic-test','segments':[
        {'beat_id':f'beat-{i}','title':'已核對重點','turns':[{'speaker':'host','text':claim['text'],
          'parts':[{'text':claim['text'],'source_refs':[{'source_index':i,'evidence_ids':[e['evidence_id'] for e in claim['evidence']]}]}]}]}
        for i,claim in enumerate(claims)]}
    podcasts.finish_step(state, script=verified, dsn=dsn)
    assert [s['status'] for s in view()]==['completed','queued','waiting','waiting']
    assert view()[0]['elapsed_seconds']>=0
    state=podcasts.claim_step(dsn=dsn)
    podcasts.finish_step(state, audio=wav(), audio_provider='synthetic-test', dsn=dsn)
    assert [s['status'] for s in view()]==['completed','completed','queued','queued']
    scene=podcast_scenes.claim(dsn=dsn)
    podcast_scenes.save_alignment(scene, alignment(scene), dsn=dsn)
    assert view()[2]['status']=='completed' and view()[2]['elapsed_seconds']>=0
    with database_session(dsn) as db:
        video=db.query(PodcastVideo).filter(PodcastVideo.podcast_id==identity).one()
        video.status='failed';video.started_at=video.finished_at=datetime.now(UTC)
    assert view()[3]['status']=='failed'
    assert all(s['queue_ahead'] is None for s in view())


def test_legacy_script_does_not_automatically_enqueue_alignment(closed_loop):
    owner, _, _, _, dsn, _=closed_loop
    identity=create(closed_loop)['podcast_id']
    state=podcasts.claim_step(dsn=dsn)
    podcasts.finish_step(state,script=script(state),dsn=dsn)
    podcasts.finish_step(podcasts.claim_step(dsn=dsn),audio=wav(),audio_provider='synthetic-test',dsn=dsn)
    steps=podcasts.read_podcast(owner.learner_id,identity,dsn=dsn)['generation']['steps']
    assert next(s for s in steps if s['step']=='alignment')['status']=='waiting'


def test_omitted_content_survives_script_audio_and_api(closed_loop):
    owner, _, _, _, dsn, _=closed_loop
    identity=create(closed_loop)['podcast_id']
    state=podcasts.claim_step(dsn=dsn)
    claim=state['episode']['claims'][0]
    value={'schema':'podcast-script/v2','provider':'synthetic-test','segments':[
        {'beat_id':'beat-0','title':'核對通過','turns':[{'speaker':'host','text':'已核對的內容。','parts':[
            {'text':'已核對的內容。','source_refs':[{'source_index':0,'evidence_ids':[e['evidence_id'] for e in claim['evidence']]}]}]}]}],
        'omitted_content':[{'segment_index':0,'claim_ids':[],'reason':'source_check_failed'}]}
    assert podcasts.finish_step(state,script=value,dsn=dsn)
    audio_state=podcasts.claim_step(dsn=dsn)
    assert 'omitted_content' not in audio_state['episode']['script']
    podcasts.finish_step(audio_state,audio=wav(),audio_provider='synthetic-test',dsn=dsn)
    episode=podcasts.read_podcast(owner.learner_id,identity,dsn=dsn)['episodes'][0]
    assert episode['omitted_content']==value['omitted_content']
    assert episode['script']['segments']==value['segments']


def test_step_time_migration_applies_and_rolls_back(clean_database_dsn, migrations_dir, tmp_path):
    import shutil
    import psycopg
    from runtime.storage.migrations import load_migrations,run_migrations
    previous=tmp_path/'previous';previous.mkdir()
    for path in sorted(migrations_dir.glob('*.sql'))[:-1]:
        shutil.copyfile(path,previous/path.name)
    run_migrations(clean_database_dsn,migrations_dir=previous)
    migrations=load_migrations(migrations_dir)
    with psycopg.connect(clean_database_dsn,autocommit=True) as db:
        with db.transaction(force_rollback=True):
            db.execute(migrations[-1].sql)
            db.execute('SELECT started_at,finished_at FROM podcast_scenes')
        assert db.execute("SELECT count(*) FROM information_schema.columns WHERE table_name='podcast_scenes' AND column_name='started_at'").fetchone()==(0,)
    assert run_migrations(clean_database_dsn,migrations_dir=migrations_dir)==(len(migrations),)
