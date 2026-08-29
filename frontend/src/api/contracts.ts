export type KnownApiReasonCode =
  | "REQUEST_INVALID"
  | "SESSION_REQUIRED"
  | "ORIGIN_NOT_ALLOWED"
  | "RESOURCE_NOT_FOUND"
  | "IDEMPOTENCY_CONFLICT"
  | "MATERIAL_TOO_LARGE"
  | "MATERIAL_PDF_INVALID"
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

export type MaterialProcessingCreate = {
  schema: "material-processing-create/v2";
  material_id: string;
  source_artifact_id: string;
};

export type MaterialOutputBinding = {
  schema: "material-run-output-binding/v3";
  producer_bundle_id: string;
  producer_run_id: string;
  concept_evidence_output_id: string;
  study_material_output_revision: string;
  knowledge_map_revision: string;
  runtime_binding_sha256: string;
  page_count: number;
  processing: "succeeded" | "partial";
  quality: "needs_review";
  decision: "review";
  reason_codes: string[];
  ocr_calls: number;
  concept_calls: number;
};

export type MaterialProcessingRunView = {
  schema: "material-processing-run/v3";
  run_id: string;
  material_id: string;
  source_artifact_id: string;
  status: "pending" | "running" | "succeeded" | "partial" | "failed";
  progress_stage: "queued" | "page_evidence" | "concept_generation" | "knowledge_map_generation" | "publishing" | "completed";
  completed_pages: number;
  total_pages: number | null;
  output_binding: MaterialOutputBinding | null;
  error_code: string | null;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
};

export type RegionView = {
  coordinate_space: "unrotated_pdf_points";
  bbox: [number, number, number, number];
};

export type EvidenceView = {
  evidence_id: string;
  page_ref: string;
  page_number: number;
  kind: string;
  region: RegionView;
};

export type ExcludedPageView = {
  page_ref: string;
  page_number: number;
  page_evidence_id: string | null;
  last_stage: "page_evidence" | "concept";
  processing: "failed";
  quality: "needs_review";
  decision: "reject";
  reason_codes: string[];
};

export type KnowledgeMapView = {
  schema: "knowledge-map-view/v6";
  material_ref: string;
  knowledge_map_revision: string;
  source_output_id: string;
  status: {
    processing: "succeeded" | "partial" | "failed";
    quality: "needs_review";
    decision: "review" | "reject";
    reason_codes: string[];
  };
  concepts: {
    formal_concept_id: string;
    label: string;
    claims: { claim_id: string; text: string; evidence: EvidenceView[] }[];
    source_concept_ids: string[];
    source_page_numbers: number[];
    supplementary_resources: {
      promotion_id: string;
      resource_concept_id: string;
      resource_id: string;
      label: string;
      title: string;
      authors: string[];
      source_url: string;
      citation: string;
      license: string;
      license_url: string;
      use_boundary: string;
      page_numbers: number[];
      resource_evidence_ids: string[];
      match_ids: string[];
      study_concept_ids: string[];
      match_reason: "EXACT_NORMALIZED_LABEL";
    }[];
    quality: "needs_review";
    decision: "review";
    reason_codes: string[];
  }[];
  relations: {
    relation_id: string;
    type: "prerequisite" | "contains" | "related";
    source_formal_concept_id: string;
    target_formal_concept_id: string;
    relation_evidence: {
      owner_formal_concept_id: string;
      claim_id: string;
      evidence_ids: string[];
    }[];
    quality: "needs_review";
    decision: "review";
    reason_codes: string[];
    is_in_prerequisite_cycle: boolean;
  }[];
  relation_diagnostics: {
    possible_pairs: number;
    candidate_pairs: number;
    selected_pairs: number;
    selected_signal_counts: Record<string, number>;
    evidence_gated_pairs: number;
    rejected_no_evidence: number;
    direction_conflicts: number;
    verifier_calls: number;
    verifier_accepted: number;
    verifier_rejected: number;
    verifier_unsupported: number;
    structural_proposals: number;
    contains_proposals: number;
    prerequisite_proposals: number;
    related_proposals: number;
    accepted_relations: number;
  };
  resource_binding: {
    context_revision: string;
    library_revision: string;
    matching_policy: "resource-context-exact-distinct-source/v3";
    promotion_policy: "resource-formal-concept-promotion/v1";
  };
  resource_diagnostics: {
    matches: number;
    promoted_matches: number;
    promoted_resources: number;
    dropped_matches: number;
    split_review_matches: number;
  };
  resource_decisions: {
    decision_id: string;
    match_id: string;
    study_concept_id: string;
    resource_concept_id: string;
    formal_concept_ids: string[];
    decision: "review" | "reject";
    reason_code: "RESOURCE_SPLIT_REVIEW_REQUIRED" | "RESOURCE_SOURCE_CONCEPT_DROPPED";
  }[];
  initial_learning_path: string[];
  excluded_pages: ExcludedPageView[];
};

export type KnowledgeMapRequest = {
  materialId: string;
  runId: string;
  mapRevision: string;
};

export type StudySessionCreate = {
  schema: "study-session-create/v1";
  material_id: string;
  knowledge_map_revision: string;
  current_formal_concept_id?: string | null;
};

export type StudySessionView = {
  schema: "study-session/v1";
  study_session_id: string;
  material_id: string;
  knowledge_map_revision: string;
  current_formal_concept_id: string | null;
  deferred_formal_concept_id: string | null;
  status: "active" | "completed";
  started_at: string;
  completed_at: string | null;
  event_watermark: number;
};

export type StudyConceptContextView = {
  formal_concept_id: string;
  label: string;
  claim_ids: string[];
  supplementary_resource_promotion_ids: string[];
};

export type StudyContextView = {
  schema: "study-context/v1";
  study_session_id: string;
  base_knowledge_map_revision: string;
  current_formal_concept_id: string | null;
  deferred_formal_concept_id: string | null;
  initial_learning_path: StudyConceptContextView[];
};

export type AssessmentCreate = {
  schema: "assessment-create/v1";
  target_claim_id: string;
};

export type AssessmentOptionView = {
  option_id: string;
  text: string;
};

export type AssessmentView = {
  schema: "single-choice-assessment-public/v1";
  study_session_id: string;
  knowledge_map_revision: string;
  assessment_revision: string;
  question_id: string;
  target_formal_concept_id: string;
  target_claim_id: string;
  source_evidence_ids: string[];
  question_type: "single_choice";
  prompt: string;
  options: [AssessmentOptionView, AssessmentOptionView, AssessmentOptionView, AssessmentOptionView];
  policy_revision: "single-choice-assessment-policy/v1";
};

export type AnswerSubmissionCreate = {
  schema: "answer-submission-create/v1";
  question_id: string;
  selected_option_id: string;
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

export type LearningStatus = "not_started" | "learning" | "needs_review" | "mastered";
export type LearningConfidence = "none" | "limited" | "supported";

export type ConceptLearningStateView = {
  formal_concept_id: string;
  status: LearningStatus;
  mastery_band: "no_evidence" | "developing" | "demonstrated";
  confidence: LearningConfidence;
  needs_more_data: boolean;
  required_claim_ids: string[];
  attempted_claim_ids: string[];
  latest_correct_claim_ids: string[];
  claim_coverage_complete: boolean;
  required_evidence_ids: string[];
  observed_evidence_ids: string[];
  evidence_coverage_complete: boolean;
  valid_attempts: number;
  correct_attempts: number;
  distinct_item_attempts: number;
  recent_result: "correct" | "incorrect" | null;
  repeated_error: boolean;
  post_error_improvement: boolean;
  explanation: string;
};

export type LearningStateView = {
  schema: "learning-state/v1";
  study_session_id: string;
  base_knowledge_map_revision: string;
  state_revision: string;
  event_watermark: number;
  all_mastered: boolean;
  concept_states: ConceptLearningStateView[];
};

export type WeaknessFindingView = {
  target_formal_concept_id: string;
  target_label: string;
  category: "observed_weak" | "needs_review" | "not_enough_data";
  confidence: LearningConfidence;
  claim_coverage_complete: boolean;
  remediation_intent: "practice" | "review" | "collect_more_data";
  reason: string;
};

export type PrerequisiteGapView = {
  category: "possible_prerequisite_gap";
  target_formal_concept_id: string;
  prerequisite_formal_concept_id: string;
  prerequisite_label: string;
  relation_id: string;
  prerequisite_status: LearningStatus;
  prerequisite_confidence: LearningConfidence;
  remediation_intent: "relearn_prerequisite";
  reason: string;
};

export type WeaknessView = {
  schema: "weakness/v1";
  study_session_id: string;
  base_knowledge_map_revision: string;
  source_learning_state_revision: string;
  event_watermark: number;
  current_formal_concept_id: string | null;
  weakness_revision: string;
  findings: WeaknessFindingView[];
  immediate_prerequisite_gaps: PrerequisiteGapView[];
};

export type AdaptiveAction =
  | "start"
  | "continue"
  | "practice"
  | "review"
  | "relearn_prerequisite"
  | "use_resource"
  | "follow_path"
  | "collect_more_data"
  | "no_action";

export type AdaptiveRouteView = {
  study_session_id: string;
  formal_concept_id: string | null;
  resource_promotion_id: string | null;
};

export type AdaptiveStepView = {
  action: AdaptiveAction;
  target_formal_concept_id: string | null;
  target_label: string | null;
  reason: string;
  confidence: LearningConfidence;
  claim_coverage_complete: boolean;
  route: AdaptiveRouteView;
};

export type AdaptivePlanView = {
  schema: "adaptive-plan/v1";
  study_session_id: string;
  base_knowledge_map_revision: string;
  inline_initial_learning_path_sha256: string;
  source_learning_state_revision: string;
  event_watermark: number;
  current_formal_concept_id: string | null;
  deferred_formal_concept_id: string | null;
  primary_step: AdaptiveStepView;
  adaptive_plan_revision: string;
};

export type SuggestionView = {
  schema: "learning-suggestion/v1";
  adaptive_plan_revision: string;
  study_session_id: string;
  base_knowledge_map_revision: string;
  action: AdaptiveAction;
  target_formal_concept_id: string | null;
  target_label: string | null;
  reason: string;
  confidence: LearningConfidence;
  claim_coverage_complete: boolean;
  route: AdaptiveRouteView;
  fallback_action: "follow_path" | "collect_more_data" | "no_action";
  fallback_reason: string;
};

export type AdaptiveResponseView = {
  schema: "adaptive-response/v1";
  plan: AdaptivePlanView;
  suggestion: SuggestionView;
};

export type AdaptivePlanApply = {
  schema: "adaptive-plan-apply/v1";
  adaptive_plan_revision: string;
};
