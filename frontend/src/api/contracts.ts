export type KnownApiReasonCode =
  | "INVALID_EMAIL"
  | "INVALID_CREDENTIALS"
  | "ACCOUNT_UNAVAILABLE"
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
  progress_stage: "queued" | "evidence" | "semantics" | "publishing" | "completed";
  completed_pages: number;
  total_pages: number | null;
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
  status: "not_started" | "learning" | "needs_review" | "mastered";
  attempts: number;
  correct_answers: number;
  qualified_correct_items: number;
  covered_claim_ids: string[];
  mastered_claim_ids: string[];
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
  included?: boolean;
  source_id: string;
  normalization_id: string;
  original_artifact_id: string;
  original_name: string;
  media_type: string;
  status: "pending" | "running" | "ready" | "failed";
  normalized_artifact_id: string | null;
  page_count: number | null;
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
