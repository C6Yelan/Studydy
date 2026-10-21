"""固定舊版的獨立 Schema 與必要資料升級案例；不供產品啟動使用。"""

from datetime import datetime, UTC
from pathlib import Path
import shutil
from uuid import uuid4
import psycopg
from runtime.learner_session import _add_session, _password_hash
from runtime.storage.baseline import BASELINE_VERSIONS, legacy_manifest
from runtime.storage.database import database_session
from runtime.storage.migrations import DEFAULT_MIGRATIONS_DIR

FIXTURES = Path(__file__).parent / "fixtures"


def legacy_sql(name):
    return (FIXTURES / "legacy_migrations" / name).read_text()


def create_legacy_database(dsn):
    with psycopg.connect(dsn) as connection:
        connection.execute((FIXTURES / "legacy_0032.sql").read_text())
        with connection.cursor() as cursor:
            cursor.executemany(
                "INSERT INTO schema_migrations VALUES (%s,%s,%s)",
                [
                    (int(version), checksum, datetime(2026, 10, 18, tzinfo=UTC))
                    for version, checksum in legacy_manifest()["migrations"].items()
                ],
            )


def legacy_account(dsn, email, password):
    """舊 Schema 沒有 baseline 之後新增的欄位，帳號以原欄位直接寫入，session 沿用產品流程。"""
    learner_id = uuid4()
    with psycopg.connect(dsn) as connection:
        connection.execute(
            "INSERT INTO learners (learner_id, created_at, email, password_hash) VALUES (%s, now(), %s, %s)",
            (learner_id, email, _password_hash(password)),
        )
    with database_session(dsn) as session:
        return _add_session(session, learner_id)


def baseline_migrations_dir(tmp_path):
    """只含 baseline 版本的 migration 目錄，用來單獨驗證接軌語意。"""
    target = tmp_path / "baseline-migrations"
    target.mkdir()
    for path in sorted(DEFAULT_MIGRATIONS_DIR.glob("*.sql"))[:BASELINE_VERSIONS]:
        shutil.copy(path, target / path.name)
    return target


def apply_legacy_data_case(dsn, *names):
    with psycopg.connect(dsn) as connection:
        for name in names:
            connection.execute(legacy_sql(name))
