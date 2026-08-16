export type KnownApiReasonCode =
  | "REQUEST_INVALID"
  | "SESSION_REQUIRED"
  | "ORIGIN_NOT_ALLOWED"
  | "RESOURCE_NOT_FOUND"
  | "IDEMPOTENCY_CONFLICT"
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

export type MaterialView = {
  schema: "material/v1";
  material_id: string;
  source_artifact_id: string;
  source_sha256: string;
  size_bytes: number;
};

export type MaterialSubject = "data_structures" | "economics";

export type MaterialProcessingCreate = {
  schema: "material-processing-create/v1";
  material_id: string;
  source_artifact_id: string;
  subject: MaterialSubject;
};

export type ProviderCallCounts = {
  page_structure: number;
  visual_alignment_adjudication: number;
  concept_candidate: number;
  concept_content: number;
  total: number;
};

export type MaterialOutputBinding = {
  schema: "material-run-output-binding/v1";
  study_material_output_revision: string;
  catalog_revision: string;
  learning_resource_result_revision: string;
  knowledge_map_revision: string;
  learning_path_revision: string;
  assessment_revision: string;
  processing: "succeeded" | "partial";
  quality: "accepted" | "needs_review";
  decision: "retain" | "review";
  reason_code:
    | "DEVELOPMENT_OUTPUT_ACCEPTED"
    | "DEVELOPMENT_OUTPUT_NEEDS_REVIEW"
    | "DEVELOPMENT_FULL_DOCUMENT_PARTIAL";
  provider_call_counts: ProviderCallCounts;
  development_only: true;
};

export type MaterialProcessingRunView = {
  schema: "material-processing-run/v1";
  run_id: string;
  material_id: string;
  source_artifact_id: string;
  status: "pending" | "running" | "succeeded" | "partial" | "failed";
  catalog_revision?: string | null;
  output_binding: MaterialOutputBinding | null;
  error_code:
    | "RESTART_INTERRUPTED"
    | "MATERIAL_CONFIGURATION_INVALID"
    | "MATERIAL_ANALYSIS_FAILED"
    | "LOCAL_PROVIDER_TIMEOUT"
    | "LOCAL_PROVIDER_RATE_LIMITED"
    | "LOCAL_PROVIDER_TRANSIENT_ERROR"
    | "CONTROLLED_RESOURCE_INVALID"
    | "MATERIAL_OUTPUT_FAILED"
    | null;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
};

export type RegionView = {
  bbox: [number, number, number, number];
  coordinate_space: string;
};

export type EvidenceView = {
  element_id: string;
  evidence_id: string;
  material_ref: string;
  page_number: number;
  page_ref: string;
  region: RegionView;
};

export type MapConceptView = {
  id: string;
  label: string;
  definition: string;
  members: { name: string; definition: string; page_number: number }[];
  evidence: EvidenceView[];
  position: { x: number; y: number };
  quality: "accepted" | "needs_review";
  reason_code: string;
};

export type MapRelationType =
  | "prerequisite"
  | "contains"
  | "similar"
  | "confusing"
  | "application"
  | "example";

export type MapRelationView = {
  id: string;
  source: string;
  target: string;
  statement: string;
  evidence: EvidenceView[];
  reason_code: string;
  type: MapRelationType;
};

export type MapReviewView = {
  id: string;
  source: string;
  target: string;
  statement: string;
  evidence: EvidenceView[];
  reason_code: string;
  kind: string;
};

export type ArtifactStatusView = {
  processing: "succeeded" | "partial" | "failed";
  quality: "accepted" | "needs_review" | "unsupported";
  decision: "retain" | "review" | "reject";
  reason_code: string;
};

export type KnowledgeMapView = {
  schema: "knowledge-map-view/v1";
  material_ref: string;
  knowledge_map_revision: string;
  learning_path_revision: string;
  status: ArtifactStatusView;
  concepts: MapConceptView[];
  relations: MapRelationView[];
  review_items: MapReviewView[];
  path: ArtifactStatusView & { ordered_concept_ids: string[] };
  limitations: {
    reason_code: string;
    page_numbers: number[];
    affected_page_count: number;
  }[];
};

export type LearningResourceView = {
  resource_id: string;
  concept_id: string;
  subject: string;
  resource_key: string;
  title: string;
  source_locator: string;
  artifact_sha256: string;
  use_boundary: string;
  learning_use: string;
  match_basis: string;
  matched_terms: string[];
  processing: "succeeded" | "partial" | "failed";
  quality: "accepted" | "needs_review" | "unsupported";
  decision: "retain" | "review" | "reject";
  reason_code: string;
};

export type LearningResourceResultView = {
  schema: "learning-resource-result-view/v1";
  result_revision: string;
  source_study_material_output_revision: string;
  catalog_revision: string;
  subject: string;
  resources: LearningResourceView[];
  produced_at: string;
  run_id: string;
  processing: "succeeded" | "partial" | "failed";
  quality: "accepted" | "needs_review" | "unsupported";
  decision: "retain" | "review" | "reject";
  reason_code: string;
};

export type AssessmentView = {
  schema: "assessment-view/v1";
  assessment_view_id: string;
  version: string;
  knowledge_map_revision: string;
  learning_path_revision: string;
  scoring_rule_version: string;
  questions: {
    question_id: string;
    concept_id: string;
    question_type: "single_choice";
    prompt: string;
    options: { option_id: string; text: string }[];
    source_evidence_ids: string[];
  }[];
  practice_sets: {
    practice_set_id: string;
    concept_id: string;
    question_ids: string[];
  }[];
  processing: "succeeded";
  quality: "accepted";
  decision: "retain";
  reason_code: "ASSESSMENT_ACCEPTED";
};

export type LearningUpdateCreate = {
  schema: "learning-update-create/v1";
  material_id: string;
  map_revision: string;
  path_revision: string;
  assessment_revision: string;
  responses: {
    question_id: string;
    selected_option_id: string;
  }[];
};

export type MasteryView = {
  concept_id: string;
  valid_answer_count: number;
  correct_rate: number | null;
  practice_score: number;
  review_score: number;
  completion_score: number;
  recent_error_penalty: number | null;
  mastery_score: number | null;
  raw_band: "weak" | "learning" | "mastered" | null;
  final_status: "not_started" | "weak" | "review" | "learning" | "mastered";
  needs_review: boolean;
  source_answer_event_ids: string[];
  source_learning_event_ids: string[];
  reason_codes: string[];
};

export type WeaknessView = {
  concept_id: string;
  kind: "remediation_required" | "weak_mastery";
  reason_codes: string[];
  source_answer_event_ids: string[];
  source_learning_event_ids: string[];
};

export type SuggestionView = {
  is_personalized: boolean;
  action: "no_action" | "review_concept" | "practice_concept" | "start_concept" | "follow_initial_path";
  target_concept_id: string | null;
  mastery_data_score: number | null;
  weakness_score: number | null;
  path_alignment_score: number | null;
  prerequisite_score: number | null;
  action_clarity_score: number | null;
  learning_suggestion_score: number | null;
  level: "no_action" | "low" | "medium" | "high";
  fallback_action: "follow_initial_path" | null;
  fallback_target_concept_id: string | null;
  needs_review: boolean;
  decision: "retain" | "review" | "reject";
  reason_code: string;
  source_answer_event_ids: string[];
  source_learning_event_ids: string[];
};

export type LearningStateView = {
  schema: "learning-state-view/v1";
  state_revision: string;
  knowledge_map_revision: string;
  learning_path_revision: string;
  assessment_id: string;
  assessment_revision: string;
  scoring_rule_version: "single-choice-exact/v1";
  source_answer_event_ids: string[];
  source_learning_event_ids: string[];
  mastery: MasteryView[];
  weaknesses: WeaknessView[];
  suggestion: SuggestionView;
  processing: "succeeded" | "partial";
  quality: "accepted" | "needs_review";
  decision: "retain" | "review";
  reason_code: "LEARNING_STATE_ACCEPTED" | "LEARNING_STATE_NEEDS_REVIEW";
};

export type KnowledgeMapRequest = {
  materialId: string;
  runId: string;
  mapRevision: string;
  pathRevision: string;
};

export type LearningResourceRequest = {
  materialId: string;
  runId: string;
  resultRevision: string;
};

export type AssessmentRequest = {
  materialId: string;
  outputRevision: string;
  mapRevision: string;
  pathRevision: string;
  assessmentRevision: string;
};

export type LearningStateRequest = {
  materialId: string;
  stateRevision: string;
};
