"""受控模型走一次全量重分析；正式教材與真實模型不在測試範圍。"""

from copy import deepcopy

import pytest
from sqlalchemy import delete

from product_fixtures import closed_loop
from test_source_revisions import revisions
from test_material_review_flow import enable_review, keep_response
from runtime import maintenance_reanalysis as maintenance
from runtime.luna_test import test_lock as luna_lock
from runtime.source_revisions import retry_revision, SourceError
from runtime.storage.database import database_session
from runtime.storage.knowledge_structures import read_knowledge_structure, _prune_unreferenced_structures
from runtime.storage.tables import MaterialProcessingRun, Material, MaterialNotes, KnowledgeChapters


@pytest.fixture
def maintained(revisions, monkeypatch, request):
    from pdf_evidence.material_pipeline import analyze_material
    owner, material, settings, dsn, add, start, execute, first, original, requests = revisions
    if getattr(request, "param", 1) == 2:
        second = add("B.pdf", "A queue removes the earliest inserted element first.")
        start([second], "two-concept-fixture", original["revision"])
        prior = execute()
        assert prior.status == "succeeded"
        original = read_knowledge_structure(owner.learner_id, material, run_id=prior.run_id, dsn=dsn).document
    enable_review(settings)
    settings["runtime_lock"] = luna_lock(settings["runtime_lock"])
    monkeypatch.setattr(maintenance, "MATERIAL_ID", material)
    monkeypatch.setenv("STUDYDY_TEXT_TEST_PROVIDER", "codex-exec-luna")
    monkeypatch.setattr("runtime.material_processing.analyze_material", analyze_material)
    sent = []

    def provider(path, body):
        assert path == "/semantics"
        request = body["request"]
        sent.append(deepcopy(request))
        if "chapter_count" in request:
            return {"chapters": [{"title": "堆疊操作方式", "start": 0},
                {"title": "佇列操作方式", "start": 1}]}
        if request.get("phase") == "organize_sections":
            return {"paragraph_ids": []}
        if request.get("phase") == "review_paragraph":
            return {"checks": [{"part": p, "missing_evidence": [], "supported": True, "reason": ""}
                for p in request["parts"]], "coverage": [{"claim": c, "explained": True,
                "note_parts": ["blocks.0"], "reason": ""} for c in request["coverage_claims"]]}
        if request.get("kind") == "summary":
            return {"paragraphs": [{"heading": c["label"], "level": 1,
                "blocks": [{"type": "text", "text": q["text"]}], "claim_ids": [q["claim_id"]],
                "evidence_ids": q.get("evidence_ids") or [e["evidence_id"] for e in q["evidence"]]}
                for c in request["concepts"] for q in c["claims"]]}
        if "sections" not in request:
            return keep_response(request)
        rows = [row for section in request["sections"] for row in section["evidence"] if row[2] != "heading"]
        assert rows  # 同來源仍須送完整原文，不可落入增量零頁或review-only。
        return {"concepts": [{"k": f"new_{row[0]}", "l": f"Concept {row[0]}", "a": [],
            "c": [{"m": None, "s": [row[0]]}]} for row in rows], "relations": []}

    monkeypatch.setattr("runtime.provider_http.provider", provider)
    return owner, material, settings, dsn, execute, original, sent


def test_full_reanalysis_keeps_old_version_and_has_one_budget(maintained):
    owner, material, settings, dsn, execute, original, sent = maintained
    run_id = maintenance.enqueue(settings, dsn=dsn)
    assert maintenance.enqueue(settings, dsn=dsn) == run_id
    completed = execute()
    assert completed.status == "succeeded", completed.error_code
    assert any("sections" in request for request in sent)
    assert any("concepts" in request for request in sent)
    document = read_knowledge_structure(owner.learner_id, material, run_id=run_id, dsn=dsn).document
    assert document["revision"] != original["revision"]
    assert document["source_set_sha256"] == original["source_set_sha256"]
    assert len(document["concepts"]) == len(original["concepts"])
    assert maintenance.enqueue(settings, dsn=dsn) == run_id  # head已換也不建立第二次。
    with database_session(dsn) as session:
        _prune_unreferenced_structures(session, owner.learner_id, material, document["revision"])
    assert read_knowledge_structure(owner.learner_id, material, revision=original["revision"], dsn=dsn).document == original
    report = maintenance.status(dsn=dsn)
    assert report["budget"]["reserved_calls"] == len(sent)
    assert report["chapters"] == report["summary"] == "pending"

    # 章節及摘要都透過新版revision找到同一個持久run，額度不另開。
    chapters = maintenance.semantic_call_for_revision(owner.learner_id, material, document["revision"], dsn=dsn)
    notes = maintenance.semantic_call_for_revision(owner.learner_id, material, document["revision"], dsn=dsn)
    chapters.keywords["before_request"]()
    notes.keywords["before_request"]()
    assert maintenance.status(dsn=dsn)["budget"]["reserved_calls"] == len(sent) + 2


def test_exhaustion_preserves_head_and_cannot_reset_through_retry(maintained):
    owner, material, settings, dsn, execute, original, sent = maintained
    run_id = maintenance.enqueue(settings, dsn=dsn)
    with database_session(dsn) as session:
        row = session.get(MaterialProcessingRun, run_id)
        row.maintenance = {**row.maintenance, "reserved_calls": 300}
    completed = execute()
    assert completed.status == "failed"
    assert completed.error_code == "MATERIAL_MODEL_BUDGET_EXHAUSTED"
    assert sent == []
    with database_session(dsn) as session:
        assert session.get(Material, material).head_revision == original["revision"]
    assert maintenance.enqueue(settings, dsn=dsn) == run_id
    with pytest.raises(SourceError, match="REQUEST_INVALID"):
        retry_revision(owner.learner_id, run_id, "cannot-reset-budget", settings, dsn=dsn)


def test_cli_refuses_non_luna_before_enqueuing(revisions, monkeypatch):
    _owner, material, settings, dsn, *_ = revisions
    monkeypatch.setattr(maintenance, "MATERIAL_ID", material)
    with pytest.raises(ValueError, match="DEMO_REANALYSIS_REQUIRES_LUNA"):
        maintenance.enqueue(settings, dsn=dsn)
    assert maintenance.status(dsn=dsn) == {"state": "not_started"}


@pytest.mark.parametrize("maintained", [2], indirect=True)
def test_automatic_chapters_and_summary_share_actual_call_reservations(maintained):
    from runtime import knowledge_chapters, material_notes
    _owner, _material, settings, dsn, execute, original, sent = maintained
    maintenance.enqueue(settings, dsn=dsn)
    assert execute().status == "succeeded"
    revision = maintenance.status(dsn=dsn)["revision"]
    # 移除fixture舊版未執行的合成衍生工作，只處理本次發布的新工作。
    with database_session(dsn) as session:
        session.execute(delete(MaterialNotes).where(MaterialNotes.structure_revision != revision))
        session.execute(delete(KnowledgeChapters).where(KnowledgeChapters.structure_revision != revision))
    before = len(sent)
    assert knowledge_chapters.step(dsn=dsn)
    assert material_notes.step(dsn=dsn)
    report = maintenance.status(dsn=dsn)
    assert report["chapters"] == "ready", report
    assert report["summary"] == "ready", report
    assert len(sent) > before + 1
    assert report["budget"]["reserved_calls"] == len(sent)


def test_completed_summary_manual_regeneration_is_outside_maintenance_budget(maintained):
    from runtime import material_notes
    owner, material, settings, dsn, execute, _original, sent = maintained
    maintenance.enqueue(settings, dsn=dsn)
    assert execute().status == "succeeded"
    revision = maintenance.status(dsn=dsn)["revision"]
    with database_session(dsn) as session:
        session.execute(delete(MaterialNotes).where(MaterialNotes.structure_revision != revision))
    assert material_notes.step(dsn=dsn)
    report = maintenance.status(dsn=dsn)
    assert report["summary"] == "ready", report
    reserved = report["budget"]["reserved_calls"]
    assert reserved == len(sent)
    material_notes.generate(owner.learner_id, material, revision, "summary", 1, dsn=dsn)
    assert material_notes.step(dsn=dsn)
    assert maintenance.status(dsn=dsn)["summary"] == "ready"
    assert len(sent) > reserved
    assert maintenance.status(dsn=dsn)["budget"]["reserved_calls"] == reserved


def test_unpublished_summary_retry_keeps_exhausted_budget(maintained):
    from runtime import material_notes
    owner, material, settings, dsn, execute, _original, sent = maintained
    run_id = maintenance.enqueue(settings, dsn=dsn)
    assert execute().status == "succeeded"
    revision = maintenance.status(dsn=dsn)["revision"]
    with database_session(dsn) as session:
        session.execute(delete(MaterialNotes).where(MaterialNotes.structure_revision != revision))
        run = session.get(MaterialProcessingRun, run_id)
        run.maintenance = {**run.maintenance, "reserved_calls": 300}
    before = len(sent)
    assert material_notes.step(dsn=dsn)
    material_notes.generate(owner.learner_id, material, revision, "summary", 0, dsn=dsn)
    assert material_notes.step(dsn=dsn)
    with database_session(dsn) as session:
        row = session.get(MaterialNotes, (owner.learner_id, material, revision, "summary"))
        assert row.generation == 0 and row.status == "failed"
        assert row.error_code == "MATERIAL_MODEL_BUDGET_EXHAUSTED"
    assert len(sent) == before
    assert maintenance.status(dsn=dsn)["budget"]["reserved_calls"] == 300
