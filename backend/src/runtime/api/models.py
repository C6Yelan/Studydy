from __future__ import annotations

from datetime import date as date_type, datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, StrictInt


class _Closed(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class StorageQuotaDetail(_Closed):
    used_bytes: int = Field(ge=0)
    limit_bytes: int = Field(ge=1)


class ApiErrorView(_Closed):
    schema_: Literal["api-error/v1"] = Field(alias="schema")
    request_id: UUID
    reason_code: str
    retryable: bool
    message: Literal["Request could not be completed."]
    # 只有 STORAGE_QUOTA_EXCEEDED 附帶已用與上限，畫面據此說明原因。
    storage: StorageQuotaDetail | None = None


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


class StorageBreakdown(_Closed):
    material_files_bytes: int
    podcast_audio_bytes: int
    podcast_video_bytes: int
    analysis_bytes: int
    reserved_bytes: int


class StorageMaterial(_Closed):
    material_id: UUID
    display_name: str
    used_bytes: int


class AccountStorageView(_Closed):
    breakdown: StorageBreakdown
    largest_materials: list[StorageMaterial]
    schema_: Literal["account-storage/v2"] = Field(alias="schema")
    # 已用含 reserved_bytes：未收齊的分段上傳（宣告大小）與生成中 Podcast／影片的預估額度。
    used_bytes: int = Field(ge=0)
    reserved_bytes: int = Field(ge=0)
    limit_bytes: int = Field(ge=1)
    remaining_bytes: int = Field(ge=0)


class LearnerProfileUpdate(_Closed):
    schema_: Literal["learner-profile-update/v1"] = Field(alias="schema")
    # 長度與字元在正規化後由 learner_profile 核對；這裡只限制請求大小。
    display_name: str | None = Field(max_length=200)


class PasswordChange(_Closed):
    schema_: Literal["password-change/v1"] = Field(alias="schema")
    # 目前密碼只限制大小，錯誤一律回 CURRENT_PASSWORD_INVALID；新密碼沿用註冊規則。
    current_password: str = Field(min_length=1, max_length=128, repr=False)
    new_password: str = Field(min_length=15, max_length=128, repr=False)


class AccountSessionView(_Closed):
    # 只公開 session_id；token 雜湊不離開後端。device_label 為 None 表示未知裝置。
    session_id: UUID
    device_label: str | None
    created_at: datetime
    last_used_at: datetime
    current: bool


class AccountSessionsView(_Closed):
    schema_: Literal["account-sessions/v2"] = Field(
        default="account-sessions/v2", alias="schema"
    )
    # 本人仍有效的登入：目前這個排第一，其餘依最後使用時間由新到舊。
    sessions: list[AccountSessionView]


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


class AddedRunSource(_Closed):
    source_id: UUID
    display_name: str


class StructureCounts(_Closed):
    concepts: int
    relations: int


class StructureChanges(_Closed):
    before: StructureCounts
    after: StructureCounts


class MaterialProcessingRunView(_Closed):
    auto_retry_count: Literal[0, 1] = 0
    auto_retry_reason: str | None = None
    added_sources: list[AddedRunSource] = Field(default_factory=list)
    existing_source_count: int = 0
    structure_changes: StructureChanges | None = None
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


class SourceTranscriptView(_Closed):
    schema_: Literal["source-transcript/v1"] = Field(alias="schema")
    text: str


class SourcePresentation(_Closed):
    display_name: str
    source_kind: Literal["document", "image", "audio", "video", "youtube"]
    duration_seconds: float | None
    image_count: int | None
    transcript_source: Literal["whisper", "uploader_subtitles"] | None
    transcript_display_name: str | None


class SourceView(SourcePresentation):
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


class MaterialSourceTotals(_Closed):
    page_count: int
    image_count: int
    media_seconds: float | None


class MaterialLibraryItem(_Closed):
    source_totals: MaterialSourceTotals
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
    uncertain: bool = False
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
    uncertain: bool = False
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
    # xp-rules/v5：分天關卡與準時達成目標、克服難點。
    goals: int
    difficulties: int


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
        # badge-rules/v8
        "first_goal", "first_stage", "stages_10", "stages_50",
        "first_difficulty", "difficulties_5", "difficulties_20",
        "first_note", "first_annotation", "annotations_10", "annotations_50",
        "upload_image", "upload_audio", "upload_video", "upload_youtube", "first_export", "export_kinds_4",
        "podcast_series", "podcast_hosts_all",
        "all_tools_material", "media_concepts_5", "stage_streak_7",
        "goal_on_time", "all_media", "last_minute", "night_listener", "nemesis", "same_day_learner",
    ]
    earned: bool
    # 取得日期（台灣日期）；推導不出日期時為 null。
    earned_on: date_type | None
    progress: int
    target: int


class LearningBadgesView(_Closed):
    rule_version: Literal["badge-rules/v8"]
    items: list[LearningBadgeView]


class LearningActivityView(_Closed):
    schema_: Literal["learning-activity/v4"] = Field(alias="schema")
    timezone: Literal["Asia/Taipei"]
    rule_version: Literal["xp-rules/v5"]
    today: date_type
    streak_days: int
    active_today: bool
    total_answers: int
    total_correct: int
    xp: LearningXpView
    quests: list[LearningQuestView]
    badges: LearningBadgesView


class CelebrationClaim(_Closed):
    schema_: Literal["celebration-claim/v1"] = Field(alias="schema")


class CelebrationClaimView(CelebrationClaim):
    level: int | None
    badge_ids: list[str]


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
    name: str = Field(min_length=1, max_length=40)
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
    name: str = Field(min_length=1, max_length=40)
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
    correct_option_id: str | None = Field(default=None, exclude_if=lambda value: value is None)
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
    schema_: Literal["assessment-set-list/v2"] = Field(alias="schema")
    study_session_id: UUID
    knowledge_structure_revision: str
    active_set_ids: list[UUID]
    sets: list[AssessmentSetSummary]


class AssessmentSetItemView(_Closed):
    ordinal: int
    target_concept_id: str
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
    schema_: Literal["assessment-set/v2"] = Field(alias="schema")
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
    schema_: Literal["learner-progress/v2"] = Field(alias="schema")
    study_session_id: UUID
    knowledge_structure_revision: str
    learning_path: list[str]
    event_watermark: int
    current_concept_id: str | None
    deferred_concept_ids: list[str]
    concept_states: list[dict[str, Any]]
    weaknesses: list[dict[str, Any]]
    next_action: dict[str, Any]
    guidance_revision: str


class StudyProgressView(LearnerProgressView):
    schema_: Literal["study-progress/v2"] = Field(alias="schema")
    concept_count: int


class MapConceptStateView(_Closed):
    concept_id: str
    status: Literal["not_started", "in_progress", "needs_review", "completed"]
    attempts: int
    assessable: bool


class MapProgressView(_Closed):
    schema_: Literal["map-progress/v2"] = Field(alias="schema")
    study_session_id: UUID
    knowledge_structure_revision: str
    learning_path: list[str]
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
    schema_: Literal["study-workspace/v2"] = Field(alias="schema")
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
            **run.display_summary,
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
                    "auto_retry_count",
                    "auto_retry_reason",
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
                    "uncertain": concept.get("uncertain", False),
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
    movable: bool
    carried: list[VersionMoveCarried]
    already_completed: list[VersionMoveCarried]
    not_carried: list[VersionMoveUnmapped]
    current_concept: VersionMoveFocus | None


class VersionMoveBlocker(_Closed):
    code: Literal["unmapped_cards", "unmapped_progress", "assessment_in_progress", "source_unavailable"]
    count: int = Field(gt=0)


class VersionMovePrevious(_Closed):
    move_id: UUID
    revision: str
    card_set_ids: list[UUID]
    study_session_id: UUID | None


class VersionMoveAutomatic(_Closed):
    status: Literal["pending", "completed", "failed"]
    move_id: UUID | None
    error_code: str | None


class VersionMoveNotice(_Closed):
    schema_: Literal["version-move-notice/v1"] = Field(alias="schema")
    dismissed: bool = Field(strict=True)


class VersionMoveReturn(_Closed):
    schema_: Literal["version-move-return/v1"] = Field(alias="schema")
    expected_move_id: UUID


class VersionMoveReturnView(VersionMovePrevious):
    schema_: Literal["version-move-return/v1"] = Field(alias="schema")


class VersionMovePreviewView(_Closed):
    schema_: Literal["version-move-preview/v2"] = Field(alias="schema")
    automatic_safe: bool
    blocking_reasons: list[VersionMoveBlocker]
    notice_dismissed: bool
    previous: VersionMovePrevious | None
    automatic_move: VersionMoveAutomatic | None
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


class FormatCapability(_Closed):
    extension: str
    media_type: str
    max_bytes: int
    kind: Literal["document", "image", "audio", "video"]


class SourceCapabilities(_Closed):
    schema_: Literal["source-capabilities/v2"] = Field(
        default="source-capabilities/v2",
        alias="schema",
    )
    formats: list[FormatCapability]
    # 單次請求上限；較大的檔案改用分段上傳。圖片為一組最多張數，總大小依圖片格式上限。
    max_request_bytes: int
    max_images: int
    max_media_seconds: int
    youtube: bool


class SourceUploadProgress(_Closed):
    schema_: Literal["source-upload-progress/v1"] = Field(
        default="source-upload-progress/v1", alias="schema"
    )
    received_bytes: int
    source_id: UUID | None


class YoutubeSourceCreate(_Closed):
    schema_: Literal["youtube-source-create/v1"] = Field(alias="schema")
    url: str = Field(min_length=1, max_length=2048)


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


class ImageOrigin(_Closed):
    image_number: int = Field(ge=1)


class MediaOrigin(_Closed):
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(ge=0)


class MediaPlayback(_Closed):
    """音訊（含影片音軌）由私人媒體端點播放；YouTube 用內嵌播放器。"""

    kind: Literal["audio", "youtube"]
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(ge=0)
    url: str | None = Field(default=None, exclude_if=lambda value: value is None)
    video_id: str | None = Field(default=None, exclude_if=lambda value: value is None)


class EvidenceSourceView(SourcePresentation):
    schema_: Literal["evidence-source/v1"] = Field(alias="schema")
    format: Literal[
        "pdf", "docx", "pptx", "doc", "ppt", "txt", "md",
        "images", "audio", "video", "youtube",
    ]
    original_name: str
    original_url: str
    preview_url: str
    normalized_page: int
    accuracy: Literal["exact", "ambiguous", "unavailable"]
    origin_locators: list[
        PdfOrigin | SlideOrigin | DocumentOrigin | TextOrigin | ImageOrigin | MediaOrigin
    ]
    label: str
    playback: MediaPlayback | None = Field(
        default=None, exclude_if=lambda value: value is None
    )


class PodcastCreate(_Closed):
    schema_: Literal["podcast-create/v1"] = Field(alias="schema")
    knowledge_structure_revision: str
    name: str = Field(min_length=1, max_length=40)
    concept_ids: list[str] = Field(min_length=1)
    delivery: Literal["solo", "dialogue"]
    # 勾選才在每集音訊完成後生成影片；預設只有音訊。
    video: bool = Field(default=False, strict=True)


class PodcastAction(_Closed):
    schema_: Literal["podcast-action/v1"] = Field(alias="schema")
    action: Literal["rename", "retry", "cancel"]
    expected_version: int = Field(ge=1, strict=True)
    name: str | None = Field(default=None, min_length=1, max_length=40)


class PodcastGenerationStep(_Closed):
    episode_index: int = Field(ge=0)
    step: Literal["script", "audio", "alignment", "video"]
    status: Literal["waiting", "queued", "running", "completed", "failed", "skipped"]
    started_at: datetime | None
    elapsed_seconds: float | None
    queue_ahead: int | None


class PodcastGeneration(_Closed):
    steps: list[PodcastGenerationStep]
    active: bool
    preview_ready: bool
    retryable_videos: bool
    completed_videos: int = Field(ge=0)
    failed_videos: int = Field(ge=0)
    # 預期會有影片的集數（勾選影片的全部分集扣掉已刪除的；沒勾選時為實際影片工作數）。
    expected_videos: int = Field(ge=0)


class PodcastSummary(_Closed):
    podcast_id: UUID
    material_id: UUID
    material_name: str
    knowledge_structure_revision: str
    name: str
    delivery: Literal["solo", "dialogue"]
    video_enabled: bool
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


class PodcastOmittedContent(_Closed):
    segment_index: int = Field(ge=0)
    claim_ids: list[str]
    reason: Literal["source_check_failed"]


class PodcastEpisode(_Closed):
    omitted_content: list[PodcastOmittedContent] = Field(default_factory=list)
    script_sha256: str | None = None
    # 短集名（衍生顯示資料）；舊資料或取名失敗時為 None。
    title: str | None = None
    delivery: Literal["solo", "dialogue"]
    # 主持人名字（建立時抽定，同系列相同）；節目化前的舊集為 None。
    hosts: dict[Literal["host", "guest"], str] | None = None
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


class LearningDiagnosisEvidence(_Closed):
    evidence_id: str
    page: int
    source_id: str
    source_name: str
    normalized_page: int


class LearningDiagnosisPoint(_Closed):
    consecutive_correct_count: int
    required_consecutive_correct: int
    completed: bool
    claim_id: str
    title: str
    answer_count: int
    correct_count: int
    latest_is_correct: bool | None
    evidence: list[LearningDiagnosisEvidence]


class LearningDiagnosisFinding(_Closed):
    concept_id: str
    label: str
    points: list[LearningDiagnosisPoint]


class LearningDiagnosisRecommendation(_Closed):
    concept_id: str
    label: str
    reason: str


class LearningDiagnosisView(_Closed):
    schema_: Literal["learning-diagnosis/v2"] = Field(alias="schema")
    material_id: UUID
    knowledge_structure_revision: str
    scope: Literal["material"]
    source_resolver: str
    strengths: list[LearningDiagnosisFinding]
    weaknesses: list[LearningDiagnosisFinding]
    recommendations: list[LearningDiagnosisRecommendation]


class ConceptDifficultyItem(_Closed):
    concept_id: str
    label: str
    level: Literal["standard", "hard"]
    reason: str
    personal_difficulty: bool
    incorrect_count: int
    answer_count: int


class ConceptDifficultyView(_Closed):
    schema_: Literal["concept-difficulty/v1"] = Field(alias="schema")
    material_id: UUID
    knowledge_structure_revision: str
    mode: Literal["structure"]
    concepts: list[ConceptDifficultyItem]


class StudyStartCreate(_Closed):
    schema_: Literal["study-start/v1"] = Field(alias="schema")
    knowledge_structure_revision: str
    current_concept_id: str | None = None


class StudyStartView(_Closed):
    schema_: Literal["study-start/v2"] = Field(alias="schema")
    session: StudySessionView
    selected_set_id: UUID | None


class GoalConceptView(_Closed):
    concept_id: str
    label: str
    remaining_points: int


class GoalStageView(_Closed):
    date: date_type
    quota_points: int
    completed_points: int
    remaining_points: int
    scheduled_points: int
    done: bool
    concepts: list[GoalConceptView]


class GoalProgressView(_Closed):
    target_date: date_type
    target_percent: Literal[60, 80, 100]
    assessable_points: int
    completed_points: int
    target_points: int
    remaining_points: int
    achieved: bool
    days_remaining: int
    overdue_days: int
    behind_points: int
    daily_limit: Literal[20]
    quota_over_limit: bool
    scheduled_over_limit: bool
    today_stage: GoalStageView


class GoalUpdateNotice(_Closed):
    knowledge_structure_revision: str
    previous_points: int
    current_points: int


class GoalSuggestion(_Closed):
    kind: Literal["extend_date", "lower_target"]
    target_date: date_type
    target_percent: Literal[60, 80, 100]
    daily_points: int


class PendingLearningGoal(_Closed):
    target_date: date_type
    target_percent: Literal[60, 80, 100]


class GoalPlanView(GoalProgressView):
    suggestions: list[GoalSuggestion]
    future_stages: list[GoalStageView]
    preview_offset: int
    preview_has_more: bool
    manual_review: list[GoalConceptView]
    update_notice: GoalUpdateNotice | None


class LearningGoalView(_Closed):
    schema_: Literal["learning-goal/v2"] = Field(alias="schema")
    pending_goal: PendingLearningGoal | None
    material_id: UUID
    knowledge_structure_revision: str | None
    run_id: UUID | None
    today: date_type
    goal: GoalPlanView | None


class LearningGoalUpdate(_Closed):
    schema_: Literal["learning-goal-update/v1"] = Field(alias="schema")
    target_date: date_type
    target_percent: StrictInt


class LearningGoalAck(_Closed):
    schema_: Literal["learning-goal-ack/v1"] = Field(alias="schema")
    knowledge_structure_revision: str


class LearningGoalSummary(GoalProgressView):
    material_id: UUID
    material_name: str
    knowledge_structure_revision: str
    run_id: UUID


class LearningGoalListView(_Closed):
    schema_: Literal["learning-goals/v1"] = Field(alias="schema")
    today: date_type
    goals: list[LearningGoalSummary]


class NoteTableView(_Closed):
    headers: list[str]
    rows: list[list[str]]


class NoteConceptView(_Closed):
    concept_id: str
    label: str


class NoteItem3View(_Closed):
    text: str


class NoteItem2View(_Closed):
    text: str
    children: list[NoteItem3View]


class NoteItemView(_Closed):
    text: str
    children: list[NoteItem2View]


class NoteTextBlockView(_Closed):
    type: Literal["text"]
    text: str


class NoteBulletsBlockView(_Closed):
    type: Literal["bullets"]
    items: list[NoteItemView]


class NoteTableBlockView(_Closed):
    type: Literal["table"]
    caption: str | None
    headers: list[str]
    rows: list[list[str]]


class NoteFormulaBlockView(_Closed):
    type: Literal["formula"]
    latex: str


class NoteCalloutBlockView(_Closed):
    type: Literal["callout"]
    style: Literal["case_study", "discussion", "reminder", "lesson"]
    title: str | None
    items: list[NoteItemView]


class NoteSectionView(_Closed):
    """material-notes/v12 起的小節：有層級的標題與條列、表格、公式、醒目區塊。"""
    id: str
    anchor: str
    heading: str
    level: Literal[1, 2]
    blocks: list[Annotated[NoteTextBlockView | NoteBulletsBlockView | NoteTableBlockView
        | NoteFormulaBlockView | NoteCalloutBlockView, Field(discriminator="type")]]
    claim_ids: list[str]
    evidence_ids: list[str]
    concepts: list[NoteConceptView]
    evidence: list[EvidenceView]


class NoteParagraphView(_Closed):
    """v11 以前的段落格式；重新生成前仍可閱讀。"""
    id: str
    anchor: str
    heading: str
    text: str
    table: NoteTableView | None
    formula: str | None
    claim_ids: list[str]
    evidence_ids: list[str]
    concepts: list[NoteConceptView]
    evidence: list[EvidenceView]


class NoteOutlineView(_Closed):
    paragraph_id: str
    level: Literal[1, 2]
    number: str
    heading: str


class NoteAnnotationView(_Closed):
    annotation_id: UUID
    anchor: str
    body: str
    original_revision: str
    original_paragraph: NoteSectionView | NoteParagraphView
    paragraph_id: str | None
    updated_at: datetime


class NoteProgressView(_Closed):
    stage: Literal["generating", "validating", "completed"]
    completed_sections: int
    total_sections: int
    completed_parts: int
    total_parts: int
    current_section: str | None


class MaterialNotesView(_Closed):
    schema_: Literal["material-notes/v3"] = Field(alias="schema")
    content_revision: str | None
    content_generation: int | None
    update_available: bool
    updating: bool
    started_at: datetime | None
    updated_at: datetime | None
    material_id: UUID
    knowledge_structure_revision: str
    run_id: UUID
    kind: Literal["summary", "full"]
    status: Literal["not_generated", "pending", "running", "ready", "failed"]
    generation: int
    source_status: StatusView
    progress: NoteProgressView | None
    paragraphs: list[NoteSectionView | NoteParagraphView]
    outline: list[NoteOutlineView]
    annotations: list[NoteAnnotationView]
    source_resolver: str
    error_code: str | None
