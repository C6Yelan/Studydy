"""從 B02 schema 升級保存的原題／答案／no-safe／completed；只使用測試 container。"""
import shutil
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from conftest import DatabaseDsn
from test_learning_resume import learning_records
from test_material_library import library_materials
from test_closed_loop_v1 import closed_loop
from runtime.storage.migrations import run_migrations
from runtime.storage.knowledge_structures import read_knowledge_structure
from learning_adaptation.answer_events import read_assessment_records


def test_b02_upgrade_preserves_saved_learning_and_repeats_as_noop(learning_records,postgres_dsn,migrations_dir,tmp_path):
    accepted=tmp_path/'accepted-b02';accepted.mkdir()
    for path in migrations_dir.glob('*.sql'):
        if int(path.name.split('_',1)[0])<=8:shutil.copyfile(path,accepted/path.name)
    name=f'studydy_case_b03_upgrade_{uuid4().hex}'
    fields=conninfo_to_dict(postgres_dsn);fields['dbname']=name
    upgraded=DatabaseDsn(make_conninfo(**fields))
    with psycopg.connect(postgres_dsn,autocommit=True) as connection:
        connection.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
    tables=['learners','learner_sessions','materials','artifacts','material_sources','source_normalizations',
        'material_source_sets','material_source_set_items','material_processing_runs','knowledge_structures',
        'study_sessions','assessments','answer_events']
    try:
        assert run_migrations(upgraded,migrations_dir=accepted)==tuple(range(1,9))
        columns={}
        before={}
        with psycopg.connect(learning_records['dsn']) as source,psycopg.connect(upgraded) as target:
            target.execute('SET CONSTRAINTS ALL DEFERRED')
            for table in tables:
                names=[row[0] for row in target.execute("SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name=%s ORDER BY ordinal_position",(table,))]
                columns[table]=sql.SQL(',').join(map(sql.Identifier,names))
                copy=sql.SQL('COPY {} ({})').format(sql.Identifier(table),columns[table])
                with source.cursor().copy(copy+sql.SQL(' TO STDOUT')) as output,target.cursor().copy(copy+sql.SQL(' FROM STDIN')) as incoming:
                    for chunk in output:incoming.write(chunk)
            target.commit()
            for table in tables:
                before[table]=target.execute(sql.SQL('SELECT {} FROM {} ORDER BY 1').format(columns[table],sql.Identifier(table))).fetchall()
        assert run_migrations(upgraded)==(9,10,11,12,13)
        assert run_migrations(upgraded)==()
        with psycopg.connect(upgraded) as connection:
            for table in tables:
                assert connection.execute(sql.SQL('SELECT {} FROM {} ORDER BY 1').format(columns[table],sql.Identifier(table))).fetchall()==before[table]
        owner=learning_records['learner']
        assert read_knowledge_structure(owner.learner_id,learning_records['first'].material_id,
            revision=learning_records['structure']['revision'],dsn=upgraded).document==learning_records['structure']
        for key in ('active','completed','no_safe'):
            study=learning_records[key]
            assert read_assessment_records(owner,study.study_session_id,dsn=upgraded)==read_assessment_records(owner,study.study_session_id,dsn=learning_records['dsn'])
    finally:
        with psycopg.connect(postgres_dsn,autocommit=True) as connection:
            connection.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(name)))
