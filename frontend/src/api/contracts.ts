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
  schema: "material-processing-run/v2";
  run_id: string;
  material_id: string;
  source_artifact_id: string;
  status: "pending" | "running" | "succeeded" | "partial" | "failed";
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
