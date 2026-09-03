from __future__ import annotations

from psycopg.conninfo import conninfo_to_dict
import pytest

from conftest import DatabaseDsn, _test_postgres_dsn_from_environment


def test_explicit_test_postgres_dsn_is_redacted_and_keeps_test_database():
    dsn = _test_postgres_dsn_from_environment(
        {
            "STUDYDY_TEST_POSTGRES_DSN": (
                "host=127.0.0.1 dbname=studydy_test_control "
                "user=studydy_test_owner"
            )
        }
    )

    assert isinstance(dsn, DatabaseDsn)
    assert repr(dsn) == "<redacted database DSN>"
    assert conninfo_to_dict(dsn)["dbname"] == "studydy_test_control"
    assert conninfo_to_dict(dsn)["connect_timeout"] == "2"


@pytest.mark.parametrize(
    "environment",
    [
        {"STUDYDY_TEST_POSTGRES_DSN": "dbname=studydy_v2"},
        {
            "STUDYDY_TEST_POSTGRES_DSN": (
                "host=database.example dbname=studydy_test_control"
            )
        },
        {
            "STUDYDY_TEST_POSTGRES_DSN": (
                "host=127.0.0.1 dbname=studydy_test_control user=tester"
            ),
            "STUDYDY_DATABASE_DSN": (
                "user=tester dbname=studydy_test_control host=127.0.0.1"
            ),
        },
        {"STUDYDY_TEST_POSTGRES_DSN": "not-a-dsn"},
    ],
)
def test_explicit_test_postgres_dsn_rejects_production_or_invalid_targets(
    environment,
):
    with pytest.raises(ValueError, match="TEST_POSTGRES_DSN_INVALID"):
        _test_postgres_dsn_from_environment(environment)
