"""領域 baseline 與 runner 的持久資料保護；只使用隔離 PostgreSQL。"""

from concurrent.futures import ThreadPoolExecutor
import shutil
from uuid import uuid4

import psycopg
import pytest

from runtime.storage.migrations import (
    MigrationChecksumError,
    MigrationSqlError,
    load_migrations,
    run_migrations,
)


def test_domain_baseline_and_repeat_preserve_account_and_session(clean_database_dsn):
    assert run_migrations(clean_database_dsn) == (1, 2, 3, 4)
    learner_id, session_id = uuid4(), uuid4()
    with psycopg.connect(clean_database_dsn) as connection:
        connection.execute(
            "INSERT INTO learners (learner_id, created_at, email, password_hash) "
            "VALUES (%s, now(), 'baseline@example.com', 'synthetic-hash')",
            (learner_id,),
        )
        connection.execute(
            """INSERT INTO learner_sessions (
                   session_id, learner_id, token_sha256, created_at,
                   idle_expires_at, absolute_expires_at, revoked_at, updated_at
               ) VALUES (
                   %s, %s, %s, now(), now() + interval '1 day',
                   now() + interval '7 days', NULL, now()
               )""",
            (session_id, learner_id, bytes(32)),
        )
        before = connection.execute(
            "SELECT to_jsonb(l), to_jsonb(s) "
            "FROM learners l JOIN learner_sessions s USING (learner_id)"
        ).fetchall()
        ledger = connection.execute(
            "SELECT * FROM schema_migrations ORDER BY version"
        ).fetchall()
        columns = set(
            connection.execute(
                """SELECT table_name, column_name FROM information_schema.columns
                   WHERE table_schema = 'public'
                     AND table_name IN (
                         'learners', 'assessment_sets', 'material_processing_runs', 'materials'
                     )"""
            ).fetchall()
        )
        assert {
            ('learners', 'email'),
            ('assessment_sets', 'diagnostic_set_id'),
            ('material_processing_runs', 'runtime_lock_document'),
        } <= columns
        assert not {
            ('learners', 'username'),
            ('assessment_sets', 'review_actions'),
            ('assessment_sets', 'cycle_closed_at'),
            ('materials', 'ingestion_kind'),
        } & columns
        enabled_triggers = {
            row[0] for row in connection.execute(
                "SELECT tgname FROM pg_trigger WHERE NOT tgisinternal AND tgenabled = 'O'"
            )
        }
        assert {
            'source_identity_immutable',
            'source_set_immutable',
            'source_set_item_immutable',
            'normalization_ready_immutable',
            'assessment_set_item_immutable',
            'assessment_set_scope_immutable',
            'assessment_set_origin_immutable',
        } <= enabled_triggers
    assert run_migrations(clean_database_dsn) == ()
    with psycopg.connect(clean_database_dsn) as connection:
        assert connection.execute(
            "SELECT to_jsonb(l), to_jsonb(s) "
            "FROM learners l JOIN learner_sessions s USING (learner_id)"
        ).fetchall() == before
        assert connection.execute(
            "SELECT * FROM schema_migrations ORDER BY version"
        ).fetchall() == ledger


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
    assert 'ingestion_kind' not in item
    with psycopg.connect(clean_database_dsn) as connection:
        with pytest.raises(psycopg.errors.CheckViolation, match='artifact_role_media'):
            with connection.transaction():
                connection.execute(
                    """INSERT INTO artifacts (
                           artifact_id,learner_id,material_id,kind,media_type,sha256,size_bytes,created_at
                       ) VALUES (%s,%s,%s,'source_pdf','application/pdf',%s,1,now())""",
                    (uuid4(), learner_id, material_id, bytes(32)),
                )
        assert connection.execute("SELECT to_regclass('public.artifacts_one_source_pdf')").fetchone() == (None,)


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
            "INSERT INTO schema_migrations VALUES (14, repeat('0', 64), now())",
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
    next_version = candidate / '0005_probe.sql'
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
        ).fetchone() == (4,)
    next_version.write_text('CREATE TABLE migration_probe (id integer);')
    assert run_migrations(clean_database_dsn, migrations_dir=candidate) == (5,)
    assert run_migrations(clean_database_dsn, migrations_dir=candidate) == ()


def test_concurrent_install_applies_each_version_once(clean_database_dsn):
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: run_migrations(clean_database_dsn), range(2)))
    assert sorted(results) == [(), (1, 2, 3, 4)]
    with psycopg.connect(clean_database_dsn) as connection:
        assert dict(connection.execute(
            'SELECT version, sql_sha256 FROM schema_migrations'
        )) == {
            m.version: m.sql_sha256 for m in load_migrations()
        }
