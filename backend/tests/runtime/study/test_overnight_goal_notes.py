"""建立前目標、可達建議與跨版筆記來源，真 DB 與受控生成。"""
from datetime import date, timedelta
from uuid import UUID
from sqlalchemy import select

from product_fixtures import closed_loop
from assessment_fixtures import concept_fixture
from runtime import topics, material_notes, research
from runtime.source_normalization import create_draft, upload_source, normalize_next, read_sources
from runtime.storage.database import database_session
from runtime.storage.tables import Material, LearningGoal
from learning_adaptation import learning_goals
from test_note_queue import setup, drain
from test_version_moves import new_revision


def test_goal_is_kept_before_first_publication(closed_loop, monkeypatch, tmp_path):
    from test_source_revisions import pdf
    from test_media_source_flow import _publish
    owner, _, _, _, dsn, _ = closed_loop
    material = create_draft(owner.learner_id, 'pending goal', 'pending-goal', dsn=dsn)
    target = date.today() + timedelta(days=7)
    saved = learning_goals.set_goal(owner, material, target, 80, dsn=dsn)
    assert saved['schema'] == 'learning-goal/v2' and saved['goal'] is None
    assert saved['pending_goal'] == {'target_date':target.isoformat(),'target_percent':80}
    upload_source(owner.learner_id, material, pdf('A queue removes the earliest element.'), 'queue.pdf', 'application/pdf', 'source', dsn=dsn)
    assert normalize_next(dsn=dsn)
    source, = read_sources(owner.learner_id, material, dsn=dsn)['sources']
    _publish(closed_loop, material, source['normalization_id'], 'pending-goal-publish', monkeypatch, tmp_path)
    with database_session(dsn) as db:
        actual = learning_goals.read_goal(db, owner.learner_id, material)
        assert actual['pending_goal'] is None and actual['goal']['target_percent'] == 80
        assert db.get(LearningGoal, material).target_date == target


def test_topic_preferences_survive_source_selection(closed_loop, monkeypatch):
    from test_topics import fixtures, PLAN
    from research_fixtures import search_to_selection
    owner, _, _, _, dsn, _ = closed_loop
    fixtures(monkeypatch)
    goal = {'target_date':(date.today()+timedelta(days=7)).isoformat(),'target_percent':60}
    created = topics.create(owner.learner_id, 'Queue', 'topic-preferences', learning_goal=goal, dsn=dsn)
    assert topics.step(dsn=dsn)
    ready = topics.read(owner.learner_id, created['topic_id'], dsn=dsn)
    assert ready['learning_goal'] == goal
    approved = topics.approve(owner.learner_id, created['topic_id'], ready['version'], PLAN, 'approve', dsn=dsn)
    search_to_selection(owner.learner_id, approved['research_id'], dsn)
    result = topics.create_material(owner.learner_id, created['topic_id'], ['paper'], dsn=dsn)
    with database_session(dsn) as db:
        row = db.get(Material, result['material_id'])
        assert row.pending_goal == goal


def test_goal_suggestions_apply_to_real_schedule_without_over_twenty(closed_loop):
    f = concept_fixture(closed_loop, 20)
    owner, material, dsn = f['learner'], f['source'].material_id, f['dsn']
    view = learning_goals.set_goal(owner, material, learning_goals._now().astimezone(learning_goals.TIMEZONE).date(), 100, dsn=dsn)
    assert view['goal']['quota_over_limit'] and view['goal']['suggestions']
    for suggested in view['goal']['suggestions']:
        target = suggested['target_date']
        updated = learning_goals.set_goal(owner, material, target, suggested['target_percent'], dsn=dsn)
        assert not updated['goal']['quota_over_limit'] and not updated['goal']['scheduled_over_limit']
        assert suggested['daily_points'] <= 20


def test_new_revision_reads_old_notes_with_old_sources_until_replacement(closed_loop, tmp_path, monkeypatch):
    f, client, path = setup(closed_loop, tmp_path, monkeypatch)
    drain(f)
    before = client.get(path+'/summary').json()
    newer = new_revision(f, 'rewrite')
    new_path = path.replace(f['document']['revision'], newer['revision'])
    pending = client.get(new_path+'/summary').json()
    assert pending['schema'] == 'material-notes/v3'
    assert pending['updating'] and pending['update_available']
    assert pending['content_revision'] == f['document']['revision'] and pending['content_generation'] == before['generation']
    assert pending['paragraphs'] == before['paragraphs'] and pending['source_resolver'] == before['source_resolver']
    claim = material_notes._claim(f['dsn'])
    running = client.get(new_path+'/summary').json()
    assert running['started_at'] is not None and running['updated_at'] is not None
    material_notes._save(claim, f['dsn'], stopping=True)
    drain(f)
    ready = client.get(new_path+'/summary').json()
    assert ready['content_revision'] == newer['revision'] and not ready['updating']
    assert ready['progress']['stage'] == 'completed'
