"""真 DB 刪除、去重、每日上限與跨裝置成就領取。"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from threading import Barrier

from fastapi.testclient import TestClient
from sqlalchemy import text

from product_fixtures import closed_loop, ORIGIN, _app
from assessment_fixtures import concept_fixture, create, finish, answer
from test_learning_rewards import activity, rhythm, ready_cards, ready_podcast, TAIPEI
from runtime import card_reviews, card_sets, podcasts, podcast_progress
from runtime.material_discard import request_material_discard
from runtime.learning_retention import claim_celebrations
from runtime.storage.database import database_session


def test_material_deletion_preserves_xp_counts_days_and_earned_badges(closed_loop):
    f = concept_fixture(closed_loop, 2)
    owner, material, dsn = f['learner'].learner_id, f['source'].material_id, f['dsn']
    identity = create(f)
    finish(f)
    answer(f, identity)
    cards, ids = ready_cards(f)
    card_reviews.submit_review(owner, cards, ids[0], 'remembered', 0, 'mark', dsn=dsn)
    podcast = ready_podcast(f)
    now = datetime.now(TAIPEI)
    for index in range(6):
        podcast_progress.save_progress(owner, podcast, 0, 60 * (index + 1), 60,
            dsn=dsn, now=now - timedelta(minutes=6-index))
    before, days = activity(f), rhythm(f)
    assert before['xp']['total'] > 0
    assert request_material_discard(owner, material, dsn=dsn) == 'removed'
    after = activity(f)
    assert after['xp'] == before['xp']
    assert (after['total_answers'], after['total_correct'], after['streak_days']) == (before['total_answers'], before['total_correct'], before['streak_days'])
    assert rhythm(f)['days'] == days['days']
    earned = {b['badge_id']: b['earned_on'] for b in before['badges']['items'] if b['earned']}
    assert all(b['earned'] and b['earned_on'] == earned[b['badge_id']] for b in after['badges']['items'] if b['badge_id'] in earned)
    with database_session(dsn) as db:
        saved = str(db.execute(text('SELECT metrics FROM learning_retained_days WHERE learner_id=:owner'), {'owner': owner}).all())
        assert 'question' not in saved and 'rating' not in saved and 'paragraph' not in saved


def test_deleted_cards_preserve_cross_card_gap_without_double_counting(closed_loop):
    f = concept_fixture(closed_loop, 2)
    owner, dsn = f['learner'].learner_id, f['dsn']
    cards, ids = ready_cards(f)
    second = card_sets.create_card_set(owner, f['source'].material_id, f['document']['revision'], 'second', ids, 'second', dsn=dsn)['card_set_id']
    with database_session(dsn) as db:
        db.execute(text("UPDATE card_sets SET status='ready' WHERE card_set_id=:id"), {'id': second})
    now = datetime.now(TAIPEI) - timedelta(minutes=1)
    card_reviews.submit_review(owner, cards, ids[0], 'remembered', 0, 'old', dsn=dsn, now=now)
    card_sets.delete_card_set(owner, cards, dsn=dsn)
    card_reviews.submit_review(owner, second, ids[0], 'remembered', 0, 'new', dsn=dsn, now=now+timedelta(seconds=1))
    view = activity(f)
    assert view['xp']['sources']['cards'] == 2
    card_sets.delete_card_set(owner, cards, dsn=dsn)
    assert activity(f)['xp'] == view['xp']
    card_sets.delete_card_set(owner, second, dsn=dsn)
    assert activity(f)['xp'] == view['xp']


def test_podcast_deletion_preserves_seconds_and_daily_cap(closed_loop):
    f = concept_fixture(closed_loop, 2)
    owner, dsn = f['learner'].learner_id, f['dsn']
    podcast = ready_podcast(f)
    day = datetime.now(TAIPEI).date()
    with database_session(dsn) as db:
        db.execute(text('INSERT INTO podcast_listening_days (learner_id,podcast_id,episode_index,listened_on,listened_seconds,updated_at) VALUES (:owner,:podcast,0,:day,3900,now())'), {'owner':owner,'podcast':podcast,'day':day})
    before = activity(f)
    assert before['xp']['sources']['podcast'] == 60
    podcasts.delete_podcast(owner, podcast, dsn=dsn)
    assert activity(f)['xp'] == before['xp']
    assert next(d for d in rhythm(f)['days'] if d['date'] == day)['listened_seconds'] == 3900
    podcasts.delete_podcast(owner, podcast, dsn=dsn)
    assert activity(f)['xp'] == before['xp']


def test_celebrations_are_claimed_atomically_and_initial_baseline_is_seen(closed_loop, tmp_path, monkeypatch):
    f = concept_fixture(closed_loop, 2)
    owner, dsn = f['learner'].learner_id, f['dsn']
    identity = create(f)
    finish(f)
    answer(f, identity)
    barrier = Barrier(2)
    def claim():
        barrier.wait()
        with database_session(dsn) as db:
            return claim_celebrations(db, owner)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: claim(), range(2)))
    assert sum('first_answer' in item['badge_ids'] for item in results) == 1
    assert sum(item['level'] is not None for item in results) <= 1
    with database_session(dsn) as db:
        db.execute(text('UPDATE learning_celebrations SET baseline_pending=true WHERE learner_id=:owner'), {'owner':owner})
        db.execute(text('UPDATE learning_awards SET seen=false WHERE learner_id=:owner'), {'owner':owner})
        assert claim_celebrations(db, owner) == {'schema':'celebration-claim/v1','level':None,'badge_ids':[]}
    client = TestClient(_app(dsn, tmp_path, monkeypatch), base_url=ORIGIN)
    client.cookies.set('studydy_session', f['token'])
    response = client.post('/v1/learning-activity/celebrations/claim', headers={'Origin':ORIGIN}, json={'schema':'celebration-claim/v1'})
    assert response.status_code == 200 and response.json()['badge_ids'] == []


def test_card_and_stage_caps_are_applied_after_combining_deleted_sources(closed_loop):
    from runtime.source_normalization import create_draft
    f = concept_fixture(closed_loop, 2)
    owner, dsn = f['learner'].learner_id, f['dsn']
    now = datetime.now(TAIPEI) - timedelta(minutes=3)
    concept = f['document']['concepts'][0]['concept_id']
    cards = []
    for index in range(25):
        card = card_sets.create_card_set(owner, f['source'].material_id, f['document']['revision'],
            'cap', [concept], f'cap-{index}', dsn=dsn)['card_set_id']
        with database_session(dsn) as db:
            db.execute(text("UPDATE card_sets SET status='ready' WHERE card_set_id=:id"), {'id': card})
        cards.append(card)
        card_reviews.submit_review(owner, card, concept, 'remembered', 0, str(index), dsn=dsn, now=now+timedelta(seconds=4*index))
    assert activity(f)['xp']['sources']['cards'] == 40
    for card in cards:
        card_sets.delete_card_set(owner, card, dsn=dsn)
    assert activity(f)['xp']['sources']['cards'] == 40
    materials = [create_draft(owner, 'stage', f'stage-{i}', dsn=dsn) for i in range(4)]
    with database_session(dsn) as db:
        for material in materials:
            db.execute(text("INSERT INTO goal_milestones (learner_id,material_id,kind,day,created_at) VALUES (:owner,:material,'stage',:day,now())"), {'owner':owner,'material':material,'day':now.date()})
    assert activity(f)['xp']['sources']['goals'] == 45
    for material in materials[:3]:
        assert request_material_discard(owner, material, dsn=dsn) == 'removed'
        assert activity(f)['xp']['sources']['goals'] == 45


def test_retention_migration_preserves_users_and_rolls_back(clean_database_dsn, migrations_dir, tmp_path):
    import shutil
    import psycopg
    from uuid import uuid4
    from runtime.storage.migrations import load_migrations, run_migrations
    migrations = load_migrations(migrations_dir)
    previous = tmp_path/'previous'
    previous.mkdir()
    for path in sorted(migrations_dir.glob('*.sql'))[:-1]:
        shutil.copyfile(path, previous/path.name)
    run_migrations(clean_database_dsn, migrations_dir=previous)
    owner = uuid4()
    with psycopg.connect(clean_database_dsn, autocommit=True) as db:
        db.execute('INSERT INTO learners (learner_id,created_at) VALUES (%s,now())', (owner,))
        with db.transaction(force_rollback=True):
            db.execute(migrations[-1].sql)
            assert db.execute('SELECT baseline_pending FROM learning_celebrations WHERE learner_id=%s', (owner,)).fetchone() == (True,)
        assert db.execute("SELECT to_regclass('learning_retained_days')").fetchone() == (None,)
    assert run_migrations(clean_database_dsn, migrations_dir=migrations_dir) == (len(migrations),)
    with psycopg.connect(clean_database_dsn) as db:
        assert db.execute('SELECT count(*) FROM learners WHERE learner_id=%s', (owner,)).fetchone() == (1,)
        assert db.execute('SELECT seen_level,baseline_pending FROM learning_celebrations WHERE learner_id=%s', (owner,)).fetchone() == (1,True)
