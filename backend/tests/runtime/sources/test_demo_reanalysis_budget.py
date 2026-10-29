"""共同額度在真隔離 DB 的並行保留與重開連線後不可超支。"""

from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from product_fixtures import closed_loop, seed_run
from runtime.maintenance_reanalysis import reserve_call, semantic_call_for_revision
from runtime.semantic_service import SemanticServiceError
from runtime.storage.database import database_session
from runtime.storage.tables import MaterialProcessingRun


def test_parallel_reservation_persists_and_never_exceeds_limit(closed_loop):
    learner, source, settings, _structure, dsn, _token = closed_loop
    run = seed_run(learner.learner_id, source.material_id, "budget-test", settings, dsn=dsn)
    with database_session(dsn) as session:
        session.get(MaterialProcessingRun, run.run_id).maintenance = {
            "mode": "demo-full-reanalysis/v1", "limit": 300, "reserved_calls": 298}

    def attempt(_):
        try:
            reserve_call(run.run_id, dsn=dsn)
            return True
        except SemanticServiceError as error:
            assert error.reason_code == "MATERIAL_MODEL_BUDGET_EXHAUSTED"
            return False

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert sum(pool.map(attempt, range(8))) == 2
    with database_session(dsn) as session:
        assert session.scalar(select(MaterialProcessingRun.maintenance).where(
            MaterialProcessingRun.run_id == run.run_id))["reserved_calls"] == 300
    with pytest.raises(SemanticServiceError, match="MATERIAL_MODEL_BUDGET_EXHAUSTED"):
        reserve_call(run.run_id, dsn=dsn)


def test_ordinary_revision_has_no_budget_wrapper(closed_loop):
    learner, source, _settings, structure, dsn, _token = closed_loop
    assert semantic_call_for_revision(learner.learner_id, source.material_id,
        structure["revision"], dsn=dsn) is None


def test_run_limit_can_exceed_300_but_stays_bounded(closed_loop):
    learner, source, settings, _structure, dsn, _token = closed_loop
    run = seed_run(learner.learner_id, source.material_id, "budget-wide", settings, dsn=dsn)
    with database_session(dsn) as session:
        session.get(MaterialProcessingRun, run.run_id).maintenance = {
            "mode": "demo-full-reanalysis/v1", "limit": 2000, "reserved_calls": 300}
    reserve_call(run.run_id, dsn=dsn)
    with database_session(dsn) as session:
        assert session.get(MaterialProcessingRun, run.run_id).maintenance["reserved_calls"] == 301
    for maintenance in ({"mode": "demo-full-reanalysis/v1", "limit": 2001, "reserved_calls": 0},
            {"mode": "demo-full-reanalysis/v1", "limit": 500, "reserved_calls": 501}):
        with pytest.raises(IntegrityError, match="demo_reanalysis_budget"):
            with database_session(dsn) as session:
                session.get(MaterialProcessingRun, run.run_id).maintenance = maintenance
