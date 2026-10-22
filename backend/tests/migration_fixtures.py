"""歷史資料轉換測試使用的固定 SQL；不供產品啟動使用。"""

from pathlib import Path
import psycopg

FIXTURES = Path(__file__).parent / "fixtures"


def legacy_sql(name):
    return (FIXTURES / "legacy_migrations" / name).read_text()


def apply_legacy_data_case(dsn, *names):
    with psycopg.connect(dsn) as connection:
        for name in names:
            connection.execute(legacy_sql(name))
