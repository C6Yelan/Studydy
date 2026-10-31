"""資料升級後仍能讀取正常作答、目標及移版進度。"""
from datetime import datetime, timedelta
import re
import shutil
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb
import pytest
from sqlalchemy import text

from assessment_fixtures import answer, concept_fixture, create, create_other, finish, other_model
from learning_adaptation import assessment_sets, learning_goals
from learning_adaptation.learner_progress import apply_guidance, derive_learner_progress
from product_fixtures import closed_loop
from runtime import version_moves
from runtime.learning_activity import learning_activity
from runtime.storage.database import database_session
from runtime.storage.migrations import DEFAULT_MIGRATIONS_DIR, load_migrations, run_migrations
from backend.tests.runtime.study.test_version_moves import new_revision


@pytest.fixture
def migrations_dir(tmp_path):
    previous = tmp_path / 'previous'
    previous.mkdir()
    for path in sorted(DEFAULT_MIGRATIONS_DIR.glob('*.sql'))[:28]:
        shutil.copyfile(path, previous/path.name)
    return previous


def test_empty_upgrade_keeps_single_concept_constraints(clean_database_dsn, migrations_dir):
    run_migrations(clean_database_dsn, migrations_dir=migrations_dir)
    assert run_migrations(clean_database_dsn) == (29, 30, 31, 32, 33)
    assert run_migrations(clean_database_dsn) == ()
    with psycopg.connect(clean_database_dsn) as db:
        assert db.execute("SELECT is_nullable FROM information_schema.columns WHERE table_name='assessment_sets' AND column_name='target_concept_id'").fetchone() == ('NO',)
        assert db.execute("SELECT conkey,confkey FROM pg_constraint WHERE conname='assessment_set_items_set_id_study_session_id_knowledge_str_fkey'").fetchone() == ([1,3,4,5],[1,4,5,6])


def test_upgrade_preserves_normal_answers_goals_and_only_supported_moved_completion(closed_loop):
    f = concept_fixture(closed_loop, 2)
    owner,material,dsn = f['learner'].learner_id,f['source'].material_id,f['dsn']
    retired = create(f)
    finish(f)
    answer(f, retired)
    kept = create_other(f)
    while work := assessment_sets.claim_set_work(dsn=dsn):
        assessment_sets.execute_set_work(work, dsn=dsn, semantic_call=other_model)
    answer(f, kept)
    day = datetime.now(ZoneInfo('Asia/Taipei')).date()
    learning_goals.set_goal(f['learner'],material,day+timedelta(days=5),100,dsn=dsn)
    # 真發布及真移版，完成標記包含即將被清除與應保留的兩種來源。
    head = new_revision(f, 'rewrite')
    preview = version_moves.preview_version_move(f['learner'],material,dsn=dsn)
    moved = version_moves.execute_version_move(f['learner'],material,head['revision'],[],True,
        preview['preview_digest'],'upgrade-move',dsn=dsn)
    target_study = UUID(moved['study_session_id'])
    other_old = next(c['concept_id'] for c in f['document']['concepts'] if c['label']=='Other topic')
    other_new = next(c['concept_id'] for c in head['concepts'] if c['label']=='Other topic')
    upgraded_sql = next(migration.sql for migration in load_migrations() if migration.version == 29)
    old_sql = sorted(DEFAULT_MIGRATIONS_DIR.glob('0014_*.sql'))[0].read_text()
    kind = re.findall(r"'([^']+)'",re.search(r'CHECK \(kind IN \(([^)]+)\)',old_sql)[1])[-1]
    level_column = re.search(r'materials ADD COLUMN (\w+)',old_sql)[1]
    reward_key = re.search(r"metrics \? '([^']+)'",upgraded_sql)[1]
    badge_ids = re.findall(r"'([^']+)'",re.search(r"badge_id IN \(([^)]+)\)",upgraded_sql)[1])
    preserved_source = uuid4()
    with psycopg.connect(dsn) as db:
        # 合成舊種類；只有升級fixture繞過來源不可變trigger，原始題目/答案仍由真流程建立。
        db.execute('ALTER TABLE assessment_sets DISABLE TRIGGER assessment_set_origin_immutable')
        db.execute('ALTER TABLE assessment_sets DISABLE TRIGGER assessment_set_scope_immutable')
        db.execute('UPDATE assessment_sets SET kind=%s,target_concept_id=NULL WHERE set_id=%s',(kind,retired))
        db.execute('ALTER TABLE assessment_sets ENABLE TRIGGER assessment_set_scope_immutable')
        db.execute('ALTER TABLE assessment_sets ENABLE TRIGGER assessment_set_origin_immutable')
        db.execute(sql.SQL('UPDATE materials SET {}=%s WHERE material_id=%s').format(sql.Identifier(level_column)),('familiar',material))
        db.execute('UPDATE study_sessions SET start_receipts=%s,last_applied_guidance_revision=%s,last_applied_progress_sha256=%s WHERE study_session_id=%s',
            (Jsonb({'old':{'set_id':str(retired),'fingerprint':'a'},'keep':{'set_id':str(kept),'fingerprint':'b'}}),'learner-guidance:sha256:'+'0'*64,'0'*64,f['study'].study_session_id))
        for badge in [*badge_ids,'upload_image']:
            db.execute('INSERT INTO learning_awards VALUES (%s,%s,%s,true) ON CONFLICT DO NOTHING',(owner,badge,day))
        for source_id,kind_name,metrics in [(material,'material',{reward_key:20,'answer_xp':20}),
                                            (preserved_source,'material',{'answer_xp':10}),
                                            (uuid4(),'cards',{'marks':[]})]:
            db.execute('INSERT INTO learning_retained_days VALUES (%s,%s,%s,%s,%s)',(owner,kind_name,source_id,day,Jsonb(metrics)))
        db.execute("INSERT INTO learning_retained_days VALUES (%s,'material',%s,%s,%s)",
            (owner,material,day-timedelta(days=1),Jsonb({'answers':2,'answer_xp':20,'concepts':1})))
        db.execute("INSERT INTO goal_milestones VALUES (%s,%s,'stage',%s,NULL,now())",(owner,material,day))
        db.execute("INSERT INTO goal_milestones VALUES (%s,%s,'achieved',%s,%s,now())",(owner,material,day,day))
        kept_events = db.execute('SELECT answer_event_id,assessment_revision,is_correct FROM answer_events WHERE target_concept_id=%s AND material_id=%s',(other_old,material)).fetchall()
        db.execute("INSERT INTO assessment_sets (set_id,learner_id,material_id,study_session_id,knowledge_structure_revision,target_concept_id,kind,target_plan,requested_count,runtime_lock_document,status,idempotency_key_sha256,request_fingerprint,created_at,updated_at) SELECT %s,learner_id,material_id,%s,%s,NULL,kind,target_plan,0,runtime_lock_document,'failed',%s,%s,now(),now() FROM assessment_sets WHERE set_id=%s",
            (uuid4(),target_study,head['revision'],bytes([4])*32,bytes([5])*32,retired))
        kept_group = db.execute('SELECT * FROM assessment_sets WHERE set_id=%s',(kept,)).fetchone()
    assert run_migrations(dsn) == (29, 30, 31, 32, 33)
    with psycopg.connect(dsn) as db:
        assert db.execute('SELECT answer_event_id,assessment_revision,is_correct FROM answer_events WHERE material_id=%s',(material,)).fetchall()==kept_events
        assert db.execute('SELECT event_number FROM answer_events WHERE material_id=%s',(material,)).fetchall()==[(1,)]
        # 單概念題組資料逐欄不變。
        assert db.execute('SELECT * FROM assessment_sets WHERE set_id=%s',(kept,)).fetchone()==kept_group
        assert db.execute('SELECT last_event_number,start_receipts FROM study_sessions WHERE study_session_id=%s',(f['study'].study_session_id,)).fetchone()==(1,{'keep':{'set_id':str(kept),'fingerprint':'b'}})
        assert db.execute('SELECT completed_concept_ids FROM study_session_moves WHERE target_study_session_id=%s',(target_study,)).fetchone()==([other_new],)
        assert db.execute('SELECT source_id,metrics FROM learning_retained_days WHERE source_kind=%s',('material',)).fetchall()==[(preserved_source,{'answer_xp':10})]
        assert db.execute('SELECT earned_on,seen FROM learning_awards WHERE learner_id=%s AND badge_id=%s',(owner,'upload_image')).fetchone()==(day,True)
        assert db.execute('SELECT count(*) FROM goal_milestones WHERE material_id=%s',(material,)).fetchone()==(0,)
    old = derive_learner_progress(f['learner'],f['study'].study_session_id,dsn=dsn)
    current = derive_learner_progress(f['learner'],target_study,dsn=dsn)
    assert {s.label:s.status for s in old.concept_states}=={'Signals':'not_started','Other topic':'completed'}
    assert {s.label:s.status for s in current.concept_states}=={'Signal codes':'not_started','Other topic':'completed','Extra topic':'not_started'}
    with database_session(dsn) as db:
        goal = learning_goals.read_goal(db,owner,material)['goal']
        assert goal['completed_points']==1 and not goal['achieved']
        assert db.execute(text('SELECT baseline_completed_points,daily_completed_points FROM learning_goals WHERE material_id=:material'),{'material':material}).one()==(0,0)
        view = learning_activity(db,owner)
        assert view['total_answers']==1
        assert next(b for b in view['badges']['items'] if b['badge_id']=='first_answer')['earned']


def test_upgrade_preserves_finished_study_when_only_unassessable_concept_remains(closed_loop):
    f = concept_fixture(closed_loop,1)
    dsn,study = f['dsn'],f['study'].study_session_id
    kept = create_other(f)
    while work := assessment_sets.claim_set_work(dsn=dsn):
        assessment_sets.execute_set_work(work,dsn=dsn,semantic_call=other_model)
    answer(f,kept)
    with psycopg.connect(dsn) as db:
        db.execute('UPDATE study_sessions SET no_safe_claim_ids=%s WHERE study_session_id=%s',
            ([c['claim_id'] for c in f['concept']['claims']],study))
    progress = derive_learner_progress(f['learner'],study,dsn=dsn)
    assert progress.next_action.action=='complete'
    apply_guidance(f['learner'],study,progress.guidance_revision,dsn=dsn)
    old_sql = sorted(DEFAULT_MIGRATIONS_DIR.glob('0014_*.sql'))[0].read_text()
    kind = re.findall(r"'([^']+)'",re.search(r'CHECK \(kind IN \(([^)]+)\)',old_sql)[1])[-1]
    with psycopg.connect(dsn) as db:
        db.execute("INSERT INTO assessment_sets (set_id,learner_id,material_id,study_session_id,knowledge_structure_revision,target_concept_id,kind,target_plan,requested_count,runtime_lock_document,status,idempotency_key_sha256,request_fingerprint,created_at,updated_at) SELECT %s,learner_id,material_id,study_session_id,knowledge_structure_revision,NULL,%s,target_plan,0,runtime_lock_document,'failed',%s,%s,now(),now() FROM assessment_sets WHERE set_id=%s",
            (uuid4(),kind,bytes([4])*32,bytes([5])*32,kept))
        before = db.execute('SELECT status,completed_at,last_event_number FROM study_sessions WHERE study_session_id=%s',(study,)).fetchone()
    assert before[0]=='completed' and before[1] is not None and before[2]==1
    assert run_migrations(dsn)==(29,30,31,32,33)
    with psycopg.connect(dsn) as db:
        assert db.execute('SELECT status,completed_at,last_event_number FROM study_sessions WHERE study_session_id=%s',(study,)).fetchone()==before
    progress = derive_learner_progress(f['learner'],study,dsn=dsn)
    assert progress.next_action.action=='complete'
    assert next(s for s in progress.concept_states if s.label=='Other topic').status=='completed'
