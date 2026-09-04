from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
import json
import re
from typing import Any, Callable
import unicodedata
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


def _exact(value: Any, maximum: int = 4096) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > maximum
        or "\x00" in value
    ):
        raise AssessmentError("ASSESSMENT_OUTPUT_INVALID")
    return value


def _normalized(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


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
    public = row.public_document
    private = row.private_answer_document
    provenance = row.generation_provenance
    if not all(isinstance(value, dict) for value in (public, private, provenance)):
        raise AssessmentError("ASSESSMENT_UNAVAILABLE")
    public_fields = {
        "schema", "assessment_revision", "study_session_id",
        "knowledge_structure_revision", "question_id", "target_concept_id",
        "target_claim_id", "source_evidence_ids", "question_type", "prompt", "options",
    }
    private_fields = {
        "schema", "assessment_revision", "question_id", "correct_option_id",
        "correct_answer", "rationale",
    }
    provenance_fields = {
        "schema", "assessment_revision", "runtime_lock_sha256", "model_id",
        "model_revision", "policy", "source_evidence_ids", "counterfactual_proofs",
        "learning_angle", "novelty", "mastery_qualified",
    }
    try:
        options = public["options"]
        option_ids = [option["option_id"] for option in options]
        option_texts = [option["text"] for option in options]
        question_identity = {
            "study_session_id": str(row.study_session_id),
            "knowledge_structure_revision": row.knowledge_structure_revision,
            "target_concept_id": row.target_concept_id,
            "target_claim_id": row.target_claim_id,
            "prompt": public["prompt"],
            "options": sorted(option_texts),
        }
        question_id = "question:sha256:" + canonical_sha256(question_identity)
        semantic_identity = "assessment-semantic:sha256:" + canonical_sha256(
            {
                "prompt": _normalized(public["prompt"]),
                "correct": _normalized(private["correct_answer"]),
            }
        )
        public_core = {key: value for key, value in public.items() if key != "assessment_revision"}
        private_core = {key: value for key, value in private.items() if key != "assessment_revision"}
        provenance_core = {
            key: value for key, value in provenance.items() if key != "assessment_revision"
        }
        revision = "assessment:sha256:" + canonical_sha256(
            {
                "public": public_core,
                "private_sha256": canonical_sha256(private_core),
                "provenance_sha256": canonical_sha256(provenance_core),
            }
        )
    except (KeyError, TypeError, ValueError):
        raise AssessmentError("ASSESSMENT_UNAVAILABLE") from None
    if (
        set(public) != public_fields
        or set(private) != private_fields
        or set(provenance) != provenance_fields
        or public["schema"] != "single-choice-assessment/v2"
        or private["schema"] != "single-choice-answer/v2"
        or provenance["schema"] != "assessment-generation-provenance/v4"
        or revision != row.assessment_revision
        or public["assessment_revision"] != revision
        or private["assessment_revision"] != revision
        or provenance["assessment_revision"] != revision
        or re.fullmatch(r"assessment:sha256:[0-9a-f]{64}", revision) is None
        or public["study_session_id"] != str(row.study_session_id)
        or public["knowledge_structure_revision"] != row.knowledge_structure_revision
        or public["question_id"] != question_id
        or private["question_id"] != question_id
        or row.question_id != question_id
        or re.fullmatch(r"question:sha256:[0-9a-f]{64}", question_id) is None
        or row.semantic_identity != semantic_identity
        or re.fullmatch(
            r"assessment-semantic:sha256:[0-9a-f]{64}", semantic_identity
        )
        is None
        or public["target_concept_id"] != row.target_concept_id
        or re.fullmatch(r"concept:sha256:[0-9a-f]{64}", row.target_concept_id)
        is None
        or public["target_claim_id"] != row.target_claim_id
        or re.fullmatch(r"claim:sha256:[0-9a-f]{64}", row.target_claim_id) is None
        or public["question_type"] != "single_choice"
        or not isinstance(public["prompt"], str)
        or not public["prompt"].strip()
        or not isinstance(options, list)
        or len(options) != 4
        or any(
            not isinstance(option, dict)
            or set(option) != {"option_id", "text"}
            or not isinstance(option["text"], str)
            or not option["text"].strip()
            or option["option_id"]
            != "option:sha256:" + canonical_sha256(
                {"question_id": question_id, "text": option["text"]}
            )
            for option in options
        )
        or option_ids != sorted(option_ids)
        or len(option_ids) != len(set(option_ids))
        or len(option_texts) != len(set(option_texts))
        or private["correct_option_id"] not in option_ids
        or private["correct_answer"]
        != next(
            option["text"]
            for option in options
            if option["option_id"] == private["correct_option_id"]
        )
        or not isinstance(private["rationale"], str)
        or not private["rationale"].strip()
        or not isinstance(public["source_evidence_ids"], list)
        or not public["source_evidence_ids"]
        or len(public["source_evidence_ids"])
        != len(set(public["source_evidence_ids"]))
        or any(
            not isinstance(reference, str)
            or re.fullmatch(r"evidence:sha256:[0-9a-f]{64}", reference) is None
            for reference in public["source_evidence_ids"]
        )
        or provenance["source_evidence_ids"] != public["source_evidence_ids"]
        or re.fullmatch(r"[0-9a-f]{64}", provenance["runtime_lock_sha256"])
        is None
        or provenance["model_id"] != "Qwen/Qwen3.8-27B-FP8"
        or re.fullmatch(r"[0-9a-f]{40}", provenance["model_revision"]) is None
        or provenance["policy"] != "source-span-single-choice/v2"
        or provenance["learning_angle"] != row.learning_angle
        or not isinstance(row.learning_angle, str)
        or not row.learning_angle.strip()
        or provenance["novelty"] not in {"distinct", "uncertain"}
        or type(provenance["mastery_qualified"]) is not bool
        or provenance["mastery_qualified"] != row.mastery_qualified
        or not isinstance(provenance["counterfactual_proofs"], list)
        or len(provenance["counterfactual_proofs"]) != 3
        or len(bytes(row.request_idempotency_key_sha256)) != 32
        or len(bytes(row.request_fingerprint)) != 32
        or bytes(row.request_fingerprint)
        != _fingerprint(
            row.study_session_id,
            row.knowledge_structure_revision,
            row.target_claim_id,
        )
    ):
        raise AssessmentError("ASSESSMENT_UNAVAILABLE")
    distractor_texts = set(option_texts) - {private["correct_answer"]}
    for proof in provenance["counterfactual_proofs"]:
        if (
            not isinstance(proof, dict)
            or set(proof) != {"changed_from", "changed_to"}
            or not isinstance(proof["changed_from"], str)
            or not isinstance(proof["changed_to"], str)
            or proof["changed_from"] not in private["correct_answer"]
            or proof["changed_from"] not in private["rationale"]
            or proof["changed_to"].casefold()
            in private["correct_answer"].casefold()
            or proof["changed_to"].casefold() in private["rationale"].casefold()
            or not any(
                proof["changed_to"].casefold() in text.casefold()
                for text in distractor_texts
            )
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
        correct = _exact(candidate["correct_answer"])
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
    source_folded = source_text.casefold()
    correct_folded = correct.casefold()
    options = [correct]
    proofs = []
    for distractor in distractors:
        if not isinstance(distractor, dict) or set(distractor) != {"text", "changed_from", "changed_to"}:
            return None
        try:
            text = _clean(distractor["text"])
            changed_from = _exact(distractor["changed_from"])
            changed_to = _exact(distractor["changed_to"])
        except AssessmentError:
            return None
        if (
            changed_from not in correct
            or changed_from not in source_text
            or changed_to.casefold() in correct_folded
            or changed_to.casefold() in source_folded
            or changed_to not in text
            or text.casefold() in source_folded
            or any(reference in text for reference in evidence)
        ):
            return None
        options.append(text)
        proofs.append({"changed_from": changed_from, "changed_to": changed_to})
    normalized = [_normalized(option) for option in options]
    if len(normalized) != len(set(normalized)):
        return None
    semantic_identity = "assessment-semantic:sha256:" + canonical_sha256(
        {"prompt": _normalized(prompt), "correct": _normalized(correct)}
    )
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
        "options": sorted(candidate["options"]),
    }
    question_id = "question:sha256:" + canonical_sha256(question_identity)
    option_documents = sorted(
        ({"option_id": "option:sha256:" + canonical_sha256({"question_id": question_id, "text": text}), "text": text} for text in candidate["options"]),
        key=lambda option: option["option_id"],
    )
    correct_option_id = next(option["option_id"] for option in option_documents if option["text"] == candidate["correct_answer"])
    mastery_qualified = not prior_angles or (
        candidate["novelty"] == "distinct"
        and _normalized(candidate["learning_angle"]) not in prior_angles
    )
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
    service = runtime_lock["semantic_service"]
    provenance_core = {
        "schema": "assessment-generation-provenance/v4",
        "runtime_lock_sha256": canonical_sha256(runtime_lock),
        "model_id": service["model_id"],
        "model_revision": service["revision"],
        "policy": runtime_lock["assessment"]["policy"],
        "source_evidence_ids": candidate["supporting_evidence_ids"],
        "counterfactual_proofs": candidate["proofs"],
        "learning_angle": candidate["learning_angle"],
        "novelty": candidate["novelty"],
        "mastery_qualified": mastery_qualified,
    }
    revision = "assessment:sha256:" + canonical_sha256(
        {
            "public": public_core,
            "private_sha256": canonical_sha256(private_core),
            "provenance_sha256": canonical_sha256(provenance_core),
        }
    )
    public = {**public_core, "assessment_revision": revision}
    private = {**private_core, "assessment_revision": revision}
    provenance = {**provenance_core, "assessment_revision": revision}
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
            for prior_assessment in prior:
                _stored(prior_assessment)
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
                    prior_angles={_normalized(row.learning_angle) for row in prior},
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
