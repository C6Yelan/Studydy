from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import shutil
from uuid import uuid4

import psycopg
from psycopg.types.json import Jsonb
import pytest

from learning_adaptation.assessment_items import (
    ASSESSMENT_POLICY_REVISION,
    AssessmentError,
    build_single_choice_assessment,
    project_public_assessment,
    read_assessment,
    store_assessment,
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
    learner, knowledge_map, _, study_session = _active_study_session(
        clean_database_dsn
    )
    with psycopg.connect(clean_database_dsn) as connection:
        before = connection.execute(
            "SELECT * FROM study_sessions WHERE study_session_id = %s",
            (study_session.study_session_id,),
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
            (study_session.study_session_id,),
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
    assert learner.learner_id == study_session.learner_id
    assert knowledge_map["revision"] == study_session.knowledge_map_revision
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
    ):
        assert forbidden not in serialized


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
    different_map["material_ref"] = "material:sha256:" + "2" * 64
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
