"""G4 單次 SQL／目錄遍歷與加總一致；隔離資料，不讀產品目錄。"""
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import text
from product_fixtures import closed_loop
from runtime import storage_usage
from runtime.source_normalization import create_draft
from runtime.storage.database import database_session
from runtime.storage.artifacts import _root


def test_breakdown_and_top_five_share_one_query_and_one_tree_scan(closed_loop, monkeypatch):
    owner, _, _, _, dsn, _ = closed_loop
    materials = [create_draft(owner.learner_id, f'教材{i}', f'storage-{i}', dsn=dsn) for i in range(1,7)]
    root = _root()/'analysis'/owner.learner_id.hex
    for index, material in enumerate(materials, 1):
        directory = root/material.hex/'run'
        directory.mkdir(parents=True)
        (directory/'checkpoint.json').write_bytes(b'x'*(index*100000))
        with database_session(dsn) as db:
            storage_usage.reserve(db, owner.learner_id, material, 'upload', index*10,
                intent=bytes(32), lease=timedelta(days=1), writer_id=uuid4())
    (root/'unassigned').write_bytes(b'x'*1000)
    foreign = _root()/'analysis'/uuid4().hex
    foreign.mkdir()
    (foreign/'private').write_bytes(b'x'*99999)
    seen = []
    scandir = storage_usage.os.scandir
    def scan(path):
        seen.append(str(path))
        return scandir(path)
    monkeypatch.setattr(storage_usage.os, 'scandir', scan)
    with database_session(dsn) as db:
        calls = []
        execute = db.execute
        def query(*args, **kwargs):
            calls.append(1)
            return execute(*args, **kwargs)
        monkeypatch.setattr(db, 'execute', query)
        result = storage_usage._usage(db, owner.learner_id)
    assert len(calls) == 1 and len(seen) == len(set(seen))
    assert sum(result['breakdown'].values()) == result['used_bytes']
    assert result['breakdown']['analysis_bytes'] == 2101000
    assert result['reserved_bytes'] == result['breakdown']['reserved_bytes'] == 210
    assert [r['material_id'] for r in result['largest_materials']] == [str(m) for m in reversed(materials[1:])]
    assert [r['used_bytes'] for r in result['largest_materials']] == [i*100010 for i in range(6,1,-1)]
