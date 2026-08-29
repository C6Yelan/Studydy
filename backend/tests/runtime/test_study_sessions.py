from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
from psycopg.types.json import Jsonb
import pytest

from learning_adaptation.map_context import MapContextError, read_map_context
from learning_adaptation.study_sessions import (
    StudySessionError,
    complete_study_session,
    create_study_session,
    read_study_session,
)
from learning_resources.map_resources import MATCHING_POLICY, PROMOTION_POLICY
from pdf_evidence.document_context import build_document_contexts
from pdf_evidence.ocr_page_evidence import canonical_sha256
from runtime.learner_session import (
    TrustedLearner,
    create_session,
    refresh_session,
    revoke_session,
)
from runtime.storage.migrations import run_migrations


@pytest.fixture
def study_database_dsn(clean_database_dsn: str, migrations_dir: Path) -> str:
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
    )
    return clean_database_dsn


def _formal_id(concept: dict) -> str:
    return "formal-concept:sha256:" + canonical_sha256(
        {
            "group_id": concept["group_id"],
            "operation": concept["operation"],
            "source_concept_ids": concept["source_concept_ids"],
            "label": concept["label"],
            "claims": concept["claims"],
        }
    )


def _relation(relation_type: str, source: dict, target: dict) -> dict:
    relation_evidence = [
        {
            "owner_formal_concept_id": source["formal_concept_id"],
            "claim_id": source["claims"][0]["claim_id"],
            "evidence_ids": source["claims"][0]["evidence_ids"],
        }
    ]
    identity = {
        "type": relation_type,
        "source_formal_concept_id": source["formal_concept_id"],
        "target_formal_concept_id": target["formal_concept_id"],
        "relation_evidence": relation_evidence,
    }
    return {
        "relation_id": "formal-relation:sha256:" + canonical_sha256(identity),
        **identity,
        "quality": "needs_review",
        "decision": "review",
        "reason_codes": ["RELATION_REVIEW_REQUIRED"],
        "is_in_prerequisite_cycle": False,
    }


def _knowledge_map() -> dict:
    evidence_id = "evidence:sha256:" + "1" * 64
    concepts = []
    for index, label in enumerate(
        ("Core concept", "Applied concept", "Related concept")
    ):
        claim = {
            "claim_id": "claim:sha256:" + str(index + 1) * 64,
            "text": f"Grounded claim {index + 1}",
            "evidence_ids": [evidence_id],
        }
        concept = {
            "formal_concept_id": "",
            "group_id": f"group-{index}",
            "operation": "KEEP",
            "source_concept_ids": [
                "concept:sha256:" + str(index + 1) * 64
            ],
            "label": label,
            "claims": [claim],
            "source_page_refs": ["page:sha256:" + "1" * 64],
            "source_page_numbers": [1],
            "quality": "needs_review",
            "decision": "review",
            "reason_codes": ["FORMAL_CONCEPT_REVIEW_REQUIRED"],
            "resolution_order": [index, 0],
            "supplementary_resources": [],
        }
        concept["formal_concept_id"] = _formal_id(concept)
        concepts.append(concept)

    promoted_resource = {
        "resource_concept_id": "resource-concept:sha256:" + "1" * 64,
        "resource_id": "resource:sha256:" + "1" * 64,
        "label": "Core concept",
        "title": "Reviewed supplementary notes",
        "authors": ["Ada Student"],
        "source_url": "https://example.edu/notes.pdf",
        "citation": "Ada Student. Reviewed supplementary notes.",
        "license": "CC BY 4.0 International",
        "license_url": "https://creativecommons.org/licenses/by/4.0/",
        "use_boundary": "Attribution required.",
        "page_numbers": [2],
        "resource_evidence_ids": ["resource-evidence:sha256:" + "1" * 64],
        "match_ids": ["resource-match:sha256:" + "1" * 64],
        "study_concept_ids": concepts[0]["source_concept_ids"],
        "match_reason": "EXACT_NORMALIZED_LABEL",
    }
    promoted_resource["promotion_id"] = (
        "resource-promotion:sha256:" + canonical_sha256(promoted_resource)
    )
    concepts[0]["supplementary_resources"] = [promoted_resource]

    related_source, related_target = sorted(
        concepts[1:], key=lambda concept: concept["formal_concept_id"]
    )
    relations = [
        _relation("prerequisite", concepts[0], concepts[1]),
        _relation("contains", concepts[0], concepts[2]),
        _relation("related", related_source, related_target),
    ]
    knowledge_map = {
        "schema": "knowledge-map/v6",
        "source_output_id": "study-material-output:sha256:" + "1" * 64,
        "source_binding": {
            "study_material_output_id": "study-material-output:sha256:" + "1" * 64,
            "producer_output_id": "concept-evidence-output:sha256:" + "1" * 64,
            "producer_runtime_lock_sha256": "1" * 64,
            "material_runtime_binding_sha256": "2" * 64,
        },
        "material_ref": "material:sha256:" + "1" * 64,
        "formal_concepts": concepts,
        "relations": relations,
        "relation_diagnostics": {
            "possible_pairs": 3,
            "candidate_pairs": 3,
            "selected_pairs": 3,
            "selected_signal_counts": {},
            "evidence_gated_pairs": 3,
            "rejected_no_evidence": 0,
            "direction_conflicts": 0,
            "verifier_calls": 1,
            "verifier_accepted": 1,
            "verifier_rejected": 0,
            "verifier_unsupported": 0,
            "structural_proposals": 2,
            "contains_proposals": 1,
            "prerequisite_proposals": 1,
            "related_proposals": 1,
            "accepted_relations": 3,
        },
        "resource_binding": {
            "context_revision": "map-resource-context:sha256:" + "1" * 64,
            "library_revision": "resource-library:sha256:" + "1" * 64,
            "matching_policy": MATCHING_POLICY,
            "promotion_policy": PROMOTION_POLICY,
        },
        "resource_diagnostics": {
            "matches": 1,
            "promoted_matches": 1,
            "promoted_resources": 1,
            "dropped_matches": 0,
            "split_review_matches": 0,
        },
        "resource_decisions": [],
        "initial_learning_path": [
            concept["formal_concept_id"] for concept in concepts
        ],
        "evidence_index": [
            {
                "evidence_id": evidence_id,
                "page_ref": "page:sha256:" + "1" * 64,
                "page_number": 1,
                "kind": "paragraph",
                "region": {
                    "coordinate_space": "unrotated_pdf_points",
                    "bbox": [10.0, 20.0, 300.0, 60.0],
                },
            }
        ],
        "excluded_pages": [],
        "processing": "succeeded",
        "quality": "needs_review",
        "decision": "review",
        "reason_codes": [
            "KNOWLEDGE_MAP_REVIEW_REQUIRED",
            "RELATION_REVIEW_REQUIRED",
        ],
    }
    knowledge_map["revision"] = "knowledge-map:sha256:" + canonical_sha256(
        knowledge_map
    )
    return knowledge_map


def _rejected_knowledge_map() -> dict:
    knowledge_map = _knowledge_map()
    knowledge_map["formal_concepts"] = []
    knowledge_map["relations"] = []
    knowledge_map["relation_diagnostics"] = {
        key: {} if key == "selected_signal_counts" else 0
        for key in knowledge_map["relation_diagnostics"]
    }
    knowledge_map["resource_diagnostics"] = {
        key: 0 for key in knowledge_map["resource_diagnostics"]
    }
    knowledge_map["initial_learning_path"] = []
    knowledge_map["processing"] = "partial"
    knowledge_map["decision"] = "reject"
    knowledge_map["reason_codes"] = [
        "KNOWLEDGE_MAP_REVIEW_REQUIRED",
        "NO_FORMAL_CONCEPT",
    ]
    knowledge_map.pop("revision")
    knowledge_map["revision"] = "knowledge-map:sha256:" + canonical_sha256(
        knowledge_map
    )
    return knowledge_map


def _insert_material_map(
    dsn: str,
    learner_id: UUID,
    knowledge_map: dict,
    *,
    material_id: UUID | None = None,
) -> UUID:
    stored_material_id = material_id or uuid4()
    source_artifact_id = uuid4()
    upload_key = sha256(uuid4().bytes).digest()
    evidence_index = deepcopy(knowledge_map["evidence_index"])
    material_revision = "material-revision:sha256:" + "9" * 64
    pages = [
        {
            "page_ref": evidence["page_ref"],
            "page_number": evidence["page_number"],
            "page_evidence_id": "page-evidence:sha256:"
            + canonical_sha256(
                {
                    "page_ref": evidence["page_ref"],
                    "page_number": evidence["page_number"],
                }
            ),
            "native_evidence_ref": "native-evidence:sha256:"
            + canonical_sha256({"page_ref": evidence["page_ref"]}),
            "processing": "succeeded",
            "quality": "needs_review",
            "decision": "review",
            "reason_codes": ["CONTENT_REVIEW_REQUIRED"],
        }
        for evidence in evidence_index
    ]
    context_source_pages = [
        {
            "schema": "page-evidence/v3",
            "material_id": knowledge_map["material_ref"],
            "material_revision": material_revision,
            "section_id": "page-section-not-used-by-document-context",
            "page_ref": page["page_ref"],
            "page_number": page["page_number"],
            "page_evidence_id": page["page_evidence_id"],
            "evidence_blocks": [{
                "evidence_id": evidence["evidence_id"],
                "block_id": f"block:sha256:{index:064x}",
                "kind": evidence["kind"],
                "text": f"Canonical Evidence {index}",
                "reading_order": 0,
            }],
        }
        for index, (page, evidence) in enumerate(
            zip(pages, evidence_index, strict=True), start=1
        )
    ]
    document_contexts = build_document_contexts(context_source_pages)
    study_material_output = {
        "schema": "study-material-output/v6",
        "run_id": "text-first-run:00000000-0000-4000-8000-000000000001",
        "produced_at": "2026-08-26T00:00:00+00:00",
        "material_ref": knowledge_map["material_ref"],
        "source_binding": {
            "source_sha256": knowledge_map["material_ref"].removeprefix(
                "material:sha256:"
            ),
            "page_count": len(pages),
            "producer_output_id": knowledge_map["source_binding"][
                "producer_output_id"
            ],
            "runtime_binding_sha256": knowledge_map["source_binding"][
                "producer_runtime_lock_sha256"
            ],
        },
        "pages": pages,
        "excluded_pages": [],
        "concepts": [],
        "evidence_index": evidence_index,
        "evidence_text_index": [
            {
                "evidence_id": evidence["evidence_id"],
                "text": f"Canonical Evidence {index}",
            }
            for index, evidence in enumerate(evidence_index, start=1)
        ],
        "document_contexts": document_contexts,
        "images": [],
        "processing": "succeeded",
        "quality": "needs_review",
        "decision": "review",
        "reason_codes": ["CONTENT_REVIEW_REQUIRED"],
    }
    study_material_output["output_id"] = (
        "study-material-output:sha256:" + canonical_sha256(study_material_output)
    )
    knowledge_map["source_output_id"] = study_material_output["output_id"]
    knowledge_map["source_binding"]["study_material_output_id"] = (
        study_material_output["output_id"]
    )
    knowledge_map.pop("revision")
    knowledge_map["revision"] = "knowledge-map:sha256:" + canonical_sha256(
        knowledge_map
    )
    with psycopg.connect(dsn) as connection:
        connection.execute("SET CONSTRAINTS materials_source_artifact_fk DEFERRED")
        connection.execute(
            "INSERT INTO learners VALUES (%s, clock_timestamp()) ON CONFLICT DO NOTHING",
            (learner_id,),
        )
        connection.execute(
            """
            INSERT INTO materials (
                material_id, learner_id, source_artifact_id,
                upload_idempotency_key_sha256, upload_request_fingerprint,
                created_at
            ) VALUES (%s, %s, %s, %s, %s, clock_timestamp())
            """,
            (
                stored_material_id,
                learner_id,
                source_artifact_id,
                upload_key,
                sha256(upload_key).digest(),
            ),
        )
        connection.execute(
            """
            INSERT INTO artifacts (
                artifact_id, learner_id, material_id, kind, media_type,
                sha256, size_bytes, created_at
            ) VALUES (%s, %s, %s, 'source_pdf', 'application/pdf', %s, 1,
                clock_timestamp())
            """,
            (source_artifact_id, learner_id, stored_material_id, b"s" * 32),
        )
        connection.execute(
            """
            INSERT INTO study_material_outputs (
                learner_id, material_id, output_revision, document, created_at
            ) VALUES (%s, %s, %s, %s, clock_timestamp())
            """,
            (
                learner_id,
                stored_material_id,
                study_material_output["output_id"],
                Jsonb(study_material_output),
            ),
        )
        connection.execute(
            """
            INSERT INTO knowledge_maps (
                learner_id, material_id, map_revision, source_output_revision,
                document, created_at
            ) VALUES (%s, %s, %s, %s, %s, clock_timestamp())
            """,
            (
                learner_id,
                stored_material_id,
                knowledge_map["revision"],
                knowledge_map["source_output_id"],
                Jsonb(knowledge_map),
            ),
        )
    return stored_material_id


def test_map_context_projects_only_validated_current_fields(
    study_database_dsn: str,
):
    learner_id = uuid4()
    knowledge_map = _knowledge_map()
    material_id = _insert_material_map(
        study_database_dsn, learner_id, knowledge_map
    )

    context = read_map_context(
        learner_id,
        material_id,
        knowledge_map["revision"],
        dsn=study_database_dsn,
    )

    assert context.initial_learning_path == tuple(
        knowledge_map["initial_learning_path"]
    )
    assert {relation.relation_type for relation in context.relations} == {
        "prerequisite",
        "contains",
        "related",
    }
    assert [relation.is_in_prerequisite_cycle for relation in context.relations] == [
        relation["is_in_prerequisite_cycle"] for relation in knowledge_map["relations"]
    ]
    evidence = context.formal_concepts[0].claims[0].evidence[0]
    assert (evidence.page_ref, evidence.page_number, evidence.bbox) == (
        knowledge_map["evidence_index"][0]["page_ref"],
        knowledge_map["evidence_index"][0]["page_number"],
        tuple(knowledge_map["evidence_index"][0]["region"]["bbox"]),
    )
    assert evidence.text == "Canonical Evidence 1"
    promoted = context.formal_concepts[0].supplementary_resources[0]
    assert promoted.promotion_id.startswith("resource-promotion:sha256:")
    assert promoted.resource_id.startswith("resource:sha256:")
    assert not hasattr(promoted, "match_ids")
    assert set(vars(context)) == {
        "learner_id",
        "material_id",
        "knowledge_map_revision",
        "formal_concepts",
        "relations",
        "initial_learning_path",
    }


def test_map_context_rejects_canonical_evidence_text_tamper(
    study_database_dsn: str,
):
    owner = uuid4()
    knowledge_map = _knowledge_map()
    material_id = _insert_material_map(
        study_database_dsn, owner, knowledge_map
    )
    with psycopg.connect(study_database_dsn) as connection:
        document = connection.execute(
            """
            SELECT document FROM study_material_outputs
            WHERE learner_id = %s AND material_id = %s AND output_revision = %s
            """,
            (owner, material_id, knowledge_map["source_output_id"]),
        ).fetchone()[0]
        document["evidence_text_index"][0]["text"] = "Tampered Evidence"
        connection.execute(
            """
            UPDATE study_material_outputs SET document = %s
            WHERE learner_id = %s AND material_id = %s AND output_revision = %s
            """,
            (
                Jsonb(document),
                owner,
                material_id,
                knowledge_map["source_output_id"],
            ),
        )
    with pytest.raises(MapContextError, match="^KNOWLEDGE_MAP_UNAVAILABLE$"):
        read_map_context(
            owner,
            material_id,
            knowledge_map["revision"],
            dsn=study_database_dsn,
        )


def test_map_context_rejects_wrong_binding_tampering_and_rejected_map(
    study_database_dsn: str,
):
    owner = uuid4()
    other = uuid4()
    knowledge_map = _knowledge_map()
    material_id = _insert_material_map(study_database_dsn, owner, knowledge_map)
    missing_revision = "knowledge-map:sha256:" + "0" * 64

    for learner_id, requested_material, revision in (
        (other, material_id, knowledge_map["revision"]),
        (owner, uuid4(), knowledge_map["revision"]),
        (owner, material_id, missing_revision),
    ):
        with pytest.raises(MapContextError, match="^KNOWLEDGE_MAP_UNAVAILABLE$"):
            read_map_context(
                learner_id,
                requested_material,
                revision,
                dsn=study_database_dsn,
            )

    tampered = deepcopy(knowledge_map)
    tampered["formal_concepts"][0]["label"] = "未重新綁定 revision"
    with psycopg.connect(study_database_dsn) as connection:
        connection.execute(
            """
            UPDATE knowledge_maps SET document = %s
            WHERE learner_id = %s AND material_id = %s AND map_revision = %s
            """,
            (Jsonb(tampered), owner, material_id, knowledge_map["revision"]),
        )
    with pytest.raises(MapContextError, match="^KNOWLEDGE_MAP_UNAVAILABLE$"):
        read_map_context(
            owner, material_id, knowledge_map["revision"], dsn=study_database_dsn
        )

    rejected_map = _rejected_knowledge_map()
    rejected_material = _insert_material_map(
        study_database_dsn, owner, rejected_map
    )
    with pytest.raises(MapContextError, match="^KNOWLEDGE_MAP_UNAVAILABLE$"):
        read_map_context(
            owner,
            rejected_material,
            rejected_map["revision"],
            dsn=study_database_dsn,
        )


def test_create_read_complete_replay_conflict_and_session_isolation(
    study_database_dsn: str,
):
    owner = TrustedLearner(uuid4())
    other = TrustedLearner(uuid4())
    knowledge_map = _knowledge_map()
    material_id = _insert_material_map(
        study_database_dsn, owner.learner_id, knowledge_map
    )
    with psycopg.connect(study_database_dsn) as connection:
        connection.execute(
            "INSERT INTO learners VALUES (%s, clock_timestamp())", (other.learner_id,)
        )

    created = create_study_session(
        owner,
        material_id,
        knowledge_map["revision"],
        "create-session",
        dsn=study_database_dsn,
    )
    replay = create_study_session(
        owner,
        material_id,
        knowledge_map["revision"],
        "create-session",
        dsn=study_database_dsn,
    )
    second = create_study_session(
        owner,
        material_id,
        knowledge_map["revision"],
        "create-session-b",
        dsn=study_database_dsn,
    )
    assert replay == created
    assert second.study_session_id != created.study_session_id
    assert created.current_formal_concept_id is None
    assert created.status == "active"
    assert created.last_event_number == second.last_event_number == 0

    target = knowledge_map["initial_learning_path"][0]
    with pytest.raises(
        StudySessionError, match="^STUDY_SESSION_IDEMPOTENCY_CONFLICT$"
    ):
        create_study_session(
            owner,
            material_id,
            knowledge_map["revision"],
            "create-session",
            current_formal_concept_id=target,
            dsn=study_database_dsn,
        )
    with pytest.raises(StudySessionError, match="^STUDY_SESSION_UNAVAILABLE$"):
        read_study_session(
            other, created.study_session_id, dsn=study_database_dsn
        )

    completed = complete_study_session(
        owner, created.study_session_id, dsn=study_database_dsn
    )
    completed_replay = complete_study_session(
        owner, created.study_session_id, dsn=study_database_dsn
    )
    assert completed.status == "completed"
    assert completed.completed_at is not None
    assert completed_replay == completed
    assert read_study_session(
        owner, created.study_session_id, dsn=study_database_dsn
    ) == completed
    assert create_study_session(
        owner,
        material_id,
        knowledge_map["revision"],
        "create-session",
        dsn=study_database_dsn,
    ) == completed


def test_create_fails_closed_for_missing_map_material_and_concept(
    study_database_dsn: str,
):
    owner = TrustedLearner(uuid4())
    knowledge_map = _knowledge_map()
    material_id = _insert_material_map(
        study_database_dsn, owner.learner_id, knowledge_map
    )
    missing_revision = "knowledge-map:sha256:" + "0" * 64

    for requested_material, revision in (
        (material_id, missing_revision),
        (uuid4(), knowledge_map["revision"]),
    ):
        with pytest.raises(
            StudySessionError, match="^STUDY_SESSION_MAP_UNAVAILABLE$"
        ):
            create_study_session(
                owner,
                requested_material,
                revision,
                str(uuid4()),
                dsn=study_database_dsn,
            )
    with pytest.raises(StudySessionError, match="^STUDY_SESSION_TARGET_INVALID$"):
        create_study_session(
            owner,
            material_id,
            knowledge_map["revision"],
            "invalid-target",
            current_formal_concept_id="formal-concept:sha256:" + "0" * 64,
            dsn=study_database_dsn,
        )


def test_read_revalidates_stored_current_concept_binding(
    study_database_dsn: str,
):
    owner = TrustedLearner(uuid4())
    knowledge_map = _knowledge_map()
    material_id = _insert_material_map(
        study_database_dsn, owner.learner_id, knowledge_map
    )
    created = create_study_session(
        owner,
        material_id,
        knowledge_map["revision"],
        "tamper-target",
        current_formal_concept_id=knowledge_map["initial_learning_path"][0],
        dsn=study_database_dsn,
    )
    with psycopg.connect(study_database_dsn) as connection:
        connection.execute(
            """
            UPDATE study_sessions SET current_formal_concept_id = %s
            WHERE study_session_id = %s
            """,
            ("formal-concept:sha256:" + "0" * 64, created.study_session_id),
        )
    with pytest.raises(StudySessionError, match="^STUDY_SESSION_UNAVAILABLE$"):
        read_study_session(owner, created.study_session_id, dsn=study_database_dsn)


def test_auth_refresh_revoke_and_cookie_row_cleanup_do_not_delete_study_session(
    study_database_dsn: str,
):
    auth_session = create_session(dsn=study_database_dsn)
    learner = TrustedLearner(auth_session.learner_id)
    knowledge_map = _knowledge_map()
    material_id = _insert_material_map(
        study_database_dsn, learner.learner_id, knowledge_map
    )
    study_session = create_study_session(
        learner,
        material_id,
        knowledge_map["revision"],
        "auth-independent",
        dsn=study_database_dsn,
    )

    assert refresh_session(auth_session.raw_token, dsn=study_database_dsn) == learner
    assert revoke_session(auth_session.raw_token, dsn=study_database_dsn)
    with psycopg.connect(study_database_dsn) as connection:
        connection.execute(
            "DELETE FROM learner_sessions WHERE learner_id = %s",
            (learner.learner_id,),
        )
        assert connection.execute(
            "SELECT count(*) FROM study_sessions WHERE study_session_id = %s",
            (study_session.study_session_id,),
        ).fetchone() == (1,)
    assert read_study_session(
        learner, study_session.study_session_id, dsn=study_database_dsn
    ) == study_session
