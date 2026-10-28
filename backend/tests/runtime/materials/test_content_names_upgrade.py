"""名稱限制升級使用真實舊 schema 與隔離資料庫。"""

import shutil
from uuid import uuid4

import psycopg
import pytest

from product_fixtures import closed_loop
from runtime import card_sets, podcasts
from runtime.storage.migrations import DEFAULT_MIGRATIONS_DIR, run_migrations


@pytest.fixture
def migrations_dir(tmp_path):
    previous = tmp_path / "before_content_names"
    previous.mkdir()
    for path in DEFAULT_MIGRATIONS_DIR.glob("*.sql"):
        if int(path.name[:4]) < 31:
            shutil.copy2(path, previous / path.name)
    return previous


def test_upgrade_truncates_all_names_and_preserves_deleted_cards(closed_loop):
    owner, source, _, document, dsn, _ = closed_loop
    ids = [c["concept_id"] for c in document["concepts"]]
    cards = card_sets.create_card_set(owner.learner_id, source.material_id, document["revision"], "短名稱", ids, "cards", dsn=dsn)
    podcast = podcasts.create_podcast(owner.learner_id, source.material_id, document["revision"], "短名稱", ids, "podcast", delivery="solo", dsn=dsn)
    deleted = card_sets.create_card_set(owner.learner_id, source.material_id, document["revision"], "刪除前", ids, "deleted", dsn=dsn)
    card_sets.delete_card_set(owner.learner_id, deleted["card_set_id"], dsn=dsn)
    names = ["字" * 40, "課" * 80, "🐍" * 50, " " * 45 + "有效名稱"]
    with psycopg.connect(dsn) as db:
        for table, identity in [("card_sets", cards["card_set_id"]), ("podcasts", podcast["podcast_id"])]:
            for index, name in enumerate(names):
                digest = bytes([index + 1]) * 32
                if table == "card_sets":
                    db.execute("""INSERT INTO card_sets (card_set_id,learner_id,material_id,knowledge_structure_revision,name,concept_ids,idempotency_key_sha256,request_fingerprint,created_at)
                        SELECT %s,learner_id,material_id,knowledge_structure_revision,%s,concept_ids,%s,request_fingerprint,now() FROM card_sets WHERE card_set_id=%s""", (uuid4(), name, digest, identity))
                else:
                    db.execute("""INSERT INTO podcasts (podcast_id,learner_id,material_id,knowledge_structure_revision,name,mode,concept_ids,episodes,status,video_enabled,idempotency_key_sha256,request_fingerprint,created_at)
                        SELECT %s,learner_id,material_id,knowledge_structure_revision,%s,mode,concept_ids,episodes,status,video_enabled,%s,request_fingerprint,now() FROM podcasts WHERE podcast_id=%s""", (uuid4(), name, digest, identity))
            # 超過常見單批大小，驗證升級沒有只更新首批的限制。
            if table == "card_sets":
                db.execute("""INSERT INTO card_sets (card_set_id,learner_id,material_id,knowledge_structure_revision,name,concept_ids,idempotency_key_sha256,request_fingerprint,created_at)
                    SELECT gen_random_uuid(),learner_id,material_id,knowledge_structure_revision,repeat('長',80),concept_ids,sha256(g::text::bytea),request_fingerprint,now()
                    FROM card_sets CROSS JOIN generate_series(1,1001) g WHERE card_set_id=%s""", (identity,))
            else:
                db.execute("""INSERT INTO podcasts (podcast_id,learner_id,material_id,knowledge_structure_revision,name,mode,concept_ids,episodes,status,video_enabled,idempotency_key_sha256,request_fingerprint,created_at)
                    SELECT gen_random_uuid(),learner_id,material_id,knowledge_structure_revision,repeat('長',80),mode,concept_ids,episodes,status,video_enabled,sha256(g::text::bytea),request_fingerprint,now()
                    FROM podcasts CROSS JOIN generate_series(1,1001) g WHERE podcast_id=%s""", (identity,))
    assert run_migrations(dsn) == (31,)
    with psycopg.connect(dsn) as db:
        for table in ("card_sets", "podcasts"):
            rows = db.execute(f"SELECT name FROM {table}").fetchall()
            assert all(len(name) <= 40 for name, in rows)
            assert all((name.strip()[:40],) in rows for name in names)
            assert sum(name == "長" * 40 for name, in rows) == 1001
            assert ("短名稱",) in rows
        assert db.execute("SELECT name,knowledge_structure_revision,concept_ids FROM card_sets WHERE card_set_id=%s", (deleted["card_set_id"],)).fetchone() == ("", None, [])
        with pytest.raises(psycopg.errors.CheckViolation, match="card_sets_check"):
            with db.transaction():
                db.execute("UPDATE card_sets SET name=%s WHERE card_set_id=%s", ("字" * 41, cards["card_set_id"]))
