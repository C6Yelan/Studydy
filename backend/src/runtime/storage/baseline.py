"""新資料庫初始化的識別標記與 migration prefix 驗證。"""

from datetime import datetime, UTC

from psycopg.types.json import Jsonb

from content_identity import canonical_sha256

BASELINE_ID = "studydy-schema-20261019"
BASELINE_VERSIONS = 7
CONTROL_SQL = """CREATE TABLE public.schema_baseline (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    baseline_id text NOT NULL,
    prefix_sha256 character(64) NOT NULL CHECK (prefix_sha256 ~ '^[0-9a-f]{64}$'),
    method text NOT NULL CHECK (method IN ('fresh', 'adopted')),
    recorded_at timestamptz NOT NULL,
    legacy_ledger jsonb,
    verification jsonb NOT NULL
)"""


class BaselineError(RuntimeError):
    """只回傳固定原因碼；不回顯 SQL、教材或連線設定。"""


def prefix_digest(migrations):
    if len(migrations) < BASELINE_VERSIONS:
        raise BaselineError("BASELINE_FILES_INCOMPLETE")
    return canonical_sha256(
        {str(m.version): m.sql_sha256 for m in migrations[:BASELINE_VERSIONS]}
    )


def read_marker(connection):
    if (
        connection.execute("SELECT to_regclass('public.schema_baseline')").fetchone()[0]
        is None
    ):
        return None
    rows = connection.execute(
        "SELECT baseline_id,prefix_sha256,method,legacy_ledger,verification FROM schema_baseline"
    ).fetchall()
    if len(rows) != 1:
        raise BaselineError("BASELINE_MARKER_INVALID")
    return rows[0]


def verify_marker(marker, migrations):
    if marker[0] != BASELINE_ID or marker[1] != prefix_digest(migrations):
        raise BaselineError("BASELINE_IDENTITY_MISMATCH")


def record_fresh(connection, migrations):
    connection.execute(
        "INSERT INTO schema_baseline VALUES (true,%s,%s,%s,%s,NULL,%s)",
        (BASELINE_ID, prefix_digest(migrations), "fresh", datetime.now(UTC), Jsonb({})),
    )
