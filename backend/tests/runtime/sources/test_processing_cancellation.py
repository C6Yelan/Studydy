
"""教材處理取消必須具有刪除教材或取消更新的權限。"""

from uuid import UUID, uuid4

import pytest

import runtime.material_processing as processing
from product_fixtures import seed_run, closed_loop


@pytest.fixture
def cancellation_run(closed_loop):
    learner, source, settings, structure, dsn, _token = closed_loop
    run = seed_run(learner.learner_id, source.material_id, "cancel-test", settings, dsn=dsn)
    return learner, source, settings, structure, dsn, run


def read(fixture):
    learner, _, _, _, dsn, run = fixture
    return processing.read_material_processing_run(learner.learner_id, run.run_id, dsn=dsn)


@pytest.mark.parametrize("stage", ["pending", "queued", "evidence", "semantics"])
def test_internal_primitive_cannot_create_cancel_only_intent(cancellation_run, stage):
    learner, _, _, _, dsn, run = cancellation_run
    if stage != "pending":
        processing.claim_next_material_processing_run(dsn=dsn)
        for next_stage in ("evidence", "semantics"):
            if stage == "queued": break
            processing._record_progress(run.run_id, next_stage, 1, 1, dsn=dsn)
            if next_stage == stage: break
    before = read(cancellation_run)
    with pytest.raises(processing.MaterialProcessingError, match="MATERIAL_RUN_INVALID"):
        processing.request_material_processing_cancellation(learner.learner_id, run.run_id, dsn=dsn)
    assert read(cancellation_run) == before and before.cancel_requested_at is None
    with pytest.raises(processing.MaterialProcessingError, match="MATERIAL_RUN_NOT_FOUND"):
        processing.request_material_processing_cancellation(uuid4(), run.run_id, dsn=dsn)






def test_publishing_and_terminal_primitive_is_unchanged(cancellation_run):
    learner, _, _, original, dsn, run = cancellation_run
    processing.claim_next_material_processing_run(dsn=dsn)
    for stage in ("evidence", "semantics", "publishing"):
        processing._record_progress(run.run_id, stage, 1, 1, dsn=dsn)
    before = read(cancellation_run)
    with pytest.raises(processing.MaterialProcessingError,match='MATERIAL_RUN_INVALID'):
        processing.request_material_processing_cancellation(learner.learner_id, run.run_id, dsn=dsn)
    assert read(cancellation_run)==before
    processing._record_failure(run.run_id, "EXPECTED_FAILURE", dsn=dsn)
    failed = read(cancellation_run)
    assert processing.request_material_processing_cancellation(learner.learner_id, run.run_id, dsn=dsn) == failed
    published = processing.read_material_processing_run(learner.learner_id, UUID(original["run_id"]), dsn=dsn)
    assert processing.request_material_processing_cancellation(learner.learner_id, published.run_id, dsn=dsn) == published
