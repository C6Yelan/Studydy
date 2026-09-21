"""Legacy cancellation remains honor-able; new requests require discard authority."""
from uuid import UUID, uuid4

import psycopg
import pytest

import runtime.material_processing as processing
from runtime.material_discard import finish_material_discards
from runtime.storage.materials import read_material_library
from runtime.storage.knowledge_structures import read_knowledge_structure
from learning_adaptation.study_sessions import create_study_session
from test_closed_loop_v1 import closed_loop, _structure


@pytest.fixture
def cancellation_run(closed_loop):
    learner, source, settings, structure, dsn, _token = closed_loop
    run = processing.create_material_processing_run(learner.learner_id, source.material_id, source.artifact_id, "legacy-cancel-test", settings, dsn=dsn)
    return learner, source, settings, structure, dsn, run


def legacy_cancel(fixture):
    """Represent a request already accepted by 0005, never a new product request."""
    *_, dsn, run = fixture
    with psycopg.connect(dsn) as connection:
        connection.execute("UPDATE material_processing_runs SET cancel_requested_at=clock_timestamp() WHERE run_id=%s", (run.run_id,))


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


@pytest.mark.parametrize("checkpoint", ["before-preflight", "after-preflight", "evidence", "semantics", "before-publishing", "analysis-failure"])
def test_legacy_worker_honors_cancel_without_discard_and_preserves_old_data(cancellation_run, monkeypatch, checkpoint):
    learner, source, settings, original, dsn, run = cancellation_run
    study = create_study_session(learner, source.material_id, original["revision"], "old-study", dsn=dsn)
    claim = processing.claim_next_material_processing_run(dsn=dsn)
    failures = []
    if checkpoint != "analysis-failure":
        monkeypatch.setattr(processing, "_record_failure", lambda *args, **kwargs: failures.append(args))
    if checkpoint == "before-preflight": legacy_cancel(cancellation_run)
    def preflight(_settings):
        assert checkpoint != "before-preflight"
        if checkpoint == "after-preflight": legacy_cancel(cancellation_run)
        return run.runtime_binding
    monkeypatch.setattr(processing, "runtime_preflight", preflight)
    def analyze(_request, _settings, *, progress_callback, cancellation_check, **_kwargs):
        assert checkpoint not in {"before-preflight", "after-preflight"}
        if checkpoint == "analysis-failure":
            legacy_cancel(cancellation_run)
            raise processing.MaterialAnalysisError("NO_USABLE_EVIDENCE")
        for stage in ("evidence", "semantics"):
            if checkpoint == stage: legacy_cancel(cancellation_run)
            progress_callback(stage, 1, 1)
        if checkpoint == "before-publishing": legacy_cancel(cancellation_run)
        return _structure(str(run.run_id), source.sha256, settings["runtime_lock"])
    monkeypatch.setattr(processing, "analyze_material", analyze)
    result = processing.execute_claimed_material_processing_run(claim, settings, dsn=dsn)
    assert result.status == "cancelled" and result.error_code is None and result.output_binding is None
    assert failures == []
    assert processing.request_material_processing_cancellation(learner.learner_id, run.run_id, dsn=dsn) == result
    processing._record_failure(run.run_id, "UNEXPECTED", dsn=dsn)
    assert read(cancellation_run) == result
    finish_material_discards(dsn=dsn)
    with psycopg.connect(dsn) as connection:
        assert connection.execute("SELECT count(*) FROM knowledge_structures WHERE run_id=%s", (run.run_id,)).fetchone() == (0,)
        assert connection.execute("SELECT discard_requested_at FROM materials WHERE material_id=%s", (source.material_id,)).fetchone() == (None,)
        assert connection.execute("SELECT count(*) FROM artifacts WHERE artifact_id=%s", (source.artifact_id,)).fetchone() == (1,)
        assert connection.execute("SELECT count(*) FROM study_sessions WHERE study_session_id=%s", (study.study_session_id,)).fetchone() == (1,)
    assert read_knowledge_structure(learner.learner_id, source.material_id, revision=original["revision"], dsn=dsn).document == original
    item = read_material_library(learner.learner_id, dsn=dsn)[0]
    assert item["latest_attempt"]["status"] == "cancelled"
    assert item["available_structures"][0]["knowledge_structure_revision"] == original["revision"]


def test_restart_honors_legacy_cancel_but_does_not_discard_material(cancellation_run):
    learner, source, settings, _, dsn, run = cancellation_run
    processing.claim_next_material_processing_run(dsn=dsn)
    legacy_cancel(cancellation_run)
    assert processing.recover_interrupted_material_runs(dsn=dsn) == 1
    ordinary = processing.create_material_processing_run(learner.learner_id, source.material_id, source.artifact_id, "ordinary-restart", settings, dsn=dsn)
    processing.claim_next_material_processing_run(dsn=dsn)
    assert processing.recover_interrupted_material_runs(dsn=dsn) == 0
    with psycopg.connect(dsn) as connection:
        connection.execute("UPDATE material_processing_runs SET lease_expires_at=now()-interval '1 second' WHERE run_id=%s",(ordinary.run_id,))
    assert processing.recover_interrupted_material_runs(dsn=dsn) == 1
    finish_material_discards(dsn=dsn)
    assert read(cancellation_run).status == "cancelled" and read(cancellation_run).error_code is None
    interrupted = processing.read_material_processing_run(learner.learner_id, ordinary.run_id, dsn=dsn)
    assert interrupted.status == "failed" and interrupted.error_code == "RESTART_INTERRUPTED"
    assert processing.recover_interrupted_material_runs(dsn=dsn) == 0


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
