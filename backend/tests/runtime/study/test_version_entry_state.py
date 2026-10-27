from copy import deepcopy
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from product_fixtures import closed_loop, ORIGIN, HEADERS, _app
from assessment_fixtures import create_other
from study.test_version_moves import learned, new_revision, activity, snapshot, finish_cards
from runtime import version_moves, card_reviews
from runtime.learner_session import TrustedLearner
from runtime.storage.database import database_session
from runtime.storage.tables import CardSet, CardSetMove, Material, MaterialVersionMove, MaterialVersionState


def test_publication_automatically_moves_safe_entries_and_return_keeps_new_events(closed_loop):
    f,old_set=learned(closed_loop)
    learner,material,dsn=f['learner'],f['source'].material_id,f['dsn']
    old_revision=f['document']['revision']
    before=snapshot(f,old_set)
    document=new_revision(f,'rewrite')
    preview=version_moves.preview_version_move(learner,material,dsn=dsn)
    assert preview['automatic_safe'] and preview['blocking_reasons']==[]
    assert preview['automatic_move']['status']=='pending'
    xp=activity(f)['xp']['total']
    assert version_moves.run_next(dsn=dsn)
    result=version_moves.preview_version_move(learner,material,dsn=dsn)
    assert result['automatic_move']['status']=='completed'
    assert result['previous']=={'move_id':str(result['automatic_move']['move_id']),'revision':old_revision,'card_set_ids':[str(old_set)],'study_session_id':str(f['study'].study_session_id)}
    assert not version_moves.run_next(dsn=dsn)
    assert activity(f)['xp']['total']==xp and snapshot(f,old_set)==before
    with database_session(dsn) as db:
        move=db.get(MaterialVersionMove,result['automatic_move']['move_id'])
        target=UUID(move.result['card_sets'][0]['card_set_id'])
    finish_cards(dsn,[])
    with database_session(dsn) as db:
        concept=db.get(CardSet,target).concept_ids[0]
    # 移動後仍可標記；返回只能改導航，不能撤回這些新事件。
    card_reviews.submit_review(learner.learner_id,target,concept,'remembered',1,'new-mark',dsn=dsn)
    recorded=activity(f)
    returned=version_moves.return_previous(learner,material,result['automatic_move']['move_id'],dsn=dsn)
    assert returned['card_set_ids']==[str(old_set)]
    assert activity(f)==recorded
    with database_session(dsn) as db:
        assert db.get(Material,material).head_revision==document['revision']
        assert db.get(CardSet,target).deleted_at is None


@pytest.mark.parametrize('reason',['assessment_in_progress','unmapped_cards','source_unavailable'])
def test_unsafe_updates_keep_manual_confirmation(closed_loop,reason):
    f,old_set=learned(closed_loop)
    if reason=='assessment_in_progress':
        create_other(f)
    if reason=='source_unavailable':
        with database_session(f['dsn']) as db:db.get(CardSet,old_set).status='pending'
    new_revision(f,'split' if reason=='unmapped_cards' else 'rewrite')
    preview=version_moves.preview_version_move(f['learner'],f['source'].material_id,dsn=f['dsn'])
    assert not preview['automatic_safe']
    assert reason in {item['code'] for item in preview['blocking_reasons']}
    assert version_moves.run_next(dsn=f['dsn'])
    after=version_moves.preview_version_move(f['learner'],f['source'].material_id,dsn=f['dsn'])
    assert after['automatic_move'] is None and after['previous'] is None
    with database_session(f['dsn']) as db:
        assert not db.scalars(select(MaterialVersionMove)).all()


def test_notice_is_account_scoped_and_return_checks_snapshot(closed_loop,tmp_path,monkeypatch):
    f,old_set=learned(closed_loop)
    material,dsn=f['source'].material_id,f['dsn']
    new_revision(f,'rewrite')
    url=f'/v1/materials/{material}/version-move'
    client=TestClient(_app(dsn,tmp_path,monkeypatch),base_url=ORIGIN)
    client.cookies.set('studydy_session',f['token'])
    assert client.post(url+'/return',headers=HEADERS,json={'schema':'version-move-return/v1','expected_move_id':str(uuid4())}).status_code==409
    saved=client.put(url+'/notice',headers=HEADERS,json={'schema':'version-move-notice/v1','dismissed':True})
    assert saved.status_code==200,saved.text
    second=TestClient(_app(dsn,tmp_path,monkeypatch),base_url=ORIGIN)
    second.cookies.set('studydy_session',f['token'])
    assert second.get(url).json()['notice_dismissed'] is True
    version_moves.run_next(dsn=dsn)
    stale=client.post(url+'/return',headers=HEADERS,json={'schema':'version-move-return/v1','expected_move_id':str(uuid4())})
    assert stale.status_code==409 and stale.json()['reason_code']=='VERSION_MOVE_PREVIEW_STALE'
    move_id=second.get(url).json()['previous']['move_id']
    returned=client.post(url+'/return',headers=HEADERS,json={'schema':'version-move-return/v1','expected_move_id':move_id})
    assert returned.status_code==200 and returned.json()['card_set_ids']==[str(old_set)]
    with pytest.raises(Exception,match='RESOURCE_NOT_FOUND'):
        version_moves.set_notice(TrustedLearner(uuid4()),material,False,dsn=dsn)


def test_cleanup_discards_only_unreferenced_copy_and_preserves_receipt_and_events(closed_loop):
    f,old_set=learned(closed_loop)
    learner,material,dsn=f['learner'],f['source'].material_id,f['dsn']
    new_revision(f,'rewrite')
    version_moves.run_next(dsn=dsn)
    before=snapshot(f,old_set)
    with database_session(dsn) as db:
        state=db.get(MaterialVersionState,(learner.learner_id,material))
        move=db.get(MaterialVersionMove,state.automatic_move_id)
        source=db.get(CardSet,old_set)
        values={column.name:deepcopy(getattr(source,column.name)) for column in CardSet.__table__.columns}
        identity=uuid4();values.update(card_set_id=identity,idempotency_key_sha256=uuid4().bytes+uuid4().bytes)
        db.add(CardSet(**values));db.flush()
        db.add(CardSetMove(target_card_set_id=identity,source_card_set_id=old_set,move_id=move.move_id,
            learner_id=learner.learner_id,concept_map={c:c for c in source.concept_ids}))
        db.flush()
        version_moves._clean_unreferenced_copies(db,learner.learner_id,material,state.target_revision,state.previous)
    with database_session(dsn) as db:
        receipt=db.get(CardSet,identity)
        assert receipt.deleted_at and receipt.knowledge_structure_revision is None and receipt.generation=={}
        assert db.get(CardSet,old_set).deleted_at is None
    assert snapshot(f,old_set)==before


def test_version_entry_migration_applies_and_rolls_back(clean_database_dsn,migrations_dir,tmp_path):
    import shutil
    import psycopg
    from runtime.storage.migrations import load_migrations,run_migrations
    previous=tmp_path/'previous';previous.mkdir()
    for path in sorted(migrations_dir.glob('*.sql'))[:-1]:shutil.copyfile(path,previous/path.name)
    run_migrations(clean_database_dsn,migrations_dir=previous)
    migrations=load_migrations(migrations_dir)
    with psycopg.connect(clean_database_dsn,autocommit=True) as db:
        with db.transaction(force_rollback=True):
            db.execute(migrations[-1].sql)
            assert db.execute("SELECT to_regclass('material_version_state')").fetchone()==('material_version_state',)
        assert db.execute("SELECT to_regclass('material_version_state')").fetchone()==(None,)
    assert run_migrations(clean_database_dsn,migrations_dir=migrations_dir)==(len(migrations),)


def test_latest_snapshot_replaces_prior_move_even_when_revision_is_same(closed_loop):
    from runtime import card_sets
    f,old_set=learned(closed_loop)
    owner,material,dsn=f['learner'].learner_id,f['source'].material_id,f['dsn']
    second_set=card_sets.create_card_set(owner,material,f['document']['revision'],'第二組',
        [c['concept_id'] for c in f['document']['concepts']],'second-set',dsn=dsn)['card_set_id']
    finish_cards(dsn,[])
    head=new_revision(f,'rewrite')['revision']
    def move(identity,key):
        preview=version_moves.preview_version_move(f['learner'],material,dsn=dsn)
        return version_moves.execute_version_move(f['learner'],material,head,[identity],False,
            preview['preview_digest'],key,dsn=dsn)
    first=move(old_set,'first-entry')
    second=move(second_set,'second-entry')
    latest=version_moves.preview_version_move(f['learner'],material,dsn=dsn)['previous']
    assert latest['move_id']==second['move_id'] and latest['card_set_ids']==[str(second_set)]
    assert latest['revision']==f['document']['revision']
    with pytest.raises(version_moves.VersionMoveError,match='VERSION_MOVE_PREVIEW_STALE'):
        version_moves.return_previous(f['learner'],material,first['move_id'],dsn=dsn)
    assert version_moves.return_previous(f['learner'],material,second['move_id'],dsn=dsn)['card_set_ids']==[str(second_set)]
