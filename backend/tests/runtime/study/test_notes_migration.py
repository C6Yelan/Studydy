"""0016 真實舊資料升級 0017：只新增筆記與註記，不重寫教材／題組；0017 升級 0018 只加空的診斷欄位。"""
import json
from pathlib import Path
import pytest
from sqlalchemy import text
from assessment_fixtures import concept_fixture, create, finish, answer
from product_fixtures import closed_loop
from learning_adaptation import learning_goals
from runtime import knowledge_publication, material_notes
from runtime.storage.database import database_session
from runtime.storage.migrations import DEFAULT_MIGRATIONS_DIR, run_migrations


@pytest.fixture
def migrations_dir(request, tmp_path, monkeypatch):
    last = getattr(request, "param", 16)
    monkeypatch.setattr(knowledge_publication, "enqueue_notes", lambda *args: None)
    # 舊版資料表沒有後續 migration 新增的目標欄位，ORM 不能讀目標；這些案例沒有設定目標，略過目標的重排與日基準。
    for name in ("rebase_goal_for_revision", "freeze_goal_day", "record_goal_milestones"):
        monkeypatch.setattr(learning_goals, name, lambda *args: None)
    return upto(tmp_path, last)


def upto(tmp_path, last):
    target = tmp_path / f"migrations-{last:04d}"
    target.mkdir(exist_ok=True)
    for source in DEFAULT_MIGRATIONS_DIR.glob("*.sql"):
        if int(source.name[:4]) <= last:
            (target / source.name).write_bytes(source.read_bytes())
    return target


def snapshot(dsn):
    with database_session(dsn) as db:
        names = db.scalars(text("SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename NOT IN ('schema_migrations','material_notes','note_annotations') ORDER BY tablename")).all()
        return {name: db.scalars(text(f'SELECT row_to_json(t)::text FROM "{name}" t ORDER BY row_to_json(t)::text')).all() for name in names}


def test_upgrade_keeps_every_existing_table_and_can_queue_notes(closed_loop, tmp_path):
    f = concept_fixture(closed_loop, 2)
    root = create(f)
    finish(f)
    answer(f, root)
    before = snapshot(f["dsn"])
    assert run_migrations(f["dsn"], migrations_dir=upto(tmp_path, 18)) == (17, 18)
    assert snapshot(f["dsn"]) == before
    assert run_migrations(f["dsn"], migrations_dir=upto(tmp_path, 18)) == ()
    queued = material_notes.queue_existing(owner=f["learner"].learner_id, dsn=f["dsn"])
    assert queued == 2


@pytest.mark.parametrize("migrations_dir", [17], indirect=True)
def test_0018_adds_empty_diagnostics_without_changing_existing_rows(closed_loop, tmp_path):
    f = concept_fixture(closed_loop, 2)
    root = create(f)
    finish(f)
    answer(f, root)
    with database_session(f["dsn"]) as db:
        # 0017 的資料庫尚無新欄位，不能經 ORM 讀取；以 0017 欄位建立失敗與待處理筆記。
        material_notes.enqueue(db, f["learner"].learner_id, f["source"].material_id, f["document"]["revision"])
        material_notes.enqueue(db, f["learner"].learner_id, f["source"].material_id, f["document"]["revision"], "full")
        db.execute(text("UPDATE material_notes SET status='failed', error_code='NOTE_CONTENT_UNSUPPORTED', calls=3 WHERE kind='full'"))

    def rows(dsn):
        with database_session(dsn) as db:
            names = db.scalars(text("SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename <> 'schema_migrations' ORDER BY tablename")).all()
            return {name: sorted(json.dumps({k: v for k, v in row.items() if k != "generation_diagnostics"}, sort_keys=True, default=str)
                for row in db.execute(text(f'SELECT row_to_json(t) AS r FROM "{name}" t')).scalars())
                for name in names}
    before = rows(f["dsn"])
    assert run_migrations(f["dsn"], migrations_dir=upto(tmp_path, 18)) == (18,)
    assert rows(f["dsn"]) == before
    with database_session(f["dsn"]) as db:
        assert db.scalars(text("SELECT generation_diagnostics FROM material_notes")).all() == [None, None]
    assert run_migrations(f["dsn"], migrations_dir=upto(tmp_path, 18)) == ()
