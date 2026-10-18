"""固定舊版的獨立 Schema 與必要資料升級案例；不供產品啟動使用。"""

from datetime import datetime, UTC
from pathlib import Path
import psycopg
from runtime.storage.baseline import legacy_manifest

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


def apply_legacy_data_case(dsn, *names):
    with psycopg.connect(dsn) as connection:
        for name in names:
            connection.execute(legacy_sql(name))
