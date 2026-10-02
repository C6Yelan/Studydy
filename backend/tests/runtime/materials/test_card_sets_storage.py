from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import shutil
from uuid import uuid4
from threading import Barrier

import psycopg
import pytest

from runtime import card_sets as cards
from runtime.material_discard import request_material_discard
from runtime.storage.knowledge_structures import _prune_unreferenced_structures, KnowledgeStructureStoreError
from runtime.storage.migrations import run_migrations
from runtime.storage.tables import database_session, Material
from product_fixtures import closed_loop, library_materials, product_snapshot
from assessment_fixtures import concept_fixture


def create(f, **overrides):
    args = dict(owner=f['learner'].learner_id, material_id=f['first'].material_id,
                revision=f['structure']['revision'], name='我的圖卡組',
                concept_ids=[c['concept_id'] for c in f['structure']['concepts']],
                policy='published_order', key='create', dsn=f['dsn'])
    args.update(overrides)
    return cards.create_card_set(**args)


def test_upgrade_from_five_preserves_data_and_is_repeatable(clean_database_dsn, migrations_dir, tmp_path):
    old = tmp_path / 'old'; old.mkdir()
    for path in migrations_dir.glob('*.sql'):
        if int(path.name[:4]) <= 5: shutil.copy2(path, old / path.name)
    assert run_migrations(clean_database_dsn, migrations_dir=old) == (1, 2, 3, 4, 5)
    owner, material = uuid4(), uuid4()
    with psycopg.connect(clean_database_dsn) as db:
        db.execute('INSERT INTO learners (learner_id, created_at) VALUES (%s, now())', (owner,))
        db.execute("INSERT INTO materials (material_id,learner_id,upload_idempotency_key_sha256,upload_request_fingerprint,created_at,display_name) VALUES (%s,%s,%s,%s,now(),'Existing')", (material, owner, bytes(32), bytes(32)))
        before = db.execute('SELECT row_to_json(m)::text FROM materials m').fetchall()
        ledger = db.execute('SELECT * FROM schema_migrations ORDER BY version').fetchall()
    assert run_migrations(clean_database_dsn) == (6,)
    assert run_migrations(clean_database_dsn) == ()
    with psycopg.connect(clean_database_dsn) as db:
        assert db.execute('SELECT row_to_json(m)::text FROM materials m').fetchall() == before
        assert db.execute('SELECT * FROM schema_migrations WHERE version <= 5 ORDER BY version').fetchall() == ledger
        assert db.execute('SELECT count(*) FROM card_sets').fetchone() == (0,)


def test_card_set_crud_resume_and_non_mutation(closed_loop):
    f = concept_fixture(closed_loop, source_review_required=True)
    owner, material, revision = f['learner'].learner_id, f['source'].material_id, f['document']['revision']
    ids = [c['concept_id'] for c in f['document']['concepts']]
    before = product_snapshot(f['dsn'])
    row = cards.create_card_set(owner, material, revision, '複習', ids[::-1] + ids, 'published_order', 'new', dsn=f['dsn'])
    assert row['concept_ids'] == ids and row['current_position'] == 0
    card_id = row['card_set_id']
    # 並行同一 create intent 不建立第二組。
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: cards.create_card_set(owner, material, revision, '複習', ids, 'published_order', 'new', dsn=f['dsn']), range(2)))
    assert all(item == row for item in results)
    row = cards.set_card_position(owner, material, card_id, 1, row['version'], dsn=f['dsn'])
    assert cards.read_card_set(owner, material, card_id, dsn=f['dsn'])['current_position'] == 1
    projection = cards.read_card_set(owner, material, card_id, cards=True, dsn=f['dsn'])
    assert projection['cards']['selection']['knowledge_structure_revision'] == revision
    assert projection['cards']['status']['quality'] == 'needs_review'
    assert [c['concept_id'] for c in projection['cards']['cards']] == ids
    assert projection['cards']['relations'] == []
    with pytest.raises(cards.CardSetError, match='REQUEST_INVALID'):
        cards.set_card_position(owner, material, card_id, 2, row['version'], dsn=f['dsn'])
    with pytest.raises(cards.CardSetError, match='CARD_SET_CONFLICT'):
        cards.set_card_position(owner, material, card_id, 0, 1, dsn=f['dsn'])
    renamed = cards.edit_card_set(owner, material, card_id, revision, '新名稱', ids, 'published_order', row['version'], dsn=f['dsn'])
    assert renamed['current_position'] == 1
    updated = cards.edit_card_set(owner, material, card_id, revision, '新名稱', ids[:1], 'published_order', renamed['version'], dsn=f['dsn'])
    assert updated['current_position'] == 0 and updated['concept_ids'] == ids[:1]
    assert cards.edit_card_set(owner, material, card_id, revision, '新名稱', ids[:1], 'published_order', renamed['version'], dsn=f['dsn']) == updated
    with psycopg.connect(f['dsn']) as db:
        assert db.execute('SELECT position,concept_id FROM card_set_items WHERE card_set_id=%s ORDER BY position', (card_id,)).fetchall() == [(0, ids[0])]
        columns = {r[0] for r in db.execute("SELECT column_name FROM information_schema.columns WHERE table_name IN ('card_sets','card_set_items')")}
        assert not columns & {'claims','evidence','document','text','relations','html'}
    cards.delete_card_set(owner, material, card_id, updated['version'], dsn=f['dsn'])
    assert cards.list_card_sets(owner, material, dsn=f['dsn'])['card_sets'] == []
    assert product_snapshot(f['dsn']) == before


def test_retention_and_deletion_release_without_fake_session(library_materials):
    f=library_materials; owner=f['learner'].learner_id; material=f['first'].material_id
    saved=create(f)
    def prune():
        with database_session(f['dsn']) as session:
            session.get(Material, material, with_for_update=True)
            _prune_unreferenced_structures(session, owner, material, f['second_structure']['revision'])
    prune()
    assert cards.read_card_set(owner, material, saved['card_set_id'], cards=True, dsn=f['dsn'])['cards']['selection']['knowledge_structure_revision'] == f['structure']['revision']
    with psycopg.connect(f['dsn']) as db:
        assert db.execute('SELECT count(*) FROM study_sessions').fetchone() == (0,)
    cards.delete_card_set(owner, material, saved['card_set_id'], saved['version'], dsn=f['dsn'])
    prune()
    with psycopg.connect(f['dsn']) as db:
        assert db.execute('SELECT count(*) FROM knowledge_structures WHERE structure_revision=%s',(f['structure']['revision'],)).fetchone() == (0,)


def test_material_delete_and_revocation_override_retention(library_materials):
    f=library_materials; owner=f['learner'].learner_id; material=f['first'].material_id
    row=create(f)
    with database_session(f['dsn']) as session:
        session.get(Material, material).discard_requested_at = row['created_at']
    with pytest.raises(cards.CardSetError, match='RESOURCE_NOT_FOUND'):
        cards.read_card_set(owner, material, row['card_set_id'], cards=True, dsn=f['dsn'])
    assert request_material_discard(owner, material, dsn=f['dsn']) == 'removed'
    with psycopg.connect(f['dsn']) as db:
        assert db.execute('SELECT count(*) FROM card_sets').fetchone() == (0,)
        assert db.execute('SELECT count(*) FROM card_set_items').fetchone() == (0,)


def test_scope_and_conflicts(library_materials):
    f=library_materials; row=create(f); owner=f['learner'].learner_id; material=f['first'].material_id
    id=row['card_set_id']; revision=row['knowledge_structure_revision']; ids=row['concept_ids']
    for foreign_owner, foreign_material in [(f['foreign'].learner_id,material),(owner,f['uploaded'].material_id)]:
        for operation in [
            lambda: cards.read_card_set(foreign_owner,foreign_material,id,dsn=f['dsn']),
            lambda: cards.edit_card_set(foreign_owner,foreign_material,id,revision,'x',ids,'published_order',1,dsn=f['dsn']),
            lambda: cards.delete_card_set(foreign_owner,foreign_material,id,1,dsn=f['dsn']),
        ]:
            with pytest.raises(cards.CardSetError,match='RESOURCE_NOT_FOUND'): operation()
    with pytest.raises(cards.CardSetError,match='IDEMPOTENCY_CONFLICT'): create(f,name='Different')
    for values in ([], ['concept:sha256:'+'f'*64]):
        with pytest.raises(cards.CardSetError,match='REQUEST_INVALID'): create(f,key='bad',concept_ids=values)
    for name in ['', '   ', '\u007f', '\ud800', 'x' * 201]:
        with pytest.raises(cards.CardSetError, match='REQUEST_INVALID'): create(f, key='bad-name', name=name)
    with pytest.raises(KnowledgeStructureStoreError): create(f,key='wrong',revision='knowledge-structure:sha256:'+'f'*64)
    with pytest.raises(cards.CardSetError,match='REVISION_CONFLICT'):
        cards.edit_card_set(owner,material,id,f['second_structure']['revision'],'x',ids,'published_order',1,dsn=f['dsn'])
    with pytest.raises(cards.CardSetError,match='CARD_SET_CONFLICT'):
        cards.edit_card_set(owner,material,id,revision,'x',ids,'published_order',99,dsn=f['dsn'])


def test_create_racing_prune_never_leaves_a_dangling_deck(library_materials):
    f=library_materials; gate=Barrier(2)
    def creating():
        gate.wait(timeout=5)
        try:
            return create(f)
        except KnowledgeStructureStoreError:
            return None
    def pruning():
        gate.wait(timeout=5)
        with database_session(f['dsn']) as session:
            session.get(Material, f['first'].material_id, with_for_update=True)
            _prune_unreferenced_structures(session, f['learner'].learner_id, f['first'].material_id, f['second_structure']['revision'])
    with ThreadPoolExecutor(max_workers=2) as pool:
        saved=pool.submit(creating); cleanup=pool.submit(pruning)
        row=saved.result(timeout=15); cleanup.result(timeout=15)
    if row:
        assert cards.read_card_set(f['learner'].learner_id, f['first'].material_id, row['card_set_id'], cards=True, dsn=f['dsn'])['card_set']==row
    else:
        assert cards.list_card_sets(f['learner'].learner_id, f['first'].material_id, dsn=f['dsn'])['card_sets']==[]
