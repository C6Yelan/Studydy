from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
import json
import re
from typing import Any, Callable
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from pdf_evidence.ocr_page_evidence import canonical_sha256
from runtime.learner_session import TrustedLearner
from runtime.semantic_service import SemanticServiceError, request_semantics, semantic_client
from runtime.storage.tables import Assessment, KnowledgeStructure, StudySession, database_session

from .map_context import ClaimContext, ConceptContext, MapContextError, context_from_structure


_ID = re.compile(r"[a-z-]+:sha256:[0-9a-f]{64}")


class AssessmentError(RuntimeError):
    pass


@dataclass(frozen=True)
class StoredAssessment:
    assessment_revision: str
    study_session_id: UUID
    knowledge_structure_revision: str
    question_id: str
    semantic_identity: str = field(repr=False)
    learning_angle: str
    target_concept_id: str
    target_claim_id: str
    public_document: dict[str, Any]
    private_answer_document: dict[str, Any] = field(repr=False)
    generation_provenance: dict[str, Any] = field(repr=False)
    mastery_qualified: bool


def assessment_response_schema() -> dict[str, Any]:
    distractor = {
        "type": "object",
        "additionalProperties": False,
        "required": ["text", "changed_from", "changed_to"],
        "properties": {
            "text": {"type": "string", "minLength": 1},
            "changed_from": {"type": "string", "minLength": 1},
            "changed_to": {"type": "string", "minLength": 1},
        },
    }
    candidate = {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "learning_angle", "novelty", "safety", "prompt", "correct_answer",
            "supporting_evidence_ids", "distractors",
        ],
        "properties": {
            "learning_angle": {"type": "string", "minLength": 1},
            "novelty": {"type": "string", "enum": ["distinct", "uncertain"]},
            "safety": {"type": "string", "enum": ["safe", "reject"]},
            "prompt": {"type": "string", "minLength": 1},
            "correct_answer": {"type": "string", "minLength": 1},
            "supporting_evidence_ids": {"type": "array", "minItems": 1, "items": {"type": "string"}},
            "distractors": {"type": "array", "minItems": 3, "maxItems": 3, "items": distractor},
        },
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["schema", "candidates"],
        "properties": {
            "schema": {"type": "string", "const": "assessment-semantics-response/v1"},
            "candidates": {"type": "array", "minItems": 3, "maxItems": 3, "items": candidate},
        },
    }


def _learner(learner: TrustedLearner) -> UUID:
    if not isinstance(learner, TrustedLearner) or not isinstance(learner.learner_id, UUID):
        raise AssessmentError("ASSESSMENT_REQUEST_INVALID")
    return learner.learner_id


def _clean(value: Any, maximum: int = 4096) -> str:
    if not isinstance(value, str):
        raise AssessmentError("ASSESSMENT_OUTPUT_INVALID")
    cleaned = " ".join(value.split())
    if not cleaned or len(cleaned) > maximum or "\x00" in cleaned:
        raise AssessmentError("ASSESSMENT_OUTPUT_INVALID")
    return cleaned


def _key(value: str) -> bytes:
    if not isinstance(value, str) or not 1 <= len(value.encode()) <= 256:
        raise AssessmentError("ASSESSMENT_REQUEST_INVALID")
    return sha256(value.encode()).digest()


def _fingerprint(study_session_id: UUID, revision: str, claim_id: str) -> bytes:
    return sha256(json.dumps(
        {"study_session_id": str(study_session_id), "knowledge_structure_revision": revision, "target_claim_id": claim_id},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()).digest()


def _stored(row: Assessment) -> StoredAssessment:
    if not all(isinstance(value, dict) for value in (row.public_document, row.private_answer_document, row.generation_provenance)):
        raise AssessmentError("ASSESSMENT_UNAVAILABLE")
    if (
        row.public_document.get("assessment_revision") != row.assessment_revision
        or row.public_document.get("question_id") != row.question_id
        or row.private_answer_document.get("assessment_revision") != row.assessment_revision
        or row.generation_provenance.get("assessment_revision") != row.assessment_revision
    ):
        raise AssessmentError("ASSESSMENT_UNAVAILABLE")
    return StoredAssessment(
        row.assessment_revision, row.study_session_id, row.knowledge_structure_revision,
        row.question_id, row.semantic_identity, row.learning_angle,
        row.target_concept_id, row.target_claim_id, deepcopy(row.public_document),
        deepcopy(row.private_answer_document), deepcopy(row.generation_provenance),
        row.mastery_qualified,
    )


def _target(session, learner_id: UUID, study_session_id: UUID, claim_id: str) -> tuple[StudySession, ConceptContext, ClaimContext]:
    study = session.scalar(select(StudySession).where(
        StudySession.learner_id == learner_id,
        StudySession.study_session_id == study_session_id,
    ).with_for_update())
    if study is None or study.status not in {"active", "no_safe"} or study.current_concept_id is None:
        raise AssessmentError("ASSESSMENT_SESSION_UNAVAILABLE")
    document = session.scalar(select(KnowledgeStructure.document).where(
        KnowledgeStructure.learner_id == learner_id,
        KnowledgeStructure.material_id == study.material_id,
        KnowledgeStructure.structure_revision == study.knowledge_structure_revision,
    ))
    try:
        context = context_from_structure(study.material_id, document)
        concept = next(item for item in context.concepts if item.concept_id == study.current_concept_id)
        claim = next(item for item in concept.claims if item.claim_id == claim_id)
    except (MapContextError, StopIteration, TypeError):
        raise AssessmentError("ASSESSMENT_TARGET_INVALID") from None
    return study, concept, claim


def _request(study: StudySession, concept: ConceptContext, claim: ClaimContext, prior: list[Assessment]) -> dict[str, Any]:
    return {
        "schema": "assessment-semantics-request/v1",
        "knowledge_structure_revision": study.knowledge_structure_revision,
        "concept": {"concept_id": concept.concept_id, "label": concept.label},
        "claim": {
            "claim_id": claim.claim_id,
            "text": claim.text,
            "evidence": [
                {"evidence_id": evidence.evidence_id, "exact_text": evidence.quote}
                for evidence in claim.evidence
            ],
        },
        "prior_questions": [
            {
                "learning_angle": row.learning_angle,
                "prompt": row.public_document["prompt"],
                "correct_answer": row.private_answer_document["correct_answer"],
            }
            for row in prior
        ],
    }


def _candidate(candidate: Any, claim: ClaimContext, used_identities: set[str]) -> dict[str, Any] | None:
    fields = {
        "learning_angle", "novelty", "safety", "prompt", "correct_answer",
        "supporting_evidence_ids", "distractors",
    }
    if not isinstance(candidate, dict) or set(candidate) != fields or candidate.get("safety") != "safe" or candidate.get("novelty") not in {"distinct", "uncertain"}:
        return None
    try:
        angle = _clean(candidate["learning_angle"], 256)
        prompt = _clean(candidate["prompt"])
        correct = _clean(candidate["correct_answer"])
    except AssessmentError:
        return None
    evidence = {item.evidence_id: item.quote for item in claim.evidence}
    references = candidate["supporting_evidence_ids"]
    distractors = candidate["distractors"]
    if (
        not isinstance(references, list)
        or not references
        or len(references) != len(set(references))
        or any(reference not in evidence for reference in references)
        or not any(correct in evidence[reference] for reference in references)
        or not isinstance(distractors, list)
        or len(distractors) != 3
        or any(reference in prompt for reference in evidence)
    ):
        return None
    source_text = "\n".join(evidence.values())
    options = [correct]
    proofs = []
    for distractor in distractors:
        if not isinstance(distractor, dict) or set(distractor) != {"text", "changed_from", "changed_to"}:
            return None
        try:
            text = _clean(distractor["text"])
            changed_from = _clean(distractor["changed_from"])
            changed_to = _clean(distractor["changed_to"])
        except AssessmentError:
            return None
        if (
            changed_from not in correct
            or changed_from not in source_text
            or changed_to in correct
            or changed_to in source_text
            or changed_to not in text
            or text in source_text
            or any(reference in text for reference in evidence)
        ):
            return None
        options.append(text)
        proofs.append({"changed_from": changed_from, "changed_to": changed_to})
    normalized = [" ".join(option.casefold().split()) for option in options]
    if len(normalized) != len(set(normalized)):
        return None
    semantic_identity = "assessment-semantic:sha256:" + canonical_sha256({"prompt": prompt.casefold(), "correct": correct.casefold()})
    if semantic_identity in used_identities:
        return None
    return {
        "learning_angle": angle,
        "novelty": candidate["novelty"],
        "prompt": prompt,
        "correct_answer": correct,
        "supporting_evidence_ids": references,
        "options": options,
        "proofs": proofs,
        "semantic_identity": semantic_identity,
    }


def _documents(
    study: StudySession,
    concept: ConceptContext,
    claim: ClaimContext,
    candidate: dict[str, Any],
    *,
    runtime_lock: dict[str, Any],
    prior_angles: set[str],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], bool]:
    question_identity = {
        "study_session_id": str(study.study_session_id),
        "knowledge_structure_revision": study.knowledge_structure_revision,
        "target_concept_id": concept.concept_id,
        "target_claim_id": claim.claim_id,
        "prompt": candidate["prompt"],
        "options": candidate["options"],
    }
    question_id = "question:sha256:" + canonical_sha256(question_identity)
    option_documents = sorted(
        ({"option_id": "option:sha256:" + canonical_sha256({"question_id": question_id, "text": text}), "text": text} for text in candidate["options"]),
        key=lambda option: option["option_id"],
    )
    correct_option_id = next(option["option_id"] for option in option_documents if option["text"] == candidate["correct_answer"])
    mastery_qualified = not prior_angles or (candidate["novelty"] == "distinct" and candidate["learning_angle"].casefold() not in prior_angles)
    public_core = {
        "schema": "single-choice-assessment/v2",
        "study_session_id": str(study.study_session_id),
        "knowledge_structure_revision": study.knowledge_structure_revision,
        "question_id": question_id,
        "target_concept_id": concept.concept_id,
        "target_claim_id": claim.claim_id,
        "source_evidence_ids": candidate["supporting_evidence_ids"],
        "question_type": "single_choice",
        "prompt": candidate["prompt"],
        "options": option_documents,
    }
    private_core = {
        "schema": "single-choice-answer/v2",
        "question_id": question_id,
        "correct_option_id": correct_option_id,
        "correct_answer": candidate["correct_answer"],
        "rationale": " ".join(evidence.quote for evidence in claim.evidence if evidence.evidence_id in candidate["supporting_evidence_ids"]),
    }
    revision = "assessment:sha256:" + canonical_sha256({"public": public_core, "private_sha256": canonical_sha256(private_core)})
    public = {**public_core, "assessment_revision": revision}
    private = {**private_core, "assessment_revision": revision}
    service = runtime_lock["semantic_service"]
    provenance = {
        "schema": "assessment-generation-provenance/v3",
        "assessment_revision": revision,
        "runtime_lock_sha256": canonical_sha256(runtime_lock),
        "model_id": service["model_id"],
        "model_revision": service["revision"],
        "policy": runtime_lock["assessment"]["policy"],
        "source_evidence_ids": candidate["supporting_evidence_ids"],
        "counterfactual_proofs": candidate["proofs"],
    }
    return public, private, provenance, mastery_qualified


def generate_assessment(
    learner: TrustedLearner,
    study_session_id: UUID,
    target_claim_id: str,
    idempotency_key: str,
    local_config: dict[str, Any],
    *,
    dsn: str | None = None,
    client: httpx.Client | None = None,
    semantic_call: Callable[..., dict[str, Any]] = request_semantics,
) -> StoredAssessment:
    learner_id = _learner(learner)
    if not isinstance(study_session_id, UUID) or not isinstance(target_claim_id, str) or _ID.fullmatch(target_claim_id) is None:
        raise AssessmentError("ASSESSMENT_REQUEST_INVALID")
    key = _key(idempotency_key)
    runtime_lock = local_config.get("runtime_lock")
    no_safe = False
    try:
        with database_session(dsn) as session:
            study, concept, claim = _target(session, learner_id, study_session_id, target_claim_id)
            fingerprint = _fingerprint(study_session_id, study.knowledge_structure_revision, target_claim_id)
            existing = session.scalar(select(Assessment).where(Assessment.study_session_id == study_session_id, Assessment.request_idempotency_key_sha256 == key))
            if existing is not None:
                if bytes(existing.request_fingerprint) != fingerprint:
                    raise AssessmentError("ASSESSMENT_IDEMPOTENCY_CONFLICT")
                return _stored(existing)
            prior = list(session.scalars(select(Assessment).where(Assessment.study_session_id == study_session_id, Assessment.target_claim_id == target_claim_id).order_by(Assessment.created_at)))
            owned = client is None
            http = semantic_client() if client is None else client
            try:
                response = semantic_call(
                    http,
                    runtime_lock=runtime_lock,
                    task="assessment",
                    request=_request(study, concept, claim, prior),
                    response_schema=assessment_response_schema(),
                )
            finally:
                if owned:
                    http.close()
            if not isinstance(response, dict) or set(response) != {"schema", "candidates"} or response["schema"] != "assessment-semantics-response/v1" or not isinstance(response["candidates"], list) or len(response["candidates"]) != 3:
                raise AssessmentError("ASSESSMENT_OUTPUT_INVALID")
            used = {row.semantic_identity for row in prior}
            chosen = next((projected for item in response["candidates"] if (projected := _candidate(item, claim, used)) is not None), None)
            if chosen is None:
                if target_claim_id not in study.no_safe_claim_ids:
                    study.no_safe_claim_ids = [*study.no_safe_claim_ids, target_claim_id]
                study.status = "no_safe"
                no_safe = True
            else:
                public, private, provenance, mastery_qualified = _documents(
                    study, concept, claim, chosen,
                    runtime_lock=runtime_lock,
                    prior_angles={row.learning_angle.casefold() for row in prior},
                )
                session.execute(insert(Assessment).values(
                    assessment_revision=public["assessment_revision"],
                    study_session_id=study_session_id,
                    knowledge_structure_revision=study.knowledge_structure_revision,
                    question_id=public["question_id"],
                    semantic_identity=chosen["semantic_identity"],
                    learning_angle=chosen["learning_angle"],
                    target_concept_id=concept.concept_id,
                    target_claim_id=claim.claim_id,
                    public_document=public,
                    private_answer_document=private,
                    generation_provenance=provenance,
                    mastery_qualified=mastery_qualified,
                    request_idempotency_key_sha256=key,
                    request_fingerprint=fingerprint,
                    created_at=datetime.now(UTC),
                ))
                study.status = "active"
                stored = session.scalar(select(Assessment).where(Assessment.assessment_revision == public["assessment_revision"]))
                if stored is None:
                    raise AssessmentError("ASSESSMENT_STORE_FAILED")
                return _stored(stored)
        if no_safe:
            raise AssessmentError("NO_SAFE_ASSESSMENT")
    except AssessmentError:
        raise
    except SemanticServiceError as error:
        raise AssessmentError(error.reason_code) from None
    except Exception:
        raise AssessmentError("ASSESSMENT_STORE_FAILED") from None


def read_assessment(
    learner: TrustedLearner,
    study_session_id: UUID,
    assessment_revision: str,
    *,
    dsn: str | None = None,
) -> StoredAssessment:
    learner_id = _learner(learner)
    try:
        with database_session(dsn) as session:
            row = session.scalar(select(Assessment).join(StudySession, StudySession.study_session_id == Assessment.study_session_id).where(
                StudySession.learner_id == learner_id,
                Assessment.study_session_id == study_session_id,
                Assessment.assessment_revision == assessment_revision,
            ))
        if row is None:
            raise AssessmentError("ASSESSMENT_UNAVAILABLE")
        return _stored(row)
    except AssessmentError:
        raise
    except Exception:
        raise AssessmentError("ASSESSMENT_UNAVAILABLE") from None
