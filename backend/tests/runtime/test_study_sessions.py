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
from knowledge_map.artifacts import (
    _document_tree_and_learning_path,
    _formal_concepts_are_valid,
    validate_knowledge_map,
)
from pdf_evidence.concept_generation import (
    build_semantic_request,
    claim_id,
    concept_id,
)
from pdf_evidence.document_context import build_document_contexts
from pdf_evidence.ocr_page_evidence import canonical_sha256
from pdf_evidence.study_material_output import validate_study_material_output
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
        15,
        16,
    )
    return clean_database_dsn


def _formal_id(concept: dict) -> str:
    return "formal-concept:sha256:" + canonical_sha256(
        {
            "operation": concept["operation"],
            "source_concept_ids": concept["source_concept_ids"],
            "label": concept["label"],
            "aliases": concept["aliases"],
            "claims": concept["claims"],
            "source_members": concept["source_members"],
        }
    )


def _tree_and_path(concepts: list[dict], material_ref: str) -> tuple[dict, list]:
    ordered = sorted(concepts, key=lambda concept: concept["formal_concept_id"])
    section_id = ordered[0]["source_members"][0]["section_ids"][0]
    evidence_id = ordered[0]["claims"][0]["evidence_ids"][0]
    page_ref = ordered[0]["source_members"][0]["page_ref"]
    section = {
        "section_id": section_id,
        "label": "第 1 頁未命名段落",
        "label_source": "unheaded_fallback",
        "heading_evidence_id": None,
        "source_order": {
            "page_ref": page_ref,
            "page_number": 1,
            "reading_order": 0,
            "evidence_id": evidence_id,
        },
        "concept_ids": [concept["formal_concept_id"] for concept in ordered],
    }
    tree = {
        "root": {"material_ref": material_ref, "section_ids": [section_id]},
        "sections": [section],
    }
    path = [
        {
            "step_number": index,
            "formal_concept_id": concept["formal_concept_id"],
            "placement_reason": "依教材第 1 頁的首次 Claim Evidence 安排。",
            "order_basis": {
                "prerequisite_constraint_ids": [],
                "section_id": section_id,
                "page_ref": page_ref,
                "page_number": 1,
                "reading_order": 0,
                "evidence_id": evidence_id,
            },
        }
        for index, concept in enumerate(ordered, start=1)
    ]
    return tree, path


def _knowledge_map() -> dict:
    evidence_id = "evidence:sha256:" + "1" * 64
    page_ref = "page:sha256:" + "1" * 64
    page_evidence_id = "page-evidence:sha256:" + canonical_sha256(
        {"page_ref": page_ref, "page_number": 1}
    )
    context = build_document_contexts([{
        "schema": "page-evidence/v3",
        "material_id": "material:sha256:" + "1" * 64,
        "material_revision": "material-revision:sha256:" + "9" * 64,
        "section_id": "page-section-not-used-by-document-context",
        "page_ref": page_ref,
        "page_number": 1,
        "page_evidence_id": page_evidence_id,
        "evidence_blocks": [{
            "evidence_id": evidence_id,
            "block_id": f"block:sha256:{1:064x}",
            "kind": "paragraph",
            "text": "Canonical Evidence 1",
            "reading_order": 0,
        }],
    }])[0]
    concepts = []
    for index, label in enumerate(
        ("Core concept", "Applied concept", "Related concept")
    ):
        claim = {
            "text": f"Grounded claim {index + 1}",
            "evidence_ids": [evidence_id],
        }
        claim = {
            "claim_id": claim_id(page_ref, claim, index=0),
            **claim,
        }
        source_concept_id = concept_id(page_ref, label, [claim])
        concept = {
            "formal_concept_id": "",
            "operation": "KEEP",
            "source_concept_ids": [source_concept_id],
            "label": label,
            "aliases": [],
            "claims": [claim],
            "source_members": [{
                "source_concept_id": source_concept_id,
                "label": label,
                "claim_ids": [claim["claim_id"]],
                "evidence_ids": [evidence_id],
                "page_ref": page_ref,
                "document_context_id": context["context_id"],
                "section_ids": context["section_ids"],
            }],
            "source_page_refs": [page_ref],
            "source_page_numbers": [1],
            "quality": "needs_review",
            "decision": "review",
            "reason_codes": ["FORMAL_CONCEPT_REVIEW_REQUIRED"],
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
    concepts.sort(key=lambda concept: concept["formal_concept_id"])
    concepts[0]["supplementary_resources"] = [promoted_resource]
    document_tree, initial_learning_path = _tree_and_path(
        concepts, "material:sha256:" + "1" * 64
    )
    knowledge_map = {
        "schema": "knowledge-map/v10",
        "source_output_id": "study-material-output:sha256:" + "1" * 64,
        "source_binding": {
            "study_material_output_id": "study-material-output:sha256:" + "1" * 64,
            "producer_output_id": "concept-evidence-output:sha256:" + "1" * 64,
            "producer_runtime_lock_sha256": "1" * 64,
            "material_runtime_binding_sha256": "2" * 64,
        },
        "material_ref": "material:sha256:" + "1" * 64,
        "formal_concepts": concepts,
        "concept_diagnostics": {
            "possible_pairs": 3,
            "candidate_pairs": 0,
            "selected_pairs": 0,
            "pair_ceiling": 16,
            "qwen_same_pairs": 0,
            "qwen_distinct_pairs": 0,
            "qwen_uncertain_pairs": 0,
            "verifier_requested_pairs": 0,
            "verifier_scored_pairs": 0,
            "verifier_allowed_pairs": 0,
            "verifier_vetoed_pairs": 0,
            "verifier_unsupported_pairs": 0,
            "verifier_failed_pairs": 0,
            "source_concepts_before": 3,
            "canonical_concepts_after": 3,
            "duplicate_delta": 0,
            "coverage_before": 3,
            "coverage_after": 3,
        },
        "document_tree": document_tree,
        "prerequisite_constraints": [],
        "initial_learning_path": initial_learning_path,
        "supplementary_resources": {
            "processing": "succeeded",
            "quality": "needs_review",
            "decision": "review",
            "reason_codes": [],
            "binding": {
                "context_revision": "map-resource-context:sha256:" + "1" * 64,
                "library_revision": "resource-library:sha256:" + "1" * 64,
                "matching_policy": MATCHING_POLICY,
                "promotion_policy": PROMOTION_POLICY,
            },
            "diagnostics": {
                "matches": 1,
                "promoted_matches": 1,
                "promoted_resources": 1,
                "dropped_matches": 0,
                "split_review_matches": 0,
            },
            "decisions": [],
        },
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
        "reason_codes": ["KNOWLEDGE_MAP_REVIEW_REQUIRED"],
    }
    knowledge_map["revision"] = "knowledge-map:sha256:" + canonical_sha256(
        knowledge_map
    )
    return knowledge_map


def _rejected_knowledge_map() -> dict:
    knowledge_map = _knowledge_map()
    knowledge_map["formal_concepts"] = []
    knowledge_map["concept_diagnostics"] = {
        key: 0 for key in knowledge_map["concept_diagnostics"]
    }
    knowledge_map["supplementary_resources"]["diagnostics"] = {
        key: 0
        for key in knowledge_map["supplementary_resources"]["diagnostics"]
    }
    knowledge_map["document_tree"] = {
        "root": {
            "material_ref": knowledge_map["material_ref"],
            "section_ids": [],
        },
        "sections": [],
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
    persist_material_run: bool = True,
) -> UUID:
    stored_material_id = material_id or uuid4()
    source_artifact_id = uuid4()
    run_id = uuid4()
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
        "schema": "study-material-output/v8",
        "run_id": f"text-first-run:{run_id}",
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
        "concepts": [
            {
                "concept_id": member["source_concept_id"],
                "page_ref": member["page_ref"],
                "label": member["label"],
                "claims": [
                    deepcopy(claim)
                    for claim in formal_concept["claims"]
                    if claim["claim_id"] in member["claim_ids"]
                ],
                "processing": "succeeded",
                "quality": "needs_review",
                "decision": "review",
                "reason_codes": ["CONTENT_REVIEW_REQUIRED"],
            }
            for formal_concept in knowledge_map["formal_concepts"]
            for member in formal_concept["source_members"]
        ],
        "evidence_index": evidence_index,
        "evidence_text_index": [
            {
                "evidence_id": evidence["evidence_id"],
                "text": f"Canonical Evidence {index}",
            }
            for index, evidence in enumerate(evidence_index, start=1)
        ],
        "document_contexts": document_contexts,
        "semantic_batches": [
            {
                "page_ref": context["page_ref"],
                "batch_index": 0,
                "semantic_request_sha256": canonical_sha256(semantic_request),
                "semantic_request": semantic_request,
            }
            for page, context in zip(
                context_source_pages, document_contexts, strict=True
            )
            for semantic_request, _ in [build_semantic_request(page, context)]
        ],
        "semantic_page_outcomes": [
            {
                "page_ref": page["page_ref"],
                "processing": "succeeded",
                "quality": "needs_review",
                "decision": "review",
                "reason_codes": ["CONTENT_REVIEW_REQUIRED"],
            }
            for page in pages
        ],
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
    (
        knowledge_map["document_tree"],
        knowledge_map["initial_learning_path"],
    ) = _document_tree_and_learning_path(
        study_material_output,
        knowledge_map["formal_concepts"],
        knowledge_map["prerequisite_constraints"],
    )
    knowledge_map.pop("revision")
    knowledge_map["revision"] = "knowledge-map:sha256:" + canonical_sha256(
        knowledge_map
    )
    assert validate_study_material_output(study_material_output) is None
    assert _formal_concepts_are_valid(
        knowledge_map["formal_concepts"], study_material_output
    )
    assert _document_tree_and_learning_path(
        study_material_output,
        knowledge_map["formal_concepts"],
        knowledge_map["prerequisite_constraints"],
    ) == (
        knowledge_map["document_tree"],
        knowledge_map["initial_learning_path"],
    )
    assert validate_knowledge_map(knowledge_map, study_material_output) is None
    page_count = len(pages)
    material_runtime_binding_sha256 = knowledge_map["source_binding"][
        "material_runtime_binding_sha256"
    ]
    output_binding = {
        "schema": "material-run-output-binding/v3",
        "producer_bundle_id": "text-first-producer-bundle:sha256:" + "1" * 64,
        "producer_run_id": study_material_output["run_id"],
        "concept_evidence_output_id": knowledge_map["source_binding"][
            "producer_output_id"
        ],
        "study_material_output_revision": study_material_output["output_id"],
        "knowledge_map_revision": knowledge_map["revision"],
        "runtime_binding_sha256": material_runtime_binding_sha256,
        "page_count": page_count,
        "processing": "succeeded",
        "quality": "needs_review",
        "decision": "review",
        "reason_codes": ["CONTENT_REVIEW_REQUIRED"],
        "ocr_calls": page_count,
        "concept_calls": 1,
    }
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
        if persist_material_run:
            connection.execute(
                """
                INSERT INTO material_processing_runs (
                    run_id, learner_id, material_id, source_artifact_id,
                    idempotency_key_sha256, request_fingerprint, runtime_binding,
                    status, progress_stage, completed_pages, total_pages,
                    output_binding, created_at, updated_at, completed_at
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, 'succeeded', 'completed',
                    %s, %s, %s, clock_timestamp(), clock_timestamp(),
                    clock_timestamp()
                )
                """,
                (
                    run_id,
                    learner_id,
                    stored_material_id,
                    source_artifact_id,
                    sha256(run_id.bytes).digest(),
                    sha256(stored_material_id.bytes + run_id.bytes).digest(),
                    Jsonb({
                        "runtime_lock_sha256": knowledge_map["source_binding"][
                            "producer_runtime_lock_sha256"
                        ],
                        "runtime_binding_sha256": material_runtime_binding_sha256,
                    }),
                    page_count,
                    page_count,
                    Jsonb(output_binding),
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
        step["formal_concept_id"]
        for step in knowledge_map["initial_learning_path"]
    )
    assert tuple(
        concept.formal_concept_id for concept in context.formal_concepts
    ) == tuple(
        concept["formal_concept_id"] for concept in knowledge_map["formal_concepts"]
    )
    evidence = context.formal_concepts[0].claims[0].evidence[0]
    assert (evidence.page_ref, evidence.page_number, evidence.bbox) == (
        knowledge_map["evidence_index"][0]["page_ref"],
        knowledge_map["evidence_index"][0]["page_number"],
        tuple(knowledge_map["evidence_index"][0]["region"]["bbox"]),
    )
    assert evidence.text == "Canonical Evidence 1"
    promoted = next(
        resource
        for concept in context.formal_concepts
        for resource in concept.supplementary_resources
    )
    assert promoted.promotion_id.startswith("resource-promotion:sha256:")
    assert promoted.resource_id.startswith("resource:sha256:")
    assert not hasattr(promoted, "match_ids")
    assert set(vars(context)) == {
        "learner_id",
        "material_id",
        "knowledge_map_revision",
        "formal_concepts",
        "prerequisite_constraints",
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


def test_map_context_rejects_rehashed_section_source_order_tamper(
    study_database_dsn: str,
):
    owner = uuid4()
    knowledge_map = _knowledge_map()
    material_id = _insert_material_map(
        study_database_dsn, owner, knowledge_map
    )
    forged = deepcopy(knowledge_map)
    forged["document_tree"]["sections"][0]["source_order"][
        "reading_order"
    ] += 23
    forged.pop("revision")
    forged["revision"] = "knowledge-map:sha256:" + canonical_sha256(forged)
    with psycopg.connect(study_database_dsn) as connection:
        connection.execute(
            """
            UPDATE knowledge_maps SET map_revision = %s, document = %s
            WHERE learner_id = %s AND material_id = %s
            """,
            (forged["revision"], Jsonb(forged), owner, material_id),
        )

    with pytest.raises(MapContextError, match="^KNOWLEDGE_MAP_UNAVAILABLE$"):
        read_map_context(
            owner,
            material_id,
            forged["revision"],
            dsn=study_database_dsn,
        )


@pytest.mark.parametrize(
    "tampered_bindings",
    (
        {"producer_output_id": "concept-evidence-output:sha256:" + "a" * 64},
        {"producer_runtime_lock_sha256": "b" * 64},
        {"material_runtime_binding_sha256": "c" * 64},
        {
            "producer_output_id": "concept-evidence-output:sha256:" + "a" * 64,
            "producer_runtime_lock_sha256": "b" * 64,
            "material_runtime_binding_sha256": "c" * 64,
        },
    ),
    ids=("producer-output", "producer-runtime", "material-runtime", "combined"),
)
def test_map_context_rejects_rehashed_source_binding_tamper(
    study_database_dsn: str,
    tampered_bindings: dict[str, str],
):
    owner = uuid4()
    knowledge_map = _knowledge_map()
    material_id = _insert_material_map(study_database_dsn, owner, knowledge_map)
    forged = deepcopy(knowledge_map)
    forged["source_binding"].update(tampered_bindings)
    forged.pop("revision")
    forged["revision"] = "knowledge-map:sha256:" + canonical_sha256(forged)
    with psycopg.connect(study_database_dsn) as connection:
        connection.execute(
            """
            UPDATE knowledge_maps SET map_revision = %s, document = %s
            WHERE learner_id = %s AND material_id = %s
            """,
            (forged["revision"], Jsonb(forged), owner, material_id),
        )
        run_binding = connection.execute(
            """
            SELECT output_binding FROM material_processing_runs
            WHERE learner_id = %s AND material_id = %s
            """,
            (owner, material_id),
        ).fetchone()[0]
        run_binding["knowledge_map_revision"] = forged["revision"]
        connection.execute(
            """
            UPDATE material_processing_runs SET output_binding = %s
            WHERE learner_id = %s AND material_id = %s
            """,
            (Jsonb(run_binding), owner, material_id),
        )

    with pytest.raises(MapContextError, match="^KNOWLEDGE_MAP_UNAVAILABLE$"):
        read_map_context(
            owner,
            material_id,
            forged["revision"],
            dsn=study_database_dsn,
        )


@pytest.mark.parametrize(
    ("run_binding", "field", "tampered_value"),
    (
        (
            "output_binding",
            "concept_evidence_output_id",
            "concept-evidence-output:sha256:" + "a" * 64,
        ),
        ("runtime_binding", "runtime_lock_sha256", "b" * 64),
    ),
    ids=("concept-evidence-output", "runtime-lock"),
)
def test_map_context_rejects_persisted_run_authority_tamper(
    study_database_dsn: str,
    run_binding: str,
    field: str,
    tampered_value: str,
):
    owner = uuid4()
    knowledge_map = _knowledge_map()
    material_id = _insert_material_map(study_database_dsn, owner, knowledge_map)
    with psycopg.connect(study_database_dsn) as connection:
        runtime_binding, output_binding = connection.execute(
            """
            SELECT runtime_binding, output_binding FROM material_processing_runs
            WHERE learner_id = %s AND material_id = %s
            """,
            (owner, material_id),
        ).fetchone()
        binding = (
            output_binding if run_binding == "output_binding" else runtime_binding
        )
        binding[field] = tampered_value
        connection.execute(
            """
            UPDATE material_processing_runs
            SET runtime_binding = %s, output_binding = %s
            WHERE learner_id = %s AND material_id = %s
            """,
            (Jsonb(runtime_binding), Jsonb(output_binding), owner, material_id),
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

    target = knowledge_map["initial_learning_path"][0]["formal_concept_id"]
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
        current_formal_concept_id=knowledge_map["initial_learning_path"][0][
            "formal_concept_id"
        ],
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
