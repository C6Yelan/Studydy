"""固定舊版的獨立 Schema 與必要資料升級案例；不供產品啟動使用。"""

from datetime import datetime, UTC
from hashlib import sha256
from pathlib import Path
import secrets
import shutil
from uuid import uuid4
import psycopg
from runtime.learner_session import (
    ABSOLUTE_LIFETIME, IDLE_LIFETIME, CreatedSession, _encode_token, _password_hash,
)
from runtime.storage.baseline import BASELINE_VERSIONS, legacy_manifest
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
    """舊 Schema 沒有 baseline 之後新增的欄位，帳號與 session 都以原欄位直接寫入。"""
    learner_id = uuid4()
    token_bytes = secrets.token_bytes(32)
    now = datetime.now(UTC)
    with psycopg.connect(dsn) as connection:
        connection.execute(
            "INSERT INTO learners (learner_id, created_at, email, password_hash) VALUES (%s, now(), %s, %s)",
            (learner_id, email, _password_hash(password)),
        )
        connection.execute(
            "INSERT INTO learner_sessions (session_id, learner_id, token_sha256, created_at, "
            "idle_expires_at, absolute_expires_at, revoked_at, updated_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, NULL, %s)",
            (uuid4(), learner_id, sha256(token_bytes).digest(), now,
             now + IDLE_LIFETIME, now + ABSOLUTE_LIFETIME, now),
        )
    return CreatedSession(learner_id=learner_id, raw_token=_encode_token(token_bytes))


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
