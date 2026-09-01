import type {
  AdaptiveAction,
  AdaptiveResponseView,
  AnswerFeedbackView,
  AssessmentView,
  KnowledgeMapView,
  LearningStateView,
  MaterialProcessingRunView,
  StudyContextView,
  StudySessionView,
  WeaknessView,
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
export const learningStateRevision = revision("learning-state", "f");

export function revision(prefix: string, value: string) {
  return `${prefix}:sha256:${value.repeat(64)}`;
}

function concept(id: string, claimId: string, label: string, pageNumber: number): KnowledgeMapView["concepts"][number] {
  const value = String(pageNumber);
  return {
    formal_concept_id: id,
    label,
    aliases: [],
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
    schema: "knowledge-map-view/v9",
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
    concept_diagnostics: {
      possible_pairs: 1,
      candidate_pairs: 0,
      selected_pairs: 0,
      pair_ceiling: 16,
      qwen_same_pairs: 0,
      qwen_distinct_pairs: 0,
      qwen_uncertain_pairs: 0,
      verifier_requested_pairs: 0,
      verifier_scored_pairs: 0,
      verifier_allowed_pairs: 0,
      verifier_vetoed_pairs: 0,
      verifier_unsupported_pairs: 0,
      verifier_failed_pairs: 0,
      source_concepts_before: 2,
      canonical_concepts_after: 2,
      duplicate_delta: 0,
      coverage_before: 2,
      coverage_after: 2,
    },
    relations: [{
      relation_id: revision("formal-relation", "7"),
      type: "prerequisite",
      source_formal_concept_id: prerequisiteConceptId,
      target_formal_concept_id: targetConceptId,
      reason: "The prerequisite supports learning the target concept.",
      inference_basis: "claim_semantics",
      relation_evidence: [{
        owner_formal_concept_id: prerequisiteConceptId,
        claim_id: prerequisiteClaimId,
        evidence_ids: [prerequisite.claims[0].evidence[0].evidence_id],
      }],
      relation_context: [],
      needs_review: false,
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
      model_calls: 1,
      model_no_relation_pairs: 0,
      model_review_pairs: 0,
      unexpected_pairs: 0,
      canonical_rejections: 0,
      verifier_calls: 1,
      verifier_accepted: 1,
      verifier_rejected: 0,
      verifier_unsupported: 0,
      model_contains_pairs: 0,
      model_prerequisite_pairs: 1,
      model_related_pairs: 0,
      invalid_pairs: 0,
      verifier_failures: 0,
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
    topology: {
      roots: [prerequisiteConceptId, targetConceptId],
      nodes: [prerequisite, target].map((item, index) => ({
        formal_concept_id: item.formal_concept_id,
        depth: 0,
        primary_parent_formal_concept_id: null,
        flat_group_id: revision("document-section", String(index + 1)),
        flat_group_anchor: {
          evidence_id: item.claims[0].evidence[0].evidence_id,
          page_ref: item.claims[0].evidence[0].page_ref,
          page_number: item.source_page_numbers[0],
          reading_order: index,
        },
      })),
      flat_groups: [prerequisite, target].map((item, index) => ({
        flat_group_id: revision("document-section", String(index + 1)),
        label: index === 0 ? "先備概念" : "目標概念",
        label_source: "heading",
        heading_evidence_id: item.claims[0].evidence[0].evidence_id,
        source_order: {
          evidence_id: item.claims[0].evidence[0].evidence_id,
          page_ref: item.claims[0].evidence[0].page_ref,
          page_number: item.source_page_numbers[0],
          reading_order: index,
        },
        formal_concept_ids: [item.formal_concept_id],
      })),
    },
    topology_diagnostics: {
      component_count: 2,
      orphan_concept_count: 2,
      secondary_parent_count: 0,
      skipped_parent_before_child_count: 0,
    },
    initial_learning_path: [
      {
        step_number: 1,
        formal_concept_id: prerequisiteConceptId,
        placement_reason: "依教材第 1 頁的首次出現位置安排。",
        order_basis: {
          prerequisite_formal_concept_ids: [],
          parent_formal_concept_ids: [],
          flat_group_id: revision("document-section", "1"),
          hierarchy_depth: 0,
          source_page_number: 1,
        },
      },
      {
        step_number: 2,
        formal_concept_id: targetConceptId,
        placement_reason: "先理解「先備概念」，再進入這個概念。",
        order_basis: {
          prerequisite_formal_concept_ids: [prerequisiteConceptId],
          parent_formal_concept_ids: [],
          flat_group_id: revision("document-section", "2"),
          hierarchy_depth: 0,
          source_page_number: 2,
        },
      },
    ],
    excluded_pages: [],
  };
}

export function runView(): MaterialProcessingRunView {
  return {
    schema: "material-processing-run/v3",
    run_id: runId,
    material_id: materialId,
    source_artifact_id: artifactId,
    status: "succeeded",
    progress_stage: "completed",
    completed_pages: 2,
    total_pages: 2,
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
    no_safe_deferred_formal_concept_ids: [],
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
    no_safe_deferred_formal_concept_ids: [],
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
    rationale: isCorrect ? "教材依據支持這個選項。" : "這個選項與教材依據不一致。",
    source_evidence_ids: assessment.source_evidence_ids,
    event_number: eventNumber,
    created_at: `2026-08-27T00:0${eventNumber + 5}:00Z`,
  };
}

function conceptState(
  formalConceptId: string,
  claimId: string,
  evidenceId: string,
  status: "not_started" | "learning" | "needs_review" | "mastered",
) {
  const hasEvidence = status !== "not_started";
  const mastered = status === "mastered";
  return {
    formal_concept_id: formalConceptId,
    status,
    mastery_band: mastered ? "demonstrated" as const : hasEvidence ? "developing" as const : "no_evidence" as const,
    confidence: mastered ? "supported" as const : hasEvidence ? "limited" as const : "none" as const,
    needs_more_data: !mastered,
    required_claim_ids: [claimId],
    attempted_claim_ids: hasEvidence ? [claimId] : [],
    latest_correct_claim_ids: mastered ? [claimId] : [],
    claim_coverage_complete: mastered,
    required_evidence_ids: [evidenceId],
    observed_evidence_ids: hasEvidence ? [evidenceId] : [],
    evidence_coverage_complete: mastered,
    valid_attempts: hasEvidence ? 2 : 0,
    correct_attempts: mastered ? 2 : 0,
    distinct_item_attempts: hasEvidence ? 2 : 0,
    recent_result: mastered ? "correct" as const : hasEvidence ? "incorrect" as const : null,
    repeated_error: status === "needs_review",
    post_error_improvement: mastered,
    explanation: mastered ? "本次學習已取得足夠的可信正確作答。" : hasEvidence ? "目前結果仍需要更多練習。" : "尚未有可信作答紀錄，需要先完成評量。",
  };
}

export function learningStateView(options: {
  eventWatermark?: number;
  prerequisiteStatus?: "not_started" | "learning" | "needs_review" | "mastered";
  stateRevision?: string;
  targetStatus?: "not_started" | "learning" | "needs_review" | "mastered";
} = {}): LearningStateView {
  const prerequisiteStatus = options.prerequisiteStatus ?? "not_started";
  const targetStatus = options.targetStatus ?? "not_started";
  return {
    schema: "learning-state/v1",
    study_session_id: studySessionId,
    base_knowledge_map_revision: mapRevision,
    state_revision: options.stateRevision ?? learningStateRevision,
    event_watermark: options.eventWatermark ?? 0,
    all_mastered: prerequisiteStatus === "mastered" && targetStatus === "mastered",
    concept_states: [
      conceptState(prerequisiteConceptId, prerequisiteClaimId, revision("evidence", "1"), prerequisiteStatus),
      conceptState(targetConceptId, targetClaimId, revision("evidence", "2"), targetStatus),
    ],
  };
}

export function weaknessView(options: {
  category?: "none" | "not_enough_data" | "observed_weak" | "prerequisite_gap";
  currentConceptId?: string | null;
  eventWatermark?: number;
  stateRevision?: string;
} = {}): WeaknessView {
  const category = options.category ?? "none";
  const findings = category === "none" || category === "prerequisite_gap" ? [] : [{
    target_formal_concept_id: targetConceptId,
    target_label: "目標概念",
    category,
    confidence: category === "observed_weak" ? "supported" as const : "none" as const,
    claim_coverage_complete: false,
    remediation_intent: category === "observed_weak" ? "practice" as const : "collect_more_data" as const,
    reason: category === "observed_weak" ? "多次可信錯誤顯示這個概念目前需要練習。" : "目前資料不足，先完成更多評量。",
  }];
  const gaps = category === "prerequisite_gap" ? [{
    category: "possible_prerequisite_gap" as const,
    target_formal_concept_id: targetConceptId,
    prerequisite_formal_concept_id: prerequisiteConceptId,
    prerequisite_label: "先備概念",
    relation_id: revision("formal-relation", "7"),
    prerequisite_status: "not_started" as const,
    prerequisite_confidence: "none" as const,
    remediation_intent: "relearn_prerequisite" as const,
    reason: "先補強尚未掌握、需要先理解的基礎概念，再回到目前目標。",
  }] : [];
  return {
    schema: "weakness/v1",
    study_session_id: studySessionId,
    base_knowledge_map_revision: mapRevision,
    source_learning_state_revision: options.stateRevision ?? learningStateRevision,
    event_watermark: options.eventWatermark ?? 0,
    current_formal_concept_id: options.currentConceptId === undefined ? targetConceptId : options.currentConceptId,
    weakness_revision: revision("weakness", category === "prerequisite_gap" ? "b" : "a"),
    findings,
    immediate_prerequisite_gaps: gaps,
  };
}

export function adaptiveView(options: {
  action?: AdaptiveAction;
  currentConceptId?: string | null;
  deferredConceptId?: string | null;
  noSafeDeferredConceptIds?: string[];
  eventWatermark?: number;
  planValue?: string;
  stateRevision?: string;
  targetConceptId?: string | null;
  targetLabel?: string | null;
} = {}): AdaptiveResponseView {
  const action = options.action ?? "collect_more_data";
  const currentConceptId = options.currentConceptId === undefined ? targetConceptId : options.currentConceptId;
  const targetId = options.targetConceptId === undefined ? currentConceptId : options.targetConceptId;
  const targetLabel = options.targetLabel === undefined
    ? targetId === prerequisiteConceptId ? "先備概念" : targetId === targetConceptId ? "目標概念" : null
    : options.targetLabel;
  const planRevision = revision("adaptive-plan", options.planValue ?? "c");
  const route = {
    study_session_id: studySessionId,
    formal_concept_id: targetId,
    resource_promotion_id: null,
  };
  const step = {
    action,
    target_formal_concept_id: targetId,
    target_label: targetLabel,
    reason: action === "relearn_prerequisite"
      ? "先補強尚未掌握、需要先理解的基礎概念，再回到目前目標。"
      : "目前資料不足，先取得更多可信作答證據。",
    confidence: "limited" as const,
    claim_coverage_complete: false,
    route,
  };
  return {
    schema: "adaptive-response/v1",
    plan: {
      schema: "adaptive-plan/v1",
      study_session_id: studySessionId,
      base_knowledge_map_revision: mapRevision,
      inline_initial_learning_path_sha256: "d".repeat(64),
      source_learning_state_revision: options.stateRevision ?? learningStateRevision,
      event_watermark: options.eventWatermark ?? 0,
      current_formal_concept_id: currentConceptId,
      deferred_formal_concept_id: options.deferredConceptId ?? null,
      no_safe_deferred_formal_concept_ids: options.noSafeDeferredConceptIds ?? [],
      primary_step: step,
      adaptive_plan_revision: planRevision,
    },
    suggestion: {
      schema: "learning-suggestion/v1",
      adaptive_plan_revision: planRevision,
      study_session_id: studySessionId,
      base_knowledge_map_revision: mapRevision,
      action,
      target_formal_concept_id: targetId,
      target_label: targetLabel,
      reason: step.reason,
      confidence: step.confidence,
      claim_coverage_complete: step.claim_coverage_complete,
      route,
      fallback_action: "collect_more_data",
      fallback_reason: "若目前動作無法完成，先取得更多可信作答證據。",
    },
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
