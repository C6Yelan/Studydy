"""過期但尚未被重領的租約，不得保存成功、失敗或中間成果。"""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from product_fixtures import closed_loop
from research_fixtures import create_confirmed_research
from materials.test_podcast_scenes import ready, alignment
from materials.test_topics import PLAN
from runtime import research, topics, podcast_scenes as scenes
from runtime.storage.database import database_session
from runtime.storage.tables import MaterialResearch, TopicScope, PodcastScenes
from runtime.errors import SourceError


def change_lease(db, model, identity, condition):
    row = db.get(model, identity)
    if condition == "expired":
        row.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    elif condition == "replaced":
        row.lease_token = uuid4()


@pytest.mark.parametrize("condition", ["current", "expired", "replaced"])
def test_research_save_fences_final_and_intermediate_results(closed_loop, condition):
    learner, source, _, _, dsn, _ = closed_loop
    view = create_confirmed_research(
        learner.learner_id, source.material_id, "合成租約驗證", "fencing", dsn=dsn
    )
    state = research.claim(dsn=dsn)
    with database_session(dsn) as db:
        change_lease(db, MaterialResearch, view["research_id"], condition)
    changed = []

    def save_result(row):
        changed.append(True)
        row.error_code = "RESEARCH_SEARCH_FAILED"

    assert research.save(state, save_result, dsn=dsn, release=False) is (condition == "current")
    assert research.save(state, save_result, dsn=dsn) is (condition == "current")
    assert len(changed) == (2 if condition == "current" else 0)
    with database_session(dsn) as db:
        row = db.get(MaterialResearch, view["research_id"])
        assert row.error_code == ("RESEARCH_SEARCH_FAILED" if condition == "current" else None)


@pytest.mark.parametrize("condition", ["current", "expired", "replaced"])
@pytest.mark.parametrize("failure", [False, True])
def test_topic_generation_fences_success_and_failure(closed_loop, monkeypatch, condition, failure):
    learner, _, _, _, dsn, _ = closed_loop
    view = topics.create(learner.learner_id, "合成主題", "fencing", dsn=dsn)

    def provider(*_):
        with database_session(dsn) as db:
            change_lease(db, TopicScope, view["topic_id"], condition)
        if failure:
            raise RuntimeError("synthetic provider failure")
        return deepcopy(PLAN)

    monkeypatch.setattr(topics, "provider", provider)
    assert topics.step(dsn=dsn)
    with database_session(dsn) as db:
        row = db.get(TopicScope, view["topic_id"])
        if condition == "current":
            assert row.status == ("failed" if failure else "ready")
            assert row.version == view["version"] + 1
        else:
            assert row.status == "pending"
            assert row.version == view["version"]
            assert row.proposal is None and row.error_code is None


@pytest.mark.parametrize("condition", ["current", "expired", "replaced"])
@pytest.mark.parametrize("operation", ["alignment", "checks", "success", "failure"])
def test_scene_saves_fence_every_checkpoint_and_completion(closed_loop, condition, operation):
    learner, _, _, _, dsn, _ = closed_loop
    identity = ready(closed_loop)
    scenes.prepare(learner.learner_id, identity, dsn=dsn)
    state = scenes.claim(dsn=dsn)
    value = alignment(state)
    with database_session(dsn) as db:
        change_lease(db, PodcastScenes, state["scene_id"], condition)
    if operation == "alignment":
        assert scenes.save_alignment(state, value, dsn=dsn) is (condition == "current")
    elif operation == "checks":
        assert scenes.save_checks(state, {"synthetic": True}, dsn=dsn) is (condition == "current")
    elif operation == "success":
        scenes.finish(state, value, dsn=dsn)
    else:
        scenes.finish(state, error="SCENE_PREPARATION_FAILED", dsn=dsn)
    with database_session(dsn) as db:
        row = db.get(PodcastScenes, state["scene_id"])
        if condition != "current":
            assert row.status == "running"
            assert row.alignment is None and row.checks is None
            assert row.manifest is None and row.error_code is None
        elif operation == "alignment":
            assert row.alignment == value
        elif operation == "checks":
            assert row.checks == {"synthetic": True}
        else:
            assert row.status == ("ready" if operation == "success" else "failed")


@pytest.mark.parametrize("condition", ["current", "expired", "replaced"])
def test_research_upload_fences_artifact_creation(closed_loop, condition):
    from sources.test_source_revisions import pdf
    from runtime.source_normalization import upload_source
    from runtime.storage.tables import Artifact

    learner, source, _, _, dsn, _ = closed_loop
    view = create_confirmed_research(
        learner.learner_id, source.material_id, "合成來源", "fencing", dsn=dsn
    )
    token = uuid4()
    with database_session(dsn) as db:
        row = db.get(MaterialResearch, view["research_id"])
        row.status = "acquiring"
        row.lease_token = token
        row.lease_expires_at = datetime.now(UTC) + timedelta(minutes=5)
        change_lease(db, MaterialResearch, view["research_id"], condition)
        count = db.scalar(select(func.count()).select_from(Artifact))

    def upload():
        return upload_source(
            learner.learner_id, source.material_id, pdf("Synthetic fenced source"),
            "fenced.pdf", "application/pdf", "fenced-upload", dsn=dsn,
            research_attempt=(view["research_id"], token),
        )

    if condition == "current":
        assert upload()
    else:
        with pytest.raises(SourceError, match="REQUEST_INVALID"):
            upload()
    with database_session(dsn) as db:
        assert db.scalar(select(func.count()).select_from(Artifact)) == count + (condition == "current")


@pytest.mark.parametrize("condition", ["current", "expired", "replaced"])
def test_research_revision_handoff_fences_new_runs(closed_loop, monkeypatch, condition):
    from materials.test_research import ready as research_ready
    from runtime.storage.tables import MaterialProcessingRun

    learner, source, config, _, dsn, _ = closed_loop
    identity = research_ready(learner.learner_id, source.material_id, dsn, monkeypatch)
    token = uuid4()
    with database_session(dsn) as db:
        row = db.get(MaterialResearch, identity)
        row.lease_token = token
        row.lease_expires_at = datetime.now(UTC) + timedelta(minutes=5)
        change_lease(db, MaterialResearch, identity, condition)
        count = db.scalar(select(func.count()).select_from(MaterialProcessingRun))
    if condition == "current":
        assert research.submit(learner.learner_id, identity, config, dsn=dsn, attempt_token=token)["status"] == "submitted"
    else:
        with pytest.raises(SourceError, match="SOURCE_NOT_READY"):
            research.submit(learner.learner_id, identity, config, dsn=dsn, attempt_token=token)
    with database_session(dsn) as db:
        assert db.scalar(select(func.count()).select_from(MaterialProcessingRun)) == count + (condition == "current")
