"""真 DB 的出題契約；語意回應為受控 fixture，不宣稱模型品質。"""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from knowledge_map.structure import SemanticState, apply_semantic_response, build_document_context, build_knowledge_structure
from learning_adaptation.assessments import AssessmentError, _stored, generate_assessment, read_assessment
from learning_adaptation.answer_events import read_answer_events
from learning_adaptation.learner_progress import derive_learner_progress
from learning_adaptation.study_sessions import create_study_session, set_current_study_concept
from pdf_evidence.ocr_page_evidence import canonical_sha256
from runtime.api.models import project_assessment
from runtime.material_processing import create_material_processing_run, claim_next_material_processing_run, _record_progress
from runtime.storage.knowledge_structures import publish_knowledge_structure
from runtime.storage.tables import Assessment, database_session
from test_closed_loop_v1 import closed_loop, Client, _assessment_response, _page, generate_assessment as fixture_generate


def _model(response, observed, *, duplicate=None, issues=()):
    def call(_client, **kwargs):
        observed.append(kwargs)
        if kwargs["task"] == "assessment":
            return deepcopy(response)
        return {"schema": "assessment-check-response/v2", "verdicts": [
            {"question_index": item["question_index"], "answer_status": "unique",
             "selected_option_index": item["options"].index("LIFO"),
             "duplicate_prior_index": duplicate, "quality_issues": list(issues)}
            for item in kwargs["request"]["questions"]
        ]}
    return call


def test_partial_review_flag_allows_safe_recall_but_cross_claim_duplicate_adds_no_wrong_event(closed_loop):
    learner, source, settings, old, dsn, _ = closed_loop
    old_session = create_study_session(learner, source.material_id, old["revision"], "old", dsn=dsn)
    old_concept = old["concepts"][0]
    old_question = fixture_generate(learner, old_session.study_session_id, old_concept["claims"][0]["claim_id"], "old-q", settings,
        dsn=dsn, client=Client(), semantic_call=lambda *_a, **_k: _assessment_response("order", "Old revision question", old_concept["evidence_refs"][0]))

    run = create_material_processing_run(learner.learner_id, source.material_id, source.artifact_id, "partial", settings, dsn=dsn)
    assert claim_next_material_processing_run(dsn=dsn).run.run_id == run.run_id
    for stage in ("evidence", "semantics", "publishing"):
        _record_progress(run.run_id, stage, 1, 1, dsn=dsn)
    context = build_document_context([_page(source.sha256)], page_count=1)
    state = SemanticState()
    apply_semantic_response({"concepts": [
        {"k": "stack", "l": "Stack", "a": [], "c": [{"m": None, "s": [0]}]},
        {"k": "order", "l": "Stack order", "a": [], "c": [{"m": "Stacks use LIFO order.", "s": [0]}]},
    ], "relations": []}, context=context, bundle={"sections": context["sections"], "evidence": context["evidence"]}, state=state)
    state.rejected_claims = 1
    state.source_review_required = True
    document = build_knowledge_structure(context, state, source_sha256=source.sha256, run_id=str(run.run_id),
        produced_at="2026-09-20T00:00:00+00:00", runtime_lock_sha256=canonical_sha256(settings["runtime_lock"]),
        model_id=settings["runtime_lock"]["semantic_service"]["model_id"],
        model_revision=settings["runtime_lock"]["semantic_service"]["revision"], semantic_calls=1, ocr_calls=0)
    publish_knowledge_structure(learner.learner_id, source.material_id, run.run_id, document, dsn=dsn)
    assert document["source_review_required"] is True
    first, second = document["concepts"]
    assert first["claims"][0]["claim_id"] != second["claims"][0]["claim_id"]
    study = create_study_session(learner, source.material_id, document["revision"], "new", current_concept_id=first["concept_id"], dsn=dsn)
    observed = []
    item = generate_assessment(learner, study.study_session_id, first["claims"][0]["claim_id"], "new-q", settings, dsn=dsn, client=Client(),
        semantic_call=_model(_assessment_response("order", "Stack 採用哪種順序？", first["evidence_refs"][0]), observed,
                             issues=["weak_distractors", "uneven_options"]))
    assert len(observed) == 2
    assert observed[0]["request"]["prior_questions"] == []  # R1 不混入 R2 的比較集合。
    assert item.generation_provenance["quality_selection"]["issues"] == ["weak_distractors", "uneven_options"]
    public = project_assessment(item).model_dump(by_alias=True)
    assert not {"quality_selection", "verification", "correct_option_id", "generation_provenance"} & public.keys()

    set_current_study_concept(learner, study.study_session_id, second["concept_id"], dsn=dsn)
    before = derive_learner_progress(learner, study.study_session_id, dsn=dsn).concept_states
    observed.clear()
    with pytest.raises(AssessmentError, match="NO_SAFE_ASSESSMENT"):
        generate_assessment(learner, study.study_session_id, second["claims"][0]["claim_id"], "duplicate-q", settings,
            dsn=dsn, client=Client(), semantic_call=_model(
                _assessment_response("paraphrase", "請問堆疊採哪個存取順序？", second["evidence_refs"][0]), observed, duplicate=0))
    assert observed[1]["request"]["prior_questions"][0]["prompt"] == item.public_document["prompt"]
    assert read_answer_events(learner, study.study_session_id, dsn=dsn) == ()
    assert derive_learner_progress(learner, study.study_session_id, dsn=dsn).concept_states == before
    assert read_assessment(learner, old_session.study_session_id, old_question.assessment_revision, dsn=dsn) == old_question


def test_large_prior_questions_are_bounded_without_truncation_and_old_exact_duplicate_still_rejected(closed_loop):
    learner, source, settings, structure, dsn, _ = closed_loop
    concept = structure["concepts"][0]
    study = create_study_session(learner, source.material_id, structure["revision"], "bounded", dsn=dsn)
    records = []
    for index in range(4):
        # 每題約 6KB；完整比較集合不會把歷史題幹累加到超出模型 context。
        prompt = f"第 {index} 個獨立情境：" + "教材內容" * 500 + "Stack 使用何種順序？"
        observed = []
        item = generate_assessment(learner, study.study_session_id, concept["claims"][0]["claim_id"], f"bounded-{index}", settings,
            dsn=dsn, client=Client(), semantic_call=_model(_assessment_response("order", prompt, concept["evidence_refs"][0]), observed))
        assert len(observed[0]["request"]["prior_questions"]) <= 1
        if records:
            assert observed[0]["request"]["prior_questions"][0]["prompt"] == records[-1].public_document["prompt"]
            assert item.generation_provenance["compared_assessment_revisions"] == [records[-1].assessment_revision]
        records.append(item)
    observed = []
    with pytest.raises(AssessmentError, match="NO_SAFE_ASSESSMENT"):
        generate_assessment(learner, study.study_session_id, concept["claims"][0]["claim_id"], "old-exact", settings,
            dsn=dsn, client=Client(), semantic_call=_model(
                _assessment_response("order", records[0].public_document["prompt"], concept["evidence_refs"][0]), observed))
    assert len(observed) == 1  # 已知 literal duplicate 不必再呼叫 checker。


@pytest.mark.parametrize("version", [6, 7])
def test_pre_quality_provenance_reads_without_new_quality_fields(closed_loop, version):
    learner, source, settings, structure, dsn, _ = closed_loop
    concept = structure["concepts"][0]
    study = create_study_session(learner, source.material_id, structure["revision"], "legacy", dsn=dsn)
    item = fixture_generate(learner, study.study_session_id, concept["claims"][0]["claim_id"], "legacy-q", settings,
        dsn=dsn, client=Client(), semantic_call=lambda *_a, **_k: _assessment_response("order", "Stack 順序？", concept["evidence_refs"][0]))
    with database_session(dsn) as session:
        row = session.get(Assessment, item.assessment_revision)
        saved = SimpleNamespace(**{column.name: deepcopy(getattr(row, column.name)) for column in Assessment.__table__.columns})
    provenance = saved.generation_provenance
    for key in ("quality_selection", "compared_assessment_revisions", "prompt_sha256", "check_prompt_sha256", "execution_identity"):
        provenance.pop(key)
    provenance.update(schema=f"assessment-generation-provenance/v{version}", policy="source-span-single-choice/v5")
    if version == 7:
        provenance.update(model_id="fixture-command-model", model_revision="fixture-revision")
        provenance["execution_identity"] = {"transport": "command", "config_sha256": "a" * 64,
            **{key: provenance[key] for key in ("model_id", "model_revision", "runtime_lock_sha256")}}
    core = lambda document: {key: value for key, value in document.items() if key != "assessment_revision"}
    revision = "assessment:sha256:" + canonical_sha256({"public": core(saved.public_document),
        "private_sha256": canonical_sha256(core(saved.private_answer_document)), "provenance_sha256": canonical_sha256(core(provenance))})
    saved.assessment_revision = revision
    for document in (saved.public_document, saved.private_answer_document, provenance):
        document["assessment_revision"] = revision
    before = deepcopy(saved.__dict__)
    assert _stored(saved).mastery_qualified is True
    assert saved.__dict__ == before
