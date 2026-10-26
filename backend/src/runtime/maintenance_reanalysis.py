"""指定教材重分析、章節與摘要的持久共同額度；不修改分析輸入 hash。"""

from functools import partial
from uuid import UUID
from sqlalchemy import select, text

from .semantic_service import SemanticServiceError, request_semantics
from .storage.database import database_session
from .storage.tables import KnowledgeStructure, MaterialProcessingRun, Material, MaterialNotes, KnowledgeChapters

# CONTRACT-demo-fixes.md 線3.1明定唯一教材；不是任意教材的公開重分析入口。
MATERIAL_ID = UUID("4003f055-12f1-462e-83a1-015daffdfdce")
KEY = "demo-post03-full-reanalysis-v1"


def reserve_call(run_id, *, dsn=None):
    # 先持久保留才外送；無法確定是否已受理的失敗請求也不退額。
    with database_session(dsn) as session:
        used = session.execute(text("""
            UPDATE material_processing_runs
            SET maintenance = jsonb_set(maintenance, '{reserved_calls}',
                to_jsonb((maintenance->>'reserved_calls')::integer + 1))
            WHERE run_id = :run AND maintenance->>'mode' = 'demo-full-reanalysis/v1'
                AND (maintenance->>'reserved_calls')::integer < (maintenance->>'limit')::integer
            RETURNING (maintenance->>'reserved_calls')::integer
        """), {"run": run_id}).scalar_one_or_none()
        if used is None:
            raise SemanticServiceError("MATERIAL_MODEL_BUDGET_EXHAUSTED")


def semantic_call_for_run(run_id, *, dsn=None):
    with database_session(dsn) as session:
        maintenance = session.scalar(select(MaterialProcessingRun.maintenance).where(
            MaterialProcessingRun.run_id == run_id))
    if maintenance is None:
        return None
    return partial(request_semantics, before_request=partial(reserve_call, run_id, dsn=dsn))


def semantic_call_for_revision(owner, material_id, revision, *, dsn=None):
    with database_session(dsn) as session:
        run_id = session.scalar(select(KnowledgeStructure.run_id).where(
            KnowledgeStructure.learner_id == owner,
            KnowledgeStructure.material_id == material_id,
            KnowledgeStructure.structure_revision == revision))
    return semantic_call_for_run(run_id, dsn=dsn)


def enqueue(config, *, dsn=None):
    from .luna_test import enabled
    from .source_revisions import create_revision
    from .storage.artifacts import _key_digest

    if not enabled(config["runtime_lock"]):
        raise ValueError("DEMO_REANALYSIS_REQUIRES_LUNA")
    with database_session(dsn) as session:
        material = session.get(Material, MATERIAL_ID)
        if material is None or material.head_revision is None or material.discard_requested_at is not None:
            raise ValueError("DEMO_MATERIAL_UNAVAILABLE")
        existing = session.scalar(select(MaterialProcessingRun.run_id).where(
            MaterialProcessingRun.learner_id == material.learner_id,
            MaterialProcessingRun.idempotency_key_sha256 == _key_digest(KEY)))
        if existing is not None:
            return existing
        owner, base_revision = material.learner_id, material.head_revision
    return create_revision(owner, MATERIAL_ID, [], KEY, config,
        base_revision=base_revision, full_reanalysis=True, dsn=dsn).run_id


def status(*, dsn=None):
    from .storage.artifacts import _key_digest

    with database_session(dsn) as session:
        run = session.scalar(select(MaterialProcessingRun).where(
            MaterialProcessingRun.material_id == MATERIAL_ID,
            MaterialProcessingRun.idempotency_key_sha256 == _key_digest(KEY)))
        if run is None:
            return {"state": "not_started"}
        revision = (run.output_binding or {}).get("knowledge_structure_revision")
        result = {"run_id": str(run.run_id), "state": run.status,
            "stage": run.progress_stage, "error_code": run.error_code,
            "budget": run.maintenance, "revision": revision}
        if revision:
            key = (run.learner_id, run.material_id, revision)
            chapters = session.get(KnowledgeChapters, key)
            summary = session.get(MaterialNotes, (*key, "summary"))
            result.update(chapters=chapters.status if chapters else None,
                summary=summary.status if summary else None)
        return result


def main():
    import argparse
    import json
    import os
    parser = argparse.ArgumentParser(description="部署後對指定數學教材重分析一次；預設只查狀態。")
    parser.add_argument("--enqueue", action="store_true", help="建立唯一工作；分析與章節／摘要合計最多300次Luna")
    args = parser.parse_args()
    # docker compose exec 是新程序，不繼承 API 啟動程序組出的 DSN。
    from .storage.database import configure_database
    configure_database(os.environ)
    if args.enqueue:
        from .local_ai_config import read_local_ai_config_from_environment
        enqueue(read_local_ai_config_from_environment(os.environ))
    print(json.dumps(status(), ensure_ascii=False))


if __name__ == "__main__":
    main()
