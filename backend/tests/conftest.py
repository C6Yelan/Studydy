import os
import sys
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

if os.environ.get("TEST_DATABASE_URL"):
    os.environ["DATABASE_URL"] = os.environ["TEST_DATABASE_URL"]

from app.db import engine as app_engine


@pytest.fixture(scope="session")
def database_url() -> str:
    value = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not value:
        pytest.skip("TEST_DATABASE_URL or DATABASE_URL is required for PostgreSQL schema tests")
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


def _truncate_tables(db_engine) -> None:
    with db_engine.begin() as connection:
        connection.execute(text(f"truncate table {', '.join(TABLES)} restart identity cascade"))


@pytest.fixture(scope="session")
def migrated_database(database_url: str) -> str:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url)

    command.downgrade(config, "base")
    command.upgrade(config, "head")
    yield database_url
    app_engine.dispose()
    command.downgrade(config, "base")


@pytest.fixture()
def engine(migrated_database: str):
    db_engine = create_engine(migrated_database)
    try:
        _truncate_tables(db_engine)
        yield db_engine
        _truncate_tables(db_engine)
    finally:
        db_engine.dispose()
