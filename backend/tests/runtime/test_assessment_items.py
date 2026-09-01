from __future__ import annotations

from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import shutil
from threading import Event
from uuid import uuid4

import psycopg
from psycopg.types.json import Jsonb
import pytest

import learning_adaptation.assessment_generation as generation_module
import learning_adaptation.assessment_requests as requests_module
from learning_adaptation.assessment_generation import (
    AssessmentGenerationError,
    generate_and_store_assessment,
)
from learning_adaptation.assessment_requests import (
    generate_assessment_for_request,
)
from learning_adaptation.map_context import (
    ClaimContext,
    EvidenceLocator,
    FormalConceptContext,
)
from learning_adaptation.learner_progress import derive_learner_progress
from learning_adaptation.assessment_items import (
    ASSESSMENT_POLICY_REVISION,
    GENERATION_PROVENANCE_SCHEMA,
    AssessmentError,
    build_assessment_semantic_novelty,
    build_single_choice_assessment,
    project_public_assessment,
    read_assessment,
    store_assessment,
    validate_assessment_generation_provenance,
    validate_assessment_documents,
)
from learning_adaptation.study_sessions import (
    complete_study_session,
    create_study_session,
)
from pdf_evidence.ocr_page_evidence import canonical_sha256
from runtime.learner_session import TrustedLearner
from runtime.storage.migrations import run_migrations
from test_study_sessions import _insert_material_map, _knowledge_map


@pytest.fixture
def assessment_database_dsn(
    clean_database_dsn: str, migrations_dir: Path
) -> str:
    assert run_migrations(clean_database_dsn, migrations_dir=migrations_dir) == (
        1,
        2,
        3,
        4,
        5,
        6,
        7,
        8,
        9,
        10,
        11,
        12,
        13,
        14,
        15,
        16,
        17,
    )
    return clean_database_dsn


def _active_study_session(dsn: str):
    learner = TrustedLearner(uuid4())
    knowledge_map = _knowledge_map()
    material_id = _insert_material_map(
        dsn, learner.learner_id, knowledge_map
    )
    target = knowledge_map["formal_concepts"][0]
    study_session = create_study_session(
        learner,
        material_id,
        knowledge_map["revision"],
        str(uuid4()),
        current_formal_concept_id=target["formal_concept_id"],
        dsn=dsn,
    )
    return learner, knowledge_map, material_id, study_session


def _documents(study_session, knowledge_map, **changes):
    target = knowledge_map["formal_concepts"][0]
    claim = target["claims"][0]
    values = {
        "study_session_id": study_session.study_session_id,
        "knowledge_map_revision": knowledge_map["revision"],
        "target_formal_concept_id": target["formal_concept_id"],
        "target_claim_id": claim["claim_id"],
        "source_evidence_ids": claim["evidence_ids"],
        "prompt": "Which statement matches the grounded claim?",
        "option_texts": [
            "Grounded answer",
            "Plausible distractor",
            "Unrelated distractor",
            "Opposite distractor",
        ],
        "correct_option_index": 0,
        "rationale": "The selected option is supported by the cited Evidence.",
    }
    values.update(changes)
    return build_single_choice_assessment(**values)


def _documents_as_dicts(documents):
    return (
        documents.public_document.model_dump(mode="json", by_alias=True),
        documents.private_answer_document.model_dump(mode="json", by_alias=True),
    )


def _qualified_novelty(documents):
    return build_assessment_semantic_novelty(
        documents,
        comparison_policy_revision="distinct-mastery-evidence:no-prior/v1",
        verifier_model_id="test-verifier",
        verifier_revision="test-verifier/v1",
        compared_semantic_identities=[],
        maximum_equivalence_score=None,
        runtime_binding_sha256="9" * 64,
    )


def _generation_provenance(documents):
    public = documents.public_document
    private = documents.private_answer_document
    correct_index = next(
        index
        for index, option in enumerate(public.options)
        if option.option_id == private.correct_option_id
    )
    probabilities = [0.1, 0.1, 0.1, 0.1]
    probabilities[correct_index] = 0.9
    value = {
        "schema": GENERATION_PROVENANCE_SCHEMA,
        "assessment_revision": public.assessment_revision,
        "question_id": public.question_id,
        "generation_policy_revision": "assessment-generation-policy/v1",
        "runtime_binding_sha256": "2" * 64,
        "model_id": "Qwen/Qwen3-14B-AWQ",
        "model_revision": "content-sha256:" + "3" * 64,
        "proposal_prompt_sha256": "4" * 64,
        "repair_prompt_sha256": "5" * 64,
        "verifier_model_id": "MoritzLaurer/mDeBERTa-v3-base-mnli-xnli",
        "verifier_revision": "6" * 40,
        "selected_stage": "proposal",
        "selected_candidate_index": 1,
        "selected_evidence_ids": public.source_evidence_ids,
        "option_entailment_probabilities": probabilities,
        "selected_evidence_option_entailment_probabilities": probabilities,
        "correct_option_index": correct_index,
        "entailment_margin_threshold": 0.1,
        "multiple_support_risk_threshold": 0.4,
        "entailment_margin": 0.8,
        "selected_evidence_entailment_margin": 0.8,
        "maximum_distractor_entailment": 0.1,
        "risk_trigger_distractor_entailment": 0.1,
        "multiple_support_risk": False,
        "provenance_sha256": "0" * 64,
    }
    identity = deepcopy(value)
    identity.pop("provenance_sha256")
    value["provenance_sha256"] = canonical_sha256(identity)
    return value


def _assessment_count(dsn: str) -> int:
    with psycopg.connect(dsn) as connection:
        return connection.execute("SELECT count(*) FROM assessments").fetchone()[0]


def test_migration_eight_preserves_valid_study_session_and_adds_only_current_fields(
    clean_database_dsn: str,
    migrations_dir: Path,
    tmp_path: Path,
):
    migration_directory = tmp_path / "migration-eight"
    migration_directory.mkdir()
    for source in sorted(migrations_dir.glob("*.sql")):
        if int(source.name[:4]) <= 7:
            shutil.copy2(source, migration_directory / source.name)
    assert run_migrations(
        clean_database_dsn, migrations_dir=migration_directory
    ) == (1, 2, 3, 4, 5, 6, 7)
    learner = TrustedLearner(uuid4())
    knowledge_map = _knowledge_map()
    material_id = _insert_material_map(
        clean_database_dsn,
        learner.learner_id,
        knowledge_map,
        persist_material_run=False,
    )
    study_session_id = uuid4()
    with psycopg.connect(clean_database_dsn) as connection:
        connection.execute(
            """
            INSERT INTO study_sessions (
                study_session_id, learner_id, material_id,
                knowledge_map_revision, current_formal_concept_id, status,
                idempotency_key_sha256, request_fingerprint, started_at,
                completed_at, last_event_number
            ) VALUES (
                %s, %s, %s, %s, %s, 'active', %s, %s,
                statement_timestamp(), NULL, 0
            )
            """,
            (
                study_session_id,
                learner.learner_id,
                material_id,
                knowledge_map["revision"],
                knowledge_map["formal_concepts"][0]["formal_concept_id"],
                b"i" * 32,
                b"f" * 32,
            ),
        )
        before = connection.execute(
            "SELECT * FROM study_sessions WHERE study_session_id = %s",
            (study_session_id,),
        ).fetchone()
    shutil.copy2(
        migrations_dir / "0008_add_single_choice_assessments.sql",
        migration_directory,
    )

    assert run_migrations(
        clean_database_dsn, migrations_dir=migration_directory
    ) == (8,)
    with psycopg.connect(clean_database_dsn) as connection:
        assert connection.execute(
            "SELECT * FROM study_sessions WHERE study_session_id = %s",
            (study_session_id,),
        ).fetchone() == before
        assert connection.execute("SELECT count(*) FROM assessments").fetchone() == (
            0,
        )
        columns = [
            row[0]
            for row in connection.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = 'assessments'
                ORDER BY ordinal_position
                """
            ).fetchall()
        ]
    assert columns == [
        "assessment_revision",
        "study_session_id",
        "knowledge_map_revision",
        "question_id",
        "target_formal_concept_id",
        "target_claim_id",
        "public_document",
        "private_answer_document",
        "policy_revision",
        "created_at",
    ]


def test_migration_ten_requires_selected_grounding_provenance_v2(
    assessment_database_dsn: str,
):
    with psycopg.connect(assessment_database_dsn) as connection:
        definition = connection.execute(
            """
            SELECT pg_get_constraintdef(oid)
            FROM pg_constraint
            WHERE conname = 'assessments_generation_provenance_object'
            """
        ).fetchone()[0]

    assert "assessment-generation-provenance/v2" in definition
    assert "assessment-generation-provenance/v1" not in definition


def test_valid_public_private_round_trip_and_public_projection_has_no_answer_leak():
    knowledge_map = _knowledge_map()
    target = knowledge_map["formal_concepts"][0]
    session_id = uuid4()
    documents = build_single_choice_assessment(
        session_id,
        knowledge_map["revision"],
        target["formal_concept_id"],
        target["claims"][0]["claim_id"],
        target["claims"][0]["evidence_ids"],
        "Which statement is grounded?",
        ["First", "Second", "Third", "Fourth"],
        2,
        "The third option follows from the cited Evidence.",
    )
    public, private = _documents_as_dicts(documents)

    assert validate_assessment_documents(public, private) == documents
    projected = project_public_assessment(documents)
    assert projected == public
    assert projected["question_type"] == "single_choice"
    assert projected["policy_revision"] == ASSESSMENT_POLICY_REVISION
    assert len(projected["options"]) == 4
    assert len({option["option_id"] for option in projected["options"]}) == 4
    serialized = json.dumps(projected, sort_keys=True)
    for forbidden in (
        "answer_key",
        "correct_option_id",
        "is_correct",
        "rationale",
        "private_answer_sha256",
        "learner_id",
        "model_output",
        "prompt_internals",
        "db_path",
        "raw_material_content",
        "semantic_focus",
        "semantic_identity",
        "semantic_novelty",
    ):
        assert forbidden not in serialized


def test_semantic_identity_ignores_claim_evidence_formatting_and_option_order():
    knowledge_map = _knowledge_map()
    target = knowledge_map["formal_concepts"][0]
    session_id = uuid4()
    first = build_single_choice_assessment(
        session_id,
        knowledge_map["revision"],
        target["formal_concept_id"],
        "claim:sha256:" + "1" * 64,
        ["evidence:sha256:" + "1" * 64],
        "  Which statement is grounded?  ",
        ["Grounded", "Second", "Third", "Fourth"],
        0,
        "Grounded is correct.",
    )
    alternate = build_single_choice_assessment(
        session_id,
        knowledge_map["revision"],
        target["formal_concept_id"],
        "claim:sha256:" + "2" * 64,
        ["evidence:sha256:" + "2" * 64],
        "which statement is grounded?",
        ["Fourth", "Third", "Grounded", "Second"],
        2,
        "Grounded is correct.",
    )
    arguments = {
        "comparison_policy_revision": (
            "entailment-or-unproven-neutral-reject/v3"
        ),
        "verifier_model_id": "MoritzLaurer/mDeBERTa-v3-base-mnli-xnli",
        "verifier_revision": "8" * 40,
        "compared_semantic_identities": [],
        "maximum_equivalence_score": None,
    }
    first_novelty = build_assessment_semantic_novelty(
        first, runtime_binding_sha256="3" * 64, **arguments
    )
    alternate_novelty = build_assessment_semantic_novelty(
        alternate, runtime_binding_sha256="4" * 64, **arguments
    )

    assert first.public_document.question_id != alternate.public_document.question_id
    assert first_novelty.semantic_identity == alternate_novelty.semantic_identity
    assert first_novelty.novelty_sha256 != alternate_novelty.novelty_sha256


def test_four_private_answers_and_predictable_rationales_have_identical_public_bytes():
    knowledge_map = _knowledge_map()
    target = knowledge_map["formal_concepts"][0]
    session_id = uuid4()
    variants = [
        build_single_choice_assessment(
            session_id,
            knowledge_map["revision"],
            target["formal_concept_id"],
            target["claims"][0]["claim_id"],
            target["claims"][0]["evidence_ids"],
            "Which statement is grounded?",
            ["First", "Second", "Third", "Fourth"],
            correct_index,
            f"Predictable rationale for option {correct_index + 1}.",
        )
        for correct_index in range(4)
    ]

    public_bytes = {
        json.dumps(
            project_public_assessment(variant),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        for variant in variants
    }
    assert len(public_bytes) == 1
    assert len(
        {
            variant.private_answer_document.correct_option_id
            for variant in variants
        }
    ) == 4
    assert len(
        {variant.public_document.assessment_revision for variant in variants}
    ) == 1
    assert len({variant.public_document.question_id for variant in variants}) == 1


def test_schema_validation_rejects_all_closed_contract_failures():
    knowledge_map = _knowledge_map()
    target = knowledge_map["formal_concepts"][0]
    documents = build_single_choice_assessment(
        uuid4(),
        knowledge_map["revision"],
        target["formal_concept_id"],
        target["claims"][0]["claim_id"],
        target["claims"][0]["evidence_ids"],
        "Choose the grounded statement.",
        ["Alpha", "Beta", "Gamma", "Delta"],
        1,
        "The cited Evidence supports Beta.",
    )
    original_public, original_private = _documents_as_dicts(documents)
    invalid_documents = []

    def changed_public(change):
        public = deepcopy(original_public)
        private = deepcopy(original_private)
        change(public)
        invalid_documents.append((public, private))

    def changed_private(change):
        public = deepcopy(original_public)
        private = deepcopy(original_private)
        change(private)
        invalid_documents.append((public, private))

    changed_public(lambda public: public.update(question_type="multiple_choice"))
    changed_public(lambda public: public.update(options=public["options"][:3]))
    changed_public(lambda public: public.update(options=public["options"] + [public["options"][0]]))
    changed_public(lambda public: public.update(prompt=" \t\n"))
    changed_public(lambda public: public["options"][0].update(text="　"))
    changed_public(
        lambda public: public["options"][1].update(
            option_id=public["options"][0]["option_id"]
        )
    )
    changed_public(
        lambda public: public["options"][1].update(text="Ａlpha")
    )
    changed_public(lambda public: public.update(source_evidence_ids=[]))
    changed_public(
        lambda public: public.update(
            source_evidence_ids=[
                public["source_evidence_ids"][0],
                public["source_evidence_ids"][0],
            ]
        )
    )
    changed_public(lambda public: public.update(question_id="question-invalid"))
    changed_public(lambda public: public["options"][0].update(option_id="option-invalid"))
    changed_public(lambda public: public.update(target_formal_concept_id="concept-invalid"))
    changed_public(lambda public: public.update(target_claim_id="claim-invalid"))
    changed_public(lambda public: public.update(source_evidence_ids=["evidence-invalid"]))
    changed_public(lambda public: public.update(study_session_id="session-invalid"))
    changed_public(lambda public: public.update(assessment_revision="assessment-invalid"))
    changed_public(lambda public: public.update(policy_revision="policy/v2"))
    changed_public(lambda public: public.update(unexpected="field"))
    changed_public(lambda public: public["options"][0].update(unexpected="field"))
    changed_public(
        lambda public: public.update(
            correct_option_id=original_private["correct_option_id"]
        )
    )
    changed_private(lambda private: private.pop("correct_option_id"))
    changed_private(lambda private: private.pop("private_answer_sha256"))
    changed_private(
        lambda private: private.update(private_answer_sha256="not-a-digest")
    )
    changed_private(
        lambda private: private.update(
            correct_option_id=[
                original_private["correct_option_id"],
                original_public["options"][0]["option_id"],
            ]
        )
    )
    changed_private(
        lambda private: private.update(
            correct_option_id="option:sha256:" + "f" * 64
        )
    )
    changed_private(lambda private: private.update(rationale="　"))
    changed_private(lambda private: private.update(unexpected="field"))
    changed_private(lambda private: private.update(prompt=original_public["prompt"]))

    assert len(invalid_documents) == 28
    for public, private in invalid_documents:
        with pytest.raises(
            AssessmentError, match="^ASSESSMENT_DOCUMENT_INVALID$"
        ):
            validate_assessment_documents(public, private)


def test_store_read_replay_conflict_and_private_document_separation(
    assessment_database_dsn: str,
):
    learner, knowledge_map, _, study_session = _active_study_session(
        assessment_database_dsn
    )
    documents = _documents(study_session, knowledge_map)
    public, private = _documents_as_dicts(documents)

    stored = store_assessment(
        learner, public, private, dsn=assessment_database_dsn
    )
    replay = store_assessment(
        learner, public, private, dsn=assessment_database_dsn
    )
    assert replay == stored
    with pytest.raises(AssessmentError, match="^ASSESSMENT_NO_NEW_ITEM$"):
        store_assessment(
            learner,
            public,
            private,
            require_new=True,
            dsn=assessment_database_dsn,
        )
    assert read_assessment(
        learner, stored.assessment_revision, dsn=assessment_database_dsn
    ) == stored
    assert project_public_assessment(stored) == public
    with psycopg.connect(assessment_database_dsn) as connection:
        public_row, private_row = connection.execute(
            """
            SELECT public_document, private_answer_document
            FROM assessments WHERE assessment_revision = %s
            """,
            (stored.assessment_revision,),
        ).fetchone()
    assert "correct_option_id" not in public_row
    assert "rationale" not in public_row
    assert private_row["correct_option_id"] == private["correct_option_id"]
    assert private_row["rationale"] == private["rationale"]

    conflicting = _documents(
        study_session,
        knowledge_map,
        correct_option_index=1,
        rationale="A conflicting answer for the same semantic question.",
    )
    conflicting_public, conflicting_private = _documents_as_dicts(conflicting)
    assert conflicting.public_document.question_id == stored.question_id
    assert [
        option.option_id for option in conflicting.public_document.options
    ] == [option.option_id for option in stored.public_document.options]
    assert conflicting_public == public
    assert conflicting.public_document.assessment_revision == stored.assessment_revision
    with pytest.raises(AssessmentError, match="^ASSESSMENT_CONFLICT$"):
        store_assessment(
            learner,
            conflicting_public,
            conflicting_private,
            dsn=assessment_database_dsn,
        )
    assert _assessment_count(assessment_database_dsn) == 1
    with psycopg.connect(assessment_database_dsn) as connection:
        assert connection.execute(
            """
            SELECT private_answer_document FROM assessments
            WHERE assessment_revision = %s
            """,
            (stored.assessment_revision,),
        ).fetchone()[0] == private

    changed_bytes = deepcopy(public)
    changed_bytes["prompt"] = "Changed after identity was assigned."
    with pytest.raises(AssessmentError, match="^ASSESSMENT_DOCUMENT_INVALID$"):
        store_assessment(
            learner, changed_bytes, private, dsn=assessment_database_dsn
        )
    assert _assessment_count(assessment_database_dsn) == 1


def test_database_allows_only_one_semantic_identity_per_study_session(
    assessment_database_dsn: str,
):
    learner, knowledge_map, _, study_session = _active_study_session(
        assessment_database_dsn
    )
    first = _documents(study_session, knowledge_map)
    first_public, first_private = _documents_as_dicts(first)
    stored = store_assessment(
        learner,
        first_public,
        first_private,
        require_new=True,
        dsn=assessment_database_dsn,
    )
    reordered = _documents(
        study_session,
        knowledge_map,
        prompt="  which statement matches the grounded claim? ",
        option_texts=[
            "Opposite distractor",
            "Grounded answer",
            "Unrelated distractor",
            "Plausible distractor",
        ],
        correct_option_index=1,
    )
    reordered_public, reordered_private = _documents_as_dicts(reordered)

    assert stored.question_id != reordered.public_document.question_id
    with pytest.raises(AssessmentError, match="^ASSESSMENT_NO_NEW_ITEM$"):
        store_assessment(
            learner,
            reordered_public,
            reordered_private,
            require_new=True,
            dsn=assessment_database_dsn,
        )
    assert _assessment_count(assessment_database_dsn) == 1

    other_learner, other_map, _, other_session = _active_study_session(
        assessment_database_dsn
    )
    other = _documents(other_session, other_map)
    other_public, other_private = _documents_as_dicts(other)
    assert store_assessment(
        other_learner,
        other_public,
        other_private,
        require_new=True,
        dsn=assessment_database_dsn,
    ).semantic_identity == stored.semantic_identity
    assert _assessment_count(assessment_database_dsn) == 2


def test_concurrent_requests_publish_at_most_one_session_semantic_identity(
    assessment_database_dsn: str,
    monkeypatch,
):
    learner, knowledge_map, _, study_session = _active_study_session(
        assessment_database_dsn
    )
    documents = _documents(study_session, knowledge_map)
    public, private = _documents_as_dicts(documents)

    def store_same_candidate(*_args, **_kwargs):
        try:
            return store_assessment(
                learner,
                public,
                private,
                require_new=True,
                dsn=assessment_database_dsn,
            )
        except AssessmentError as error:
            if str(error) == "ASSESSMENT_NO_NEW_ITEM":
                raise AssessmentGenerationError(
                    "ASSESSMENT_NO_NEW_SAFE_ITEM"
                ) from None
            raise

    monkeypatch.setattr(
        requests_module,
        "generate_and_store_assessment",
        store_same_candidate,
    )

    def request(key: str):
        try:
            return generate_assessment_for_request(
                learner,
                study_session.study_session_id,
                knowledge_map["formal_concepts"][0]["claims"][0]["claim_id"],
                {},
                key,
                dsn=assessment_database_dsn,
            )
        except AssessmentGenerationError as error:
            return str(error)

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(request, ("request-a", "request-b")))

    assert sum(not isinstance(outcome, str) for outcome in outcomes) == 1
    assert outcomes.count("ASSESSMENT_NO_NEW_SAFE_ITEM") == 1
    assert _assessment_count(assessment_database_dsn) == 1
    with psycopg.connect(assessment_database_dsn) as connection:
        assert connection.execute(
            "SELECT count(*) FROM answer_events"
        ).fetchone() == (0,)
        assert connection.execute(
            """
            SELECT last_event_number
            FROM study_sessions WHERE study_session_id = %s
            """,
            (study_session.study_session_id,),
        ).fetchone() == (0,)
    state = derive_learner_progress(
        learner,
        study_session.study_session_id,
        dsn=assessment_database_dsn,
    ).concept_states[0]
    assert state.valid_attempts == 0
    assert state.qualified_distinct_correct_items == 0
    assert state.observed_evidence_ids == []
    assert state.post_error_improvement is False


def test_no_safe_write_serializes_overlapping_safe_generation(
    assessment_database_dsn: str,
    monkeypatch,
):
    learner, knowledge_map, _, study_session = _active_study_session(
        assessment_database_dsn
    )
    target = knowledge_map["formal_concepts"][0]
    target_claim_id = target["claims"][0]["claim_id"]
    documents = _documents(study_session, knowledge_map)
    no_safe_write_started = Event()
    second_request_entered = Event()
    safe_generation_started = Event()
    first_generation_failed = Event()
    allow_no_safe_write = Event()
    original_record = requests_module._record_no_safe_assessment_in_session

    def pause_before_no_safe_write(*args):
        no_safe_write_started.set()
        assert allow_no_safe_write.wait(timeout=5)
        return original_record(*args)

    def fail_then_offer_safe_candidate(*_args, **_kwargs):
        if not first_generation_failed.is_set():
            first_generation_failed.set()
            raise AssessmentGenerationError("ASSESSMENT_NO_NEW_SAFE_ITEM")
        safe_generation_started.set()
        return store_assessment(
            learner,
            documents.public_document,
            documents.private_answer_document,
            require_new=True,
            dsn=assessment_database_dsn,
        )

    monkeypatch.setattr(
        requests_module,
        "_record_no_safe_assessment_in_session",
        pause_before_no_safe_write,
    )
    monkeypatch.setattr(
        requests_module,
        "generate_and_store_assessment",
        fail_then_offer_safe_candidate,
    )

    def request(key: str):
        if key == "request-b":
            second_request_entered.set()
        try:
            return generate_assessment_for_request(
                learner,
                study_session.study_session_id,
                target_claim_id,
                {},
                key,
                dsn=assessment_database_dsn,
            )
        except AssessmentGenerationError as error:
            return str(error)

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(request, "request-a")
        assert no_safe_write_started.wait(timeout=5)
        second = executor.submit(request, "request-b")
        assert second_request_entered.wait(timeout=5)
        try:
            assert not safe_generation_started.wait(timeout=1)
        finally:
            allow_no_safe_write.set()
        outcomes = [first.result(timeout=5), second.result(timeout=5)]

    assert outcomes == [
        "ASSESSMENT_NO_NEW_SAFE_ITEM",
        "ASSESSMENT_NO_NEW_SAFE_ITEM",
    ]
    assert safe_generation_started.is_set() is False
    assert _assessment_count(assessment_database_dsn) == 0
    with psycopg.connect(assessment_database_dsn) as connection:
        assert connection.execute(
            """
            SELECT status, no_safe_claim_ids
            FROM study_sessions WHERE study_session_id = %s
            """,
            (study_session.study_session_id,),
        ).fetchone() == ("active", [target_claim_id])


def test_generation_provenance_is_private_bound_and_tamper_evident(
    assessment_database_dsn: str,
):
    learner, knowledge_map, _, study_session = _active_study_session(
        assessment_database_dsn
    )
    documents = _documents(study_session, knowledge_map)
    public, private = _documents_as_dicts(documents)
    provenance = _generation_provenance(documents)

    checked = validate_assessment_generation_provenance(
        provenance, documents
    )
    stored = store_assessment(
        learner,
        public,
        private,
        generation_provenance=provenance,
        dsn=assessment_database_dsn,
    )

    assert stored.generation_provenance == checked
    assert "generation_provenance" not in project_public_assessment(stored)
    with psycopg.connect(assessment_database_dsn) as connection:
        stored_public, stored_provenance = connection.execute(
            """
            SELECT public_document, generation_provenance
            FROM assessments WHERE assessment_revision = %s
            """,
            (stored.assessment_revision,),
        ).fetchone()
    assert "correct_option_id" not in stored_public
    assert stored_provenance == provenance

    tampered = deepcopy(provenance)
    tampered["option_entailment_probabilities"][0] = 0.2
    with psycopg.connect(assessment_database_dsn) as connection:
        connection.execute(
            """
            UPDATE assessments SET generation_provenance = %s
            WHERE assessment_revision = %s
            """,
            (Jsonb(tampered), stored.assessment_revision),
        )
    with pytest.raises(AssessmentError, match="^ASSESSMENT_DOCUMENT_INVALID$"):
        read_assessment(
            learner, stored.assessment_revision, dsn=assessment_database_dsn
        )


def test_production_generation_uses_canonical_evidence_and_stores_private_answer(
    assessment_database_dsn: str,
    monkeypatch,
    tmp_path: Path,
):
    learner, knowledge_map, _, study_session = _active_study_session(
        assessment_database_dsn
    )
    claim = knowledge_map["formal_concepts"][0]["claims"][0]
    proposal = {
        "candidates": [
            {
                "support_ids": ["e1"],
                "prompt": f"Which statement matches grounded candidate {index}?",
                "correct_option": f"Canonical Evidence {index + 1}",
                "distractors": [
                    f"Unsupported value {index}-1",
                    f"Unsupported value {index}-2",
                    f"Unsupported value {index}-3",
                ],
            }
            for index in range(3)
        ]
    }
    policy = {
        "policy_revision": "assessment-generation-policy/v1",
        "shared_models": {
            "semantic_model_id": "Qwen/Qwen3-14B-AWQ",
            "semantic_revision": "content-sha256:" + "2" * 64,
            "verifier_model_id": "MoritzLaurer/mDeBERTa-v3-base-mnli-xnli",
            "verifier_revision": "5" * 40,
        },
        "proposal": {
            "prompt": "proposal",
            "prompt_sha256": "3" * 64,
            "generation": {"max_tokens": 2800},
            "timeout_seconds": 300,
            "retry": {
                "max_attempts": 2,
                "retryable_reasons": [
                    "CONCEPT_API_TIMEOUT",
                    "CONCEPT_API_UNAVAILABLE",
                ],
            },
        },
        "repair": {
            "prompt": "repair",
            "prompt_sha256": "4" * 64,
            "generation": {"max_tokens": 3400},
            "timeout_seconds": 300,
            "retry": {
                "max_attempts": 2,
                "retryable_reasons": [
                    "CONCEPT_API_TIMEOUT",
                    "CONCEPT_API_UNAVAILABLE",
                ],
            },
        },
        "verifier": {
            "startup_timeout_seconds": 120,
            "request_timeout_seconds": 120,
            "entailment_margin_threshold": 0.1,
            "multiple_support_risk_threshold": 0.4,
        },
        "novelty": {
            "decision_rule": (
                "publication-independent-mastery-qualification/v1"
            ),
            "novel_requirement": (
                "each-prior-no-entailment-and-directional-contradiction/v1"
            ),
            "qualification_outcomes": {
                "no_prior": "distinct-mastery-evidence:no-prior/v1",
                "verified_distinct": (
                    "distinct-mastery-evidence:verified-distinct/v1"
                ),
                "neutral": "distinct-mastery-evidence:neutral/v1",
                "timeout": "distinct-mastery-evidence:timeout/v1",
                "invalid_response": (
                    "distinct-mastery-evidence:invalid-response/v1"
                ),
                "unavailable": "distinct-mastery-evidence:unavailable/v1",
                "unsupported": "distinct-mastery-evidence:unsupported/v1",
                "over_limit": "distinct-mastery-evidence:over-limit/v1",
            },
            "maximum_prior_items": 32,
            "request_timeout_seconds": 120,
        },
        "limits": {"maximum_evidence_characters": 32768},
    }
    local_config = {
        "private_runtime_root": str(tmp_path / "runtime"),
        "concept_api_base_url": "http://127.0.0.1:8101",
        "concept_model": "Qwen/Qwen3-14B-AWQ",
        "concept_max_model_len": 8192,
    }

    class Server:
        def close(self):
            pass

    class Verifier:
        def request(self, request, _timeout):
            if request["schema"] == "local-assessment-novelty-request/v1":
                return {
                    "schema": "local-assessment-novelty-response/v1",
                    "request_id": request["request_id"],
                    "status": "scored",
                    "comparisons": [
                        {
                            "candidate_to_prior": 0.1,
                            "prior_to_candidate": 0.1,
                            "candidate_entails_prior": False,
                            "prior_entails_candidate": False,
                            "candidate_contradicts_prior": True,
                            "prior_contradicts_candidate": False,
                        }
                        for _ in request["prior_focuses"]
                    ],
                }
            scores = {
                "Canonical Evidence 1": [0.9, 0.1, 0.1, 0.1],
                "Canonical Evidence 2": [0.6, 0.2, 0.2, 0.2],
                "Canonical Evidence 3": [0.55, 0.2, 0.2, 0.2],
            }
            return {
                "schema": "local-assessment-verifier-response/v2",
                "request_id": request["request_id"],
                "status": "scored",
                "entailment_probabilities": scores[request["options"][0]],
            }

        def close(self):
            pass

        def abort(self):
            pass

    starts = []
    monkeypatch.setattr(
        generation_module,
        "assessment_runtime_preflight",
        lambda *_: {"runtime_binding_sha256": "6" * 64},
    )
    monkeypatch.setattr(
        generation_module,
        "load_assessment_runtime_lock",
        lambda: policy,
    )
    monkeypatch.setattr(
        generation_module,
        "start_concept_server",
        lambda _: starts.append("qwen") or Server(),
    )
    monkeypatch.setattr(
        generation_module,
        "start_assessment_process",
        lambda *_: starts.append("verifier") or Verifier(),
    )
    stage_calls = []

    def request_stage(*args, **kwargs):
        stage_calls.append(args[2]["prompt"])
        return json.dumps(proposal)

    monkeypatch.setattr(generation_module, "_request_stage", request_stage)

    stored = generate_and_store_assessment(
        learner,
        study_session.study_session_id,
        claim["claim_id"],
        local_config,
        dsn=assessment_database_dsn,
    )

    assert starts == ["qwen", "verifier"]
    assert stored.public_document.source_evidence_ids == claim["evidence_ids"]
    assert stored.private_answer_document.rationale == (
        "教材依據明確記載：Canonical Evidence 1"
    )
    assert stored.generation_provenance is not None
    assert stored.generation_provenance.selected_stage == "proposal"
    assert stored.generation_provenance.runtime_binding_sha256 == "6" * 64
    assert "correct_option_id" not in json.dumps(
        project_public_assessment(stored), sort_keys=True
    )

    second = generate_and_store_assessment(
        learner,
        study_session.study_session_id,
        claim["claim_id"],
        local_config,
        dsn=assessment_database_dsn,
    )
    third = generate_and_store_assessment(
        learner,
        study_session.study_session_id,
        claim["claim_id"],
        local_config,
        dsn=assessment_database_dsn,
    )
    assert len(
        {
            stored.question_id,
            second.question_id,
            third.question_id,
        }
    ) == 3
    assert [
        item.generation_provenance.selected_candidate_index
        for item in (stored, second, third)
    ] == [0, 1, 2]
    with pytest.raises(
        AssessmentGenerationError, match="^ASSESSMENT_NO_NEW_SAFE_ITEM$"
    ):
        generate_and_store_assessment(
            learner,
            study_session.study_session_id,
            claim["claim_id"],
            local_config,
            dsn=assessment_database_dsn,
        )
    assert _assessment_count(assessment_database_dsn) == 3
    assert starts == ["qwen", "verifier"] * 4
    assert stage_calls == ["proposal"] * 4 + ["repair"]

    other = TrustedLearner(uuid4())
    with pytest.raises(
        AssessmentGenerationError, match="^ASSESSMENT_GROUNDING_UNAVAILABLE$"
    ):
        generate_and_store_assessment(
            other,
            study_session.study_session_id,
            claim["claim_id"],
            local_config,
            dsn=assessment_database_dsn,
        )
    assert starts == ["qwen", "verifier"] * 4


def test_assessment_grounding_allows_unrelated_formula_tokens_in_evidence():
    evidence = EvidenceLocator(
        evidence_id="evidence:sha256:" + "1" * 64,
        page_ref="page:sha256:" + "2" * 64,
        page_number=1,
        coordinate_space="unrotated_pdf_points",
        bbox=(0, 0, 10, 10),
        text=r"教材說明 \\alpha，並在下一段介紹 \\beta。",
    )
    claim = ClaimContext(
        claim_id="claim:sha256:" + "3" * 64,
        text=r"教材以 \\alpha 表示目前概念。",
        evidence=(evidence,),
    )
    concept = FormalConceptContext(
        formal_concept_id="formal-concept:sha256:" + "4" * 64,
        label="目前概念",
        source_page_numbers=(1,),
        claims=(claim,),
        supplementary_resources=(),
    )

    grounding = generation_module._grounding(
        concept, claim.claim_id, {"limits": {"maximum_evidence_characters": 1000}}
    )

    assert grounding.claim == claim
    invented = ClaimContext(
        claim_id=claim.claim_id,
        text=r"教材以 \\gamma 表示目前概念。",
        evidence=(evidence,),
    )
    with pytest.raises(AssessmentGenerationError, match="^ASSESSMENT_INPUT_UNSAFE$"):
        generation_module._grounding(
            FormalConceptContext(
                formal_concept_id=concept.formal_concept_id,
                label=concept.label,
                source_page_numbers=concept.source_page_numbers,
                claims=(invented,),
                supplementary_resources=(),
            ),
            invented.claim_id,
            {"limits": {"maximum_evidence_characters": 1000}},
        )


def test_owner_lifecycle_and_map_bindings_fail_closed_without_row(
    assessment_database_dsn: str,
):
    learner, knowledge_map, _, active_session = _active_study_session(
        assessment_database_dsn
    )
    other = TrustedLearner(uuid4())
    with psycopg.connect(assessment_database_dsn) as connection:
        connection.execute(
            "INSERT INTO learners VALUES (%s, clock_timestamp())",
            (other.learner_id,),
        )
    valid = _documents(active_session, knowledge_map)

    null_target = create_study_session(
        learner,
        active_session.material_id,
        knowledge_map["revision"],
        str(uuid4()),
        dsn=assessment_database_dsn,
    )
    completed = create_study_session(
        learner,
        active_session.material_id,
        knowledge_map["revision"],
        str(uuid4()),
        current_formal_concept_id=active_session.current_formal_concept_id,
        dsn=assessment_database_dsn,
    )
    complete_study_session(
        learner, completed.study_session_id, dsn=assessment_database_dsn
    )
    different_map = deepcopy(knowledge_map)
    different_map["source_binding"]["material_runtime_binding_sha256"] = (
        "3" * 64
    )
    different_map.pop("revision")
    different_map["revision"] = "knowledge-map:sha256:" + canonical_sha256(
        different_map
    )
    different_material = _insert_material_map(
        assessment_database_dsn, learner.learner_id, different_map
    )
    different_session = create_study_session(
        learner,
        different_material,
        different_map["revision"],
        str(uuid4()),
        current_formal_concept_id=different_map["formal_concepts"][0][
            "formal_concept_id"
        ],
        dsn=assessment_database_dsn,
    )

    target = knowledge_map["formal_concepts"][0]
    wrong_claim = knowledge_map["formal_concepts"][1]["claims"][0]["claim_id"]
    attempts = [
        (other, valid),
        (learner, _documents(null_target, knowledge_map)),
        (learner, _documents(completed, knowledge_map)),
        (
            learner,
            _documents(
                different_session,
                knowledge_map,
            ),
        ),
        (
            learner,
            _documents(
                active_session,
                knowledge_map,
                target_formal_concept_id=knowledge_map["formal_concepts"][1][
                    "formal_concept_id"
                ],
                target_claim_id=wrong_claim,
            ),
        ),
        (
            learner,
            _documents(
                active_session,
                knowledge_map,
                target_claim_id=wrong_claim,
            ),
        ),
        (
            learner,
            _documents(
                active_session,
                knowledge_map,
                source_evidence_ids=["evidence:sha256:" + "f" * 64],
            ),
        ),
        (
            learner,
            _documents(
                active_session,
                knowledge_map,
                study_session_id=uuid4(),
            ),
        ),
        (
            learner,
            _documents(
                active_session,
                knowledge_map,
                knowledge_map_revision="knowledge-map:sha256:" + "f" * 64,
            ),
        ),
    ]
    assert target["formal_concept_id"] == active_session.current_formal_concept_id
    for requesting_learner, documents in attempts:
        public, private = _documents_as_dicts(documents)
        with pytest.raises(AssessmentError, match="^ASSESSMENT_BINDING_INVALID$"):
            store_assessment(
                requesting_learner,
                public,
                private,
                dsn=assessment_database_dsn,
            )
        assert _assessment_count(assessment_database_dsn) == 0

    tampered_map = deepcopy(knowledge_map)
    tampered_map["formal_concepts"][0]["label"] = "Tampered without revision"
    with psycopg.connect(assessment_database_dsn) as connection:
        connection.execute(
            """
            UPDATE knowledge_maps SET document = %s
            WHERE learner_id = %s AND material_id = %s AND map_revision = %s
            """,
            (
                Jsonb(tampered_map),
                learner.learner_id,
                active_session.material_id,
                knowledge_map["revision"],
            ),
        )
    public, private = _documents_as_dicts(valid)
    with pytest.raises(AssessmentError, match="^ASSESSMENT_BINDING_INVALID$"):
        store_assessment(
            learner, public, private, dsn=assessment_database_dsn
        )
    assert _assessment_count(assessment_database_dsn) == 0


def test_read_is_owner_isolated_and_detects_private_correct_or_rationale_tamper(
    assessment_database_dsn: str,
):
    learner, knowledge_map, _, study_session = _active_study_session(
        assessment_database_dsn
    )
    documents = _documents(study_session, knowledge_map)
    public, private = _documents_as_dicts(documents)
    stored = store_assessment(
        learner, public, private, dsn=assessment_database_dsn
    )
    other = TrustedLearner(uuid4())
    with psycopg.connect(assessment_database_dsn) as connection:
        connection.execute(
            "INSERT INTO learners VALUES (%s, clock_timestamp())",
            (other.learner_id,),
        )
    with pytest.raises(AssessmentError, match="^ASSESSMENT_UNAVAILABLE$"):
        read_assessment(
            other, stored.assessment_revision, dsn=assessment_database_dsn
        )

    tampered_documents = []
    changed_correct = deepcopy(private)
    changed_correct["correct_option_id"] = public["options"][1]["option_id"]
    tampered_documents.append(changed_correct)
    changed_rationale = deepcopy(private)
    changed_rationale["rationale"] = "Changed without recomputing the digest."
    tampered_documents.append(changed_rationale)

    for tampered_private in tampered_documents:
        with psycopg.connect(assessment_database_dsn) as connection:
            connection.execute(
                """
                UPDATE assessments SET private_answer_document = %s
                WHERE assessment_revision = %s
                """,
                (Jsonb(tampered_private), stored.assessment_revision),
            )
        with pytest.raises(
            AssessmentError, match="^ASSESSMENT_DOCUMENT_INVALID$"
        ):
            read_assessment(
                learner, stored.assessment_revision, dsn=assessment_database_dsn
            )
        with psycopg.connect(assessment_database_dsn) as connection:
            connection.execute(
                """
                UPDATE assessments SET private_answer_document = %s
                WHERE assessment_revision = %s
                """,
                (Jsonb(private), stored.assessment_revision),
            )
        assert read_assessment(
            learner, stored.assessment_revision, dsn=assessment_database_dsn
        ) == stored
