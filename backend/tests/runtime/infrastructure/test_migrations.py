"""以隔離 PostgreSQL 驗證 migration 安裝、升級與資料保護。"""

from concurrent.futures import ThreadPoolExecutor
import shutil
from migration_fixtures import apply_legacy_data_case
from uuid import uuid4

import psycopg
import pytest

from runtime.storage.migrations import (
    MigrationChecksumError,
    MigrationSqlError,
    load_migrations,
    run_migrations,
)


def test_draft_uses_source_collection_and_rejects_retired_artifact_kind(clean_database_dsn):
    from runtime.source_normalization import create_draft
    from runtime.storage.materials import read_material_library

    run_migrations(clean_database_dsn)
    learner_id = uuid4()
    with psycopg.connect(clean_database_dsn) as connection:
        connection.execute(
            'INSERT INTO learners (learner_id,created_at) VALUES (%s,now())', (learner_id,)
        )
    material_id = create_draft(learner_id, 'Synthetic.pdf', 'source-draft', dsn=clean_database_dsn)
    assert create_draft(learner_id, 'Synthetic.pdf', 'source-draft', dsn=clean_database_dsn) == material_id
    item, = read_material_library(learner_id, dsn=clean_database_dsn)
    assert item['source_artifact_id'] is None and item['source_count'] == 0
    with psycopg.connect(clean_database_dsn) as connection:
        with pytest.raises(psycopg.errors.CheckViolation, match='artifact_role_media'):
            with connection.transaction():
                connection.execute(
                    """INSERT INTO artifacts (
                           artifact_id,learner_id,material_id,kind,media_type,sha256,size_bytes,created_at
                       ) VALUES (%s,%s,%s,'source_pdf','application/pdf',%s,1,now())""",
                    (uuid4(), learner_id, material_id, bytes(32)),
                )


@pytest.mark.parametrize(
    'tamper, reason',
    [
        (
            "UPDATE schema_migrations SET sql_sha256 = repeat('0', 64) WHERE version = 1",
            'MIGRATION_CHECKSUM_DRIFT',
        ),
        (
            'DELETE FROM schema_migrations WHERE version = 2',
            'MIGRATION_LEDGER_SEQUENCE_INVALID',
        ),
        (
            "INSERT INTO schema_migrations VALUES (9999, repeat('0', 64), now())",
            'MIGRATION_VERSION_UNKNOWN',
        ),
    ],
)
def test_damaged_or_old_ledger_is_rejected(clean_database_dsn, tamper, reason):
    run_migrations(clean_database_dsn)
    with psycopg.connect(clean_database_dsn) as connection:
        connection.execute(tamper)
        before = connection.execute(
            'SELECT * FROM schema_migrations ORDER BY version'
        ).fetchall()
    with pytest.raises(MigrationChecksumError, match=reason):
        run_migrations(clean_database_dsn)
    with psycopg.connect(clean_database_dsn) as connection:
        assert connection.execute(
            'SELECT * FROM schema_migrations ORDER BY version'
        ).fetchall() == before


def test_next_migration_rolls_back_and_can_retry(clean_database_dsn, migrations_dir, tmp_path):
    run_migrations(clean_database_dsn)
    candidate = tmp_path / 'migrations'
    shutil.copytree(migrations_dir, candidate)
    next_number = len(load_migrations()) + 1
    next_version = candidate / f'{next_number:04d}_probe.sql'
    next_version.write_text(
        'CREATE TABLE migration_probe (id integer); '
        'SELECT missing_migration_function();'
    )
    with pytest.raises(MigrationSqlError, match='MIGRATION_SQL_FAILED'):
        run_migrations(clean_database_dsn, migrations_dir=candidate)
    with psycopg.connect(clean_database_dsn) as connection:
        assert connection.execute(
            "SELECT to_regclass('public.migration_probe')"
        ).fetchone() == (None,)
        assert connection.execute(
            'SELECT count(*) FROM schema_migrations'
        ).fetchone() == (len(load_migrations()),)
    next_version.write_text('CREATE TABLE migration_probe (id integer);')
    assert run_migrations(clean_database_dsn, migrations_dir=candidate) == (next_number,)
    assert run_migrations(clean_database_dsn, migrations_dir=candidate) == ()


def test_concurrent_install_applies_each_version_once(clean_database_dsn):
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: run_migrations(clean_database_dsn), range(2)))
    assert sorted(results) == [(), tuple(m.version for m in load_migrations())]
    with psycopg.connect(clean_database_dsn) as connection:
        assert dict(connection.execute(
            'SELECT version, sql_sha256 FROM schema_migrations'
        )) == {
            m.version: m.sql_sha256 for m in load_migrations()
        }


def test_remove_topic_level_preserves_background_and_approval_identity(clean_database_dsn, migrations_dir, tmp_path):
    from psycopg.types.json import Jsonb
    from content_identity import canonical_sha256
    from runtime.storage.artifacts import _key_digest
    from runtime import topics

    # 歷史 JSON 轉換仍獨立驗證；新初始化不再重播這些 DML。
    run_migrations(clean_database_dsn)
    owner=uuid4();records=[]
    with psycopg.connect(clean_database_dsn) as connection:
        connection.execute('INSERT INTO learners (learner_id,created_at) VALUES (%s,now())',(owner,))
        for index,level in enumerate(['入門','有基礎','深入','熟悉 Python，想理解「機率」公式與限制 \\ "例子"']):
            identity=uuid4();request='理解 Agent 的停止條件';status='approved' if index%2 else 'ready'
            proposal={'title':'Agent 停止條件','level':level,'goals':['理解成本與風險'],'topics':['比較重試與求助'],'exclude':[]}
            key='scope-'+str(index)
            connection.execute('''INSERT INTO topic_scopes
                (topic_id,learner_id,request,proposal,version,status,approved_sha256,approval_key_sha256,idempotency_key_sha256,created_at)
                VALUES (%s,%s,%s,%s,2,%s,%s,%s,%s,now())''',
                (identity,owner,request,Jsonb(proposal),status,canonical_sha256(proposal) if status=='approved' else None,
                 _key_digest(key) if status=='approved' else None,_key_digest('create-'+key)))
            expected={k:v for k,v in proposal.items() if k not in ('level','exclude')}
            if index==3:expected['goals']=[*expected['goals'],'學習背景與需求：'+level]
            records.append((identity,request,status,key,expected))
    apply_legacy_data_case(clean_database_dsn, '0031_remove_topic_level.sql', '0032_remove_topic_exclusions.sql')
    with psycopg.connect(clean_database_dsn) as connection:
        for identity,request,status,key,expected in records:
            value=connection.execute('SELECT proposal,approved_sha256,request,version,status,approval_key_sha256 FROM topic_scopes WHERE topic_id=%s',(identity,)).fetchone()
            assert value==(expected,canonical_sha256(expected) if status=='approved' else None,request,4,status,
                           _key_digest(key) if status=='approved' else None)
        assert connection.execute("SELECT to_regprocedure('pg_temp.topic_scope_json(jsonb)')").fetchone()==(None,)
    for identity,_,status,key,expected in records:
        if status=='approved':
            # 重送已確認的請求帶確認前看到的版本（確認時版本 +1）。
            assert topics.approve(owner,identity,3,expected,key,dsn=clean_database_dsn)['proposal']==expected
        else:
            with pytest.raises(topics.SourceError,match='TOPIC_CONFLICT'):
                topics.approve(owner,identity,2,expected,key,dsn=clean_database_dsn)
    assert run_migrations(clean_database_dsn)==()


def test_remove_topic_exclusions_keeps_constraints_editable(clean_database_dsn, migrations_dir, tmp_path):
    from psycopg.types.json import Jsonb
    from content_identity import canonical_sha256
    from runtime.storage.artifacts import _key_digest
    from runtime import topics

    # 歷史 JSON 轉換仍獨立驗證；新初始化不再重播這些 DML。
    run_migrations(clean_database_dsn)
    owner=uuid4();records=[]
    with psycopg.connect(clean_database_dsn) as connection:
        connection.execute('INSERT INTO learners (learner_id,created_at) VALUES (%s,now())',(owner,))
        for index,excluded in enumerate([[],['完整程式','雲端部署'],['套件 "A" 與路徑 \\ 的操作教學','   ']]):
            identity=uuid4();status='approved' if index else 'ready';key='exclude-'+str(index)
            original={'title':'Agent 決策','goals':['比較停止與重試'],'topics':['成本、風險與限制'],'exclude':excluded}
            expected={k:v for k,v in original.items() if k!='exclude'}
            expected['goals']=[*expected['goals'],*['範圍限制：不涵蓋「'+item+'」。' for item in excluded if item.strip()]]
            connection.execute('''INSERT INTO topic_scopes
                (topic_id,learner_id,request,proposal,version,status,approved_sha256,approval_key_sha256,idempotency_key_sha256,created_at)
                VALUES (%s,%s,'比較 Agent 決策',%s,8,%s,%s,%s,%s,now())''',
                (identity,owner,Jsonb(original),status,canonical_sha256(original) if status=='approved' else None,
                 _key_digest(key) if status=='approved' else None,_key_digest('create-'+key)))
            records.append((identity,status,key,expected))
    apply_legacy_data_case(clean_database_dsn, '0032_remove_topic_exclusions.sql')
    for identity,status,key,expected in records:
        saved=topics.read(owner,identity,dsn=clean_database_dsn)
        assert saved['version']==9 and saved['status']==status
        assert saved['proposal']==topics.proposal(expected)==expected
        if status=='approved':
            assert topics.approve(owner,identity,8,expected,key,dsn=clean_database_dsn)['proposal']==expected
    assert run_migrations(clean_database_dsn)==()
