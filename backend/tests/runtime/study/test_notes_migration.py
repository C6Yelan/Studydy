"""0015 真實舊資料升級 0016：只新增筆記與註記，不重寫教材／題組。"""
from pathlib import Path
import pytest
from sqlalchemy import text
from assessment_fixtures import concept_fixture, create, finish, answer
from product_fixtures import closed_loop
from runtime import knowledge_publication, material_notes
from runtime.storage.database import database_session
from runtime.storage.migrations import DEFAULT_MIGRATIONS_DIR, run_migrations


@pytest.fixture
def migrations_dir(tmp_path, monkeypatch):
    target = tmp_path / "migrations-0015"
    target.mkdir()
    for source in DEFAULT_MIGRATIONS_DIR.glob("*.sql"):
        if int(source.name[:4]) <= 15:
            (target / source.name).write_bytes(source.read_bytes())
    monkeypatch.setattr(knowledge_publication, "enqueue_notes", lambda *args: None)
    return target


def snapshot(dsn):
    with database_session(dsn) as db:
        names = db.scalars(text("SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename NOT IN ('schema_migrations','material_notes','note_annotations') ORDER BY tablename")).all()
        return {name: db.scalars(text(f'SELECT row_to_json(t)::text FROM "{name}" t ORDER BY row_to_json(t)::text')).all() for name in names}


def test_upgrade_keeps_every_existing_table_and_can_queue_notes(closed_loop):
    f = concept_fixture(closed_loop, 2)
    root = create(f)
    finish(f)
    answer(f, root)
    before = snapshot(f["dsn"])
    assert run_migrations(f["dsn"]) == (16,)
    assert snapshot(f["dsn"]) == before
    assert run_migrations(f["dsn"]) == ()
    queued = material_notes.queue_existing(owner=f["learner"].learner_id, dsn=f["dsn"])
    assert queued == 2
