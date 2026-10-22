from __future__ import annotations

from datetime import date as date_type, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, StrictInt


class _Closed(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class ApiErrorView(_Closed):
    schema_: Literal["api-error/v1"] = Field(alias="schema")
    request_id: UUID
    reason_code: str
    retryable: bool
    message: Literal["Request could not be completed."]


class AccountCredentials(_Closed):
    email: EmailStr = Field(max_length=254)
    password: str = Field(min_length=15, max_length=128, repr=False)


class LearnerIdentityView(_Closed):
    schema_: Literal["learner-identity/v1"] = Field(
        default="learner-identity/v1", alias="schema"
    )
    learner_id: UUID


class LearnerProfileView(_Closed):
    schema_: Literal["learner-profile/v1"] = Field(alias="schema")
    display_name: str | None


class LearnerProfileUpdate(_Closed):
    schema_: Literal["learner-profile-update/v1"] = Field(alias="schema")
    # 長度與字元在正規化後由 learner_profile 核對；這裡只限制請求大小。
    display_name: str | None = Field(max_length=200)


class PasswordChange(_Closed):
    schema_: Literal["password-change/v1"] = Field(alias="schema")
    # 目前密碼只限制大小，錯誤一律回 CURRENT_PASSWORD_INVALID；新密碼沿用註冊規則。
    current_password: str = Field(min_length=1, max_length=128, repr=False)
    new_password: str = Field(min_length=15, max_length=128, repr=False)


class AccountSessionsView(_Closed):
    schema_: Literal["account-sessions/v1"] = Field(
        default="account-sessions/v1", alias="schema"
    )
    # 本人仍有效的登入數，含目前這個。
    active_sessions: int


class LibrarySearchMaterialView(_Closed):
    material_id: UUID
    display_name: str
    run_id: UUID | None
    knowledge_structure_revision: str | None


class LibrarySearchConceptView(_Closed):
    material_id: UUID
    material_name: str
    run_id: UUID
    knowledge_structure_revision: str
    concept_id: str
    label: str
    claim_count: int


class LibrarySearchView(_Closed):
    schema_: Literal["library-search/v1"] = Field(alias="schema")
    query: str
    materials: list[LibrarySearchMaterialView]
    concepts: list[LibrarySearchConceptView]
    concepts_truncated: bool


class MaterialOutputBindingView(_Closed):
    schema_: Literal["material-run-output-binding/v1"] = Field(alias="schema")
    knowledge_structure_revision: str
    page_count: int


class MaterialDiscardView(_Closed):
    schema_: Literal["material-discard/v1"] = Field(
        default="material-discard/v1", alias="schema"
    )
    material_id: UUID
    state: Literal["removing", "removed"]


class MaterialProcessingRunView(_Closed):
    analysis_saved: bool = False
    base_revision: str | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    source_names: list[str] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    schema_: Literal["material-processing-run/v1"] = Field(alias="schema")
    input_source_set_id: UUID | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    run_id: UUID
    material_id: UUID
    source_artifact_id: UUID
    status: Literal["pending", "running", "succeeded", "partial", "failed", "cancelled"]
    progress_stage: Literal[
        "queued",
        "evidence",
        "semantics",
        "review",
        "indexing",
        "publishing",
        "completed",
    ]
    completed_pages: int
    total_pages: int | None
    completed_units: int | None = None
    total_units: int | None = None
    output_binding: MaterialOutputBindingView | None
    error_code: str | None
    cancel_requested_at: datetime | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None


class MaterialAttemptView(_Closed):
    base_revision: str | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    run_id: UUID
    status: Literal["pending", "running", "succeeded", "partial", "failed", "cancelled"]
    progress_stage: Literal[
        "queued",
        "evidence",
        "semantics",
        "review",
        "indexing",
        "publishing",
        "completed",
    ]
    completed_pages: int
    total_pages: int | None
    error_code: str | None
    cancel_requested_at: datetime | None
    created_at: datetime


class MaterialStructureLink(_Closed):
    base_revision: str | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    run_id: UUID
    knowledge_structure_revision: str
    created_at: datetime
    status: Literal["succeeded", "partial"]


class StudySessionLink(_Closed):
    study_session_id: UUID
    knowledge_structure_revision: str
    run_id: UUID
    current_concept_id: str | None
    status: Literal["active", "no_safe", "completed"]
    started_at: datetime


class SourceView(_Closed):
    origin: Literal["upload", "research"] = "upload"
    included: bool = False
    source_id: UUID
    normalization_id: UUID
    original_artifact_id: UUID
    original_name: str
    media_type: str
    status: Literal["pending", "running", "ready", "failed"]
    normalized_artifact_id: UUID | None
    page_count: int | None
    analysis_page_count: int | None = None
    analysis_mode: Literal["full", "selected"] = "full"
    error_code: str | None


class MaterialLibraryItem(_Closed):
    head_revision: str | None = None
    source_count: int = 1
    schema_: Literal["material-library-item/v1"] = Field(alias="schema")
    source: SourceView | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    material_id: UUID
    source_artifact_id: UUID | None
    display_name: str
    size_bytes: int
    created_at: datetime
    latest_attempt: MaterialAttemptView | None
    available_structures: list[MaterialStructureLink]
    study_sessions: list[StudySessionLink]


class MaterialRename(_Closed):
    schema_: Literal["material-rename/v1"] = Field(alias="schema")
    display_name: str


class MaterialLibraryView(_Closed):
    schema_: Literal["material-library/v1"] = Field(
        default="material-library/v1", alias="schema"
    )
    materials: list[MaterialLibraryItem]


class SourceLocatorView(_Closed):
    page: int
    block_id: str
    region: list[float] = Field(min_length=4, max_length=4)


class EvidenceView(_Closed):
    source_id: str | None = Field(default=None, exclude_if=lambda value: value is None)
    source_name: str | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    normalized_page: int | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    evidence_id: str
    page_ref: str
    page: int
    block_order: int
    kind: str
    source: Literal["native_text", "unlimited_ocr"]
    source_locator: SourceLocatorView
    quote: str


class ClaimView(_Closed):
    claim_id: str
    text: str
    evidence: list[EvidenceView]


class ConceptView(_Closed):
    concept_id: str
    label: str
    aliases: list[str]
    claims: list[ClaimView]


class RelationView(_Closed):
    relation_id: str
    source_concept_id: str
    target_concept_id: str
    type: Literal["prerequisite", "part_of", "application", "example", "contrast"]
    learner_reason: str
    evidence_refs: list[str]


class SectionView(_Closed):
    section_id: str
    title: str
    order: int
    heading_evidence_id: str | None
    concept_ids: list[str]


class DocumentTreeView(_Closed):
    material_id: str
    sections: list[SectionView]


class LearningPathStepView(_Closed):
    position: int
    concept_id: str
    reason: Literal["document_order", "prerequisite"]


class StatusView(_Closed):
    processing: Literal["succeeded", "partial", "failed"]
    quality: Literal["accepted", "needs_review"]
    decision: Literal["retain", "review", "reject"]
    reason_codes: list[str]


class ExcludedPageView(_Closed):
    page_ref: str
    page: int
    stage: Literal["evidence"]
    reason_code: str


class KnowledgeStructureView(_Closed):
    schema_: Literal["knowledge-structure-view/v1"] = Field(alias="schema")
    source_resolver: str
    material_id: str
    knowledge_structure_revision: str
    status: StatusView
    document_tree: DocumentTreeView
    concepts: list[ConceptView]
    relations: list[RelationView]
    initial_learning_path: list[LearningPathStepView]
    excluded_pages: list[ExcludedPageView]


class ConceptSummaryView(_Closed):
    concept_id: str
    label: str
    aliases: list[str]
    claim_count: int


class OutlineRelationView(_Closed):
    relation_id: str
    source_concept_id: str
    target_concept_id: str
    type: Literal["prerequisite", "part_of", "application", "example", "contrast"]


class KnowledgeOutlineView(_Closed):
    schema_: Literal["knowledge-outline/v1"] = Field(alias="schema")
    material_id: UUID
    knowledge_structure_revision: str
    run_id: UUID
    source_resolver: str
    status: StatusView
    concepts: list[ConceptSummaryView]
    relations: list[OutlineRelationView]
    section_count: int
    initial_learning_path: list[LearningPathStepView]
    excluded_pages: list[ExcludedPageView]


class KnowledgeRegionView(_Closed):
    region_id: str
    label: str
    source_id: UUID | None
    concept_ids: list[str]
    # 章節分區時所屬的島群（來源檔分區）；單層呈現時為 None。
    group_id: str | None


class KnowledgeRegionGroupView(_Closed):
    group_id: str
    label: str
    source_id: UUID | None


class KnowledgeRegionsView(_Closed):
    schema_: Literal["knowledge-regions/v1"] = Field(alias="schema")
    material_id: UUID
    knowledge_structure_revision: str
    basis: Literal["chapters", "source", "learning_order"]
    regions: list[KnowledgeRegionView]
    groups: list[KnowledgeRegionGroupView]
    # 部分章節窗未通過驗證，該部分沿用來源檔分區。
    partial: bool


class LearningActivityDayView(_Closed):
    date: date_type
    answers: int
    correct: int
    card_reviews: int
    listened_seconds: int
    active: bool


class LearningXpSources(_Closed):
    answers: int
    concepts: int
    cards: int
    podcast: int


class LearningXpView(_Closed):
    total: int
    today: int
    level: int
    # 本級已得 XP 與升到下一級所需 XP。
    level_xp: int
    level_target: int
    sources: LearningXpSources


class LearningQuestView(_Closed):
    kind: Literal["mark_cards", "continue_study", "finish_podcast_episode"]
    progress: int
    target: int
    done: bool
    material_id: UUID | None
    study_session_id: UUID | None
    podcast_id: UUID | None
    episode_index: int | None


class LearningBadgeView(_Closed):
    badge_id: Literal[
        "first_answer", "answers_100", "answers_300", "answers_1000", "answers_5000",
        "first_concept", "concepts_25", "concepts_100", "concepts_300", "concepts_1000",
        "first_material", "materials_3", "materials_10", "materials_25",
        "cards_100", "cards_500", "cards_2000", "cards_5000",
        "listen_30_minutes", "listen_180_minutes", "listen_600_minutes", "listen_1800_minutes",
        "streak_3", "streak_7", "streak_30", "streak_100", "streak_365",
        "level_5", "level_10", "level_15", "level_20", "level_30",
        "perfect_set_1", "perfect_set_10", "perfect_set_30", "perfect_set_100",
        "days_7", "days_30", "days_100", "days_200", "days_365",
        "first_set", "sets_10", "sets_50", "sets_200",
        "first_episode", "episodes_10", "episodes_50",
        "combo_10", "combo_25", "combo_50", "comeback_20", "daily_50", "daily_100",
        "explorer_5", "explorer_10", "night_owl", "early_bird",
        "weekend", "return", "curious_10", "curious_50", "researcher", "creator", "corrected_5",
        "first_try_material", "perfect_day", "all_round_day", "thousand_pages", "revisit",
        "xp_day_300", "never_give_up", "binge_3", "three_islands", "new_year",
    ]
    earned: bool
    # 取得日期（台灣日期）；推導不出日期時為 null。
    earned_on: date_type | None
    progress: int
    target: int


class LearningBadgesView(_Closed):
    rule_version: Literal["badge-rules/v6"]
    items: list[LearningBadgeView]


class LearningActivityView(_Closed):
    schema_: Literal["learning-activity/v3"] = Field(alias="schema")
    timezone: Literal["Asia/Taipei"]
    rule_version: Literal["xp-rules/v3"]
    today: date_type
    streak_days: int
    active_today: bool
    total_answers: int
    total_correct: int
    xp: LearningXpView
    quests: list[LearningQuestView]
    badges: LearningBadgesView


class LearningRhythmView(_Closed):
    schema_: Literal["learning-rhythm/v1"] = Field(alias="schema")
    timezone: Literal["Asia/Taipei"]
    today: date_type
    period: Literal["week", "month", "quarter", "year"]
    offset: int
    start: date_type
    end: date_type
    # 最早有學習紀錄的日期；沒有任何紀錄時為 null。
    first_day: date_type | None
    streak_days: int
    days: list[LearningActivityDayView]


class KnowledgeConceptsView(_Closed):
    schema_: Literal["knowledge-concepts/v1"] = Field(alias="schema")
    material_id: UUID
    knowledge_structure_revision: str
    requested_concept_ids: list[str]
    source_resolver: str
    status: StatusView
    concepts: list[ConceptView]
    relations: list[RelationView]
    relation_evidence: list[EvidenceView]


class KnowledgeSearchView(_Closed):
    schema_: Literal["knowledge-search/v1"] = Field(alias="schema")
    material_id: UUID
    knowledge_structure_revision: str
    concept_ids: list[str]


class CardSetCreate(_Closed):
    schema_: Literal["card-set-create/v1"] = Field(alias="schema")
    knowledge_structure_revision: str
    name: str = Field(min_length=1, max_length=200)
    concept_ids: list[str] = Field(min_length=1)


class CardGenerationView(_Closed):
    status: Literal["pending", "running", "ready", "failed"]
    stage: Literal["preparing", "compacting", "designing", "checking", "complete"]
    completed: int
    total: int
    error_code: str | None
    retained_original: int
    cached: int


class CardSetSummary(_Closed):
    card_set_id: UUID
    material_id: UUID
    material_name: str
    knowledge_structure_revision: str
    name: str
    card_count: int | None
    generation: CardGenerationView
    version: int
    created_at: datetime
    is_current_revision: bool
    moved_to_card_set_id: UUID | None = None


class CardSetUpdate(_Closed):
    schema_: Literal["card-set-update/v1"] = Field(alias="schema")
    name: str = Field(min_length=1, max_length=200)
    expected_version: int = Field(ge=1, strict=True)
    concept_ids: list[str] | None = Field(default=None, min_length=1)


class ConceptCardView(_Closed):
    concept_id: str
    label: str
    claims: list[ClaimView]


class CardCountSave(_Closed):
    expected_version: int = Field(ge=1, strict=True)
    card_count: int | None = Field(default=None, ge=1, le=1_000_000, strict=True)
    concept_counts: dict[str, StrictInt] = Field(default_factory=dict)
    # 分頁規則更新時須同步此政策與前端，並以 migration 清除舊張數。
    layout_policy: Literal["visual-concept-card/v4"]


class CardTextEdit(_Closed):
    source: str
    text: str


class CardSetView(CardSetSummary):
    schema_: Literal["card-set/v1"] = Field(alias="schema")
    source_resolver: str
    status: StatusView
    excluded_pages: list[ExcludedPageView]
    cards: list[ConceptCardView]
    display_edits: dict[str, list[CardTextEdit]]


class PodcastProgressUpdate(_Closed):
    schema_: Literal["podcast-progress-update/v1"] = Field(alias="schema")
    episode_index: int = Field(ge=0, le=1000, strict=True)
    position_seconds: float = Field(ge=0, le=86400, allow_inf_nan=False)
    # 距上次回報實際播放的秒數；伺服器只採計不超過實際經過時間的部分。
    listened_seconds: int = Field(ge=0, le=3600, strict=True)
    ended: bool = False
    # 舊版瀏覽器保存的位置，只在伺服器沒有紀錄時採用。
    legacy: bool = False


class PodcastProgressCurrent(_Closed):
    episode_index: int
    position_seconds: float
    updated_at: datetime


class PodcastEpisodeProgressView(_Closed):
    episode_index: int
    position_seconds: float
    completed: bool


class PodcastProgressView(_Closed):
    schema_: Literal["podcast-progress/v1"] = Field(alias="schema")
    podcast_id: UUID
    current: PodcastProgressCurrent | None
    episodes: list[PodcastEpisodeProgressView]
    credited_seconds: int


class CardReviewSubmit(_Closed):
    schema_: Literal["card-review-submit/v1"] = Field(alias="schema")
    concept_id: str = Field(pattern=r"^concept:sha256:[0-9a-f]{64}$")
    # forgot／remembered 即畫面上的不熟／熟悉。
    rating: Literal["forgot", "remembered"]
    # 送出前看到的標記次數；不一致代表另一個分頁已標記過。
    expected_review_count: int = Field(ge=0, le=1_000_000, strict=True)


class CardReviewView(_Closed):
    schema_: Literal["card-review/v2"] = Field(alias="schema")
    card_set_id: UUID
    concept_id: str
    rating: Literal["forgot", "remembered"]
    review_count: int
    reviewed_at: datetime


class CardReviewSetCount(_Closed):
    card_set_id: UUID
    card_count: int
    forgot_count: int
    remembered_count: int


class CardReviewMark(_Closed):
    card_set_id: UUID
    concept_id: str
    rating: Literal["forgot", "remembered"]
    review_count: int


class CardReviewItem(_Closed):
    card_set_id: UUID
    card_set_name: str
    material_id: UUID
    material_name: str
    knowledge_structure_revision: str
    source_resolver: str
    review_count: int
    card: ConceptCardView


class CardReviewsView(_Closed):
    schema_: Literal["card-reviews/v2"] = Field(alias="schema")
    forgot_count: int
    sets: list[CardReviewSetCount]
    # 只有指定卡組時才列出該卡組已標記的卡片。
    marks: list[CardReviewMark]
    cards: list[CardReviewItem]


class CardDesignSources(_Closed):
    claim_ids: list[str]
    relation_ids: list[str]
    evidence_ids: list[str]


class CardDesignText(CardDesignSources):
    text: str


class CardDesignBlock(CardDesignText):
    heading: str
    icon: str


class CardDesignLink(CardDesignSources):
    source: int
    target: int
    label: str


class CardDesignPage(_Closed):
    title: str
    layout: Literal["focus", "compare", "flow", "grid"]
    takeaway: CardDesignText
    blocks: list[CardDesignBlock]
    links: list[CardDesignLink]


class CardDesignView(_Closed):
    schema_: Literal["concept-card-design/v1"] = Field(alias="schema")
    revision: str
    pages: list[CardDesignPage]
    details_claim_ids: list[str]
    provider: dict[str, str]


class StudyCardView(ConceptCardView):
    display_edits: list[CardTextEdit]
    design: CardDesignView | None = None


class CardContextView(_Closed):
    source_resolver: str
    status: StatusView
    concepts: list[ConceptView]
    relations: list[RelationView]
    relation_evidence: list[EvidenceView]


class CardBatchView(_Closed):
    schema_: Literal["card-batch/v1"] = Field(alias="schema")
    card_set_id: UUID
    version: int
    offset: int
    total: int
    cards: list[StudyCardView]
    context: CardContextView


class CardNavigationView(_Closed):
    concept_id: str
    label: str
    page_count: int | None


class CardStudyView(CardSetSummary):
    schema_: Literal["card-study/v1"] = Field(alias="schema")
    source_resolver: str
    status: StatusView
    navigation: list[CardNavigationView]
    batch: CardBatchView | None


class CardSetListView(_Closed):
    schema_: Literal["card-set-list/v1"] = Field(alias="schema")
    card_sets: list[CardSetSummary]


class CardSetDeletedView(_Closed):
    schema_: Literal["card-set-deleted/v1"] = Field(alias="schema")
    card_set_id: UUID


class StudySessionCreate(_Closed):
    """Ensure a persistent state; current_concept_id applies only to its initial creation."""

    schema_: Literal["study-session-create/v1"] = Field(alias="schema")
    material_id: UUID
    knowledge_structure_revision: str
    current_concept_id: str | None = None


class StudySessionFocus(_Closed):
    schema_: Literal["study-session-focus/v1"] = Field(alias="schema")
    current_concept_id: str


class StudySessionView(_Closed):
    schema_: Literal["study-session/v1"] = Field(alias="schema")
    study_session_id: UUID
    material_id: UUID
    knowledge_structure_revision: str
    current_concept_id: str | None
    deferred_concept_ids: list[str]
    no_safe_claim_ids: list[str]
    status: Literal["active", "no_safe", "completed"]
    started_at: datetime
    completed_at: datetime | None
    event_watermark: int


class AssessmentOptionView(_Closed):
    option_id: str
    text: str


class AssessmentView(_Closed):
    schema_: Literal["single-choice-assessment/v1"] = Field(alias="schema")
    assessment_revision: str
    study_session_id: UUID
    knowledge_structure_revision: str
    question_id: str
    target_concept_id: str
    target_claim_id: str
    source_evidence_ids: list[str]
    question_type: Literal["single_choice"]
    prompt: str
    options: list[AssessmentOptionView]


class AnswerFeedbackView(_Closed):
    schema_: Literal["answer-feedback/v1"] = Field(alias="schema")
    answer_event_id: UUID
    study_session_id: UUID
    assessment_revision: str
    question_id: str
    selected_option_id: str
    is_correct: bool
    rationale: str
    source_evidence_ids: list[str]
    event_number: int
    created_at: datetime


class StudyResumeView(_Closed):
    schema_: Literal["study-resume/v1"] = Field(
        default="study-resume/v1", alias="schema"
    )
    session: StudySessionView
    run_id: UUID
    source_artifact_id: UUID
    knowledge_structure: KnowledgeStructureView
    progress: LearnerProgressView
    assessment_sets: list[AssessmentSetSummary]
    selected_set_id: UUID | None
    selected_set: AssessmentSetView | None


class AssessmentSetCreate(_Closed):
    schema_: Literal["assessment-set-create/v1"] = Field(alias="schema")
    target_concept_id: str


class AssessmentSetAction(_Closed):
    schema_: Literal["assessment-set-action/v1"] = Field(alias="schema")
    expected_set_version: int = Field(ge=1, strict=True)


class AssessmentSetAnswer(_Closed):
    assessment_revision: str
    question_id: str
    selected_option_id: str


class AssessmentSetSubmission(_Closed):
    schema_: Literal["assessment-set-submission/v1"] = Field(alias="schema")
    expected_set_version: int = Field(ge=1, strict=True)
    answers: list[AssessmentSetAnswer] = Field(min_length=1)


class AssessmentPlanTarget(_Closed):
    claim_id: str
    covered_claim_ids: list[str]
    reason: Literal["distinct_grounded_point"]


class AssessmentPlanExcluded(_Closed):
    claim_id: str
    reason: Literal["no_content_evidence"]


class AssessmentPlanView(_Closed):
    schema_: Literal["assessment-plan/v1"] = Field(alias="schema")
    study_session_id: UUID
    knowledge_structure_revision: str
    policy: Literal["single-concept-grounded-points/v1"]
    concept_id: str
    point_count: int
    requested_count: int
    targets: list[AssessmentPlanTarget]
    excluded: list[AssessmentPlanExcluded]


class InheritedCheckSource(_Closed):
    study_session_id: UUID
    knowledge_structure_revision: str
    run_id: UUID


class AssessmentCycleSummary(_Closed):
    inherited_from: InheritedCheckSource | None = None
    diagnostic_set_id: UUID
    concept_id: str
    set_version: int
    outcome: Literal["in_progress", "needs_review", "passed", "incomplete"]
    active_set_id: UUID | None
    passed_count: int
    remediation_passed_count: int
    pending_count: int
    unanswered_count: int
    unavailable_count: int


class AssessmentCyclePoint(_Closed):
    claim_id: str
    result: Literal[
        "unavailable",
        "unanswered",
        "diagnostic_pass",
        "needs_review",
        "remediation_pass",
    ]
    latest_answer_event_id: UUID | None
    latest_set_id: UUID | None


class AssessmentCycleView(AssessmentCycleSummary):
    can_create_remediation: bool
    points: list[AssessmentCyclePoint]


class AssessmentSetSummary(_Closed):
    kind: Literal["diagnostic", "remediation"]
    diagnostic_set_id: UUID | None
    set_id: UUID
    target_concept_id: str
    status: Literal[
        "preparing",
        "partial_ready",
        "failed",
        "ready",
        "in_progress",
        "completed",
        "cancelled",
    ]
    set_version: int
    requested_count: int
    published_count: int
    answered_count: int
    passed_count: int
    assessment_revisions: list[str]
    created_at: datetime
    completed_at: datetime | None


class AssessmentSetListView(_Closed):
    schema_: Literal["assessment-set-list/v1"] = Field(alias="schema")
    study_session_id: UUID
    knowledge_structure_revision: str
    active_set_ids: list[UUID]
    sets: list[AssessmentSetSummary]


class AssessmentSetItemView(_Closed):
    ordinal: int
    target_claim_id: str
    state: Literal[
        "pending", "generating", "verified", "published", "failed", "omitted"
    ]
    attempts: int
    failure_reason: str | None
    assessment: AssessmentView | None
    feedback: AnswerFeedbackView | None
    created_at: datetime | None
    can_submit: bool


class AssessmentSetView(AssessmentSetSummary):
    schema_: Literal["assessment-set/v1"] = Field(alias="schema")
    study_session_id: UUID
    material_id: UUID
    knowledge_structure_revision: str
    selection_policy: Literal[
        "single-concept-grounded-points/v1", "needs-review-points/v1"
    ]
    point_count: int
    excluded_count: int
    verified_count: int
    can_retry: bool
    can_publish_partial: bool
    can_complete: bool
    items: list[AssessmentSetItemView]
    cycle: AssessmentCycleView


class GuidanceApply(_Closed):
    schema_: Literal["guidance-apply/v1"] = Field(alias="schema")
    guidance_revision: str = Field(pattern=r"^learner-guidance:sha256:[0-9a-f]{64}$")


class LearnerProgressView(_Closed):
    assessment_cycles: list[AssessmentCycleSummary]
    schema_: Literal["learner-progress/v1"] = Field(alias="schema")
    study_session_id: UUID
    knowledge_structure_revision: str
    event_watermark: int
    current_concept_id: str | None
    deferred_concept_ids: list[str]
    concept_states: list[dict[str, Any]]
    weaknesses: list[dict[str, Any]]
    next_action: dict[str, Any]
    guidance_revision: str


class StudyProgressView(LearnerProgressView):
    schema_: Literal["study-progress/v1"] = Field(alias="schema")
    concept_count: int


class MapConceptStateView(_Closed):
    concept_id: str
    status: Literal["not_started", "in_progress", "needs_review", "completed"]
    attempts: int
    assessable: bool


class MapProgressView(_Closed):
    schema_: Literal["map-progress/v1"] = Field(alias="schema")
    study_session_id: UUID
    knowledge_structure_revision: str
    current_concept_id: str | None
    concept_states: list[MapConceptStateView]
    next_action: dict[str, Any]
    assessment_cycles: list[AssessmentCycleSummary]


class ProgressSummaryView(_Closed):
    """首頁島嶼只需數字與目前概念名稱，不下載完整 outline 與逐概念狀態。"""

    schema_: Literal["progress-summary/v1"] = Field(alias="schema")
    material_id: UUID
    knowledge_structure_revision: str
    study_session_id: UUID | None
    concept_count: int
    completed_count: int
    current_concept_id: str | None
    current_concept_label: str | None


class StudyConceptLabelView(_Closed):
    concept_id: str
    label: str


class StudyNavigationView(_Closed):
    schema_: Literal["study-navigation/v1"] = Field(alias="schema")
    material_id: UUID
    knowledge_structure_revision: str
    run_id: UUID
    source_resolver: str
    status: StatusView
    concept_count: int
    concepts: list[StudyConceptLabelView]
    initial_learning_path: list[LearningPathStepView]


class StudyWorkspaceView(_Closed):
    schema_: Literal["study-workspace/v1"] = Field(alias="schema")
    session: StudySessionView
    run_id: UUID
    source_artifact_id: UUID
    outline: StudyNavigationView
    context: KnowledgeConceptsView | None
    progress: StudyProgressView
    assessment_sets: list[AssessmentSetSummary]
    selected_set_id: UUID | None
    selected_set: AssessmentSetView | None


def project_material_run(run: Any) -> MaterialProcessingRunView:
    from ..storage.analysis_archive import has_analysis_checkpoint

    return MaterialProcessingRunView.model_validate(
        {
            "analysis_saved": (
                run.status == "failed"
                and has_analysis_checkpoint(run.learner_id, run.material_id, run.run_id)
            ),
            "schema": "material-processing-run/v1",
            "input_source_set_id": run.input_source_set_id,
            "base_revision": run.base_revision,
            "source_names": list(run.source_names)
            if run.source_names
            else None,
            "output_binding": (
                {
                    "schema": run.output_binding["schema"],
                    "knowledge_structure_revision": run.output_binding[
                        "knowledge_structure_revision"
                    ],
                    "page_count": run.output_binding["page_count"],
                }
                if run.output_binding is not None
                else None
            ),
            **{
                name: getattr(run, name)
                for name in (
                    "run_id",
                    "material_id",
                    "source_artifact_id",
                    "status",
                    "progress_stage",
                    "completed_pages",
                    "total_pages",
                    "completed_units",
                    "total_units",
                    "error_code",
                    "cancel_requested_at",
                    "created_at",
                    "updated_at",
                    "completed_at",
                )
            },
        }
    )


def project_knowledge_structure(view: dict[str, Any]) -> KnowledgeStructureView:
    """公開學習地圖與 Evidence；內部歸屬和推論資料留在正式 KS。"""
    return KnowledgeStructureView.model_validate(
        {
            **view,
            "concepts": [
                {
                    "concept_id": concept["concept_id"],
                    "label": concept["label"],
                    "aliases": concept["aliases"],
                    "claims": concept["claims"],
                }
                for concept in view["concepts"]
            ],
            "relations": [
                {
                    "relation_id": relation["relation_id"],
                    "source_concept_id": relation["source_concept_id"],
                    "target_concept_id": relation["target_concept_id"],
                    "type": relation["type"],
                    "learner_reason": relation["learner_reason"],
                    "evidence_refs": relation["evidence_refs"],
                }
                for relation in view["relations"]
            ],
        }
    )


def project_study_session(session: Any) -> StudySessionView:
    return StudySessionView.model_validate(
        {
            "schema": "study-session/v1",
            "study_session_id": session.study_session_id,
            "material_id": session.material_id,
            "knowledge_structure_revision": session.knowledge_structure_revision,
            "current_concept_id": session.current_concept_id,
            "deferred_concept_ids": list(session.deferred_concept_ids),
            "no_safe_claim_ids": list(session.no_safe_claim_ids),
            "status": session.status,
            "started_at": session.started_at,
            "completed_at": session.completed_at,
            "event_watermark": session.last_event_number,
        }
    )


def project_learner_progress(progress: Any) -> LearnerProgressView:
    document = progress.model_dump()
    document["schema"] = document.pop("schema_")
    return LearnerProgressView.model_validate(document)


class MaterialDraftCreate(_Closed):
    schema_: Literal["material-draft-create/v1"] = Field(alias="schema")
    display_name: str


class MaterialDraftView(_Closed):
    schema_: Literal["material-draft/v1"] = Field(
        default="material-draft/v1", alias="schema"
    )
    material_id: UUID


class SourceListView(_Closed):
    discard_requested: bool = False
    schema_: Literal["material-sources/v1"] = Field(
        default="material-sources/v1",
        alias="schema",
    )
    material_id: UUID
    sources: list[SourceView]


class RevisionCreate(_Closed):
    schema_: Literal["material-revision-create/v1"] = Field(alias="schema")
    base_revision: str | None = Field(
        default=None,
        pattern=r"^knowledge-structure:sha256:[0-9a-f]{64}$",
    )
    normalization_ids: list[UUID] = Field(min_length=1)


class RevisionCancel(_Closed):
    schema_: Literal["material-revision-cancel/v1"] = Field(alias="schema")
    base_revision: str = Field(pattern=r"^knowledge-structure:sha256:[0-9a-f]{64}$")


class VersionMoveConcept(_Closed):
    from_concept_id: str
    from_label: str
    to_concept_id: str
    to_label: str
    basis: Literal["evidence", "label"]


class VersionMoveUnmapped(_Closed):
    concept_id: str
    label: str
    reason: Literal["split", "ambiguous", "not_found", "merged_unfinished"]


class VersionMoveCardSet(_Closed):
    card_set_id: UUID
    name: str
    from_revision: str
    concept_count: int
    target_count: int
    carried_reviews: int
    reused_designs: int
    movable: bool
    mapped: list[VersionMoveConcept]
    unmapped: list[VersionMoveUnmapped]


class VersionMoveCarried(_Closed):
    concept_id: str
    label: str
    from_labels: list[str]


class VersionMoveFocus(_Closed):
    concept_id: str
    label: str


class VersionMoveStudy(_Closed):
    study_session_id: UUID
    from_revision: str
    target_study_session_id: UUID | None
    completed_count: int
    carried: list[VersionMoveCarried]
    not_carried: list[VersionMoveUnmapped]
    current_concept: VersionMoveFocus | None


class VersionMovePreviewView(_Closed):
    schema_: Literal["version-move-preview/v1"] = Field(alias="schema")
    material_id: UUID
    to_revision: str | None
    preview_digest: str | None
    card_sets: list[VersionMoveCardSet]
    study: VersionMoveStudy | None
    kept_podcasts: int


class VersionMoveCreate(_Closed):
    schema_: Literal["version-move-create/v1"] = Field(alias="schema")
    to_revision: str = Field(pattern=r"^knowledge-structure:sha256:[0-9a-f]{64}$")
    card_set_ids: list[UUID] = Field(max_length=500)
    include_study: bool = Field(strict=True)
    preview_digest: str = Field(pattern=r"^version-move:sha256:[0-9a-f]{64}$")


class VersionMoveCardSetResult(_Closed):
    from_card_set_id: UUID
    card_set_id: UUID
    status: Literal["pending", "ready"]


class VersionMoveView(_Closed):
    schema_: Literal["version-move/v1"] = Field(alias="schema")
    move_id: UUID
    material_id: UUID
    to_revision: str
    card_sets: list[VersionMoveCardSetResult]
    study_session_id: UUID | None
    created_at: datetime


class MaterialReviewCreate(_Closed):
    schema_: Literal["material-review-create/v1"] = Field(alias="schema")
    base_revision: str = Field(pattern=r"^knowledge-structure:sha256:[0-9a-f]{64}$")


class FormatCapability(_Closed):
    extension: str
    media_type: str
    max_bytes: int


class SourceCapabilities(_Closed):
    schema_: Literal["source-capabilities/v1"] = Field(
        default="source-capabilities/v1",
        alias="schema",
    )
    formats: list[FormatCapability]


class PdfOrigin(_Closed):
    original_page: int = Field(ge=1)


class SlideOrigin(_Closed):
    original_slide_number: int = Field(ge=1)
    slide_id: str
    hidden: bool


class DocumentOrigin(_Closed):
    document_part: Literal["word/document.xml"]
    paragraph: int = Field(ge=1)


class TextOrigin(_Closed):
    line_start: int = Field(ge=1)
    line_end: int = Field(ge=1)


class EvidenceSourceView(_Closed):
    schema_: Literal["evidence-source/v1"] = Field(alias="schema")
    format: Literal["pdf", "docx", "pptx", "doc", "ppt", "txt", "md"]
    original_name: str
    original_url: str
    preview_url: str
    normalized_page: int
    accuracy: Literal["exact", "ambiguous", "unavailable"]
    origin_locators: list[PdfOrigin | SlideOrigin | DocumentOrigin | TextOrigin]
    label: str


class PodcastCreate(_Closed):
    schema_: Literal["podcast-create/v1"] = Field(alias="schema")
    knowledge_structure_revision: str
    name: str = Field(min_length=1, max_length=200)
    concept_ids: list[str] = Field(min_length=1)
    delivery: Literal["solo", "dialogue"]


class PodcastAction(_Closed):
    schema_: Literal["podcast-action/v1"] = Field(alias="schema")
    action: Literal["rename", "retry", "cancel"]
    expected_version: int = Field(ge=1, strict=True)
    name: str | None = Field(default=None, min_length=1, max_length=200)


class PodcastGeneration(_Closed):
    active: bool
    preview_ready: bool
    retryable_videos: bool
    completed_videos: int = Field(ge=0)
    failed_videos: int = Field(ge=0)


class PodcastSummary(_Closed):
    podcast_id: UUID
    material_id: UUID
    material_name: str
    knowledge_structure_revision: str
    name: str
    delivery: Literal["solo", "dialogue"]
    concept_ids: list[str]
    status: Literal["pending", "running", "ready", "failed", "cancelled"]
    error_code: str | None
    version: int
    created_at: datetime
    episode_count: int
    completed_episodes: int
    # 所有分集音訊的總秒數；音訊未全部完成時為 null。
    duration_seconds: float | None
    is_current_revision: bool
    generation: PodcastGeneration


class PodcastClaim(ClaimView):
    concept_id: str
    label: str


class PodcastTurn(_Closed):
    speaker: Literal["host", "guest"]
    text: str


class PodcastSegment(_Closed):
    claim_id: str
    turns: list[PodcastTurn]


class PodcastScript(_Closed):
    segments: list[PodcastSegment]
    provider: str


class PodcastSourceReference(_Closed):
    source_index: int
    evidence_ids: list[str]


class PodcastPart(_Closed):
    text: str
    source_refs: list[PodcastSourceReference]


class PodcastBeatTurn(PodcastTurn):
    parts: list[PodcastPart]


class PodcastBeat(_Closed):
    beat_id: str
    title: str
    turns: list[PodcastBeatTurn]


class PodcastVerdict(_Closed):
    passed: bool
    reason: str


class PodcastReview(_Closed):
    correctness: PodcastVerdict
    teaching_quality: PodcastVerdict


class PodcastBeatScript(_Closed):
    schema_: Literal["podcast-script/v2"] = Field(alias="schema")
    segments: list[PodcastBeat]
    provider: str
    review: PodcastReview | None = None


class PodcastMastering(_Closed):
    policy: Literal["podcast-mastering/v1"]
    integrated_lufs: float
    true_peak_dbtp: float
    loudness_range_lu: float


class PodcastAudio(_Closed):
    mastering: PodcastMastering | None = None
    artifact_id: UUID
    sha256: str
    duration_seconds: float
    provider: str


class PodcastEpisode(_Closed):
    script_sha256: str | None = None
    # 短集名（衍生顯示資料）；舊資料或取名失敗時為 None。
    title: str | None = None
    delivery: Literal["solo", "dialogue"]
    claims: list[PodcastClaim]
    script: PodcastBeatScript | PodcastScript | None
    audio: PodcastAudio | None


class PodcastView(PodcastSummary):
    run_id: UUID
    schema_: Literal["podcast/v1"] = Field(alias="schema")
    source_resolver: str
    source_status: StatusView
    excluded_pages: list[ExcludedPageView]
    episodes: list[PodcastEpisode]


class PodcastListView(_Closed):
    schema_: Literal["podcast-list/v1"] = Field(alias="schema")
    podcasts: list[PodcastSummary]


class PodcastDeletedView(_Closed):
    schema_: Literal["podcast-deleted/v1"] = Field(alias="schema")
    podcast_id: UUID
