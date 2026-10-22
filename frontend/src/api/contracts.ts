export type KnownApiReasonCode =
  | "SCENE_CONFLICT"
  | "SCENE_ALIGNMENT_INVALID"
  | "SCENE_ALIGNMENT_FAILED"
  | "SCENE_SOURCE_CHANGED"
  | "TOPIC_CONFLICT"
  | "TOPIC_PROVIDER_FAILED"
  | "VOICE_TURN_IN_PROGRESS"
  | "VOICE_PROVIDER_UNAVAILABLE"
  | "VOICE_PROVIDER_FAILED"
  | "VOICE_TRANSCRIPT_INVALID"
  | "VOICE_CONTEXT_TOO_LARGE"
  | "RETRIEVAL_INDEX_REQUIRED"
  | "RETRIEVAL_INDEX_INVALID"
  | "MATERIAL_FORMAT_UNSUPPORTED"
  | "SOURCE_SCOPE_EMPTY"
  | "SOURCE_SCOPE_INVALID"
  | "SOURCE_SCOPE_UNRESOLVED"
  | "RESEARCH_CONTEXT_TOO_LARGE"
  | "RESEARCH_SEARCH_FAILED"
  | "RESEARCH_SCOPE_REQUIRED"
  | "RESEARCH_SNAPSHOT_UNAVAILABLE"
  | "RESEARCH_DOWNLOAD_FAILED"
  | "RESEARCH_LICENSE_UNCONFIRMED"
  | "RESEARCH_URL_REJECTED"
  | "VOICE_PODCAST_CONTEXT_INVALID"
  | "PODCAST_CONFLICT"
  | "PODCAST_SOURCE_INSUFFICIENT"
  | "PODCAST_SOURCE_TOO_LARGE"
  | "CARD_SET_CONFLICT"
  | "CARD_REVIEW_CONFLICT"
  | "VERSION_MOVE_STALE"
  | "INVALID_EMAIL"
  | "INVALID_CREDENTIALS"
  | "ACCOUNT_UNAVAILABLE"
  | "CURRENT_PASSWORD_INVALID"
  | "TOO_MANY_ATTEMPTS"
  | "REQUEST_INVALID"
  | "SESSION_REQUIRED"
  | "ORIGIN_NOT_ALLOWED"
  | "RESOURCE_NOT_FOUND"
  | "IDEMPOTENCY_CONFLICT"
  | "LEARNER_GUIDANCE_STALE"
  | "MATERIAL_NOT_DISCARDABLE"
  | "SOURCE_NOT_READY"
  | "NORMALIZER_UNAVAILABLE"
  | "DUPLICATE_SOURCE"
  | "REVISION_CONFLICT"
  | "REVISION_IN_PROGRESS"
  | "SOURCE_IN_USE"
  | "SOURCE_BUSY"
  | "ASSESSMENT_SET_CONFLICT"
  | "ASSESSMENT_SET_ACTIVE"
  | "MATERIAL_TOO_LARGE"
  | "UNSUPPORTED_MEDIA_TYPE"
  | "STORAGE_UNAVAILABLE"
  | "INTERNAL_ERROR";

export type ApiReasonCode = KnownApiReasonCode | "UNKNOWN_API_ERROR";

export type ApiErrorView = {
  schema: "api-error/v1";
  request_id: string;
  reason_code: string;
  retryable: boolean;
  message: "Request could not be completed.";
};

export type LearnerIdentity = { schema: "learner-identity/v1"; learner_id: string };
// 個人顯示名稱；未設定為 null，畫面沿用不含名字的問候。
export type LearnerProfile = { schema: "learner-profile/v1"; display_name: string | null };
// 本人目前仍有效的登入數，含目前這個裝置。
export type AccountSessions = { schema: "account-sessions/v1"; active_sessions: number };

// 全站搜尋：只查教材名稱與各教材目前版本的概念名稱／別名。
export type LibrarySearchView = {
  schema: "library-search/v1"; query: string;
  materials: { material_id: string; display_name: string; run_id: string | null; knowledge_structure_revision: string | null }[];
  concepts: {
    material_id: string; material_name: string; run_id: string; knowledge_structure_revision: string;
    concept_id: string; label: string; claim_count: number;
  }[];
  concepts_truncated: boolean;
};

export type MaterialOutputBinding = {
  schema: "material-run-output-binding/v1";
  knowledge_structure_revision: string;
  page_count: number;
};

export type MaterialDiscardView = {
  schema: "material-discard/v1";
  material_id: string;
  state: "removing" | "removed";
};

export type MaterialProcessingRunView = {
  analysis_saved?: boolean;
  base_revision?: string;
  source_names?: string[];
  schema: "material-processing-run/v1";
  input_source_set_id?: string;
  cancel_requested_at: string | null;
  run_id: string;
  material_id: string;
  source_artifact_id: string;
  status: "pending" | "running" | "succeeded" | "partial" | "failed" | "cancelled";
  progress_stage: "queued" | "evidence" | "semantics" | "review" | "indexing" | "publishing" | "completed";
  completed_pages: number;
  total_pages: number | null;
  completed_units?: number | null;
  total_units?: number | null;
  output_binding: MaterialOutputBinding | null;
  error_code: string | null;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
};

export type MaterialAttemptView = Pick<
  MaterialProcessingRunView,
  | "run_id"
  | "status"
  | "progress_stage"
  | "completed_pages"
  | "total_pages"
  | "error_code"
  | "created_at"
  | "cancel_requested_at"
  | "base_revision"
>;

export type MaterialStructureLink = {
  base_revision?: string;
  run_id: string;
  knowledge_structure_revision: string;
  created_at: string;
  status: "succeeded" | "partial";
};

export type StudySessionLink = {
  study_session_id: string;
  knowledge_structure_revision: string;
  run_id: string;
  current_concept_id: string | null;
  status: "active" | "no_safe" | "completed";
  started_at: string;
};

export type MaterialLibraryItem = {
  head_revision?: string | null;
  source_count?: number;
  schema: "material-library-item/v1";
  source?: SourceView;
  material_id: string;
  source_artifact_id: string | null;
  display_name: string;
  size_bytes: number;
  created_at: string;
  latest_attempt: MaterialAttemptView | null;
  available_structures: MaterialStructureLink[];
  study_sessions: StudySessionLink[];
};

export type MaterialLibraryView = {
  schema: "material-library/v1";
  materials: MaterialLibraryItem[];
};

type SourceLocatorView = {
  page: number;
  block_id: string;
  region: [number, number, number, number];
};

export type EvidenceView = {
  source_id?: string;
  source_name?: string;
  normalized_page?: number;
  evidence_id: string;
  page_ref: string;
  page: number;
  block_order: number;
  kind: string;
  source: "native_text" | "unlimited_ocr";
  source_locator: SourceLocatorView;
  quote: string;
  quote_start?: number;
  quote_end?: number;
};

export type RelationType = "prerequisite" | "part_of" | "application" | "example" | "contrast";

export type KnowledgeStructureView = {
  schema: "knowledge-structure-view/v1";
  source_resolver: string;
  material_id: string;
  knowledge_structure_revision: string;
  status: {
    processing: "succeeded" | "partial" | "failed";
    quality: "accepted" | "needs_review";
    decision: "retain" | "review" | "reject";
    reason_codes: string[];
  };
  document_tree: {
    material_id: string;
    sections: {
      section_id: string;
      title: string;
      order: number;
      heading_evidence_id: string | null;
      concept_ids: string[];
    }[];
  };
  concepts: {
    concept_id: string;
    label: string;
    aliases: string[];
    claims: { claim_id: string; text: string; evidence: EvidenceView[] }[];
  }[];
  relations: {
    relation_id: string;
    source_concept_id: string;
    target_concept_id: string;
    type: RelationType;
    learner_reason: string;
    evidence_refs: string[];
  }[];
  initial_learning_path: {
    position: number;
    concept_id: string;
    reason: "document_order" | "prerequisite";
  }[];
  excluded_pages: { page_ref: string; page: number; stage: "evidence"; reason_code: string }[];
};

export type KnowledgeOutlineView = Pick<KnowledgeStructureView,
  "source_resolver" | "status" | "initial_learning_path" | "excluded_pages"> & {
  schema: "knowledge-outline/v1";
  material_id: string;
  knowledge_structure_revision: string;
  run_id: string;
  section_count: number;
  relations: Pick<KnowledgeStructureView["relations"][number], "relation_id" | "source_concept_id" | "target_concept_id" | "type">[];
  concepts: { concept_id: string; label: string; aliases: string[]; claim_count: number }[];
};

// 地圖區域：chapters 是模型分章並經程式驗證的章節；source 依主要來源檔分區；
// learning_order 是依學習順序推導的分段，不是教材章節。
// 章節分區時 groups 是來源檔分區（島群），每章以 group_id 指向所屬島群；單層呈現時為空。
// partial：部分章節未通過驗證，該部分沿用來源檔分區。
export type KnowledgeRegionsView = {
  schema: "knowledge-regions/v1"; material_id: string; knowledge_structure_revision: string;
  basis: "chapters" | "source" | "learning_order";
  regions: { region_id: string; label: string; source_id: string | null; concept_ids: string[]; group_id?: string | null }[];
  groups?: { group_id: string; label: string; source_id: string | null }[];
  partial?: boolean;
};

// 學習統計只由作答事件推導，日期以台灣時間為界。
export type LearningActivityDay = {
  date: string; answers: number; correct: number; card_reviews: number; listened_seconds: number; active: boolean;
};
export type LearningQuestKind = "mark_cards" | "continue_study" | "finish_podcast_episode";
export type LearningQuest = {
  kind: LearningQuestKind; progress: number; target: number; done: boolean;
  material_id: string | null; study_session_id: string | null; podcast_id: string | null; episode_index: number | null;
};
export type LearningXp = {
  total: number; today: number; level: number; level_xp: number; level_target: number;
  sources: { answers: number; concepts: number; cards: number; podcast: number };
};
// 徽章（規則 badge-rules/v5）只由真實紀錄推導；同一計量的多個門檻是同一系列的層級，另有隱藏條件的特殊成就。未知代號由畫面忽略。
export type LearningBadgeId =
  | "first_answer" | "answers_100" | "answers_300" | "answers_1000" | "answers_5000"
  | "first_concept" | "concepts_25" | "concepts_100" | "concepts_300"
  | "first_material" | "materials_3" | "materials_10"
  | "cards_100" | "cards_500" | "cards_2000"
  | "listen_30_minutes" | "listen_180_minutes" | "listen_600_minutes"
  | "streak_3" | "streak_7" | "streak_30" | "streak_100" | "streak_365"
  | "level_5" | "level_10" | "level_20" | "level_30"
  | "perfect_set_1" | "perfect_set_10" | "perfect_set_30"
  | "combo_10" | "comeback_20" | "daily_50" | "explorer_5" | "night_owl" | "early_bird"
  | "weekend" | "return" | "curious_10" | "researcher" | "creator" | "corrected_5"
  | "first_try_material" | "perfect_day" | "all_round_day" | "thousand_pages" | "revisit";
export type LearningBadge = { badge_id: string; earned: boolean; earned_on: string | null; progress: number; target: number };
export type LearningActivityView = {
  schema: "learning-activity/v2"; timezone: "Asia/Taipei"; rule_version: "xp-rules/v2"; today: string;
  streak_days: number; active_today: boolean;
  week: LearningActivityDay[];
  total_answers: number; total_correct: number;
  xp: LearningXp;
  quests: LearningQuest[];
  badges: { rule_version: "badge-rules/v5"; items: LearningBadge[] };
};

export type PodcastProgressView = {
  schema: "podcast-progress/v1";
  podcast_id: string;
  current: { episode_index: number; position_seconds: number; updated_at: string } | null;
  episodes: { episode_index: number; position_seconds: number; completed: boolean }[];
  credited_seconds: number;
};

export type PodcastProgressUpdate = {
  schema: "podcast-progress-update/v1";
  episode_index: number; position_seconds: number; listened_seconds: number; ended?: boolean; legacy?: boolean;
};

export type KnowledgeConceptsView = ConceptCardContext & {
  schema: "knowledge-concepts/v1";
  material_id: string;
  knowledge_structure_revision: string;
  requested_concept_ids: string[];
  relation_evidence: EvidenceView[];
};

export type KnowledgeSearchView = {
  schema: "knowledge-search/v1"; material_id: string;
  knowledge_structure_revision: string; concept_ids: string[];
};

export type MapProgressView = Pick<LearnerProgressView,
  "study_session_id" | "knowledge_structure_revision" | "current_concept_id" | "assessment_cycles" | "next_action"> & {
  schema: "map-progress/v1";
  concept_states: (Pick<ConceptLearningStateView, "concept_id" | "status" | "attempts"> & { assessable: boolean })[];
};

// 首頁島嶼只需數字與目前概念名稱；沒有學習工作階段時只有概念數。
export type ProgressSummaryView = {
  schema: "progress-summary/v1"; material_id: string; knowledge_structure_revision: string;
  study_session_id: string | null; concept_count: number; completed_count: number;
  current_concept_id: string | null; current_concept_label: string | null;
};

export type StudyProgressView = Omit<LearnerProgressView, "schema"> & {
  schema: "study-progress/v1"; concept_count: number;
};

export type StudyNavigationView = Pick<KnowledgeOutlineView,
  "material_id" | "knowledge_structure_revision" | "run_id" | "source_resolver" | "status" | "initial_learning_path"> & {
  schema: "study-navigation/v1";
  concept_count: number;
  concepts: { concept_id: string; label: string }[];
};

export type StudyWorkspaceView = Omit<StudyResumeView, "schema" | "knowledge_structure" | "progress"> & {
  schema: "study-workspace/v1";
  outline: StudyNavigationView;
  context: KnowledgeConceptsView | null;
  progress: StudyProgressView;
};

export type CardDesignSources = { claim_ids: string[]; relation_ids: string[]; evidence_ids: string[] };
export type CardDesignPage = {
  title: string; layout: "focus" | "compare" | "flow" | "grid";
  takeaway: CardDesignSources & { text: string };
  blocks: (CardDesignSources & { heading: string; text: string; icon: string })[];
  links: (CardDesignSources & { source: number; target: number; label: string })[];
};
export type CardDesign = {
  schema: "concept-card-design/v1"; revision: string; pages: CardDesignPage[];
  details_claim_ids: string[]; provider: { model_id: string; revision: string; api_protocol: string };
};
export type ConceptCard = Pick<KnowledgeStructureView["concepts"][number], "concept_id" | "label" | "claims"> & { display_edits?: CardTextEdit[]; design?: CardDesign | null };

export type CardReviewRating = "forgot" | "remembered";

// 學習標記：forgot／remembered 即畫面上的不熟／熟悉；不排程。
// 需要加強的卡片（標成不熟，較久沒標記的在前）；張數依卡組彙總。
export type CardReviewItem = {
  card_set_id: string; card_set_name: string; material_id: string; material_name: string;
  knowledge_structure_revision: string; source_resolver: string;
  review_count: number;
  card: ConceptCard;
};

export type CardReviewMark = { card_set_id: string; concept_id: string; rating: CardReviewRating; review_count: number };

export type CardReviewsView = {
  schema: "card-reviews/v2";
  forgot_count: number;
  sets: { card_set_id: string; card_count: number; forgot_count: number; remembered_count: number }[];
  /** 只有指定卡組時才列出該卡組已標記的卡片。 */
  marks: CardReviewMark[];
  cards: CardReviewItem[];
};

export type CardReviewView = {
  schema: "card-review/v2"; card_set_id: string; concept_id: string; rating: CardReviewRating;
  review_count: number; reviewed_at: string;
};

export type ConceptCardContext = Pick<KnowledgeStructureView, "source_resolver" | "status" | "concepts" | "relations"> & {
  relation_evidence?: EvidenceView[];
};

export type CardBatch = {
  schema: "card-batch/v1";
  card_set_id: string; version: number; offset: number; total: number;
  cards: ConceptCard[]; context: ConceptCardContext;
};

export type CardStudyView = CardSetSummary & {
  schema: "card-study/v1";
  source_resolver: string; status: KnowledgeStructureView["status"];
  navigation: { concept_id: string; label: string; page_count: number | null }[];
  batch: CardBatch | null;
};

export type CardGeneration = {
  status: "pending" | "running" | "ready" | "failed";
  stage: "preparing" | "compacting" | "designing" | "checking" | "complete";
  completed: number; total: number; error_code: string | null;
  retained_original: number; cached: number;
};
export type CardTextEdit = { source: string; text: string };

export type CardSetSummary = {
  version: number;
  card_set_id: string;
  material_id: string;
  material_name: string;
  knowledge_structure_revision: string;
  name: string;
  card_count: number | null;
  generation: CardGeneration;
  created_at: string;
  is_current_revision: boolean;
  /** 已移到新版時指向新版卡組（舊後端沒有此欄位）。 */
  moved_to_card_set_id?: string | null;
};

export type CardSetView = CardSetSummary & {
  schema: "card-set/v1";
  source_resolver: string;
  status: KnowledgeStructureView["status"];
  excluded_pages: KnowledgeStructureView["excluded_pages"];
  cards: ConceptCard[];
  display_edits: Record<string, CardTextEdit[]>;
};

export type CardSetCreate = {
  schema: "card-set-create/v1";
  knowledge_structure_revision: string;
  name: string;
  concept_ids: string[];
};

export type CardSetListView = { schema: "card-set-list/v1"; card_sets: CardSetSummary[] };
export type CardSetUpdate = {
  schema: "card-set-update/v1";
  name: string;
  expected_version: number;
  concept_ids?: string[];
};
export type CardSetDeletedView = { schema: "card-set-deleted/v1"; card_set_id: string };

export type KnowledgeStructureRequest = {
  materialId: string;
  structureRevision: string;
};

export type StudySessionFocus = {
  schema: "study-session-focus/v1";
  current_concept_id: string;
};

export type StudySessionCreate = {
  schema: "study-session-create/v1";
  material_id: string;
  knowledge_structure_revision: string;
  current_concept_id?: string | null;
};

export type StudySessionView = {
  schema: "study-session/v1";
  study_session_id: string;
  material_id: string;
  knowledge_structure_revision: string;
  current_concept_id: string | null;
  deferred_concept_ids: string[];
  no_safe_claim_ids: string[];
  status: "active" | "no_safe" | "completed";
  started_at: string;
  completed_at: string | null;
  event_watermark: number;
};

type AssessmentOptionView = { option_id: string; text: string };
export type AssessmentView = {
  schema: "single-choice-assessment/v1";
  assessment_revision: string;
  study_session_id: string;
  knowledge_structure_revision: string;
  question_id: string;
  target_concept_id: string;
  target_claim_id: string;
  source_evidence_ids: string[];
  question_type: "single_choice";
  prompt: string;
  options: AssessmentOptionView[];
};

export type AnswerFeedbackView = {
  schema: "answer-feedback/v1";
  answer_event_id: string;
  study_session_id: string;
  assessment_revision: string;
  question_id: string;
  selected_option_id: string;
  is_correct: boolean;
  rationale: string;
  source_evidence_ids: string[];
  event_number: number;
  created_at: string;
};

type ConceptLearningStateView = {
  concept_id: string;
  label: string;
  status: "not_started" | "in_progress" | "needs_review" | "completed";
  attempts: number;
  correct_answers: number;
  qualified_correct_items: number;
  covered_claim_ids: string[];
  completed_claim_ids: string[];
  assessable_claim_ids: string[];
  unavailable_claim_ids: string[];
  weak_claim_ids: string[];
  latest_is_correct: boolean | null;
};

type NextActionView = {
  action:
    | "continue_set"
    | "remediate"
    | "assess"
    | "review_prerequisite"
    | "advance"
    | "defer"
    | "resume"
    | "no_safe"
    | "complete";
  target_concept_id: string | null;
  target_claim_id: string | null;
  prerequisite_concept_ids: string[];
  reason: string;
};

export type GuidanceApply = { schema: "guidance-apply/v1"; guidance_revision: string };

export type LearnerProgressView = {
  schema: "learner-progress/v1";
  assessment_cycles: AssessmentCycleSummary[];
  study_session_id: string;
  knowledge_structure_revision: string;
  event_watermark: number;
  current_concept_id: string | null;
  deferred_concept_ids: string[];
  concept_states: ConceptLearningStateView[];
  weaknesses: { concept_id: string; claim_ids: string[]; reason: string }[];
  next_action: NextActionView;
  guidance_revision: string;
};

export type AssessmentRecordView = {
  assessment: AssessmentView;
  feedback: AnswerFeedbackView | null;
  can_submit: boolean;
};

export type StudyResumeView = {
  schema: "study-resume/v1";
  session: StudySessionView;
  run_id: string;
  source_artifact_id: string;
  knowledge_structure: KnowledgeStructureView;
  progress: LearnerProgressView;
  assessment_sets: AssessmentSetSummary[];
  selected_set_id: string | null;
  selected_set: AssessmentSetView | null;
};

export type AssessmentPlanView = {
  schema: "assessment-plan/v1";
  study_session_id: string;
  knowledge_structure_revision: string;
  policy: "single-concept-grounded-points/v1";
  concept_id: string;
  point_count: number;
  requested_count: number;
  targets: { claim_id: string; covered_claim_ids: string[]; reason: "distinct_grounded_point" }[];
  excluded: { claim_id: string; reason: "no_content_evidence" }[];
};

export type AssessmentCycleSummary = {
  inherited_from?: {
    study_session_id: string;
    knowledge_structure_revision: string;
    run_id: string;
  } | null;
  diagnostic_set_id: string;
  concept_id: string;
  set_version: number;
  outcome: "in_progress" | "needs_review" | "passed" | "incomplete";
  active_set_id: string | null;
  passed_count: number;
  remediation_passed_count: number;
  pending_count: number;
  unanswered_count: number;
  unavailable_count: number;
};
type AssessmentCycleView = AssessmentCycleSummary & {
  can_create_remediation: boolean;
  points: {
    claim_id: string;
    result: "unavailable" | "unanswered" | "diagnostic_pass" | "needs_review" | "remediation_pass";
    latest_answer_event_id: string | null;
    latest_set_id: string | null;
  }[];
};

export type AssessmentSetSummary = {
  kind: "diagnostic" | "remediation";
  diagnostic_set_id: string | null;
  set_id: string;
  target_concept_id: string;
  status:
    | "preparing"
    | "partial_ready"
    | "failed"
    | "ready"
    | "in_progress"
    | "completed"
    | "cancelled";
  set_version: number;
  requested_count: number;
  published_count: number;
  answered_count: number;
  passed_count: number;
  assessment_revisions: string[];
  created_at: string;
  completed_at: string | null;
};

export type AssessmentSetListView = {
  schema: "assessment-set-list/v1";
  study_session_id: string;
  knowledge_structure_revision: string;
  active_set_ids: string[];
  sets: AssessmentSetSummary[];
};

type AssessmentSetItem = {
  ordinal: number;
  target_claim_id: string;
  state: "pending" | "generating" | "verified" | "published" | "failed" | "omitted";
  attempts: number;
  failure_reason: string | null;
  assessment: AssessmentView | null;
  feedback: AnswerFeedbackView | null;
  created_at: string | null;
  can_submit: boolean;
};

export type AssessmentSetView = AssessmentSetSummary & {
  schema: "assessment-set/v1";
  study_session_id: string;
  material_id: string;
  knowledge_structure_revision: string;
  cycle: AssessmentCycleView;
  selection_policy: "single-concept-grounded-points/v1" | "needs-review-points/v1";
  point_count: number;
  excluded_count: number;
  verified_count: number;
  can_retry: boolean;
  can_publish_partial: boolean;
  can_complete: boolean;
  items: AssessmentSetItem[];
};

export type AssessmentSetAnswer = {
  assessment_revision: string;
  question_id: string;
  selected_option_id: string;
};

export type AssessmentSetAction = "retry" | "publish-partial";

export type MaterialRename = { schema: "material-rename/v1"; display_name: string };

export type SourceView = {
  origin?: "upload" | "research";
  included?: boolean;
  source_id: string;
  normalization_id: string;
  original_artifact_id: string;
  original_name: string;
  media_type: string;
  status: "pending" | "running" | "ready" | "failed";
  normalized_artifact_id: string | null;
  page_count: number | null;
  analysis_page_count?: number | null;
  analysis_mode?: "full" | "selected";
  error_code: string | null;
};
export type SourceListView = {
  schema: "material-sources/v1";
  material_id: string;
  discard_requested?: boolean;
  sources: SourceView[];
};
export type FormatCapability = { extension: string; media_type: string; max_bytes: number };
export type SourceCapabilities = { schema: "source-capabilities/v1"; formats: FormatCapability[] };
export type EvidenceSourceView = {
  schema: "evidence-source/v1";
  format: "pdf" | "docx" | "pptx" | "doc" | "ppt" | "txt" | "md";
  original_name: string;
  original_url: string;
  preview_url: string;
  normalized_page: number;
  accuracy: "exact" | "ambiguous" | "unavailable";
  origin_locators: Record<string, unknown>[];
  label: string;
};

export type PodcastSummary = {
  podcast_id: string; material_id: string; material_name: string;
  knowledge_structure_revision: string; name: string;
  delivery: "solo" | "dialogue";
  concept_ids: string[]; status: "pending" | "running" | "ready" | "failed" | "cancelled";
  error_code: string | null; version: number; created_at: string;
  episode_count: number; completed_episodes: number; is_current_revision: boolean;
  /** 所有分集音訊的總秒數；音訊未全部完成時為 null。 */
  duration_seconds: number | null;
  generation?: {preview_ready?:boolean; retryable_videos?:boolean; active:boolean; completed_videos:number; failed_videos:number};
};
export type PodcastClaim = ConceptCard["claims"][number] & { concept_id: string; label: string };
export type PodcastContext = {
  podcast_id: string; episode_index: number; script_sha256: string;
  source_refs: {segment_index:number;turn_index:number;start:number;end:number}[];
};
export type PodcastSourceReference = {source_index:number;evidence_ids:string[]};
export type PodcastScript = {
  schema?: "podcast-script/v2";
  segments: { claim_id?: string; beat_id?: string; title?: string;
    turns: { speaker: "host" | "guest"; text: string; parts?: {text:string;source_refs:PodcastSourceReference[]}[] }[] }[];
  provider: string;
  review?: {correctness:{passed:boolean;reason:string};teaching_quality:{passed:boolean;reason:string}} | null;
};
export type PodcastEpisode = {
  // 短集名；舊資料或取名失敗時沒有，改以概念名稱組成。
  title?: string | null;
  delivery: "solo" | "dialogue";
  claims: PodcastClaim[];
  script: PodcastScript | null;
  script_sha256?: string | null;
  audio: { artifact_id: string; sha256: string; duration_seconds: number; provider: string } | null;
};
export type PodcastView = PodcastSummary & {
  schema: "podcast/v1"; run_id?:string; episodes: PodcastEpisode[]; source_resolver: string;
  source_status: KnowledgeStructureView["status"]; excluded_pages: KnowledgeStructureView["excluded_pages"];
};
export type PodcastCreate = {
  schema: "podcast-create/v1"; knowledge_structure_revision: string;
  name: string; concept_ids: string[]; delivery: "solo" | "dialogue";
};
export type PodcastAction = {
  schema: "podcast-action/v1"; action: "rename" | "retry" | "cancel"; expected_version: number; name?: string;
};
export type PodcastListView = { schema: "podcast-list/v1"; podcasts: PodcastSummary[] };
export type PodcastDeletedView = { schema: "podcast-deleted/v1"; podcast_id: string };

/** 教材新版承接（移到新版）的預覽；沒有可承接項目時 card_sets 為空、study 為 null。 */
export type VersionMoveUnmapped = {
  concept_id: string;
  label: string;
  reason: "split" | "ambiguous" | "not_found" | "merged_unfinished";
};

export type VersionMoveCardSet = {
  card_set_id: string;
  name: string;
  from_revision: string;
  concept_count: number;
  target_count: number;
  carried_reviews: number;
  reused_designs: number;
  movable: boolean;
  mapped: { from_concept_id: string; from_label: string; to_concept_id: string; to_label: string; basis: "evidence" | "label" }[];
  unmapped: VersionMoveUnmapped[];
};

export type VersionMovePreview = {
  schema: "version-move-preview/v1";
  material_id: string;
  to_revision: string | null;
  preview_digest: string | null;
  card_sets: VersionMoveCardSet[];
  study: {
    study_session_id: string;
    from_revision: string;
    target_study_session_id: string | null;
    completed_count: number;
    carried: { concept_id: string; label: string; from_labels: string[] }[];
    not_carried: VersionMoveUnmapped[];
    current_concept: { concept_id: string; label: string } | null;
  } | null;
  kept_podcasts: number;
};

export type VersionMoveCreate = {
  schema: "version-move-create/v1";
  to_revision: string;
  card_set_ids: string[];
  include_study: boolean;
  preview_digest: string;
};

export type VersionMoveResult = {
  schema: "version-move/v1";
  move_id: string;
  material_id: string;
  to_revision: string;
  card_sets: { from_card_set_id: string; card_set_id: string; status: "pending" | "ready" }[];
  study_session_id: string | null;
  created_at: string;
};
