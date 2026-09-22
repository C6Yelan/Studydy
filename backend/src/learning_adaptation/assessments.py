from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from hashlib import sha256
import json
import re
from typing import Any
import unicodedata
from uuid import UUID

from sqlalchemy import case, or_, select

from pdf_evidence.ocr_page_evidence import canonical_sha256
from runtime.learner_session import TrustedLearner
from runtime.semantic_service import request_semantics, semantic_client
from runtime.storage.tables import Assessment, StudySession

from .map_context import ClaimContext, ConceptContext


_ID = re.compile(r"[a-z-]+:sha256:[0-9a-f]{64}")
_QUALITY_ISSUES = (
    "trivial_focus", "answer_cue", "unclear_wording", "uneven_options", "weak_distractors",
)
_PRIOR_LIMIT = 8
_PRIOR_CONTEXT_MAX_BYTES = 12000


def _valid_quality_issues(value: Any) -> bool:
    return (
        isinstance(value, list)
        and len(value) <= len(_QUALITY_ISSUES)
        and all(isinstance(item, str) and item in _QUALITY_ISSUES for item in value)
    )


def _quality_rank(issues: list[str]) -> tuple[bool, bool, int]:
    # 只比較已通過正確性檢查的候選；不設品質分數門檻，基礎回憶題仍可發布。
    return "trivial_focus" in issues, "answer_cue" in issues, len(issues)


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
    candidate = {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "learning_angle", "novelty", "safety", "prompt", "correct_answer",
            "supporting_evidence_ids", "distractors",
        ],
        "properties": {
            "learning_angle": {"type": "string", "minLength": 1},
            "correct_answer": {"type": "string", "minLength": 1},
            "supporting_evidence_ids": {"type": "array", "minItems": 1, "items": {"type": "string"}},
            "prompt": {"type": "string", "minLength": 1},
            "distractors": {"type": "array", "minItems": 3, "maxItems": 3, "items": {"type": "string", "minLength": 1}},
            "novelty": {"type": "string", "enum": ["distinct", "uncertain"]},
            "safety": {"type": "string", "enum": ["safe", "reject"]},
        },
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["schema", "candidates"],
        "properties": {
            "schema": {"type": "string", "const": "assessment-semantics-response/v2"},
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
        "model_revision", "policy", "source_evidence_ids",
        "learning_angle", "novelty", "mastery_qualified",
    }
    provenance_fields.update({"verification", "quality_selection", "compared_assessment_revisions",
                              "prompt_sha256", "check_prompt_sha256", "execution_identity"})
    command_execution = provenance.get("execution_identity") is not None
    if command_execution:
        provenance_fields.add("execution_identity")
        execution=provenance.get("execution_identity")
        if (not isinstance(execution,dict) or set(execution)!={"transport","model_id","model_revision","config_sha256","runtime_lock_sha256"}
            or execution.get("transport")!="command" or any(execution.get(k)!=provenance.get(k) for k in ("model_id","model_revision","runtime_lock_sha256"))
            or not isinstance(execution.get("config_sha256"),str) or re.fullmatch(r"[0-9a-f]{64}",execution["config_sha256"]) is None):
            raise AssessmentError("ASSESSMENT_UNAVAILABLE")
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
        or provenance["schema"] != "assessment-generation-provenance/v8"
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
        or private["correct_answer"] not in private["rationale"]
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
        or not isinstance(provenance["model_id"],str) or not provenance["model_id"]
        or not isinstance(provenance["model_revision"],str) or not provenance["model_revision"]
        or (not command_execution and (provenance["model_id"]!="google/gemma-4-31B-it-qat-w4a16-ct" or provenance["model_revision"]!="52f3f65bc7a02d555763bc923bd1d9094898219d"))
        or provenance["policy"] != "source-span-single-choice/v6"
        or provenance["learning_angle"] != row.learning_angle
        or not isinstance(row.learning_angle, str)
        or not row.learning_angle.strip()
        or provenance["novelty"] not in {"distinct", "uncertain"}
        or type(provenance["mastery_qualified"]) is not bool
        or provenance["mastery_qualified"] != row.mastery_qualified
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
    verification = provenance["verification"]
    if (not isinstance(verification, dict)
        or set(verification) != {"options", "selected_option_index", "duplicate_prior_index"}
        or verification["options"] != sorted(option_texts, key=_normalized)
        or type(verification["selected_option_index"]) is not int
        or not 0 <= verification["selected_option_index"] < 4
        or verification["options"][verification["selected_option_index"]] != private["correct_answer"]
        or verification["duplicate_prior_index"] is not None
        or row.mastery_qualified is not True):
        raise AssessmentError("ASSESSMENT_UNAVAILABLE")
    quality = provenance["quality_selection"]
    compared = provenance["compared_assessment_revisions"]
    if (not isinstance(quality, dict)
        or set(quality) != {"candidate_index", "checked_candidate_count", "safe_candidate_count", "issues"}
        or any(type(quality[key]) is not int for key in (
            "candidate_index", "checked_candidate_count", "safe_candidate_count"))
        or not 1 <= quality["safe_candidate_count"] <= quality["checked_candidate_count"] <= 3
        or not 0 <= quality["candidate_index"] < quality["checked_candidate_count"]
        or not _valid_quality_issues(quality["issues"])
        or not isinstance(compared, list) or len(compared) > _PRIOR_LIMIT
        or any(not isinstance(item, str) or re.fullmatch(r"assessment:sha256:[0-9a-f]{64}", item) is None for item in compared)
        or len(compared) != len(set(compared))
        or any(not isinstance(provenance[key], str) or re.fullmatch(r"[0-9a-f]{64}", provenance[key]) is None
               for key in ("prompt_sha256", "check_prompt_sha256"))):
        raise AssessmentError("ASSESSMENT_UNAVAILABLE")
    return StoredAssessment(
        row.assessment_revision, row.study_session_id, row.knowledge_structure_revision,
        row.question_id, row.semantic_identity, row.learning_angle,
        row.target_concept_id, row.target_claim_id, deepcopy(row.public_document),
        deepcopy(row.private_answer_document), deepcopy(row.generation_provenance),
        row.mastery_qualified,
    )




def _request(study: StudySession, concept: ConceptContext, claim: ClaimContext, prior: list[Assessment]) -> dict[str, Any]:
    return {
        "schema": "assessment-semantics-request/v2",
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


def _prior_questions(session, study: StudySession, claim: ClaimContext) -> list[Assessment]:
    """同 session／exact KS 的有限比較集合；跨 Claim 同來源者優先，不宣稱全歷史去重。"""
    overlap = or_(*(Assessment.public_document["source_evidence_ids"].contains([item.evidence_id])
                    for item in claim.evidence))
    rows = session.scalars(select(Assessment).where(
        Assessment.study_session_id == study.study_session_id,
        Assessment.knowledge_structure_revision == study.knowledge_structure_revision,
    ).order_by(
        case((Assessment.target_claim_id == claim.claim_id, 0), (overlap, 1),
             (Assessment.target_concept_id == study.current_concept_id, 2), else_=3),
        Assessment.created_at.desc(), Assessment.assessment_revision,
    ).limit(32))
    prior, size = [], 0
    for row in rows:
        _stored(row)
        # 兩個 prompt 都放完整題目；只限比較集合，不截斷來源或題幹。
        entry = {"prompt": row.public_document["prompt"],
                 "options": row.public_document["options"],
                 "correct_answer": row.private_answer_document["correct_answer"],
                 "learning_angle": row.learning_angle}
        cost = len(json.dumps(entry, ensure_ascii=False).encode("utf-8"))
        if size + cost > _PRIOR_CONTEXT_MAX_BYTES:
            continue
        prior.append(row)
        size += cost
        if len(prior) == _PRIOR_LIMIT:
            break
    return prior


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
        or any(not isinstance(reference, str) for reference in references)
        or len(references) != len(set(references))
        or any(reference not in evidence for reference in references)
        or not any(correct in evidence[reference] for reference in references)
        or not isinstance(distractors, list)
        or len(distractors) != 3
        or any(reference in prompt for reference in evidence)
    ):
        return None
    options = [correct]
    # 選項在其他句子出現不代表它能回答本題；語意安全由 Gemma 判斷。
    for distractor in distractors:
        try:
            text = _clean(distractor)
        except AssessmentError:
            return None
        if any(reference in text for reference in evidence):
            return None
        options.append(text)
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
        "semantic_identity": semantic_identity,
    }



def assessment_check_schema(count: int, prior_count: int) -> dict[str, Any]:
    return {
        "type": "object", "additionalProperties": False,
        "required": ["schema", "verdicts"],
        "properties": {
            "schema": {"type": "string", "const": "assessment-check-response/v2"},
            "verdicts": {"type": "array", "minItems": count, "maxItems": count,
                "items": {"type": "object", "additionalProperties": False,
                    "required": ["question_index", "answer_status", "selected_option_index", "duplicate_prior_index", "quality_issues"],
                    "properties": {
                        "question_index": {"type": "integer", "minimum": 0, "maximum": count - 1},
                        "answer_status": {"type": "string", "enum": ["unique", "none", "multiple"]},
                        "selected_option_index": {"type": ["integer", "null"], "minimum": 0, "maximum": 3},
                        "duplicate_prior_index": {"type": ["integer", "null"], "minimum": 0, "maximum": prior_count - 1} if prior_count else {"type": "null"},
                        "quality_issues": {"type": "array", "maxItems": len(_QUALITY_ISSUES),
                                           "items": {"type": "string", "enum": list(_QUALITY_ISSUES)}},
                    }},
            },
        },
    }


def _checked_candidate(client, runtime_lock, claim, candidates, prior, semantic_call):
    if not candidates:
        return None
    # Sorting hides the generator's designated first/correct option from the solver.
    questions = [
        {"question_index": index, "prompt": candidate["prompt"],
         "options": sorted(candidate["options"], key=_normalized)}
        for index, candidate in enumerate(candidates)
    ]
    response = semantic_call(
        client, runtime_lock=runtime_lock, task="assessment_check",
        request={
            "schema": "assessment-check-request/v2",
            "claim": claim.text,
            "evidence": [{"evidence_id": item.evidence_id, "exact_text": item.quote} for item in claim.evidence],
            "questions": questions,
            "prior_questions": [
                {"question_index": index, "prompt": item.public_document["prompt"],
                 "options": [option["text"] for option in item.public_document["options"]]}
                for index, item in enumerate(prior)
            ],
        },
        response_schema=assessment_check_schema(len(questions), len(prior)),
    )
    if (not isinstance(response, dict) or set(response) != {"schema", "verdicts"}
        or response["schema"] != "assessment-check-response/v2"
        or not isinstance(response["verdicts"], list) or len(response["verdicts"]) != len(questions)):
        raise AssessmentError("ASSESSMENT_CHECK_INVALID")
    checked = {}
    for verdict in response["verdicts"]:
        if not isinstance(verdict, dict) or set(verdict) != {
            "question_index", "answer_status", "selected_option_index", "duplicate_prior_index", "quality_issues"
        }:
            raise AssessmentError("ASSESSMENT_CHECK_INVALID")
        index, selected, duplicate = (verdict[key] for key in ("question_index", "selected_option_index", "duplicate_prior_index"))
        if (type(index) is not int or not 0 <= index < len(questions) or index in checked
            or verdict["answer_status"] not in {"unique", "none", "multiple"}
            or (selected is not None and (type(selected) is not int or not 0 <= selected < 4))
            or (duplicate is not None and (type(duplicate) is not int or not 0 <= duplicate < len(prior)))
            or (verdict["answer_status"] == "unique") != (selected is not None)
            or not _valid_quality_issues(verdict["quality_issues"])):
            raise AssessmentError("ASSESSMENT_CHECK_INVALID")
        # 重複列出同一品質提示只算一次，不因此把正確題目判成不可用。
        checked[index] = {**verdict, "quality_issues": list(dict.fromkeys(verdict["quality_issues"]))}
    safe = []
    for index, candidate in enumerate(candidates):
        verdict = checked[index]
        selected = verdict["selected_option_index"]
        if (verdict["answer_status"] == "unique" and verdict["duplicate_prior_index"] is None
            and questions[index]["options"][selected] == candidate["correct_answer"]):
            safe.append(index)
    if not safe:
        return None
    index = min(safe, key=lambda item: _quality_rank(checked[item]["quality_issues"]))
    return {**candidates[index], "verification": {
                "options": questions[index]["options"],
                "selected_option_index": checked[index]["selected_option_index"],
                "duplicate_prior_index": None,
            }, "quality_selection": {
                "candidate_index": index, "checked_candidate_count": len(candidates),
                "safe_candidate_count": len(safe), "issues": checked[index]["quality_issues"],
            }, "compared_assessment_revisions": [item.assessment_revision for item in prior]}

def _documents(
    study: StudySession,
    concept: ConceptContext,
    claim: ClaimContext,
    candidate: dict[str, Any],
    *,
    runtime_lock: dict[str, Any],
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
    # A published item has passed an independent source solve and duplicate check.
    # Novelty and angle remain provenance only; old stored eligibility is not rewritten.
    mastery_qualified = True
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
    from runtime.command_semantics import identity
    command=identity(runtime_lock)
    service = runtime_lock["semantic_service"] if command is None else {"model_id":command["model_id"],"revision":command["model_revision"]}
    provenance_core = {
        "schema": "assessment-generation-provenance/v8",
        "runtime_lock_sha256": canonical_sha256(runtime_lock) if command is None else command["runtime_lock_sha256"],
        "model_id": service["model_id"],
        "model_revision": service["revision"],
        "policy": runtime_lock["assessment"]["policy"],
        "source_evidence_ids": candidate["supporting_evidence_ids"],
        "learning_angle": candidate["learning_angle"],
        "novelty": candidate["novelty"],
        "mastery_qualified": mastery_qualified,
        "verification": deepcopy(candidate["verification"]),
        "quality_selection": deepcopy(candidate["quality_selection"]),
        "compared_assessment_revisions": list(candidate["compared_assessment_revisions"]),
        "prompt_sha256": sha256(runtime_lock["assessment"]["prompt"].encode()).hexdigest(),
        "check_prompt_sha256": sha256(runtime_lock["assessment"]["check_prompt"].encode()).hexdigest(),
        "execution_identity": deepcopy(command),
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




def prepare_assessment(study, concept, claim, prior, used_identities, *, runtime_lock,
                       client=None, semantic_call=request_semantics):
    """題組中的各題共用安全管線；本函式不開 DB transaction，也不發布題目。"""
    owned = client is None
    http = semantic_client() if owned else client
    try:
        response = semantic_call(http, runtime_lock=runtime_lock, task='assessment',
                                 request=_request(study, concept, claim, prior),
                                 response_schema=assessment_response_schema())
        if (not isinstance(response, dict) or set(response) != {'schema', 'candidates'}
            or response['schema'] != 'assessment-semantics-response/v2'
            or not isinstance(response['candidates'], list) or len(response['candidates']) != 3):
            raise AssessmentError('ASSESSMENT_OUTPUT_INVALID')
        candidates = [projected for item in response['candidates']
                      if (projected := _candidate(item, claim, used_identities)) is not None]
        return _checked_candidate(http, runtime_lock, claim, candidates, prior, semantic_call)
    finally:
        if owned:
            http.close()
