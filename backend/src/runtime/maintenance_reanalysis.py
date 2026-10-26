"""指定教材重分析、章節與摘要的持久共同額度；不修改分析輸入 hash。"""

from functools import partial
from uuid import UUID
from sqlalchemy import select, text, func

from .semantic_service import SemanticServiceError, request_semantics
from .storage.database import database_session
from .storage.tables import KnowledgeStructure, MaterialProcessingRun, Material, MaterialNotes, KnowledgeChapters

# CONTRACT-demo-fixes.md 線3.1明定唯一教材；不是任意教材的公開重分析入口。
MATERIAL_ID = UUID("4003f055-12f1-462e-83a1-015daffdfdce")
KEY = "demo-post03-full-reanalysis-v1"
RESUME_RUN_ID = UUID("257bef38-6c08-4baf-aedc-641d53a26795")


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



def resume_once(config, *, dsn=None):
    """只恢復這次獲授權的失敗工作一次；原額度、來源與封存身分全部沿用。"""
    from .luna_test import enabled
    from .storage.artifacts import _key_digest

    if not enabled(config["runtime_lock"]):
        raise ValueError("DEMO_REANALYSIS_REQUIRES_LUNA")
    with database_session(dsn) as session:
        # 與來源更新／發布相同，先鎖教材，再鎖工作。
        material = session.scalar(select(Material).where(
            Material.material_id == MATERIAL_ID).with_for_update())
        if material is None or material.discard_requested_at is not None:
            raise ValueError("DEMO_MATERIAL_UNAVAILABLE")
        run = session.scalar(select(MaterialProcessingRun).where(
            MaterialProcessingRun.run_id == RESUME_RUN_ID,
            MaterialProcessingRun.material_id == MATERIAL_ID,
            MaterialProcessingRun.learner_id == material.learner_id,
            MaterialProcessingRun.idempotency_key_sha256 == _key_digest(KEY),
        ).with_for_update())
        budget = run.maintenance if run is not None else None
        if not budget or budget.get("mode") != "demo-full-reanalysis/v1":
            raise ValueError("DEMO_REANALYSIS_RESUME_UNAVAILABLE")
        if budget.get("resume_once_used"):
            raise ValueError("DEMO_REANALYSIS_RESUME_ALREADY_USED")
        if (run.status != "failed" or run.cancel_requested_at is not None
                or run.output_binding is not None
                or run.error_code != "SEMANTIC_SERVICE_UNAVAILABLE"):
            raise ValueError("DEMO_REANALYSIS_RESUME_UNAVAILABLE")
        if budget["reserved_calls"] >= budget["limit"]:
            raise ValueError("MATERIAL_MODEL_BUDGET_EXHAUSTED")
        if material.head_revision != run.base_revision:
            raise ValueError("REVISION_CONFLICT")
        if session.scalar(select(MaterialProcessingRun.run_id).where(
            MaterialProcessingRun.material_id == MATERIAL_ID,
            MaterialProcessingRun.status.in_(("pending", "running")),
        )) is not None:
            raise ValueError("REVISION_IN_PROGRESS")
        run.maintenance = {**budget, "resume_once_used": True}
        run.status, run.progress_stage = "pending", "queued"
        run.error_code = run.completed_at = None
        run.worker_token = run.lease_expires_at = None
        run.completed_pages, run.total_pages = 0, None
        run.completed_units = run.total_units = None
        run.updated_at = session.scalar(select(func.clock_timestamp()))
        return run.run_id


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
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--enqueue", action="store_true", help="建立唯一工作；分析與章節／摘要合計最多300次Luna")
    action.add_argument("--resume-once", action="store_true", help="只恢復指定失敗工作一次；沿用原300次額度")
    args = parser.parse_args()
    # docker compose exec 是新程序，不繼承 API 啟動程序組出的 DSN。
    from .storage.database import configure_database
    configure_database(os.environ)
    if args.enqueue or args.resume_once:
        from .local_ai_config import read_local_ai_config_from_environment
        operation = resume_once if args.resume_once else enqueue
        operation(read_local_ai_config_from_environment(os.environ))
    print(json.dumps(status(), ensure_ascii=False))


if __name__ == "__main__":
    main()
