"""0018 既有資料升級 0019：既有節目標為已啟用影片，其餘列與 WAV artifact 不變，Podcast 音訊可存 FLAC。"""
import json

import psycopg
import pytest
from sqlalchemy import text

from product_fixtures import closed_loop  # noqa: F401
from runtime.storage.database import database_session
from runtime.storage.migrations import DEFAULT_MIGRATIONS_DIR, run_migrations


@pytest.fixture
def migrations_dir(tmp_path):
    target = tmp_path / "migrations-0018"
    target.mkdir()
    for source in DEFAULT_MIGRATIONS_DIR.glob("*.sql"):
        if int(source.name[:4]) <= 18:
            (target / source.name).write_bytes(source.read_bytes())
    return target


def rows(dsn):
    with database_session(dsn) as db:
        names = db.scalars(text("SELECT tablename FROM pg_tables WHERE schemaname='public' "
                                "AND tablename <> 'schema_migrations' ORDER BY tablename")).all()
        return {name: sorted(json.dumps({k: v for k, v in row.items() if k != "video_enabled"}, sort_keys=True, default=str)
                for row in db.execute(text(f'SELECT row_to_json(t) AS r FROM "{name}" t')).scalars())
                for name in names}


def test_0019_keeps_existing_podcasts_on_video_and_allows_flac(closed_loop):
    owner, source, _, document, dsn, _ = closed_loop
    insert = ("INSERT INTO artifacts (artifact_id,learner_id,material_id,kind,media_type,sha256,size_bytes,created_at) "
              "VALUES (gen_random_uuid(),%s,%s,'podcast_audio',%s,%s,10,now())")
    with psycopg.connect(dsn) as connection:
        connection.execute(insert, (owner.learner_id, source.material_id, "audio/wav", b"0" * 32))
        with pytest.raises(psycopg.errors.CheckViolation):
            with connection.transaction():
                connection.execute(insert, (owner.learner_id, source.material_id, "audio/flac", b"1" * 32))
        connection.execute(
            "INSERT INTO podcasts (podcast_id,learner_id,material_id,knowledge_structure_revision,name,mode,concept_ids,"
            "episodes,status,version,idempotency_key_sha256,request_fingerprint,created_at) "
            "VALUES (gen_random_uuid(),%s,%s,%s,'舊節目','quick',%s,'[]'::jsonb,'ready',3,%s,%s,now())",
            (owner.learner_id, source.material_id, document["revision"],
             [c["concept_id"] for c in document["concepts"]], b"k" * 32, b"f" * 32))
    before = rows(dsn)
    assert run_migrations(dsn) == (19,)
    assert rows(dsn) == before
    with psycopg.connect(dsn) as connection:
        assert connection.execute("SELECT video_enabled FROM podcasts").fetchall() == [(True,)]
        # 新建節目必須由程式明確寫入是否生成影片。
        assert connection.execute(
            "SELECT column_default FROM information_schema.columns "
            "WHERE table_name='podcasts' AND column_name='video_enabled'").fetchone() == (None,)
        connection.execute(insert, (owner.learner_id, source.material_id, "audio/flac", b"2" * 32))
    assert run_migrations(dsn) == ()
