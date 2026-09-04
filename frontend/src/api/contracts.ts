export type KnownApiReasonCode =
  | "REQUEST_INVALID"
  | "SESSION_REQUIRED"
  | "ORIGIN_NOT_ALLOWED"
  | "RESOURCE_NOT_FOUND"
  | "IDEMPOTENCY_CONFLICT"
  | "NO_SAFE_ASSESSMENT"
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
  schema: "material-processing-create/v1";
  material_id: string;
  source_artifact_id: string;
};

export type MaterialOutputBinding = {
  schema: "material-run-output-binding/v4";
  knowledge_structure_revision: string;
  runtime_lock_sha256: string;
  page_count: number;
  processing: "succeeded" | "partial";
  quality: "accepted" | "needs_review";
  decision: "retain" | "review";
  reason_codes: string[];
  ocr_calls: number;
  semantic_calls: number;
};

export type MaterialProcessingRunView = {
  schema: "material-processing-run/v4";
  run_id: string;
  material_id: string;
  source_artifact_id: string;
  status: "pending" | "running" | "succeeded" | "partial" | "failed";
  progress_stage: "queued" | "evidence" | "semantics" | "publishing" | "completed";
  completed_pages: number;
  total_pages: number | null;
  output_binding: MaterialOutputBinding | null;
  error_code: string | null;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
};

export type SourceLocatorView = {
  page: number;
  block_id: string;
  region: [number, number, number, number];
};

export type EvidenceView = {
  evidence_id: string;
  page_ref: string;
  page: number;
  block_order: number;
  kind: string;
  source_locator: SourceLocatorView;
  quote: string;
};

export type ResourceView = {
  resource_id: string;
  title: string;
  authors: string[];
  citation: string;
  license: string;
  license_url: string;
  source_url: string;
  pages: number[];
};

export type RelationType = "prerequisite" | "part_of" | "application" | "example" | "contrast";

export type KnowledgeStructureView = {
  schema: "knowledge-structure-view/v1";
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
    section_ids: string[];
    source_pages: number[];
    resources: ResourceView[];
  }[];
  relations: {
    relation_id: string;
    source_concept_id: string;
    target_concept_id: string;
    type: RelationType;
    learner_reason: string;
    evidence_refs: string[];
    context_refs: string[];
    inference_basis: "dependency" | "composition" | "usage" | "instantiation" | "comparison";
    confidence: number;
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

export type StudySessionCreate = {
  schema: "study-session-create/v2";
  material_id: string;
  knowledge_structure_revision: string;
  current_concept_id?: string | null;
};

export type StudySessionView = {
  schema: "study-session/v2";
  study_session_id: string;
  material_id: string;
  knowledge_structure_revision: string;
  current_concept_id: string | null;
  deferred_concept_ids: string[];
  status: "active" | "no_safe" | "completed";
  started_at: string;
  completed_at: string | null;
  event_watermark: number;
};

export type AssessmentCreate = { schema: "assessment-create/v2"; target_claim_id: string };
export type AssessmentOptionView = { option_id: string; text: string };
export type AssessmentView = {
  schema: "single-choice-assessment/v2";
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

export type AnswerSubmissionCreate = {
  schema: "answer-submission-create/v2";
  question_id: string;
  selected_option_id: string;
};

export type AnswerFeedbackView = {
  schema: "answer-feedback/v2";
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

export type ConceptLearningStateView = {
  concept_id: string;
  label: string;
  status: "not_started" | "learning" | "needs_review" | "mastered";
  attempts: number;
  correct_answers: number;
  qualified_correct_items: number;
  covered_claim_ids: string[];
  weak_claim_ids: string[];
  latest_is_correct: boolean | null;
};

export type NextActionView = {
  action: "assess" | "review_prerequisite" | "advance" | "defer" | "resume" | "no_safe" | "complete";
  target_concept_id: string | null;
  target_claim_id: string | null;
  prerequisite_concept_ids: string[];
  reason: string;
};

export type LearnerProgressView = {
  schema: "learner-progress/v2";
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

export type GuidanceApply = { schema: "guidance-apply/v2"; guidance_revision: string };
