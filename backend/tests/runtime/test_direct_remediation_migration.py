"""由 0013 真正升級，核對題目／答案／補強歸屬不變。"""
from pathlib import Path
import shutil

import psycopg
import pytest

from runtime.storage.database import connect_database
from runtime.storage.migrations import DEFAULT_MIGRATIONS_DIR, run_migrations
from test_assessment_sets import closed_loop, concept_fixture, create, read
from test_assessment_remediation import finish, answer, supplement


@pytest.fixture
def migrations_dir(tmp_path):
    directory=tmp_path/'through_0013';directory.mkdir()
    for path in DEFAULT_MIGRATIONS_DIR.glob('*.sql'):
        if int(path.name[:4])<=13:shutil.copyfile(path,directory/path.name)
    return directory


def test_upgrade_preserves_assessments_answers_items_and_origin(closed_loop):
    f=concept_fixture(closed_loop,2);root=create(f);finish(f);answer(f,root,wrong={2})
    child=supplement(f,root)
    with connect_database(f['dsn']) as connection:
        connection.execute("UPDATE assessment_sets SET target_plan=jsonb_set(target_plan,'{policy}','\"reviewed-wrong-points/v1\"'::jsonb) WHERE set_id=%s",(child,))
    finish(f,'supplement');answer(f,child)
    def snapshot(connection):
        result={}
        for table in ('assessments','answer_events','assessment_set_items','knowledge_structures'):
            result[table]=connection.execute(f'SELECT to_jsonb(t) FROM {table} t ORDER BY to_jsonb(t)::text').fetchall()
        result['sets']=connection.execute("""SELECT (to_jsonb(s)-'review_actions'-'cycle_closed_at'-'target_plan') ||
            jsonb_build_object('target_plan',target_plan-'policy') FROM assessment_sets s ORDER BY set_id""").fetchall()
        return result
    with connect_database(f['dsn']) as connection:
        connection.execute("UPDATE assessment_sets SET review_actions='{}'::jsonb, cycle_closed_at=now()")
        before=snapshot(connection)
        ledger=connection.execute('SELECT version,sql_sha256 FROM schema_migrations ORDER BY version').fetchall()
    assert run_migrations(f['dsn'])==(14,)
    assert run_migrations(f['dsn'])==()
    with connect_database(f['dsn']) as connection:
        columns={r[0] for r in connection.execute("SELECT column_name FROM information_schema.columns WHERE table_name='assessment_sets'")}
        assert not {'review_actions','cycle_closed_at'} & columns
        assert {'diagnostic_set_id','action_receipts'} <= columns
        assert snapshot(connection)==before
        assert connection.execute('SELECT version,sql_sha256 FROM schema_migrations WHERE version<=13 ORDER BY version').fetchall()==ledger
        assert connection.execute("SELECT count(*) FROM pg_constraint WHERE conname='remediation_origin_scope' AND contype='f'").fetchone()==(1,)
        assert connection.execute("SELECT count(*) FROM pg_trigger WHERE tgname IN ('assessment_set_origin_immutable','assessment_set_scope_immutable') AND tgenabled='O'").fetchone()==(2,)
        assert connection.execute('SELECT diagnostic_set_id FROM assessment_sets WHERE set_id=%s',(child,)).fetchone()==(root,)
        assert connection.execute("SELECT target_plan->>'policy' FROM assessment_sets WHERE set_id=%s",(child,)).fetchone()==('needs-review-points/v1',)
        with pytest.raises(psycopg.Error):
            with connection.transaction():
                connection.execute("UPDATE assessment_sets SET diagnostic_set_id=NULL,kind='diagnostic' WHERE set_id=%s",(child,))
    assert read(f,root)['cycle']['outcome']=='passed'
