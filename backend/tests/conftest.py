import os
import sys
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.db import engine as app_engine


@pytest.fixture(scope="session")
def database_url() -> str:
    value = os.environ.get("DATABASE_URL")
    if not value:
        pytest.skip("DATABASE_URL is required and must point to a test database")
    _guard_test_database_url(value)
    return value


def _guard_test_database_url(database_url: str) -> None:
    try:
        database_name = make_url(database_url).database
    except Exception as exc:
        pytest.fail(
            "DATABASE_URL must point to a test database before schema seed tests run; "
            f"could not parse database URL: {exc}",
            pytrace=False,
        )

    if not database_name or "test" not in database_name.lower():
        pytest.fail(
            "DATABASE_URL must point to a test database before schema seed tests run; "
            f"database name must contain 'test', got {database_name!r}",
            pytrace=False,
        )


@pytest.fixture(scope="session")
def migrated_database(database_url: str) -> str:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url)

    _guard_test_database_url(database_url)
    command.upgrade(config, "head")
    yield database_url
    app_engine.dispose()


@pytest.fixture()
def engine(migrated_database: str):
    yield app_engine
