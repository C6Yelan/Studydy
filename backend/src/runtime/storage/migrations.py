from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import re
from typing import Any

import psycopg
from psycopg import Connection

from .database import connect_database

DEFAULT_MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "migrations"
MIGRATION_FILENAME = re.compile(r"^(?P<version>[0-9]{4})_[a-z0-9_]+\.sql$")
MIGRATION_LOCK_KEY = 7_879_485_451_541_603_879


class MigrationError(RuntimeError):
    """Migration 無法安全完成。"""


class MigrationChecksumError(MigrationError):
    """已套用 SQL 與目前檔案不一致。"""


class MigrationLockError(MigrationError):
    """無法取得或正確釋放 migration lock。"""


class MigrationSqlError(MigrationError):
    """SQL 執行失敗，該版本的 transaction 已回滾。"""


@dataclass(frozen=True)
class Migration:
    version: int
    sql: str
    sql_sha256: str


def load_migrations(migrations_dir: Path = DEFAULT_MIGRATIONS_DIR) -> tuple[Migration, ...]:
    """載入連續且命名明確的 migration，拒絕模糊順序。"""

    try:
        sql_paths = sorted(migrations_dir.glob("*.sql"))
    except OSError:
        raise MigrationError("MIGRATION_FILES_UNAVAILABLE") from None
    if not sql_paths:
        raise MigrationError("MIGRATION_FILES_MISSING")

    migrations: list[Migration] = []
    for path in sql_paths:
        match = MIGRATION_FILENAME.fullmatch(path.name)
        if match is None:
            raise MigrationError("MIGRATION_FILENAME_INVALID")
        try:
            sql = path.read_text(encoding="utf-8")
        except OSError:
            raise MigrationError("MIGRATION_FILE_UNREADABLE") from None
        if not sql.strip():
            raise MigrationError("MIGRATION_SQL_EMPTY")
        migrations.append(
            Migration(
                version=int(match.group("version")),
                sql=sql,
                sql_sha256=sha256(sql.encode("utf-8")).hexdigest(),
            )
        )

    versions = [migration.version for migration in migrations]
    if versions != list(range(1, len(migrations) + 1)):
        raise MigrationError("MIGRATION_VERSION_SEQUENCE_INVALID")
    return tuple(migrations)


def _read_applied_migrations(connection: Connection[Any]) -> dict[int, str]:
    table_name = connection.execute(
        "SELECT to_regclass(%s)", ("public.schema_migrations",)
    ).fetchone()
    if table_name is None or table_name[0] is None:
        return {}
    rows = connection.execute(
        "SELECT version, sql_sha256 FROM schema_migrations ORDER BY version"
    ).fetchall()
    return {version: checksum for version, checksum in rows}


def _verify_applied_migrations(
    migrations: tuple[Migration, ...],
    applied: dict[int, str],
) -> None:
    expected = {migration.version: migration.sql_sha256 for migration in migrations}
    if set(applied) - set(expected):
        raise MigrationChecksumError("MIGRATION_VERSION_UNKNOWN")
    if sorted(applied) != list(range(1, len(applied) + 1)):
        raise MigrationChecksumError("MIGRATION_LEDGER_SEQUENCE_INVALID")
    for version, checksum in applied.items():
        if expected[version] != checksum:
            raise MigrationChecksumError("MIGRATION_CHECKSUM_DRIFT")


def run_migrations(
    dsn: str | None = None,
    *,
    migrations_dir: Path = DEFAULT_MIGRATIONS_DIR,
) -> tuple[int, ...]:
    """依序執行尚未套用的版本，每版 schema 與 ledger 同進退。"""

    migrations = load_migrations(migrations_dir)
    applied_now: list[int] = []
    connection = connect_database(dsn, autocommit=True)
    lock_acquired = False
    try:
        try:
            connection.execute("SELECT pg_advisory_lock(%s)", (MIGRATION_LOCK_KEY,))
            lock_acquired = True
        except psycopg.Error:
            raise MigrationLockError("MIGRATION_LOCK_FAILED") from None

        try:
            applied = _read_applied_migrations(connection)
        except psycopg.Error:
            raise MigrationSqlError("MIGRATION_LEDGER_READ_FAILED") from None
        _verify_applied_migrations(migrations, applied)

        for migration in migrations:
            if migration.version in applied:
                continue
            try:
                with connection.transaction():
                    connection.execute(migration.sql)
                    connection.execute(
                        """
                        INSERT INTO schema_migrations (
                            version, sql_sha256, applied_at
                        ) VALUES (%s, %s, statement_timestamp())
                        """,
                        (migration.version, migration.sql_sha256),
                    )
            except psycopg.Error:
                raise MigrationSqlError("MIGRATION_SQL_FAILED") from None
            applied_now.append(migration.version)
    finally:
        try:
            if lock_acquired:
                connection.execute(
                    "SELECT pg_advisory_unlock(%s)", (MIGRATION_LOCK_KEY,)
                )
        except psycopg.Error:
            pass
        finally:
            try:
                connection.close()
            except Exception:
                pass
    return tuple(applied_now)
