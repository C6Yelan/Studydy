import os
import sys
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

if os.environ.get("TEST_DATABASE_URL"):
    os.environ["DATABASE_URL"] = os.environ["TEST_DATABASE_URL"]

from app.db import engine as app_engine


@pytest.fixture(scope="session")
def database_url() -> str:
    value = os.environ.get("TEST_DATABASE_URL")
    if not value:
        pytest.skip("destructive tests require TEST_DATABASE_URL pointing to a test database")
    _guard_test_database_url(value)
    return value


TABLES = [
    "evidence",
    "learning_path_nodes",
    "concept_relations",
    "learning_paths",
    "material_blocks",
    "concepts",
    "materials",
]


def _guard_test_database_url(database_url: str) -> None:
    try:
        database_name = make_url(database_url).database
    except Exception as exc:
        pytest.fail(
            "destructive tests require TEST_DATABASE_URL pointing to a test database; "
            f"could not parse database URL: {exc}",
            pytrace=False,
        )

    if not database_name or "test" not in database_name.lower():
        pytest.fail(
            "destructive tests require TEST_DATABASE_URL pointing to a test database; "
            f"database name must contain 'test', got {database_name!r}",
            pytrace=False,
        )


def _truncate_tables(db_engine, database_url: str) -> None:
    _guard_test_database_url(database_url)
    with db_engine.begin() as connection:
        connection.execute(text(f"truncate table {', '.join(TABLES)} restart identity cascade"))


@pytest.fixture(scope="session")
def migrated_database(database_url: str) -> str:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url)

    _guard_test_database_url(database_url)
    command.downgrade(config, "base")
    _guard_test_database_url(database_url)
    command.upgrade(config, "head")
    yield database_url
    app_engine.dispose()
    _guard_test_database_url(database_url)
    command.downgrade(config, "base")


@pytest.fixture()
def engine(migrated_database: str):
    db_engine = create_engine(migrated_database)
    try:
        _truncate_tables(db_engine, migrated_database)
        yield db_engine
        _truncate_tables(db_engine, migrated_database)
    finally:
        db_engine.dispose()
