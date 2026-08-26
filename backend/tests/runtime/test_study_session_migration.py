from __future__ import annotations

from pathlib import Path
import shutil
from uuid import uuid4

import psycopg
from psycopg.types.json import Jsonb
import pytest

from runtime.storage.migrations import MigrationSqlError, run_migrations


_DORMANT_TABLES = (
    "learning_paths",
    "assessments",
    "answer_events",
    "learning_states",
)


def _copy_migrations(
    source_directory: Path, target_directory: Path, through_version: int
) -> None:
    target_directory.mkdir()
    for source in sorted(source_directory.glob("*.sql")):
        if int(source.name[:4]) <= through_version:
            shutil.copy2(source, target_directory / source.name)


def _schema_signature(connection: psycopg.Connection):
    tables = connection.execute(
        """
        SELECT tablename
        FROM pg_catalog.pg_tables
        WHERE schemaname = 'public'
        ORDER BY tablename
        """
    ).fetchall()
    columns = connection.execute(
        """
        SELECT table_name, column_name, data_type, is_nullable, column_default
        FROM information_schema.columns
        WHERE table_schema = 'public'
        ORDER BY table_name, ordinal_position
        """
    ).fetchall()
    constraints = connection.execute(
        """
        SELECT conrelid::regclass::text, conname, pg_get_constraintdef(oid)
        FROM pg_catalog.pg_constraint
        WHERE connamespace = 'public'::regnamespace
        ORDER BY conrelid::regclass::text, conname
        """
    ).fetchall()
    indexes = connection.execute(
        """
        SELECT tablename, indexname, indexdef
        FROM pg_catalog.pg_indexes
        WHERE schemaname = 'public'
        ORDER BY tablename, indexname
        """
    ).fetchall()
    ledger = connection.execute(
        "SELECT version, sql_sha256 FROM schema_migrations ORDER BY version"
    ).fetchall()
    return tables, columns, constraints, indexes, ledger


def _insert_dormant_row(connection: psycopg.Connection, table_name: str) -> None:
    learner_id = uuid4()
    material_id = uuid4()
    connection.execute("SET session_replication_role = replica")
    if table_name == "learning_paths":
        connection.execute(
            """
            INSERT INTO learning_paths (
                learner_id, material_id, path_revision, map_revision,
                document, created_at
            ) VALUES (%s, %s, %s, %s, %s, clock_timestamp())
            """,
            (
                learner_id,
                material_id,
                "path-fixture",
                "map-fixture",
                Jsonb(
                    {
                        "revision": "path-fixture",
                        "knowledge_map_revision": "map-fixture",
                    }
                ),
            ),
        )
    elif table_name == "assessments":
        connection.execute(
            """
            INSERT INTO assessments (
                learner_id, material_id, assessment_revision, output_revision,
                map_revision, path_revision, public_document,
                answer_key_document, created_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, clock_timestamp())
            """,
            (
                learner_id,
                material_id,
                "assessment-fixture",
                "output-fixture",
                "map-fixture",
                "path-fixture",
                Jsonb({"assessment_view_id": "public-fixture"}),
                Jsonb({"assessment_id": "private-fixture"}),
            ),
        )
    elif table_name == "answer_events":
        connection.execute(
            """
            INSERT INTO answer_events (
                answer_event_id, submission_id, learner_id, material_id,
                assessment_revision, question_id, document, created_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, clock_timestamp())
            """,
            (
                "answer-event-fixture",
                uuid4(),
                learner_id,
                material_id,
                "assessment-fixture",
                "question-fixture",
                Jsonb({"answer_event_id": "answer-event-fixture"}),
            ),
        )
    elif table_name == "learning_states":
        connection.execute(
            """
            INSERT INTO learning_states (
                state_revision, submission_id, learner_id, material_id,
                map_revision, path_revision, assessment_revision,
                idempotency_key_sha256, request_fingerprint, document, created_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, clock_timestamp())
            """,
            (
                "state-fixture",
                uuid4(),
                learner_id,
                material_id,
                "map-fixture",
                "path-fixture",
                "assessment-fixture",
                b"i" * 32,
                b"f" * 32,
                Jsonb({"revision": "state-fixture"}),
            ),
        )
    else:
        raise AssertionError("未知的 dormant table fixture")
    connection.execute("SET session_replication_role = origin")


def test_fresh_migrations_replace_only_empty_dormant_tables(
    clean_database_dsn: str, migrations_dir: Path
):
    assert run_migrations(clean_database_dsn, migrations_dir=migrations_dir) == (
        1,
        2,
        3,
        4,
        5,
        6,
        7,
        8,
        9,
        10,
        11,
        12,
    )
    with psycopg.connect(clean_database_dsn) as connection:
        assert connection.execute(
            "SELECT to_regclass('public.study_sessions')"
        ).fetchone() == ("study_sessions",)
        for table_name in ("learning_paths", "learning_states"):
            assert connection.execute(
                "SELECT to_regclass(%s)", (f"public.{table_name}",)
            ).fetchone() == (None,)
        assert connection.execute(
            "SELECT to_regclass('public.answer_events')"
        ).fetchone() == ("answer_events",)
        assert connection.execute(
            "SELECT to_regclass('public.assessments')"
        ).fetchone() == ("assessments",)
        foreign_tables = {
            row[0]
            for row in connection.execute(
                """
                SELECT confrelid::regclass::text
                FROM pg_catalog.pg_constraint
                WHERE conrelid = 'study_sessions'::regclass AND contype = 'f'
                """
            ).fetchall()
        }
        assert foreign_tables == {"knowledge_maps"}
        assert connection.execute(
            "SELECT status, last_event_number FROM study_sessions"
        ).fetchall() == []


@pytest.mark.parametrize("populated", [*_DORMANT_TABLES, "all"])
def test_nonempty_dormant_table_rolls_back_schema_and_ledger_without_output(
    populated: str,
    clean_database_dsn: str,
    migrations_dir: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
):
    migration_directory = tmp_path / "migration-gate"
    _copy_migrations(migrations_dir, migration_directory, 6)
    assert run_migrations(
        clean_database_dsn, migrations_dir=migration_directory
    ) == (1, 2, 3, 4, 5, 6)
    selected_tables = _DORMANT_TABLES if populated == "all" else (populated,)
    with psycopg.connect(clean_database_dsn) as connection:
        for table_name in selected_tables:
            _insert_dormant_row(connection, table_name)
    shutil.copy2(
        migrations_dir / "0007_retire_dormant_learning_tables_and_add_study_sessions.sql",
        migration_directory,
    )
    with psycopg.connect(clean_database_dsn) as connection:
        before = _schema_signature(connection)

    with pytest.raises(MigrationSqlError, match="^MIGRATION_SQL_FAILED$"):
        run_migrations(clean_database_dsn, migrations_dir=migration_directory)

    assert capsys.readouterr() == ("", "")
    with psycopg.connect(clean_database_dsn) as connection:
        assert _schema_signature(connection) == before
        assert connection.execute(
            "SELECT to_regclass('public.study_sessions')"
        ).fetchone() == (None,)
        assert connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall() == [(1,), (2,), (3,), (4,), (5,), (6,)]
        for table_name in selected_tables:
            assert connection.execute(
                f"SELECT count(*) FROM {table_name}"
            ).fetchone() == (1,)
