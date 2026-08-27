import type {
  AnswerFeedbackView,
  AssessmentView,
  KnowledgeMapView,
  MaterialProcessingRunView,
  StudyContextView,
  StudySessionView,
} from "../../src/api/contracts";

export const materialId = "4f9619ff-8b86-4e3a-a2f1-2bb9424d5c81";
export const artifactId = "5f9619ff-8b86-4e3a-a2f1-2bb9424d5c82";
export const runId = "6f9619ff-8b86-4e3a-a2f1-2bb9424d5c83";
export const studySessionId = "7f9619ff-8b86-4e3a-a2f1-2bb9424d5c84";
export const mapRevision = `knowledge-map:sha256:${"a".repeat(64)}`;
export const prerequisiteConceptId = revision("formal-concept", "1");
export const targetConceptId = revision("formal-concept", "2");
export const prerequisiteClaimId = revision("claim", "3");
export const targetClaimId = revision("claim", "4");

export function revision(prefix: string, value: string) {
  return `${prefix}:sha256:${value.repeat(64)}`;
}

function concept(id: string, claimId: string, label: string, pageNumber: number): KnowledgeMapView["concepts"][number] {
  const value = String(pageNumber);
  return {
    formal_concept_id: id,
    label,
    claims: [{
      claim_id: claimId,
      text: `${label} 的教材重點。`,
      evidence: [{
        evidence_id: revision("evidence", value),
        page_ref: revision("page", value),
        page_number: pageNumber,
        kind: "paragraph",
        region: { coordinate_space: "unrotated_pdf_points", bbox: [40, 50, 260, 90] },
      }],
    }],
    source_concept_ids: [revision("concept", value)],
    source_page_numbers: [pageNumber],
    supplementary_resources: [],
    quality: "needs_review",
    decision: "review",
    reason_codes: ["FORMAL_CONCEPT_REVIEW_REQUIRED"],
  };
}

export function mapView(): KnowledgeMapView {
  const prerequisite = concept(prerequisiteConceptId, prerequisiteClaimId, "先備概念", 1);
  const target = concept(targetConceptId, targetClaimId, "目標概念", 2);
  return {
    schema: "knowledge-map-view/v6",
    material_ref: revision("material", "5"),
    knowledge_map_revision: mapRevision,
    source_output_id: revision("study-material-output", "6"),
    status: {
      processing: "succeeded",
      quality: "needs_review",
      decision: "review",
      reason_codes: ["KNOWLEDGE_MAP_REVIEW_REQUIRED"],
    },
    concepts: [prerequisite, target],
    relations: [{
      relation_id: revision("formal-relation", "7"),
      type: "prerequisite",
      source_formal_concept_id: prerequisiteConceptId,
      target_formal_concept_id: targetConceptId,
      relation_evidence: [{
        owner_formal_concept_id: prerequisiteConceptId,
        claim_id: prerequisiteClaimId,
        evidence_ids: [prerequisite.claims[0].evidence[0].evidence_id],
      }],
      quality: "needs_review",
      decision: "review",
      reason_codes: ["RELATION_REVIEW_REQUIRED"],
      is_in_prerequisite_cycle: false,
    }],
    relation_diagnostics: {
      possible_pairs: 1,
      candidate_pairs: 1,
      selected_pairs: 1,
      selected_signal_counts: { explicit_relation: 1 },
      evidence_gated_pairs: 1,
      rejected_no_evidence: 0,
      direction_conflicts: 0,
      verifier_calls: 1,
      verifier_accepted: 1,
      verifier_rejected: 0,
      verifier_unsupported: 0,
      structural_proposals: 1,
      contains_proposals: 0,
      prerequisite_proposals: 1,
      related_proposals: 0,
      accepted_relations: 1,
    },
    resource_binding: {
      context_revision: revision("map-resource-context", "8"),
      library_revision: revision("resource-library", "9"),
      matching_policy: "resource-context-exact-distinct-source/v3",
      promotion_policy: "resource-formal-concept-promotion/v1",
    },
    resource_diagnostics: {
      matches: 0,
      promoted_matches: 0,
      promoted_resources: 0,
      dropped_matches: 0,
      split_review_matches: 0,
    },
    resource_decisions: [],
    initial_learning_path: [prerequisiteConceptId, targetConceptId],
    excluded_pages: [],
  };
}

export function runView(): MaterialProcessingRunView {
  return {
    schema: "material-processing-run/v2",
    run_id: runId,
    material_id: materialId,
    source_artifact_id: artifactId,
    status: "succeeded",
    output_binding: {
      schema: "material-run-output-binding/v3",
      producer_bundle_id: revision("text-first-producer-bundle", "a"),
      producer_run_id: "text-first-run:00000000-0000-4000-8000-000000000001",
      concept_evidence_output_id: revision("concept-evidence-output", "b"),
      study_material_output_revision: revision("study-material-output", "6"),
      knowledge_map_revision: mapRevision,
      runtime_binding_sha256: "c".repeat(64),
      page_count: 2,
      processing: "succeeded",
      quality: "needs_review",
      decision: "review",
      reason_codes: ["WHOLE_DOCUMENT_REVIEW_REQUIRED"],
      ocr_calls: 0,
      concept_calls: 1,
    },
    error_code: null,
    created_at: "2026-08-27T00:00:00Z",
    updated_at: "2026-08-27T00:01:00Z",
    completed_at: "2026-08-27T00:01:00Z",
  };
}

export function sessionView(overrides: Partial<StudySessionView> = {}): StudySessionView {
  return {
    schema: "study-session/v1",
    study_session_id: studySessionId,
    material_id: materialId,
    knowledge_map_revision: mapRevision,
    current_formal_concept_id: targetConceptId,
    deferred_formal_concept_id: null,
    status: "active",
    started_at: "2026-08-27T00:05:00Z",
    completed_at: null,
    event_watermark: 0,
    ...overrides,
  };
}

export function contextView(overrides: Partial<StudyContextView> = {}): StudyContextView {
  return {
    schema: "study-context/v1",
    study_session_id: studySessionId,
    base_knowledge_map_revision: mapRevision,
    current_formal_concept_id: targetConceptId,
    deferred_formal_concept_id: null,
    initial_learning_path: [
      {
        formal_concept_id: prerequisiteConceptId,
        label: "先備概念",
        claim_ids: [prerequisiteClaimId],
        supplementary_resource_promotion_ids: [],
      },
      {
        formal_concept_id: targetConceptId,
        label: "目標概念",
        claim_ids: [targetClaimId],
        supplementary_resource_promotion_ids: [],
      },
    ],
    ...overrides,
  };
}

export function assessmentView(round = 1): AssessmentView {
  const value = round === 1 ? "d" : "e";
  return {
    schema: "single-choice-assessment-public/v1",
    study_session_id: studySessionId,
    knowledge_map_revision: mapRevision,
    assessment_revision: revision("assessment", value),
    question_id: revision("question", value),
    target_formal_concept_id: targetConceptId,
    target_claim_id: targetClaimId,
    source_evidence_ids: [revision("evidence", "2")],
    question_type: "single_choice",
    prompt: round === 1 ? "哪個選項符合目標概念？" : "重新評量：哪個敘述符合教材？",
    options: [
      { option_id: revision("option", round === 1 ? "1" : "5"), text: "選項 A" },
      { option_id: revision("option", round === 1 ? "2" : "6"), text: "選項 B" },
      { option_id: revision("option", round === 1 ? "3" : "7"), text: "選項 C" },
      { option_id: revision("option", round === 1 ? "4" : "8"), text: "選項 D" },
    ],
    policy_revision: "single-choice-assessment-policy/v1",
  };
}

export function feedbackView(assessment: AssessmentView, selectedOptionId: string, isCorrect: boolean, eventNumber: number): AnswerFeedbackView {
  return {
    schema: "answer-feedback/v1",
    answer_event_id: eventNumber === 1
      ? "8f9619ff-8b86-4e3a-a2f1-2bb9424d5c85"
      : "9f9619ff-8b86-4e3a-a2f1-2bb9424d5c86",
    study_session_id: studySessionId,
    assessment_revision: assessment.assessment_revision,
    question_id: assessment.question_id,
    selected_option_id: selectedOptionId,
    is_correct: isCorrect,
    rationale: isCorrect ? "教材 Evidence 支持這個選項。" : "這個選項與教材 Evidence 不一致。",
    source_evidence_ids: assessment.source_evidence_ids,
    event_number: eventNumber,
    created_at: `2026-08-27T00:0${eventNumber + 5}:00Z`,
  };
}

export function apiError(reasonCode: string, retryable = false) {
  return {
    schema: "api-error/v1",
    request_id: "af9619ff-8b86-4e3a-a2f1-2bb9424d5c87",
    reason_code: reasonCode,
    retryable,
    message: "Request could not be completed.",
  };
}
