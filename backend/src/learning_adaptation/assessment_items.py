from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
import re
import unicodedata
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)
from sqlalchemy import null, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError

from pdf_evidence.ocr_page_evidence import canonical_sha256
from runtime.learner_session import TrustedLearner
from runtime.storage.database import DatabaseConfigurationError
from runtime.storage.tables import Assessment, database_session

from .map_context import MapContext, MapContextError
from .study_sessions import (
    StudySessionError,
    _learner_id,
    _read_stored_row,
    _stored_session,
    _validate_binding,
)


PUBLIC_ASSESSMENT_SCHEMA = "single-choice-assessment-public/v1"
PRIVATE_ANSWER_SCHEMA = "single-choice-assessment-answer/v1"
ASSESSMENT_POLICY_REVISION = "single-choice-assessment-policy/v1"
GENERATION_PROVENANCE_SCHEMA = "assessment-generation-provenance/v2"
SEMANTIC_NOVELTY_SCHEMA = "assessment-semantic-novelty/v1"

_ASSESSMENT_ID = r"^assessment:sha256:[0-9a-f]{64}$"
_QUESTION_ID = r"^question:sha256:[0-9a-f]{64}$"
_OPTION_ID = r"^option:sha256:[0-9a-f]{64}$"
_MAP_ID = r"^knowledge-map:sha256:[0-9a-f]{64}$"
_CONCEPT_ID = r"^formal-concept:sha256:[0-9a-f]{64}$"
_CLAIM_ID = r"^claim:sha256:[0-9a-f]{64}$"
_EVIDENCE_ID = r"^evidence:sha256:[0-9a-f]{64}$"
_SEMANTIC_ID = r"^assessment-semantic:sha256:[0-9a-f]{64}$"


class AssessmentError(RuntimeError):
    """Assessment 文件或 owner binding 無法安全處理。"""


def _normalized_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split())


class AssessmentOption(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    option_id: str = Field(pattern=_OPTION_ID)
    text: str

    @field_validator("text")
    @classmethod
    def text_must_not_be_blank(cls, value: str) -> str:
        if not _normalized_text(value):
            raise ValueError("OPTION_TEXT_BLANK")
        return value


class PublicAssessmentDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_: str = Field(
        alias="schema", pattern=r"^single-choice-assessment-public/v1$"
    )
    study_session_id: str
    knowledge_map_revision: str = Field(pattern=_MAP_ID)
    assessment_revision: str = Field(pattern=_ASSESSMENT_ID)
    question_id: str = Field(pattern=_QUESTION_ID)
    target_formal_concept_id: str = Field(pattern=_CONCEPT_ID)
    target_claim_id: str = Field(pattern=_CLAIM_ID)
    source_evidence_ids: list[str] = Field(min_length=1)
    question_type: str = Field(pattern=r"^single_choice$")
    prompt: str
    options: list[AssessmentOption] = Field(min_length=4, max_length=4)
    policy_revision: str = Field(
        pattern=r"^single-choice-assessment-policy/v1$"
    )

    @field_validator("study_session_id")
    @classmethod
    def study_session_id_must_be_canonical(cls, value: str) -> str:
        try:
            parsed = UUID(value)
        except (TypeError, ValueError):
            raise ValueError("STUDY_SESSION_ID_INVALID") from None
        if str(parsed) != value:
            raise ValueError("STUDY_SESSION_ID_INVALID")
        return value

    @field_validator("source_evidence_ids")
    @classmethod
    def evidence_ids_must_be_unique(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)) or any(
            re.fullmatch(_EVIDENCE_ID, evidence_id) is None
            for evidence_id in value
        ):
            raise ValueError("EVIDENCE_IDS_INVALID")
        return value

    @field_validator("prompt")
    @classmethod
    def prompt_must_not_be_blank(cls, value: str) -> str:
        if not _normalized_text(value):
            raise ValueError("PROMPT_BLANK")
        return value

    @model_validator(mode="after")
    def options_must_be_distinct(self) -> PublicAssessmentDocument:
        option_ids = [option.option_id for option in self.options]
        normalized_options = [
            _normalized_text(option.text) for option in self.options
        ]
        if len(set(option_ids)) != 4 or len(set(normalized_options)) != 4:
            raise ValueError("OPTIONS_DUPLICATED")
        return self


class PrivateAnswerDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_: str = Field(
        alias="schema", pattern=r"^single-choice-assessment-answer/v1$"
    )
    assessment_revision: str = Field(pattern=_ASSESSMENT_ID)
    question_id: str = Field(pattern=_QUESTION_ID)
    correct_option_id: str = Field(pattern=_OPTION_ID)
    rationale: str
    source_evidence_ids: list[str] = Field(min_length=1)
    private_answer_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("rationale")
    @classmethod
    def rationale_must_not_be_blank(cls, value: str) -> str:
        if not _normalized_text(value):
            raise ValueError("RATIONALE_BLANK")
        return value

    @field_validator("source_evidence_ids")
    @classmethod
    def evidence_ids_must_be_unique(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)) or any(
            re.fullmatch(_EVIDENCE_ID, evidence_id) is None
            for evidence_id in value
        ):
            raise ValueError("EVIDENCE_IDS_INVALID")
        return value


class AssessmentGenerationProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_: str = Field(
        alias="schema", pattern=r"^assessment-generation-provenance/v2$"
    )
    assessment_revision: str = Field(pattern=_ASSESSMENT_ID)
    question_id: str = Field(pattern=_QUESTION_ID)
    generation_policy_revision: str
    runtime_binding_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    model_id: str
    model_revision: str
    proposal_prompt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    repair_prompt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    verifier_model_id: str
    verifier_revision: str
    selected_stage: str = Field(pattern=r"^(proposal|repair)$")
    selected_candidate_index: int = Field(ge=0, le=2)
    selected_evidence_ids: list[str] = Field(min_length=1)
    option_entailment_probabilities: list[float] = Field(
        min_length=4, max_length=4
    )
    selected_evidence_option_entailment_probabilities: list[float] = Field(
        min_length=4, max_length=4
    )
    correct_option_index: int = Field(ge=0, le=3)
    entailment_margin_threshold: float = Field(ge=0, le=1)
    multiple_support_risk_threshold: float = Field(ge=0, le=1)
    entailment_margin: float = Field(ge=0, le=1)
    selected_evidence_entailment_margin: float = Field(ge=0, le=1)
    maximum_distractor_entailment: float = Field(ge=0, le=1)
    risk_trigger_distractor_entailment: float = Field(ge=0, le=1)
    multiple_support_risk: bool
    provenance_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator(
        "generation_policy_revision",
        "model_id",
        "model_revision",
        "verifier_model_id",
        "verifier_revision",
    )
    @classmethod
    def provenance_text_must_not_be_blank(cls, value: str) -> str:
        if not _normalized_text(value) or len(value) > 256:
            raise ValueError("GENERATION_PROVENANCE_TEXT_INVALID")
        return value

    @field_validator("selected_evidence_ids")
    @classmethod
    def selected_evidence_ids_must_be_unique(
        cls, value: list[str]
    ) -> list[str]:
        if len(value) != len(set(value)) or any(
            re.fullmatch(_EVIDENCE_ID, evidence_id) is None
            for evidence_id in value
        ):
            raise ValueError("GENERATION_PROVENANCE_EVIDENCE_INVALID")
        return value

    @field_validator(
        "option_entailment_probabilities",
        "selected_evidence_option_entailment_probabilities",
    )
    @classmethod
    def probabilities_must_be_bounded(cls, value: list[float]) -> list[float]:
        if any(probability < 0 or probability > 1 for probability in value):
            raise ValueError("GENERATION_PROVENANCE_PROBABILITY_INVALID")
        return value


class AssessmentSemanticNovelty(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_: str = Field(
        alias="schema", pattern=r"^assessment-semantic-novelty/v1$"
    )
    assessment_revision: str = Field(pattern=_ASSESSMENT_ID)
    question_id: str = Field(pattern=_QUESTION_ID)
    semantic_identity: str = Field(pattern=_SEMANTIC_ID)
    semantic_focus: str
    comparison_policy_revision: str
    verifier_model_id: str
    verifier_revision: str
    compared_semantic_identities: list[str]
    maximum_equivalence_score: float | None = Field(default=None, ge=0, le=1)
    runtime_binding_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    novelty_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator(
        "semantic_focus",
        "comparison_policy_revision",
        "verifier_model_id",
        "verifier_revision",
    )
    @classmethod
    def novelty_text_must_not_be_blank(cls, value: str) -> str:
        if not _normalized_text(value) or len(value) > 4096:
            raise ValueError("ASSESSMENT_NOVELTY_TEXT_INVALID")
        return value

    @field_validator("compared_semantic_identities")
    @classmethod
    def compared_identities_must_be_unique(
        cls, value: list[str]
    ) -> list[str]:
        if len(value) != len(set(value)) or any(
            re.fullmatch(_SEMANTIC_ID, semantic_identity) is None
            for semantic_identity in value
        ):
            raise ValueError("ASSESSMENT_NOVELTY_IDENTITIES_INVALID")
        return value

    @model_validator(mode="after")
    def comparison_score_must_match_compared_identities(
        self,
    ) -> AssessmentSemanticNovelty:
        if bool(self.compared_semantic_identities) != (
            self.maximum_equivalence_score is not None
        ):
            raise ValueError("ASSESSMENT_NOVELTY_SCORE_INVALID")
        return self


def _private_answer_sha256(
    private_answer_document: PrivateAnswerDocument,
) -> str:
    private_identity = private_answer_document.model_dump(
        mode="json", by_alias=True, exclude={"private_answer_sha256"}
    )
    return canonical_sha256(private_identity)


def _generation_provenance_sha256(
    provenance: AssessmentGenerationProvenance,
) -> str:
    identity = provenance.model_dump(
        mode="json", by_alias=True, exclude={"provenance_sha256"}
    )
    return canonical_sha256(identity)


def _semantic_novelty_sha256(novelty: AssessmentSemanticNovelty) -> str:
    identity = novelty.model_dump(
        mode="json", by_alias=True, exclude={"novelty_sha256"}
    )
    return canonical_sha256(identity)


@dataclass(frozen=True)
class AssessmentDocuments:
    public_document: PublicAssessmentDocument
    private_answer_document: PrivateAnswerDocument = field(repr=False)


@dataclass(frozen=True)
class StoredAssessment:
    assessment_revision: str
    study_session_id: UUID
    knowledge_map_revision: str
    question_id: str
    semantic_identity: str
    target_formal_concept_id: str
    target_claim_id: str
    public_document: PublicAssessmentDocument
    private_answer_document: PrivateAnswerDocument = field(repr=False)
    generation_provenance: AssessmentGenerationProvenance | None = field(
        repr=False
    )
    semantic_novelty: AssessmentSemanticNovelty = field(repr=False)
    policy_revision: str
    created_at: datetime


def _error(reason: str) -> AssessmentError:
    return AssessmentError(reason)


def _question_semantic_content(
    public_document: PublicAssessmentDocument,
) -> dict[str, object]:
    return {
        "knowledge_map_revision": public_document.knowledge_map_revision,
        "target_formal_concept_id": public_document.target_formal_concept_id,
        "target_claim_id": public_document.target_claim_id,
        "source_evidence_ids": public_document.source_evidence_ids,
        "question_type": public_document.question_type,
        "prompt": public_document.prompt,
        "option_texts": [option.text for option in public_document.options],
        "policy_revision": public_document.policy_revision,
    }


def question_reuse_key(
    public_document: PublicAssessmentDocument,
) -> str:
    """跨Claim比較學生實際看到的同Concept題意與選項。"""

    return "question-semantic:sha256:" + canonical_sha256({
        "knowledge_map_revision": public_document.knowledge_map_revision,
        "target_formal_concept_id": public_document.target_formal_concept_id,
        "question_type": public_document.question_type,
        "prompt": _normalized_text(public_document.prompt),
        "option_texts": sorted(
            _normalized_text(option.text) for option in public_document.options
        ),
        "policy_revision": public_document.policy_revision,
    })


def _semantic_focus(documents: AssessmentDocuments) -> str:
    public = documents.public_document
    private = documents.private_answer_document
    correct_text = next(
        option.text
        for option in public.options
        if option.option_id == private.correct_option_id
    )
    return (
        f"Question: {_normalized_text(public.prompt)}\n"
        f"Correct answer: {_normalized_text(correct_text)}"
    )


def _semantic_identity(semantic_focus: str) -> str:
    normalized_focus = _normalized_text(semantic_focus).casefold()
    return "assessment-semantic:sha256:" + canonical_sha256(
        {"semantic_focus": normalized_focus}
    )


def build_assessment_semantic_novelty(
    documents: AssessmentDocuments,
    *,
    comparison_policy_revision: str,
    verifier_model_id: str,
    verifier_revision: str,
    compared_semantic_identities: list[str],
    maximum_equivalence_score: float | None,
    runtime_binding_sha256: str,
) -> AssessmentSemanticNovelty:
    """建立綁定 private 正解、且不受 Claim 或選項順序影響的題意證據。"""

    focus = _semantic_focus(documents)
    value = {
        "schema": SEMANTIC_NOVELTY_SCHEMA,
        "assessment_revision": documents.public_document.assessment_revision,
        "question_id": documents.public_document.question_id,
        "semantic_identity": _semantic_identity(focus),
        "semantic_focus": focus,
        "comparison_policy_revision": comparison_policy_revision,
        "verifier_model_id": verifier_model_id,
        "verifier_revision": verifier_revision,
        "compared_semantic_identities": compared_semantic_identities,
        "maximum_equivalence_score": maximum_equivalence_score,
        "runtime_binding_sha256": runtime_binding_sha256,
        "novelty_sha256": "0" * 64,
    }
    novelty = AssessmentSemanticNovelty.model_validate(value)
    novelty = novelty.model_copy(
        update={"novelty_sha256": _semantic_novelty_sha256(novelty)}
    )
    return validate_assessment_semantic_novelty(novelty, documents)


def validate_assessment_semantic_novelty(
    novelty: object,
    documents: AssessmentDocuments,
) -> AssessmentSemanticNovelty:
    """驗證 private semantic focus、identity 與 Assessment 正解完全綁定。"""

    try:
        checked = AssessmentSemanticNovelty.model_validate(novelty)
        public = documents.public_document
        expected_focus = _semantic_focus(documents)
        if (
            checked.schema_ != SEMANTIC_NOVELTY_SCHEMA
            or checked.assessment_revision != public.assessment_revision
            or checked.question_id != public.question_id
            or checked.semantic_focus != expected_focus
            or checked.semantic_identity != _semantic_identity(expected_focus)
            or checked.semantic_identity
            in checked.compared_semantic_identities
            or checked.novelty_sha256 != _semantic_novelty_sha256(checked)
        ):
            raise ValueError
        return checked
    except (StopIteration, ValidationError, TypeError, ValueError):
        raise _error("ASSESSMENT_DOCUMENT_INVALID") from None


def _expected_option_ids(
    question_id: str, options: list[AssessmentOption]
) -> list[str]:
    return [
        "option:sha256:"
        + canonical_sha256({"question_id": question_id, "text": option.text})
        for option in options
    ]


def _assessment_revision(
    public_document: PublicAssessmentDocument,
) -> str:
    public_identity = public_document.model_dump(
        mode="json", by_alias=True, exclude={"assessment_revision"}
    )
    return "assessment:sha256:" + canonical_sha256(
        {"public_document": public_identity}
    )


def validate_assessment_documents(
    public_document: object,
    private_answer_document: object,
) -> AssessmentDocuments:
    """嚴格驗證 public/private schema、identity 與 answer separation。"""

    try:
        public = PublicAssessmentDocument.model_validate(public_document)
        private = PrivateAnswerDocument.model_validate(private_answer_document)
        if private.private_answer_sha256 != _private_answer_sha256(private):
            raise ValueError
        if (
            public.schema_ != PUBLIC_ASSESSMENT_SCHEMA
            or private.schema_ != PRIVATE_ANSWER_SCHEMA
            or public.policy_revision != ASSESSMENT_POLICY_REVISION
            or private.question_id != public.question_id
            or private.assessment_revision != public.assessment_revision
            or private.source_evidence_ids != public.source_evidence_ids
        ):
            raise ValueError
        question_content_sha256 = canonical_sha256(
            _question_semantic_content(public)
        )
        expected_question_id = "question:sha256:" + question_content_sha256
        if public.question_id != expected_question_id:
            raise ValueError
        expected_option_ids = _expected_option_ids(
            expected_question_id, public.options
        )
        if [option.option_id for option in public.options] != expected_option_ids:
            raise ValueError
        if private.correct_option_id not in expected_option_ids:
            raise ValueError
        if public.assessment_revision != _assessment_revision(public):
            raise ValueError
        return AssessmentDocuments(public, private)
    except (ValidationError, TypeError, ValueError):
        raise _error("ASSESSMENT_DOCUMENT_INVALID") from None


def validate_assessment_generation_provenance(
    provenance: object,
    documents: AssessmentDocuments,
) -> AssessmentGenerationProvenance:
    """驗證server-private generation provenance與final documents完全綁定。"""

    try:
        checked = AssessmentGenerationProvenance.model_validate(provenance)
        public = documents.public_document
        private = documents.private_answer_document
        probabilities = checked.option_entailment_probabilities
        selected_probabilities = (
            checked.selected_evidence_option_entailment_probabilities
        )
        correct_probability = probabilities[checked.correct_option_index]
        selected_correct_probability = selected_probabilities[
            checked.correct_option_index
        ]
        maximum_distractor = max(
            probability
            for index, probability in enumerate(probabilities)
            if index != checked.correct_option_index
        )
        margin = correct_probability - maximum_distractor
        selected_margin = selected_correct_probability - max(
            probability
            for index, probability in enumerate(selected_probabilities)
            if index != checked.correct_option_index
        )
        if (
            checked.schema_ != GENERATION_PROVENANCE_SCHEMA
            or checked.provenance_sha256
            != _generation_provenance_sha256(checked)
            or checked.assessment_revision != public.assessment_revision
            or checked.question_id != public.question_id
            or checked.selected_evidence_ids != public.source_evidence_ids
            or checked.selected_evidence_ids != private.source_evidence_ids
            or public.options[checked.correct_option_index].option_id
            != private.correct_option_id
            or abs(checked.maximum_distractor_entailment - maximum_distractor)
            > 1e-12
            or abs(checked.entailment_margin - margin) > 1e-12
            or abs(
                checked.selected_evidence_entailment_margin
                - selected_margin
            )
            > 1e-12
            or margin < checked.entailment_margin_threshold
            or selected_margin < checked.entailment_margin_threshold
            or checked.multiple_support_risk
            != (
                checked.risk_trigger_distractor_entailment
                >= checked.multiple_support_risk_threshold
            )
        ):
            raise ValueError
        return checked
    except (ValidationError, IndexError, TypeError, ValueError):
        raise _error("ASSESSMENT_DOCUMENT_INVALID") from None


def build_single_choice_assessment(
    study_session_id: UUID,
    knowledge_map_revision: str,
    target_formal_concept_id: str,
    target_claim_id: str,
    source_evidence_ids: list[str],
    prompt: str,
    option_texts: list[str],
    correct_option_index: int,
    rationale: str,
) -> AssessmentDocuments:
    """由已選定的 grounded 內容建立 deterministic 單選題文件，不負責產題。"""

    if not isinstance(study_session_id, UUID) or type(correct_option_index) is not int:
        raise _error("ASSESSMENT_DOCUMENT_INVALID")
    placeholder_options = (
        [
            {
                "option_id": (
                    "option:sha256:" + "0" * 63 + str(index)
                ),
                "text": text,
            }
            for index, text in enumerate(option_texts, start=1)
        ]
        if isinstance(option_texts, list)
        else option_texts
    )
    public_input = {
        "schema": PUBLIC_ASSESSMENT_SCHEMA,
        "study_session_id": str(study_session_id),
        "knowledge_map_revision": knowledge_map_revision,
        "assessment_revision": "assessment:sha256:" + "0" * 64,
        "question_id": "question:sha256:" + "0" * 64,
        "target_formal_concept_id": target_formal_concept_id,
        "target_claim_id": target_claim_id,
        "source_evidence_ids": source_evidence_ids,
        "question_type": "single_choice",
        "prompt": prompt,
        "options": placeholder_options,
        "policy_revision": ASSESSMENT_POLICY_REVISION,
    }
    try:
        public = PublicAssessmentDocument.model_validate(public_input)
        if not 0 <= correct_option_index < 4:
            raise ValueError
        question_id = "question:sha256:" + canonical_sha256(
            _question_semantic_content(public)
        )
        options = [
            AssessmentOption(option_id=option_id, text=option.text)
            for option_id, option in zip(
                _expected_option_ids(question_id, public.options),
                public.options,
                strict=True,
            )
        ]
        public = public.model_copy(
            update={"question_id": question_id, "options": options}
        )
        private = PrivateAnswerDocument(
            schema=PRIVATE_ANSWER_SCHEMA,
            assessment_revision="assessment:sha256:" + "0" * 64,
            question_id=question_id,
            correct_option_id=options[correct_option_index].option_id,
            rationale=rationale,
            source_evidence_ids=source_evidence_ids,
            private_answer_sha256="0" * 64,
        )
        assessment_revision = _assessment_revision(public)
        public = public.model_copy(
            update={"assessment_revision": assessment_revision}
        )
        private = private.model_copy(
            update={"assessment_revision": assessment_revision}
        )
        private = private.model_copy(
            update={"private_answer_sha256": _private_answer_sha256(private)}
        )
        return validate_assessment_documents(
            public.model_dump(mode="json", by_alias=True),
            private.model_dump(mode="json", by_alias=True),
        )
    except (ValidationError, TypeError, ValueError):
        raise _error("ASSESSMENT_DOCUMENT_INVALID") from None


def _target_claim_evidence(
    context: MapContext,
    target_formal_concept_id: str,
    target_claim_id: str,
) -> set[str] | None:
    for concept in context.formal_concepts:
        if concept.formal_concept_id != target_formal_concept_id:
            continue
        for claim in concept.claims:
            if claim.claim_id == target_claim_id:
                return {evidence.evidence_id for evidence in claim.evidence}
        return None
    return None


def _stored_assessment(row: Assessment) -> StoredAssessment:
    documents = validate_assessment_documents(
        row.public_document, row.private_answer_document
    )
    provenance = (
        None
        if row.generation_provenance is None
        else validate_assessment_generation_provenance(
            row.generation_provenance, documents
        )
    )
    novelty = validate_assessment_semantic_novelty(
        row.semantic_novelty, documents
    )
    public = documents.public_document
    if (
        row.assessment_revision != public.assessment_revision
        or row.study_session_id != UUID(public.study_session_id)
        or row.knowledge_map_revision != public.knowledge_map_revision
        or row.question_id != public.question_id
        or row.semantic_identity != novelty.semantic_identity
        or row.target_formal_concept_id != public.target_formal_concept_id
        or row.target_claim_id != public.target_claim_id
        or row.policy_revision != public.policy_revision
    ):
        raise _error("ASSESSMENT_UNAVAILABLE")
    return StoredAssessment(
        assessment_revision=row.assessment_revision,
        study_session_id=row.study_session_id,
        knowledge_map_revision=row.knowledge_map_revision,
        question_id=row.question_id,
        semantic_identity=row.semantic_identity,
        target_formal_concept_id=row.target_formal_concept_id,
        target_claim_id=row.target_claim_id,
        public_document=public,
        private_answer_document=documents.private_answer_document,
        generation_provenance=provenance,
        semantic_novelty=novelty,
        policy_revision=row.policy_revision,
        created_at=row.created_at,
    )


def used_question_ids(
    learner: TrustedLearner,
    study_session_id: UUID,
    target_claim_id: str,
    *,
    dsn: str | None = None,
) -> frozenset[str]:
    """回傳同一owning StudySession / Claim已正式儲存的question identities。"""

    if (
        not isinstance(study_session_id, UUID)
        or not isinstance(target_claim_id, str)
        or re.fullmatch(_CLAIM_ID, target_claim_id) is None
    ):
        raise _error("ASSESSMENT_UNAVAILABLE")
    try:
        learner_id = _learner_id(learner)
        with database_session(dsn) as session:
            study_session = _read_stored_row(
                session, learner_id, study_session_id
            )
            _validate_binding(session, study_session)
            return frozenset(
                session.scalars(
                    select(Assessment.question_id).where(
                        Assessment.study_session_id == study_session_id,
                        Assessment.target_claim_id == target_claim_id,
                    )
                )
            )
    except AssessmentError:
        raise
    except (StudySessionError, MapContextError):
        raise _error("ASSESSMENT_UNAVAILABLE") from None
    except (DatabaseConfigurationError, SQLAlchemyError, TypeError, ValueError):
        raise _error("ASSESSMENT_STORAGE_FAILED") from None


def used_question_keys(
    learner: TrustedLearner,
    study_session_id: UUID,
    *,
    dsn: str | None = None,
) -> frozenset[str]:
    """回傳同一StudySession所有question IDs與跨Claim semantic keys。"""

    if not isinstance(study_session_id, UUID):
        raise _error("ASSESSMENT_UNAVAILABLE")
    try:
        learner_id = _learner_id(learner)
        with database_session(dsn) as session:
            study_session = _read_stored_row(
                session, learner_id, study_session_id
            )
            _validate_binding(session, study_session)
            keys: set[str] = set()
            for question_id, public_document in session.execute(
                select(Assessment.question_id, Assessment.public_document).where(
                    Assessment.study_session_id == study_session_id
                )
            ):
                public = PublicAssessmentDocument.model_validate(public_document)
                keys.add(question_id)
                keys.add(question_reuse_key(public))
            return frozenset(keys)
    except AssessmentError:
        raise
    except (StudySessionError, MapContextError):
        raise _error("ASSESSMENT_UNAVAILABLE") from None
    except (
        DatabaseConfigurationError,
        SQLAlchemyError,
        ValidationError,
        TypeError,
        ValueError,
    ):
        raise _error("ASSESSMENT_STORAGE_FAILED") from None


def used_semantic_novelties(
    learner: TrustedLearner,
    study_session_id: UUID,
    *,
    dsn: str | None = None,
) -> tuple[AssessmentSemanticNovelty, ...]:
    """回傳同一 StudySession 已發布題目的 private semantic evidence。"""

    if not isinstance(study_session_id, UUID):
        raise _error("ASSESSMENT_UNAVAILABLE")
    try:
        learner_id = _learner_id(learner)
        with database_session(dsn) as session:
            study_session = _read_stored_row(
                session, learner_id, study_session_id
            )
            _validate_binding(session, study_session)
            return tuple(
                _stored_assessment(row).semantic_novelty
                for row in session.scalars(
                    select(Assessment)
                    .where(Assessment.study_session_id == study_session_id)
                    .order_by(Assessment.created_at, Assessment.assessment_revision)
                )
            )
    except AssessmentError:
        raise
    except (StudySessionError, MapContextError):
        raise _error("ASSESSMENT_UNAVAILABLE") from None
    except (
        DatabaseConfigurationError,
        SQLAlchemyError,
        ValidationError,
        TypeError,
        ValueError,
    ):
        raise _error("ASSESSMENT_STORAGE_FAILED") from None


def store_assessment(
    learner: TrustedLearner,
    public_document: object,
    private_answer_document: object,
    *,
    generation_provenance: object | None = None,
    semantic_novelty: object | None = None,
    require_new: bool = False,
    dsn: str | None = None,
) -> StoredAssessment:
    """驗證 active StudySession 與 Map grounding 後 immutable 儲存 Assessment。"""

    if type(require_new) is not bool:
        raise _error("ASSESSMENT_DOCUMENT_INVALID")
    documents = validate_assessment_documents(
        public_document, private_answer_document
    )
    checked_provenance = (
        None
        if generation_provenance is None
        else validate_assessment_generation_provenance(
            generation_provenance, documents
        )
    )
    checked_novelty = (
        build_assessment_semantic_novelty(
            documents,
            comparison_policy_revision="normalized-exact-focus/v1",
            verifier_model_id="deterministic-normalization",
            verifier_revision="normalized-exact-focus/v1",
            compared_semantic_identities=[],
            maximum_equivalence_score=None,
            runtime_binding_sha256="0" * 64,
        )
        if semantic_novelty is None
        else validate_assessment_semantic_novelty(
            semantic_novelty, documents
        )
    )
    public = documents.public_document
    try:
        learner_id = _learner_id(learner)
        study_session_id = UUID(public.study_session_id)
        with database_session(dsn) as session:
            study_session = _read_stored_row(
                session, learner_id, study_session_id, for_update=True
            )
            context = _validate_binding(session, study_session)
            stored_session = _stored_session(study_session)
            if (
                stored_session.status != "active"
                or stored_session.current_formal_concept_id is None
                or public.knowledge_map_revision
                != stored_session.knowledge_map_revision
                or public.target_formal_concept_id
                != stored_session.current_formal_concept_id
            ):
                raise _error("ASSESSMENT_BINDING_INVALID")
            claim_evidence = _target_claim_evidence(
                context,
                public.target_formal_concept_id,
                public.target_claim_id,
            )
            if claim_evidence is None or not set(public.source_evidence_ids) <= claim_evidence:
                raise _error("ASSESSMENT_BINDING_INVALID")
            inserted_revision = session.scalar(
                insert(Assessment)
                .values(
                    assessment_revision=public.assessment_revision,
                    study_session_id=study_session_id,
                    knowledge_map_revision=public.knowledge_map_revision,
                    question_id=public.question_id,
                    semantic_identity=checked_novelty.semantic_identity,
                    target_formal_concept_id=public.target_formal_concept_id,
                    target_claim_id=public.target_claim_id,
                    public_document=public.model_dump(mode="json", by_alias=True),
                    private_answer_document=(
                        documents.private_answer_document.model_dump(
                            mode="json", by_alias=True
                        )
                    ),
                    generation_provenance=(
                        null()
                        if checked_provenance is None
                        else checked_provenance.model_dump(
                            mode="json", by_alias=True
                        )
                    ),
                    semantic_novelty=checked_novelty.model_dump(
                        mode="json", by_alias=True
                    ),
                    policy_revision=public.policy_revision,
                    created_at=datetime.now(UTC),
                )
                .on_conflict_do_nothing()
                .returning(Assessment.assessment_revision)
            )
            if require_new and inserted_revision is None:
                raise _error("ASSESSMENT_NO_NEW_ITEM")
            row = session.scalar(
                select(Assessment).where(
                    Assessment.assessment_revision == public.assessment_revision
                )
            )
            if row is None:
                raise _error("ASSESSMENT_CONFLICT")
            stored = _stored_assessment(row)
            if (
                stored.public_document != public
                or stored.private_answer_document
                != documents.private_answer_document
                or stored.generation_provenance != checked_provenance
                or stored.semantic_novelty != checked_novelty
            ):
                raise _error("ASSESSMENT_CONFLICT")
            return stored
    except AssessmentError:
        raise
    except (StudySessionError, MapContextError):
        raise _error("ASSESSMENT_BINDING_INVALID") from None
    except (DatabaseConfigurationError, SQLAlchemyError, TypeError, ValueError):
        raise _error("ASSESSMENT_STORAGE_FAILED") from None


def read_assessment(
    learner: TrustedLearner,
    assessment_revision: str,
    *,
    dsn: str | None = None,
) -> StoredAssessment:
    """只讓 owning learner 讀取完整 server-side Assessment。"""

    if not isinstance(assessment_revision, str) or re.fullmatch(
        _ASSESSMENT_ID, assessment_revision
    ) is None:
        raise _error("ASSESSMENT_UNAVAILABLE")
    try:
        learner_id = _learner_id(learner)
        with database_session(dsn) as session:
            row = session.scalar(
                select(Assessment).where(
                    Assessment.assessment_revision == assessment_revision
                )
            )
            if row is None:
                raise _error("ASSESSMENT_UNAVAILABLE")
            study_session = _read_stored_row(
                session, learner_id, row.study_session_id
            )
            context = _validate_binding(session, study_session)
            stored = _stored_assessment(row)
            claim_evidence = _target_claim_evidence(
                context,
                stored.target_formal_concept_id,
                stored.target_claim_id,
            )
            if claim_evidence is None or not set(
                stored.public_document.source_evidence_ids
            ) <= claim_evidence:
                raise _error("ASSESSMENT_UNAVAILABLE")
            return stored
    except AssessmentError:
        raise
    except (StudySessionError, MapContextError):
        raise _error("ASSESSMENT_UNAVAILABLE") from None
    except (DatabaseConfigurationError, SQLAlchemyError, TypeError, ValueError):
        raise _error("ASSESSMENT_STORAGE_FAILED") from None


def project_public_assessment(
    assessment: AssessmentDocuments | StoredAssessment,
) -> dict[str, object]:
    """只輸出 strict public document，永不投影 private answer。"""

    if isinstance(assessment, AssessmentDocuments):
        public = assessment.public_document
        private = assessment.private_answer_document
    elif isinstance(assessment, StoredAssessment):
        public = assessment.public_document
        private = assessment.private_answer_document
    else:
        raise _error("ASSESSMENT_DOCUMENT_INVALID")
    validated = validate_assessment_documents(
        public.model_dump(mode="json", by_alias=True),
        private.model_dump(mode="json", by_alias=True),
    )
    return validated.public_document.model_dump(mode="json", by_alias=True)
