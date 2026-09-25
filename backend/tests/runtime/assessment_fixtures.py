"""Runtime 題組測試共用的合成觀念、受控模型與操作。"""

from copy import deepcopy
import io
import re
from uuid import uuid4

import psycopg
import pymupdf
import pytest

from knowledge_map.structure import SemanticState, apply_semantic_response, build_document_context
from learning_adaptation import assessment_sets as sets
from learning_adaptation.study_sessions import create_study_session
from pdf_evidence.ocr_page_evidence import canonical_sha256
from product_fixtures import closed_loop, library_materials, publish_fixture_structure, seed_pdf, seed_run
from runtime.material_processing import _record_progress, claim_next_material_processing_run
from runtime.semantic_service import SemanticServiceError
from runtime.storage.tables import Assessment, database_session
from structure_fixtures import build_knowledge_structure



def concept_fixture(closed_loop, count=3, *, facts=None, label="Signals", evidence_kind="paragraph", source_review_required=False):
    learner, _, settings, _, dsn, token = closed_loop
    points = [f"Signal {index} uses code{index}." for index in range(count)] if facts is None else facts
    count = len(points)
    evidence_texts = [*points, "Other topic uses EXTERNAL."]
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        for index, value in enumerate(evidence_texts):
            page.insert_text((72, 72 + index * 22), value)
        regions = [list(page.search_for(value)[0]) for value in evidence_texts]
        payload = pdf.tobytes()

    source = seed_pdf(learner.learner_id, io.BytesIO(payload), str(uuid4()), dsn=dsn)
    run = seed_run(learner.learner_id, source.material_id, str(uuid4()), settings, dsn=dsn)
    assert claim_next_material_processing_run(dsn=dsn).run.run_id == run.run_id
    for stage in ("evidence", "semantics", "publishing"):
        _record_progress(run.run_id, stage, 1, 1, dsn=dsn)

    page_ref = "page:sha256:" + canonical_sha256({
        "source_sha256": source.sha256, "page_number": 1,
    })
    blocks = []
    for index, value in enumerate(evidence_texts):
        region = regions[index]
        block_id = "block:sha256:" + canonical_sha256({
            "page_ref": page_ref, "reading_order": index, "region": region,
        })
        content = {
            "page_ref": page_ref,
            "block_id": block_id,
            "kind": evidence_kind,
            "source": "native_text",
            "text": value,
            "reading_order": index,
            "region": region,
        }
        blocks.append({
            "evidence_id": "evidence:sha256:" + canonical_sha256(content),
            "block_id": block_id,
            "kind": evidence_kind,
            "source": "native_text",
            "text": value,
            "reading_order": index,
            "locator": {"page": 1, "block_id": block_id, "region": region},
        })
    page = {
        "schema": "page-evidence/v1",
        "material_id": "material:sha256:" + source.sha256,
        "page_ref": page_ref,
        "page_number": 1,
        "evidence_blocks": blocks,
    }
    context = build_document_context([page], page_count=1)
    state = SemanticState()
    state.source_review_required = source_review_required
    apply_semantic_response(
        {
            "concepts": [
                {
                    "k": "signals", "l": label, "a": [],
                    "c": [{"m": None, "s": [index]} for index in range(count)],
                },
                {"k": "other", "l": "Other topic", "a": [], "c": [{"m": None, "s": [count]}]},
            ],
            "relations": [],
        },
        context=context,
        bundle={"sections": context["sections"], "evidence": context["evidence"]},
        state=state,
    )

    lock = settings["runtime_lock"]
    document = build_knowledge_structure(
        context, state,
        source_sha256=source.sha256,
        run_id=str(run.run_id),
        produced_at="2026-09-20T00:00:00+00:00",
        runtime_lock_sha256=canonical_sha256(lock),
        model_id=lock["semantic_service"]["model_id"],
        model_revision=lock["semantic_service"]["revision"],
        semantic_calls=1,
        ocr_calls=0,
    )
    publish_fixture_structure(learner.learner_id, source.material_id, run.run_id, document, dsn=dsn)
    concept = next(item for item in document["concepts"] if item["label"] == label)
    study = create_study_session(
        learner, source.material_id, document["revision"], str(uuid4()),
        current_concept_id=concept["concept_id"], dsn=dsn,
    )
    return {
        "learner": learner, "settings": settings, "dsn": dsn, "token": token,
        "source": source, "run": run, "document": document, "concept": concept, "study": study,
    }


def model_for(fixture, *, fail=(), calls=None):
    answers = {}
    calls = [] if calls is None else calls

    def model(_client, **kwargs):
        calls.append(kwargs["task"])
        # 真正的另一個 connection 可立即取得 session 鎖，證明推論沒有包在長交易裡。
        with psycopg.connect(fixture["dsn"]) as connection:
            connection.execute(
                "SELECT study_session_id FROM study_sessions "
                "WHERE study_session_id=%s FOR UPDATE NOWAIT",
                (fixture["study"].study_session_id,),
            )
        if kwargs["task"] == "assessment":
            request = kwargs["request"]
            match = re.search(r"Signal (\d+) uses", request["claim"]["text"])
            assert match is not None, "題組不可跨到其他觀念"
            number = int(match[1])
            if number in fail:
                raise SemanticServiceError("SEMANTIC_SERVICE_UNAVAILABLE")
            candidate = {
                "learning_angle": "signal code",
                "novelty": "distinct",
                "safety": "safe",
                "prompt": f"Which code does Signal {number} use?",
                "correct_answer": f"code{number}",
                "supporting_evidence_ids": [request["claim"]["evidence"][0]["evidence_id"]],
                "distractors": [f"wrong{number}a", f"wrong{number}b", f"wrong{number}c"],
            }
            answers[candidate["prompt"]] = candidate["correct_answer"]
            return {
                "schema": "assessment-semantics-response/v1",
                "candidates": [candidate, {**candidate, "safety": "reject"}, {**candidate, "safety": "reject"}],
            }

        verdicts = [
            {
                "question_index": question["question_index"],
                "answer_status": "unique",
                "selected_option_index": question["options"].index(answers[question["prompt"]]),
                "duplicate_prior_index": None,
                "quality_issues": [],
            }
            for question in kwargs["request"]["questions"]
        ]
        return {"schema": "assessment-check-response/v1", "verdicts": verdicts}

    return model


def finish_set(fixture, *, fail=(), calls=None):
    model = model_for(fixture, fail=fail, calls=calls)
    while work := sets.claim_set_work(dsn=fixture["dsn"]):
        sets.execute_set_work(work, dsn=fixture["dsn"], semantic_call=model)


def create(fixture, key="round"):
    return sets.create_set(
        fixture["learner"], fixture["study"].study_session_id,
        fixture["concept"]["concept_id"], key, fixture["settings"], dsn=fixture["dsn"],
    )


def read(fixture, identity):
    return sets.read_set(
        fixture["learner"], fixture["study"].study_session_id, identity, dsn=fixture["dsn"],
    )


def other_concept(f):
    return next(
        concept for concept in f["document"]["concepts"]
        if concept["concept_id"] != f["concept"]["concept_id"]
    )


def create_other(f):
    return sets.create_set(
        f["learner"], f["study"].study_session_id, other_concept(f)["concept_id"],
        "other-concept", f["settings"], dsn=f["dsn"],
    )


def other_model(_client, **kwargs):
    if kwargs["task"] == "assessment":
        claim = kwargs["request"]["claim"]
        assert claim["text"] == "Other topic uses EXTERNAL."
        candidate = {
            "learning_angle": "topic code",
            "novelty": "distinct",
            "safety": "safe",
            "prompt": "Which code does the other topic use?",
            "correct_answer": "EXTERNAL",
            "supporting_evidence_ids": [claim["evidence"][0]["evidence_id"]],
            "distractors": ["INTERNAL", "NATIVE", "SIGNAL"],
        }
        return {
            "schema": "assessment-semantics-response/v1",
            "candidates": [candidate, {**candidate, "safety": "reject"}, {**candidate, "safety": "reject"}],
        }
    verdicts = [
        {
            "question_index": question["question_index"],
            "answer_status": "unique",
            "selected_option_index": question["options"].index("EXTERNAL"),
            "duplicate_prior_index": None,
            "quality_issues": [],
        }
        for question in kwargs["request"]["questions"]
    ]
    return {"schema": "assessment-check-response/v1", "verdicts": verdicts}


def finish(fixture, variant="initial", *, fail=(), calls=None):
    base_model = model_for(fixture, fail=fail, calls=calls)
    original_prompt_by_generated_prompt = {}

    def model(client, **kwargs):
        if kwargs["task"] == "assessment":
            response = base_model(client, **kwargs)
            for candidate in response["candidates"]:
                original_prompt = candidate["prompt"]
                candidate["prompt"] = f"{variant}: {original_prompt}"
                original_prompt_by_generated_prompt[candidate["prompt"]] = original_prompt
            return response

        request = deepcopy(kwargs["request"])
        for question in request["questions"]:
            question["prompt"] = original_prompt_by_generated_prompt[question["prompt"]]
        return base_model(client, **{**kwargs, "request": request})

    while work := sets.claim_set_work(dsn=fixture["dsn"]):
        sets.execute_set_work(work, dsn=fixture["dsn"], semantic_call=model)


def answer(fixture, group_id, wrong=()):
    group = read(fixture, group_id)
    answers = []
    for item in group["items"]:
        if not item["assessment"]:
            continue
        public = item["assessment"]
        with database_session(fixture["dsn"]) as session:
            correct_option_id = session.get(
                Assessment, public["assessment_revision"],
            ).private_answer_document["correct_option_id"]
        if item["ordinal"] in wrong:
            selected_option_id = next(
                option["option_id"] for option in public["options"]
                if option["option_id"] != correct_option_id
            )
        else:
            selected_option_id = correct_option_id
        answers.append({
            "assessment_revision": public["assessment_revision"],
            "question_id": public["question_id"],
            "selected_option_id": selected_option_id,
        })

    sets.submit_set_answers(
        fixture["learner"], fixture["study"].study_session_id, group_id,
        answers, group["set_version"], str(uuid4()), dsn=fixture["dsn"],
    )


def change(fixture, group_id, action):
    current = read(fixture, group_id)
    sets.change_set(
        fixture["learner"], fixture["study"].study_session_id, group_id,
        action, current["set_version"], str(uuid4()), dsn=fixture["dsn"],
    )


def supplement(fixture, group_id, key=None):
    cycle = read(fixture, group_id)["cycle"]
    return sets.create_remediation(
        fixture["learner"], fixture["study"].study_session_id, group_id,
        cycle["set_version"], key or str(uuid4()), fixture["settings"], dsn=fixture["dsn"],
    )


def answers_for(fixture, set_id):
    answers = []
    items = read(fixture, set_id)["items"]
    with database_session(fixture["dsn"]) as session:
        for item in items:
            if not item["assessment"]:
                continue
            public = item["assessment"]
            assessment = session.get(Assessment, public["assessment_revision"])
            answers.append({
                "assessment_revision": public["assessment_revision"],
                "question_id": public["question_id"],
                "selected_option_id": assessment.private_answer_document["correct_option_id"],
            })
    return answers


def send(fixture, set_id, answers, key="submit", version=None):
    expected_version = read(fixture, set_id)["set_version"] if version is None else version
    sets.submit_set_answers(
        fixture["learner"], fixture["study"].study_session_id, set_id,
        answers, expected_version, key, dsn=fixture["dsn"],
    )


@pytest.fixture
def learning_records(library_materials, closed_loop):
    fixture = concept_fixture(closed_loop, 2)
    set_id = create(fixture)
    finish_set(fixture)
    answer(fixture, set_id)
    return {
        **library_materials,
        "first": fixture["source"],
        "structure": fixture["document"],
        "active": fixture["study"],
        "set_id": set_id,
    }
