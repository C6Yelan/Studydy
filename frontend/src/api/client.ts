import type {
  AnswerFeedbackView,
  AnswerSubmissionCreate,
  AdaptivePlanApply,
  AdaptiveResponseView,
  ApiErrorView,
  ApiReasonCode,
  AssessmentCreate,
  AssessmentView,
  KnowledgeMapRequest,
  KnowledgeMapView,
  LearningStateView,
  KnownApiReasonCode,
  MaterialProcessingCreate,
  MaterialProcessingRunView,
  MaterialView,
  StudyContextView,
  StudySessionCreate,
  StudySessionView,
  WeaknessView,
} from "./contracts";

type FetchRequest = (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;
type JsonObject = Record<string, unknown>;

const knownApiReasons = new Set<KnownApiReasonCode>([
  "REQUEST_INVALID",
  "SESSION_REQUIRED",
  "ORIGIN_NOT_ALLOWED",
  "RESOURCE_NOT_FOUND",
  "IDEMPOTENCY_CONFLICT",
  "MATERIAL_TOO_LARGE",
  "MATERIAL_PDF_INVALID",
  "UNSUPPORTED_MEDIA_TYPE",
  "STORAGE_UNAVAILABLE",
  "INTERNAL_ERROR",
]);
const uuidPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const sha256Pattern = /^[0-9a-f]{64}$/;
const maximumPdfBytes = 100 * 1024 * 1024;

function requestOrigin(): string {
  return globalThis.location?.origin ?? "http://127.0.0.1:4173";
}

export class ApiClientError extends Error {
  readonly kind: "api" | "network" | "schema" | "input";
  readonly status: number | null;
  readonly reasonCode: ApiReasonCode | "NETWORK_ERROR" | "RESPONSE_SCHEMA_MISMATCH" | "REQUEST_INPUT_INVALID";
  readonly requestId: string | null;
  readonly retryable: boolean;

  constructor(
    kind: ApiClientError["kind"],
    message: string,
    details: {
      status?: number;
      reasonCode: ApiClientError["reasonCode"];
      requestId?: string;
      retryable?: boolean;
    },
  ) {
    super(message);
    this.name = "ApiClientError";
    this.kind = kind;
    this.status = details.status ?? null;
    this.reasonCode = details.reasonCode;
    this.requestId = details.requestId ?? null;
    this.retryable = details.retryable ?? false;
  }
}

function object(value: unknown): JsonObject | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as JsonObject
    : null;
}

function closed(value: unknown, keys: readonly string[]): JsonObject | null {
  const item = object(value);
  return item && Object.keys(item).length === keys.length && keys.every((key) => Object.hasOwn(item, key))
    ? item
    : null;
}

function isUuid(value: unknown): value is string {
  return typeof value === "string" && uuidPattern.test(value);
}

function isRevision(value: unknown, prefix: string): value is string {
  return typeof value === "string"
    && value.startsWith(`${prefix}:sha256:`)
    && sha256Pattern.test(value.slice(prefix.length + 8));
}

function isHttpUrl(value: unknown): value is string {
  if (typeof value !== "string") return false;
  try {
    const url = new URL(value);
    return url.protocol === "https:" || url.protocol === "http:";
  } catch {
    return false;
  }
}

function isStringArray(value: unknown, minimum = 0, maximum?: number): value is string[] {
  return Array.isArray(value)
    && value.length >= minimum
    && (maximum === undefined || value.length <= maximum)
    && value.every((item) => typeof item === "string" && item.length > 0);
}

function isSortedUniqueStrings(value: unknown, minimum = 1, maximum = 64): value is string[] {
  return isStringArray(value, minimum, maximum)
    && value.every((item, index) => index === 0 || value[index - 1] < item);
}

function isMaterial(value: unknown): value is MaterialView {
  const item = closed(value, ["schema", "material_id", "source_artifact_id", "source_sha256", "size_bytes"]);
  return !!item
    && item.schema === "material/v1"
    && isUuid(item.material_id)
    && isUuid(item.source_artifact_id)
    && typeof item.source_sha256 === "string"
    && sha256Pattern.test(item.source_sha256)
    && Number.isInteger(item.size_bytes)
    && Number(item.size_bytes) > 0;
}

function isBinding(value: unknown): boolean {
  const item = closed(value, [
    "schema", "producer_bundle_id", "producer_run_id", "concept_evidence_output_id",
    "study_material_output_revision", "knowledge_map_revision", "runtime_binding_sha256",
    "page_count", "processing", "quality", "decision", "reason_codes", "ocr_calls", "concept_calls",
  ]);
  return !!item
    && item.schema === "material-run-output-binding/v3"
    && isRevision(item.producer_bundle_id, "text-first-producer-bundle")
    && typeof item.producer_run_id === "string"
    && item.producer_run_id.startsWith("text-first-run:")
    && isUuid(item.producer_run_id.slice("text-first-run:".length))
    && isRevision(item.concept_evidence_output_id, "concept-evidence-output")
    && isRevision(item.study_material_output_revision, "study-material-output")
    && isRevision(item.knowledge_map_revision, "knowledge-map")
    && typeof item.runtime_binding_sha256 === "string"
    && sha256Pattern.test(item.runtime_binding_sha256)
    && Number.isInteger(item.page_count)
    && Number(item.page_count) >= 1
    && (item.processing === "succeeded" || item.processing === "partial")
    && item.quality === "needs_review"
    && item.decision === "review"
    && isSortedUniqueStrings(item.reason_codes)
    && Number.isInteger(item.ocr_calls)
    && Number(item.ocr_calls) >= 0
    && Number(item.ocr_calls) <= Number(item.page_count)
    && Number.isInteger(item.concept_calls)
    && Number(item.concept_calls) >= 0;
}

function isMaterialRun(value: unknown): value is MaterialProcessingRunView {
  const item = closed(value, [
    "schema", "run_id", "material_id", "source_artifact_id", "status", "output_binding",
    "progress_stage", "completed_pages", "total_pages", "error_code", "created_at",
    "updated_at", "completed_at",
  ]);
  if (!item || item.schema !== "material-processing-run/v3") return false;
  if (!isUuid(item.run_id) || !isUuid(item.material_id) || !isUuid(item.source_artifact_id)) return false;
  if (typeof item.created_at !== "string" || typeof item.updated_at !== "string") return false;
  const stages = ["queued", "page_evidence", "concept_generation", "knowledge_map_generation", "publishing", "completed"];
  if (!stages.includes(String(item.progress_stage)) || !Number.isInteger(item.completed_pages) || Number(item.completed_pages) < 0) return false;
  if (item.progress_stage === "queued") {
    if (item.completed_pages !== 0 || item.total_pages !== null) return false;
  } else if (
    !Number.isInteger(item.total_pages)
    || Number(item.total_pages) < 1
    || Number(item.completed_pages) > Number(item.total_pages)
    || ((item.progress_stage === "knowledge_map_generation" || item.progress_stage === "publishing")
      && item.completed_pages !== item.total_pages)
  ) return false;
  const hasBinding = isBinding(item.output_binding);
  if (item.status === "succeeded" || item.status === "partial") {
    return hasBinding
      && object(item.output_binding)?.processing === item.status
      && item.progress_stage === "completed"
      && item.completed_pages === object(item.output_binding)?.page_count
      && item.total_pages === object(item.output_binding)?.page_count
      && item.error_code === null
      && typeof item.completed_at === "string";
  }
  if (item.status === "failed") {
    return item.output_binding === null
      && item.progress_stage !== "completed"
      && typeof item.error_code === "string"
      && typeof item.completed_at === "string";
  }
  return (item.status === "pending" || item.status === "running")
    && item.progress_stage !== "completed"
    && (item.status !== "pending" || item.progress_stage === "queued")
    && item.output_binding === null
    && item.error_code === null
    && item.completed_at === null;
}

function isRegion(value: unknown): boolean {
  const region = closed(value, ["coordinate_space", "bbox"]);
  return !!region
    && region.coordinate_space === "unrotated_pdf_points"
    && Array.isArray(region.bbox)
    && region.bbox.length === 4
    && region.bbox.every((number) => typeof number === "number" && Number.isFinite(number))
    && Number(region.bbox[0]) < Number(region.bbox[2])
    && Number(region.bbox[1]) < Number(region.bbox[3]);
}

function isEvidence(value: unknown, pageRef?: string): boolean {
  const item = closed(value, ["evidence_id", "page_ref", "page_number", "kind", "region"]);
  return !!item
    && isRevision(item.evidence_id, "evidence")
    && isRevision(item.page_ref, "page")
    && (pageRef === undefined || item.page_ref === pageRef)
    && Number.isInteger(item.page_number)
    && Number(item.page_number) >= 1
    && typeof item.kind === "string" && item.kind.length >= 1 && item.kind.length <= 64
    && isRegion(item.region);
}

function isExcludedPage(value: unknown): boolean {
  const page = closed(value, [
    "page_ref", "page_number", "page_evidence_id", "last_stage", "processing",
    "quality", "decision", "reason_codes",
  ]);
  return !!page
    && isRevision(page.page_ref, "page")
    && Number.isInteger(page.page_number)
    && Number(page.page_number) >= 1
    && (page.page_evidence_id === null || typeof page.page_evidence_id === "string")
    && (page.last_stage === "page_evidence" || page.last_stage === "concept")
    && page.processing === "failed"
    && page.quality === "needs_review"
    && page.decision === "reject"
    && isSortedUniqueStrings(page.reason_codes);
}

function isRelationDiagnostics(value: unknown): boolean {
  const diagnostics = closed(value, [
    "possible_pairs", "candidate_pairs", "selected_pairs", "selected_signal_counts",
    "evidence_gated_pairs", "rejected_no_evidence", "direction_conflicts",
    "verifier_calls", "verifier_accepted", "verifier_rejected", "verifier_unsupported",
    "structural_proposals", "contains_proposals", "prerequisite_proposals",
    "related_proposals", "accepted_relations",
  ]);
  if (!diagnostics) return false;
  const countNames = [
    "possible_pairs", "candidate_pairs", "selected_pairs", "evidence_gated_pairs",
    "rejected_no_evidence", "direction_conflicts", "verifier_calls", "verifier_accepted",
    "verifier_rejected", "verifier_unsupported", "structural_proposals",
    "contains_proposals", "prerequisite_proposals", "related_proposals",
    "accepted_relations",
  ];
  const signals = object(diagnostics.selected_signal_counts);
  const allowedSignals = new Set([
    "adjacent", "same_group", "same_page", "explicit_relation", "cross_reference",
    "label_mention", "shared_evidence", "shared_formula",
  ]);
  return countNames.every((name) => Number.isInteger(diagnostics[name]) && Number(diagnostics[name]) >= 0)
    && !!signals
    && Object.entries(signals).every(([name, count]) => allowedSignals.has(name)
      && Number.isInteger(count) && Number(count) >= 0)
    && Number(diagnostics.selected_pairs) <= Number(diagnostics.candidate_pairs)
    && Number(diagnostics.candidate_pairs) <= Number(diagnostics.possible_pairs)
    && Number(diagnostics.verifier_accepted) + Number(diagnostics.verifier_rejected)
      <= Number(diagnostics.verifier_calls)
    && Number(diagnostics.structural_proposals) === Number(diagnostics.contains_proposals)
      + Number(diagnostics.prerequisite_proposals);
}

function isConceptDiagnostics(value: unknown): boolean {
  const names = [
    "possible_pairs", "candidate_pairs", "selected_pairs", "pair_ceiling",
    "qwen_same_pairs", "qwen_distinct_pairs", "qwen_uncertain_pairs",
    "verifier_requested_pairs", "verifier_scored_pairs", "verifier_allowed_pairs",
    "verifier_vetoed_pairs", "verifier_unsupported_pairs", "verifier_failed_pairs",
    "source_concepts_before", "canonical_concepts_after", "duplicate_delta",
    "coverage_before", "coverage_after",
  ];
  const diagnostics = closed(value, names);
  return !!diagnostics
    && names.every((name) => Number.isInteger(diagnostics[name]) && Number(diagnostics[name]) >= 0)
    && Number(diagnostics.selected_pairs) <= Number(diagnostics.candidate_pairs)
    && Number(diagnostics.candidate_pairs) <= Number(diagnostics.possible_pairs)
    && Number(diagnostics.selected_pairs) === Number(diagnostics.qwen_same_pairs)
      + Number(diagnostics.qwen_distinct_pairs) + Number(diagnostics.qwen_uncertain_pairs)
    && Number(diagnostics.verifier_requested_pairs) === Number(diagnostics.qwen_same_pairs)
    && Number(diagnostics.verifier_requested_pairs) === Number(diagnostics.verifier_scored_pairs)
      + Number(diagnostics.verifier_unsupported_pairs) + Number(diagnostics.verifier_failed_pairs)
    && Number(diagnostics.verifier_scored_pairs) === Number(diagnostics.verifier_allowed_pairs)
      + Number(diagnostics.verifier_vetoed_pairs)
    && Number(diagnostics.duplicate_delta) === Number(diagnostics.source_concepts_before)
      - Number(diagnostics.canonical_concepts_after)
    && diagnostics.coverage_before === diagnostics.coverage_after;
}

function isKnowledgeMap(value: unknown): value is KnowledgeMapView {
  const item = closed(value, [
    "schema", "material_ref", "knowledge_map_revision", "source_output_id", "status",
    "concepts", "concept_diagnostics", "relations", "relation_diagnostics", "resource_binding",
    "resource_diagnostics", "resource_decisions", "initial_learning_path", "excluded_pages",
  ]);
  if (!item
    || item.schema !== "knowledge-map-view/v6"
    || !isRevision(item.material_ref, "material")
    || !isRevision(item.knowledge_map_revision, "knowledge-map")
    || !isRevision(item.source_output_id, "study-material-output")
    || !Array.isArray(item.concepts)
    || !isConceptDiagnostics(item.concept_diagnostics)
    || !Array.isArray(item.relations)
    || !isRelationDiagnostics(item.relation_diagnostics)
    || !Array.isArray(item.resource_decisions)
    || !Array.isArray(item.initial_learning_path)
    || !Array.isArray(item.excluded_pages)) return false;
  const status = closed(item.status, ["processing", "quality", "decision", "reason_codes"]);
  if (!status
    || !["succeeded", "partial", "failed"].includes(String(status.processing))
    || status.quality !== "needs_review"
    || (status.decision !== "review" && status.decision !== "reject")
    || !isSortedUniqueStrings(status.reason_codes)) return false;
  const conceptsAreValid = item.concepts.every((value) => {
    const concept = closed(value, [
      "formal_concept_id", "label", "claims", "source_concept_ids", "source_page_numbers",
      "aliases", "supplementary_resources", "quality", "decision", "reason_codes",
    ]);
    return !!concept
      && isRevision(concept.formal_concept_id, "formal-concept")
      && typeof concept.label === "string" && concept.label.length >= 1
      && isSortedUniqueStrings(concept.aliases, 0)
      && !(concept.aliases as unknown[]).includes(concept.label)
      && isStringArray(concept.source_concept_ids, 1)
      && Array.isArray(concept.source_page_numbers)
      && concept.source_page_numbers.length > 0
      && concept.source_page_numbers.every((page) => Number.isInteger(page) && Number(page) >= 1)
      && Array.isArray(concept.supplementary_resources)
      && concept.supplementary_resources.every((value) => {
        const resource = closed(value, [
          "promotion_id",
          "resource_concept_id", "resource_id", "label", "title", "authors", "source_url",
          "citation", "license", "license_url", "use_boundary", "page_numbers",
          "resource_evidence_ids", "match_ids", "study_concept_ids", "match_reason",
        ]);
        return !!resource
          && isRevision(resource.promotion_id, "resource-promotion")
          && isRevision(resource.resource_concept_id, "resource-concept")
          && isRevision(resource.resource_id, "resource")
          && ["label", "title", "citation", "license", "use_boundary"]
            .every((field) => typeof resource[field] === "string" && String(resource[field]).length > 0)
          && isHttpUrl(resource.source_url)
          && isHttpUrl(resource.license_url)
          && isStringArray(resource.authors, 1)
          && Array.isArray(resource.page_numbers) && resource.page_numbers.length > 0
          && resource.page_numbers.every((page) => Number.isInteger(page) && Number(page) >= 1)
          && isStringArray(resource.resource_evidence_ids, 1)
          && isStringArray(resource.match_ids, 1)
          && isStringArray(resource.study_concept_ids, 1)
          && (resource.study_concept_ids as unknown[]).every((id) =>
            (concept.source_concept_ids as unknown[]).includes(id))
          && resource.match_reason === "EXACT_NORMALIZED_LABEL";
      })
      && Array.isArray(concept.claims)
      && concept.claims.length > 0
      && concept.claims.every((value) => {
        const claim = closed(value, ["claim_id", "text", "evidence"]);
        return !!claim
          && isRevision(claim.claim_id, "claim")
          && typeof claim.text === "string" && claim.text.length > 0
          && Array.isArray(claim.evidence) && claim.evidence.length > 0
          && new Set(claim.evidence.map((evidence) => object(evidence)?.evidence_id)).size === claim.evidence.length
          && claim.evidence.every((evidence) => isEvidence(evidence)
            && (concept.source_page_numbers as unknown[]).includes(object(evidence)?.page_number));
      })
      && concept.quality === "needs_review"
      && concept.decision === "review"
      && isSortedUniqueStrings(concept.reason_codes);
  });
  if (!conceptsAreValid
    || !item.excluded_pages.every(isExcludedPage)) return false;
  const resourceBinding = closed(item.resource_binding, [
    "context_revision", "library_revision", "matching_policy", "promotion_policy",
  ]);
  const resourceDiagnostics = closed(item.resource_diagnostics, [
    "matches", "promoted_matches", "promoted_resources", "dropped_matches",
    "split_review_matches",
  ]);
  const resourceCounts = resourceDiagnostics && [
    "matches", "promoted_matches", "promoted_resources", "dropped_matches",
    "split_review_matches",
  ].every((field) => Number.isInteger(resourceDiagnostics[field])
    && Number(resourceDiagnostics[field]) >= 0);
  if (!resourceBinding
    || !isRevision(resourceBinding.context_revision, "map-resource-context")
    || !isRevision(resourceBinding.library_revision, "resource-library")
    || resourceBinding.matching_policy !== "resource-context-exact-distinct-source/v3"
    || resourceBinding.promotion_policy !== "resource-formal-concept-promotion/v1"
    || !resourceCounts
    || Number(resourceDiagnostics.matches) !== Number(resourceDiagnostics.promoted_matches)
      + Number(resourceDiagnostics.dropped_matches) + Number(resourceDiagnostics.split_review_matches)) return false;
  const conceptIds = item.concepts.map((entry) => object(entry)?.formal_concept_id);
  const claimsByConcept = new Map(
    (item.concepts as unknown as KnowledgeMapView["concepts"]).map((concept) => [
      concept.formal_concept_id,
      new Map(concept.claims.map((claim) => [
        claim.claim_id,
        new Set(claim.evidence.map((evidence) => evidence.evidence_id)),
      ])),
    ]),
  );
  const relationsAreValid = item.relations.every((value) => {
    const relation = closed(value, [
      "relation_id", "type", "source_formal_concept_id", "target_formal_concept_id",
      "relation_evidence", "quality", "decision",
      "reason_codes", "is_in_prerequisite_cycle",
    ]);
    return !!relation
      && isRevision(relation.relation_id, "formal-relation")
      && ["prerequisite", "contains", "related"].includes(String(relation.type))
      && conceptIds.includes(relation.source_formal_concept_id)
      && conceptIds.includes(relation.target_formal_concept_id)
      && relation.source_formal_concept_id !== relation.target_formal_concept_id
      && Array.isArray(relation.relation_evidence)
      && relation.relation_evidence.length > 0
      && relation.relation_evidence.every((value) => {
        const evidence = closed(value, [
          "owner_formal_concept_id", "claim_id", "evidence_ids",
        ]);
        return !!evidence
          && [relation.source_formal_concept_id, relation.target_formal_concept_id]
            .includes(evidence.owner_formal_concept_id)
          && isRevision(evidence.claim_id, "claim")
          && isStringArray(evidence.evidence_ids, 1)
          && (evidence.evidence_ids as string[]).every(
            (item, index, items) => index === 0 || items[index - 1] < item,
          )
          && (evidence.evidence_ids as unknown[]).every((evidenceId) =>
            claimsByConcept.get(String(evidence.owner_formal_concept_id))
              ?.get(String(evidence.claim_id))?.has(String(evidenceId)) === true);
      })
      && new Set((relation.relation_evidence as unknown as Array<Record<string, unknown>>)
        .map((evidence) => `${String(evidence.owner_formal_concept_id)}:${String(evidence.claim_id)}`))
        .size === relation.relation_evidence.length
      && relation.quality === "needs_review"
      && relation.decision === "review"
      && isSortedUniqueStrings(relation.reason_codes)
      && typeof relation.is_in_prerequisite_cycle === "boolean";
  });
  const excludedRefs = item.excluded_pages.map((entry) => object(entry)?.page_ref);
  const excludedNumbers = item.excluded_pages.map((entry) => object(entry)?.page_number);
  const promotedMatchIds = (item.concepts as unknown as KnowledgeMapView["concepts"])
    .flatMap((concept) => concept.supplementary_resources.flatMap((resource) => resource.match_ids));
  const resourceDecisionsAreValid = item.resource_decisions.every((value) => {
    const decision = closed(value, [
      "decision_id",
      "match_id", "study_concept_id", "resource_concept_id", "formal_concept_ids",
      "decision", "reason_code",
    ]);
    return !!decision
      && isRevision(decision.decision_id, "resource-promotion-decision")
      && isRevision(decision.match_id, "resource-match")
      && typeof decision.study_concept_id === "string"
      && isRevision(decision.resource_concept_id, "resource-concept")
      && isStringArray(decision.formal_concept_ids)
      && (decision.formal_concept_ids as unknown[]).every((id) => conceptIds.includes(id))
      && ((decision.decision === "reject"
        && decision.reason_code === "RESOURCE_SOURCE_CONCEPT_DROPPED"
        && (decision.formal_concept_ids as unknown[]).length === 0)
        || (decision.decision === "review"
          && decision.reason_code === "RESOURCE_SPLIT_REVIEW_REQUIRED"
          && (decision.formal_concept_ids as unknown[]).length >= 2));
  });
  const decisionMatchIds = item.resource_decisions.map((entry) => object(entry)?.match_id);
  return new Set(conceptIds).size === conceptIds.length
    && relationsAreValid
    && new Set(item.relations.map((entry) => object(entry)?.relation_id)).size === item.relations.length
    && resourceDecisionsAreValid
    && new Set(promotedMatchIds).size === promotedMatchIds.length
    && new Set(decisionMatchIds).size === decisionMatchIds.length
    && promotedMatchIds.every((id) => !decisionMatchIds.includes(id))
    && promotedMatchIds.length === Number(resourceDiagnostics.promoted_matches)
    && promotedMatchIds.length + decisionMatchIds.length === Number(resourceDiagnostics.matches)
    && isStringArray(item.initial_learning_path)
    && item.initial_learning_path.length === conceptIds.length
    && new Set(item.initial_learning_path).size === conceptIds.length
    && item.initial_learning_path.every((id) => conceptIds.includes(id))
    && new Set(excludedRefs).size === excludedRefs.length
    && new Set(excludedNumbers).size === excludedNumbers.length
    && (item.excluded_pages.length === 0 || status.processing === "partial");
}

function isDateTime(value: unknown): value is string {
  return typeof value === "string" && value.length > 0 && Number.isFinite(Date.parse(value));
}

function isStudySession(value: unknown): value is StudySessionView {
  const item = closed(value, [
    "schema", "study_session_id", "material_id", "knowledge_map_revision",
    "current_formal_concept_id", "deferred_formal_concept_id", "status",
    "started_at", "completed_at", "event_watermark",
  ]);
  if (!item
    || item.schema !== "study-session/v1"
    || !isUuid(item.study_session_id)
    || !isUuid(item.material_id)
    || !isRevision(item.knowledge_map_revision, "knowledge-map")
    || !(item.current_formal_concept_id === null || isRevision(item.current_formal_concept_id, "formal-concept"))
    || !(item.deferred_formal_concept_id === null || isRevision(item.deferred_formal_concept_id, "formal-concept"))
    || (item.deferred_formal_concept_id !== null && item.deferred_formal_concept_id === item.current_formal_concept_id)
    || !isDateTime(item.started_at)
    || !Number.isInteger(item.event_watermark)
    || Number(item.event_watermark) < 0) return false;
  if (item.status === "active") return item.completed_at === null;
  return item.status === "completed" && isDateTime(item.completed_at);
}

function isStudyContext(value: unknown): value is StudyContextView {
  const item = closed(value, [
    "schema", "study_session_id", "base_knowledge_map_revision",
    "current_formal_concept_id", "deferred_formal_concept_id", "initial_learning_path",
  ]);
  if (!item
    || item.schema !== "study-context/v1"
    || !isUuid(item.study_session_id)
    || !isRevision(item.base_knowledge_map_revision, "knowledge-map")
    || !(item.current_formal_concept_id === null || isRevision(item.current_formal_concept_id, "formal-concept"))
    || !(item.deferred_formal_concept_id === null || isRevision(item.deferred_formal_concept_id, "formal-concept"))
    || (item.deferred_formal_concept_id !== null && item.deferred_formal_concept_id === item.current_formal_concept_id)
    || !Array.isArray(item.initial_learning_path)
    || item.initial_learning_path.length < 1) return false;
  const concepts = item.initial_learning_path.map((value) => {
    const concept = closed(value, [
      "formal_concept_id", "label", "claim_ids", "supplementary_resource_promotion_ids",
    ]);
    if (!concept
      || !isRevision(concept.formal_concept_id, "formal-concept")
      || typeof concept.label !== "string"
      || concept.label.length < 1
      || !isStringArray(concept.claim_ids, 1)
      || !(concept.claim_ids as string[]).every((id) => isRevision(id, "claim"))
      || new Set(concept.claim_ids as string[]).size !== (concept.claim_ids as string[]).length
      || !isStringArray(concept.supplementary_resource_promotion_ids)
      || !(concept.supplementary_resource_promotion_ids as string[])
        .every((id) => isRevision(id, "resource-promotion"))) return null;
    return concept.formal_concept_id as string;
  });
  if (concepts.includes(null) || new Set(concepts).size !== concepts.length) return false;
  return (item.current_formal_concept_id === null || concepts.includes(item.current_formal_concept_id))
    && (item.deferred_formal_concept_id === null || concepts.includes(item.deferred_formal_concept_id));
}

function isAssessment(value: unknown): value is AssessmentView {
  const item = closed(value, [
    "schema", "study_session_id", "knowledge_map_revision", "assessment_revision",
    "question_id", "target_formal_concept_id", "target_claim_id", "source_evidence_ids",
    "question_type", "prompt", "options", "policy_revision",
  ]);
  if (!item
    || item.schema !== "single-choice-assessment-public/v1"
    || !isUuid(item.study_session_id)
    || !isRevision(item.knowledge_map_revision, "knowledge-map")
    || !isRevision(item.assessment_revision, "assessment")
    || !isRevision(item.question_id, "question")
    || !isRevision(item.target_formal_concept_id, "formal-concept")
    || !isRevision(item.target_claim_id, "claim")
    || !isStringArray(item.source_evidence_ids, 1)
    || !(item.source_evidence_ids as string[]).every((id) => isRevision(id, "evidence"))
    || new Set(item.source_evidence_ids as string[]).size !== (item.source_evidence_ids as string[]).length
    || item.question_type !== "single_choice"
    || typeof item.prompt !== "string"
    || item.prompt.length < 1
    || !Array.isArray(item.options)
    || item.options.length !== 4
    || item.policy_revision !== "single-choice-assessment-policy/v1") return false;
  const optionIds = item.options.map((value) => {
    const option = closed(value, ["option_id", "text"]);
    if (!option
      || !isRevision(option.option_id, "option")
      || typeof option.text !== "string"
      || option.text.length < 1) return null;
    return option.option_id as string;
  });
  return !optionIds.includes(null) && new Set(optionIds).size === 4;
}

function isAnswerFeedback(value: unknown): value is AnswerFeedbackView {
  const item = closed(value, [
    "schema", "answer_event_id", "study_session_id", "assessment_revision",
    "question_id", "selected_option_id", "is_correct", "rationale",
    "source_evidence_ids", "event_number", "created_at",
  ]);
  return !!item
    && item.schema === "answer-feedback/v1"
    && isUuid(item.answer_event_id)
    && isUuid(item.study_session_id)
    && isRevision(item.assessment_revision, "assessment")
    && isRevision(item.question_id, "question")
    && isRevision(item.selected_option_id, "option")
    && typeof item.is_correct === "boolean"
    && typeof item.rationale === "string"
    && item.rationale.length > 0
    && isStringArray(item.source_evidence_ids, 1)
    && (item.source_evidence_ids as string[]).every((id) => isRevision(id, "evidence"))
    && new Set(item.source_evidence_ids as string[]).size === (item.source_evidence_ids as string[]).length
    && Number.isInteger(item.event_number)
    && Number(item.event_number) >= 1
    && isDateTime(item.created_at);
}

const learningStatuses = new Set(["not_started", "learning", "needs_review", "mastered"]);
const learningConfidences = new Set(["none", "limited", "supported"]);
const adaptiveActions = new Set([
  "start", "continue", "practice", "review", "relearn_prerequisite",
  "use_resource", "follow_path", "collect_more_data", "no_action",
]);

function isLearningState(value: unknown): value is LearningStateView {
  const item = closed(value, [
    "schema", "study_session_id", "base_knowledge_map_revision", "state_revision",
    "event_watermark", "all_mastered", "concept_states",
  ]);
  if (!item
    || item.schema !== "learning-state/v1"
    || !isUuid(item.study_session_id)
    || !isRevision(item.base_knowledge_map_revision, "knowledge-map")
    || !isRevision(item.state_revision, "learning-state")
    || !Number.isInteger(item.event_watermark)
    || Number(item.event_watermark) < 0
    || typeof item.all_mastered !== "boolean"
    || !Array.isArray(item.concept_states)
    || item.concept_states.length < 1) return false;
  const conceptIds = item.concept_states.map((value) => {
    const state = closed(value, [
      "formal_concept_id", "status", "mastery_band", "confidence", "needs_more_data",
      "required_claim_ids", "attempted_claim_ids", "latest_correct_claim_ids",
      "claim_coverage_complete", "required_evidence_ids", "observed_evidence_ids",
      "evidence_coverage_complete", "valid_attempts", "correct_attempts",
      "distinct_item_attempts", "recent_result", "repeated_error",
      "post_error_improvement", "explanation",
    ]);
    if (!state
      || !isRevision(state.formal_concept_id, "formal-concept")
      || !learningStatuses.has(String(state.status))
      || !["no_evidence", "developing", "demonstrated"].includes(String(state.mastery_band))
      || !learningConfidences.has(String(state.confidence))
      || typeof state.needs_more_data !== "boolean"
      || !isStringArray(state.required_claim_ids)
      || !(state.required_claim_ids as string[]).every((id) => isRevision(id, "claim"))
      || !isStringArray(state.attempted_claim_ids)
      || !(state.attempted_claim_ids as string[]).every((id) => isRevision(id, "claim"))
      || !isStringArray(state.latest_correct_claim_ids)
      || !(state.latest_correct_claim_ids as string[]).every((id) => isRevision(id, "claim"))
      || typeof state.claim_coverage_complete !== "boolean"
      || !isStringArray(state.required_evidence_ids)
      || !(state.required_evidence_ids as string[]).every((id) => isRevision(id, "evidence"))
      || !isStringArray(state.observed_evidence_ids)
      || !(state.observed_evidence_ids as string[]).every((id) => isRevision(id, "evidence"))
      || typeof state.evidence_coverage_complete !== "boolean"
      || !Number.isInteger(state.valid_attempts)
      || Number(state.valid_attempts) < 0
      || !Number.isInteger(state.correct_attempts)
      || Number(state.correct_attempts) < 0
      || Number(state.correct_attempts) > Number(state.valid_attempts)
      || !Number.isInteger(state.distinct_item_attempts)
      || Number(state.distinct_item_attempts) < 0
      || Number(state.distinct_item_attempts) > Number(state.valid_attempts)
      || !(state.recent_result === null || state.recent_result === "correct" || state.recent_result === "incorrect")
      || typeof state.repeated_error !== "boolean"
      || typeof state.post_error_improvement !== "boolean"
      || typeof state.explanation !== "string"
      || state.explanation.length < 1) return null;
    const arrays = [
      state.required_claim_ids, state.attempted_claim_ids, state.latest_correct_claim_ids,
      state.required_evidence_ids, state.observed_evidence_ids,
    ] as string[][];
    if (arrays.some((values) => new Set(values).size !== values.length)) return null;
    return state.formal_concept_id as string;
  });
  return !conceptIds.includes(null)
    && new Set(conceptIds).size === conceptIds.length
    && item.all_mastered === item.concept_states.every((state) => object(state)?.status === "mastered");
}

function isWeakness(value: unknown): value is WeaknessView {
  const item = closed(value, [
    "schema", "study_session_id", "base_knowledge_map_revision",
    "source_learning_state_revision", "event_watermark", "current_formal_concept_id",
    "weakness_revision", "findings", "immediate_prerequisite_gaps",
  ]);
  if (!item
    || item.schema !== "weakness/v1"
    || !isUuid(item.study_session_id)
    || !isRevision(item.base_knowledge_map_revision, "knowledge-map")
    || !isRevision(item.source_learning_state_revision, "learning-state")
    || !Number.isInteger(item.event_watermark)
    || Number(item.event_watermark) < 0
    || !(item.current_formal_concept_id === null || isRevision(item.current_formal_concept_id, "formal-concept"))
    || !isRevision(item.weakness_revision, "weakness")
    || !Array.isArray(item.findings)
    || !Array.isArray(item.immediate_prerequisite_gaps)) return false;
  const findingIds = item.findings.map((value) => {
    const finding = closed(value, [
      "target_formal_concept_id", "target_label", "category", "confidence",
      "claim_coverage_complete", "remediation_intent", "reason",
    ]);
    if (!finding
      || !isRevision(finding.target_formal_concept_id, "formal-concept")
      || typeof finding.target_label !== "string"
      || finding.target_label.length < 1
      || !["observed_weak", "needs_review", "not_enough_data"].includes(String(finding.category))
      || !learningConfidences.has(String(finding.confidence))
      || typeof finding.claim_coverage_complete !== "boolean"
      || !["practice", "review", "collect_more_data"].includes(String(finding.remediation_intent))
      || typeof finding.reason !== "string"
      || finding.reason.length < 1) return null;
    return finding.target_formal_concept_id as string;
  });
  const gapIds = item.immediate_prerequisite_gaps.map((value) => {
    const gap = closed(value, [
      "category", "target_formal_concept_id", "prerequisite_formal_concept_id",
      "prerequisite_label", "relation_id", "prerequisite_status",
      "prerequisite_confidence", "remediation_intent", "reason",
    ]);
    if (!gap
      || gap.category !== "possible_prerequisite_gap"
      || !isRevision(gap.target_formal_concept_id, "formal-concept")
      || !isRevision(gap.prerequisite_formal_concept_id, "formal-concept")
      || gap.target_formal_concept_id === gap.prerequisite_formal_concept_id
      || typeof gap.prerequisite_label !== "string"
      || gap.prerequisite_label.length < 1
      || !isRevision(gap.relation_id, "formal-relation")
      || !learningStatuses.has(String(gap.prerequisite_status))
      || !learningConfidences.has(String(gap.prerequisite_confidence))
      || gap.remediation_intent !== "relearn_prerequisite"
      || typeof gap.reason !== "string"
      || gap.reason.length < 1) return null;
    return gap.relation_id as string;
  });
  return !findingIds.includes(null)
    && new Set(findingIds).size === findingIds.length
    && !gapIds.includes(null)
    && new Set(gapIds).size === gapIds.length;
}

function readAdaptiveRoute(value: unknown, studySessionId: string): JsonObject | null {
  const route = closed(value, ["study_session_id", "formal_concept_id", "resource_promotion_id"]);
  return route
    && route.study_session_id === studySessionId
    && (route.formal_concept_id === null || isRevision(route.formal_concept_id, "formal-concept"))
    && (route.resource_promotion_id === null || isRevision(route.resource_promotion_id, "resource-promotion"))
    ? route
    : null;
}

function readAdaptiveStep(value: unknown, studySessionId: string): JsonObject | null {
  const step = closed(value, [
    "action", "target_formal_concept_id", "target_label", "reason", "confidence",
    "claim_coverage_complete", "route",
  ]);
  if (!step
    || !adaptiveActions.has(String(step.action))
    || !(step.target_formal_concept_id === null || isRevision(step.target_formal_concept_id, "formal-concept"))
    || !(step.target_label === null || (typeof step.target_label === "string" && step.target_label.length > 0))
    || typeof step.reason !== "string"
    || step.reason.length < 1
    || !learningConfidences.has(String(step.confidence))
    || typeof step.claim_coverage_complete !== "boolean") return null;
  const route = readAdaptiveRoute(step.route, studySessionId);
  return route && route.formal_concept_id === step.target_formal_concept_id ? step : null;
}

function isAdaptiveResponse(value: unknown): value is AdaptiveResponseView {
  const item = closed(value, ["schema", "plan", "suggestion"]);
  if (!item || item.schema !== "adaptive-response/v1") return false;
  const plan = closed(item.plan, [
    "schema", "study_session_id", "base_knowledge_map_revision",
    "inline_initial_learning_path_sha256", "source_learning_state_revision",
    "event_watermark", "current_formal_concept_id", "deferred_formal_concept_id",
    "primary_step", "adaptive_plan_revision",
  ]);
  if (!plan
    || plan.schema !== "adaptive-plan/v1"
    || !isUuid(plan.study_session_id)
    || !isRevision(plan.base_knowledge_map_revision, "knowledge-map")
    || typeof plan.inline_initial_learning_path_sha256 !== "string"
    || !sha256Pattern.test(plan.inline_initial_learning_path_sha256)
    || !isRevision(plan.source_learning_state_revision, "learning-state")
    || !Number.isInteger(plan.event_watermark)
    || Number(plan.event_watermark) < 0
    || !(plan.current_formal_concept_id === null || isRevision(plan.current_formal_concept_id, "formal-concept"))
    || !(plan.deferred_formal_concept_id === null || isRevision(plan.deferred_formal_concept_id, "formal-concept"))
    || (plan.deferred_formal_concept_id !== null && plan.deferred_formal_concept_id === plan.current_formal_concept_id)
    || !isRevision(plan.adaptive_plan_revision, "adaptive-plan")) return false;
  const step = readAdaptiveStep(plan.primary_step, String(plan.study_session_id));
  const suggestion = closed(item.suggestion, [
    "schema", "adaptive_plan_revision", "study_session_id", "base_knowledge_map_revision",
    "action", "target_formal_concept_id", "target_label", "reason", "confidence",
    "claim_coverage_complete", "route", "fallback_action", "fallback_reason",
  ]);
  if (!step
    || !suggestion
    || suggestion.schema !== "learning-suggestion/v1"
    || suggestion.adaptive_plan_revision !== plan.adaptive_plan_revision
    || suggestion.study_session_id !== plan.study_session_id
    || suggestion.base_knowledge_map_revision !== plan.base_knowledge_map_revision
    || suggestion.action !== step.action
    || suggestion.target_formal_concept_id !== step.target_formal_concept_id
    || suggestion.target_label !== step.target_label
    || suggestion.reason !== step.reason
    || suggestion.confidence !== step.confidence
    || suggestion.claim_coverage_complete !== step.claim_coverage_complete
    || !["follow_path", "collect_more_data", "no_action"].includes(String(suggestion.fallback_action))
    || typeof suggestion.fallback_reason !== "string"
    || suggestion.fallback_reason.length < 1) return false;
  const suggestionRoute = readAdaptiveRoute(suggestion.route, String(plan.study_session_id));
  const stepRoute = object(step.route);
  return !!suggestionRoute
    && !!stepRoute
    && JSON.stringify(suggestionRoute) === JSON.stringify(stepRoute);
}

function isApiError(value: unknown): value is ApiErrorView {
  const item = closed(value, ["schema", "request_id", "reason_code", "retryable", "message"]);
  return !!item
    && item.schema === "api-error/v1"
    && isUuid(item.request_id)
    && typeof item.reason_code === "string"
    && typeof item.retryable === "boolean"
    && item.message === "Request could not be completed.";
}

function safeMessage(reasonCode: ApiReasonCode): string {
  if (reasonCode === "SESSION_REQUIRED") return "工作階段已失效，請重新整理後再試。";
  if (reasonCode === "RESOURCE_NOT_FOUND") return "找不到這筆資料，或你沒有權限讀取。";
  if (reasonCode === "MATERIAL_TOO_LARGE") return "PDF 不可超過 100 MiB。";
  if (reasonCode === "MATERIAL_PDF_INVALID") return "這份 PDF 已損毀、加密或無法開啟，請改用可正常閱讀的 PDF。";
  if (reasonCode === "UNSUPPORTED_MEDIA_TYPE") return "只接受 PDF 教材。";
  if (reasonCode === "STORAGE_UNAVAILABLE") return "資料服務暫時無法使用，請稍後再試。";
  return "目前無法完成請求，請稍後再試。";
}

async function apiFailure(response: Response): Promise<ApiClientError> {
  let value: unknown;
  try {
    value = await response.json();
  } catch {
    value = null;
  }
  if (!isApiError(value)) {
    return new ApiClientError("schema", "伺服器回應格式不符。", {
      status: response.status,
      reasonCode: "RESPONSE_SCHEMA_MISMATCH",
    });
  }
  const reasonCode = knownApiReasons.has(value.reason_code as KnownApiReasonCode)
    ? value.reason_code as KnownApiReasonCode
    : "UNKNOWN_API_ERROR";
  return new ApiClientError("api", safeMessage(reasonCode), {
    status: response.status,
    reasonCode,
    requestId: value.request_id,
    retryable: value.retryable,
  });
}

export class StudydyApiClient {
  private sessionChecked = false;
  private sessionRequest: Promise<void> | null = null;
  private readonly fetchRequest: FetchRequest;

  constructor(fetchRequest: FetchRequest = fetch.bind(globalThis)) {
    this.fetchRequest = fetchRequest;
  }

  async ensureSession(): Promise<void> {
    if (this.sessionChecked) return;
    if (!this.sessionRequest) {
      this.sessionRequest = this.refreshSession()
        .then(() => { this.sessionChecked = true; })
        .finally(() => { this.sessionRequest = null; });
    }
    await this.sessionRequest;
  }

  private async refreshSession(): Promise<void> {
    const response = await this.fetchRequest("/v1/session/refresh", {
      method: "POST",
      credentials: "same-origin",
      headers: { Origin: requestOrigin() },
    });
    if (response.status === 204) return;
    if (response.status === 401) {
      const created = await this.fetchRequest("/v1/session", {
        method: "POST",
        credentials: "same-origin",
        headers: { Origin: requestOrigin() },
      });
      if (created.status === 204) return;
      throw await apiFailure(created);
    }
    throw await apiFailure(response);
  }

  private async request(path: string, init: RequestInit, retrySession = true): Promise<Response> {
    await this.ensureSession();
    let response: Response;
    try {
      response = await this.fetchRequest(path, { credentials: "same-origin", ...init });
    } catch {
      throw new ApiClientError("network", "網路連線失敗，請稍後再試。", { reasonCode: "NETWORK_ERROR", retryable: true });
    }
    if (response.status === 401 && retrySession) {
      await this.refreshSession();
      return this.request(path, init, false);
    }
    if (!response.ok) throw await apiFailure(response);
    return response;
  }

  private async json<T>(path: string, init: RequestInit, read: (value: unknown) => value is T): Promise<T> {
    const response = await this.request(path, init);
    let value: unknown;
    try {
      value = await response.json();
    } catch {
      value = null;
    }
    if (!read(value)) {
      throw new ApiClientError("schema", "伺服器回應格式不符。", { reasonCode: "RESPONSE_SCHEMA_MISMATCH" });
    }
    return value;
  }

  private async idempotentJson<T>(
    path: string,
    init: RequestInit,
    read: (value: unknown) => value is T,
  ): Promise<T> {
    const request = () => this.json(path, init, read);
    try {
      return await request();
    } catch (error) {
      if (error instanceof ApiClientError && error.kind === "network") return request();
      throw error;
    }
  }

  async createMaterial(pdf: Blob, idempotencyKey: string = crypto.randomUUID()): Promise<MaterialView> {
    if (pdf.type !== "application/pdf" || pdf.size < 1 || pdf.size > maximumPdfBytes) {
      throw new ApiClientError("input", "請選擇 100 MiB 以內的 PDF。", { reasonCode: "REQUEST_INPUT_INVALID" });
    }
    return this.idempotentJson(
      "/v1/materials",
      {
        method: "POST",
        headers: {
          Origin: requestOrigin(),
          "Content-Type": "application/pdf",
          "Idempotency-Key": idempotencyKey,
        },
        body: pdf,
      },
      isMaterial,
    );
  }

  async createMaterialRun(body: MaterialProcessingCreate, idempotencyKey: string = crypto.randomUUID()): Promise<MaterialProcessingRunView> {
    if (
      body.schema !== "material-processing-create/v2"
      || !isUuid(body.material_id)
      || !isUuid(body.source_artifact_id)
      || Object.keys(body).length !== 3
    ) {
      throw new ApiClientError("input", "教材處理請求無效。", { reasonCode: "REQUEST_INPUT_INVALID" });
    }
    return this.json(
      "/v1/material-processing-runs",
      {
        method: "POST",
        headers: {
          Origin: requestOrigin(),
          "Content-Type": "application/json",
          "Idempotency-Key": idempotencyKey,
        },
        body: JSON.stringify(body),
      },
      isMaterialRun,
    );
  }

  async getMaterialRun(runId: string): Promise<MaterialProcessingRunView> {
    if (!isUuid(runId)) throw new ApiClientError("input", "處理作業編號無效。", { reasonCode: "REQUEST_INPUT_INVALID" });
    return this.json(`/v1/material-processing-runs/${runId}`, { method: "GET" }, isMaterialRun);
  }

  async getKnowledgeMap(request: KnowledgeMapRequest): Promise<KnowledgeMapView> {
    if (!isUuid(request.materialId) || !isUuid(request.runId) || !isRevision(request.mapRevision, "knowledge-map")) {
      throw new ApiClientError("input", "知識地圖識別資訊無效。", { reasonCode: "REQUEST_INPUT_INVALID" });
    }
    const path = `/v1/materials/${request.materialId}/knowledge-maps/${encodeURIComponent(request.mapRevision)}?run_id=${request.runId}`;
    const view = await this.json(path, { method: "GET" }, isKnowledgeMap);
    if (view.knowledge_map_revision !== request.mapRevision) {
      throw new ApiClientError("schema", "伺服器回應格式不符。", { reasonCode: "RESPONSE_SCHEMA_MISMATCH" });
    }
    return view;
  }

  async createStudySession(body: StudySessionCreate, idempotencyKey: string = crypto.randomUUID()): Promise<StudySessionView> {
    const keys = Object.keys(body);
    if (
      body.schema !== "study-session-create/v1"
      || !isUuid(body.material_id)
      || !isRevision(body.knowledge_map_revision, "knowledge-map")
      || !(body.current_formal_concept_id === undefined
        || body.current_formal_concept_id === null
        || isRevision(body.current_formal_concept_id, "formal-concept"))
      || keys.some((key) => !["schema", "material_id", "knowledge_map_revision", "current_formal_concept_id"].includes(key))
      || keys.length < 3
      || keys.length > 4
    ) {
      throw new ApiClientError("input", "本次學習請求無效。", { reasonCode: "REQUEST_INPUT_INVALID" });
    }
    const session = await this.idempotentJson(
      "/v1/study-sessions",
      {
        method: "POST",
        headers: {
          Origin: requestOrigin(),
          "Content-Type": "application/json",
          "Idempotency-Key": idempotencyKey,
        },
        body: JSON.stringify(body),
      },
      isStudySession,
    );
    if (session.material_id !== body.material_id
      || session.knowledge_map_revision !== body.knowledge_map_revision
      || (body.current_formal_concept_id !== undefined
        && session.current_formal_concept_id !== body.current_formal_concept_id)) {
      throw new ApiClientError("schema", "伺服器回應格式不符。", { reasonCode: "RESPONSE_SCHEMA_MISMATCH" });
    }
    return session;
  }

  async getStudySession(studySessionId: string): Promise<StudySessionView> {
    if (!isUuid(studySessionId)) {
      throw new ApiClientError("input", "本次學習識別資訊無效。", { reasonCode: "REQUEST_INPUT_INVALID" });
    }
    const session = await this.json(`/v1/study-sessions/${studySessionId}`, { method: "GET" }, isStudySession);
    if (session.study_session_id !== studySessionId) {
      throw new ApiClientError("schema", "伺服器回應格式不符。", { reasonCode: "RESPONSE_SCHEMA_MISMATCH" });
    }
    return session;
  }

  async getStudyContext(studySessionId: string): Promise<StudyContextView> {
    if (!isUuid(studySessionId)) {
      throw new ApiClientError("input", "本次學習識別資訊無效。", { reasonCode: "REQUEST_INPUT_INVALID" });
    }
    const context = await this.json(`/v1/study-sessions/${studySessionId}/context`, { method: "GET" }, isStudyContext);
    if (context.study_session_id !== studySessionId) {
      throw new ApiClientError("schema", "伺服器回應格式不符。", { reasonCode: "RESPONSE_SCHEMA_MISMATCH" });
    }
    return context;
  }

  async completeStudySession(studySessionId: string): Promise<StudySessionView> {
    if (!isUuid(studySessionId)) {
      throw new ApiClientError("input", "本次學習識別資訊無效。", { reasonCode: "REQUEST_INPUT_INVALID" });
    }
    const session = await this.json(
      `/v1/study-sessions/${studySessionId}/complete`,
      { method: "POST", headers: { Origin: requestOrigin() } },
      isStudySession,
    );
    if (session.study_session_id !== studySessionId || session.status !== "completed") {
      throw new ApiClientError("schema", "伺服器回應格式不符。", { reasonCode: "RESPONSE_SCHEMA_MISMATCH" });
    }
    return session;
  }

  async createAssessment(
    studySessionId: string,
    body: AssessmentCreate,
    idempotencyKey: string = crypto.randomUUID(),
  ): Promise<AssessmentView> {
    if (!isUuid(studySessionId)
      || body.schema !== "assessment-create/v1"
      || !isRevision(body.target_claim_id, "claim")
      || Object.keys(body).length !== 2) {
      throw new ApiClientError("input", "評量請求無效。", { reasonCode: "REQUEST_INPUT_INVALID" });
    }
    const assessment = await this.idempotentJson(
      `/v1/study-sessions/${studySessionId}/assessments`,
      {
        method: "POST",
        headers: {
          Origin: requestOrigin(),
          "Content-Type": "application/json",
          "Idempotency-Key": idempotencyKey,
        },
        body: JSON.stringify(body),
      },
      isAssessment,
    );
    if (assessment.study_session_id !== studySessionId || assessment.target_claim_id !== body.target_claim_id) {
      throw new ApiClientError("schema", "伺服器回應格式不符。", { reasonCode: "RESPONSE_SCHEMA_MISMATCH" });
    }
    return assessment;
  }

  async getAssessment(studySessionId: string, assessmentRevision: string): Promise<AssessmentView> {
    if (!isUuid(studySessionId) || !isRevision(assessmentRevision, "assessment")) {
      throw new ApiClientError("input", "評量識別資訊無效。", { reasonCode: "REQUEST_INPUT_INVALID" });
    }
    const assessment = await this.json(
      `/v1/study-sessions/${studySessionId}/assessments/${encodeURIComponent(assessmentRevision)}`,
      { method: "GET" },
      isAssessment,
    );
    if (assessment.study_session_id !== studySessionId || assessment.assessment_revision !== assessmentRevision) {
      throw new ApiClientError("schema", "伺服器回應格式不符。", { reasonCode: "RESPONSE_SCHEMA_MISMATCH" });
    }
    return assessment;
  }

  async submitAssessmentAnswer(
    studySessionId: string,
    assessmentRevision: string,
    body: AnswerSubmissionCreate,
    idempotencyKey: string = crypto.randomUUID(),
  ): Promise<AnswerFeedbackView> {
    if (!isUuid(studySessionId)
      || !isRevision(assessmentRevision, "assessment")
      || body.schema !== "answer-submission-create/v1"
      || !isRevision(body.question_id, "question")
      || !isRevision(body.selected_option_id, "option")
      || Object.keys(body).length !== 3) {
      throw new ApiClientError("input", "作答內容無效。", { reasonCode: "REQUEST_INPUT_INVALID" });
    }
    const feedback = await this.idempotentJson(
      `/v1/study-sessions/${studySessionId}/assessments/${encodeURIComponent(assessmentRevision)}/submissions`,
      {
        method: "POST",
        headers: {
          Origin: requestOrigin(),
          "Content-Type": "application/json",
          "Idempotency-Key": idempotencyKey,
        },
        body: JSON.stringify(body),
      },
      isAnswerFeedback,
    );
    if (feedback.study_session_id !== studySessionId
      || feedback.assessment_revision !== assessmentRevision
      || feedback.question_id !== body.question_id
      || feedback.selected_option_id !== body.selected_option_id) {
      throw new ApiClientError("schema", "伺服器回應格式不符。", { reasonCode: "RESPONSE_SCHEMA_MISMATCH" });
    }
    return feedback;
  }

  async getLearningState(studySessionId: string): Promise<LearningStateView> {
    if (!isUuid(studySessionId)) {
      throw new ApiClientError("input", "本次學習識別資訊無效。", { reasonCode: "REQUEST_INPUT_INVALID" });
    }
    const state = await this.json(
      `/v1/study-sessions/${studySessionId}/learning-state`,
      { method: "GET" },
      isLearningState,
    );
    if (state.study_session_id !== studySessionId) {
      throw new ApiClientError("schema", "伺服器回應格式不符。", { reasonCode: "RESPONSE_SCHEMA_MISMATCH" });
    }
    return state;
  }

  async getWeakness(studySessionId: string): Promise<WeaknessView> {
    if (!isUuid(studySessionId)) {
      throw new ApiClientError("input", "本次學習識別資訊無效。", { reasonCode: "REQUEST_INPUT_INVALID" });
    }
    const weakness = await this.json(
      `/v1/study-sessions/${studySessionId}/weakness`,
      { method: "GET" },
      isWeakness,
    );
    if (weakness.study_session_id !== studySessionId) {
      throw new ApiClientError("schema", "伺服器回應格式不符。", { reasonCode: "RESPONSE_SCHEMA_MISMATCH" });
    }
    return weakness;
  }

  async getAdaptivePlan(studySessionId: string): Promise<AdaptiveResponseView> {
    if (!isUuid(studySessionId)) {
      throw new ApiClientError("input", "本次學習識別資訊無效。", { reasonCode: "REQUEST_INPUT_INVALID" });
    }
    const adaptive = await this.json(
      `/v1/study-sessions/${studySessionId}/adaptive-plan`,
      { method: "GET" },
      isAdaptiveResponse,
    );
    if (adaptive.plan.study_session_id !== studySessionId) {
      throw new ApiClientError("schema", "伺服器回應格式不符。", { reasonCode: "RESPONSE_SCHEMA_MISMATCH" });
    }
    return adaptive;
  }

  async applyAdaptivePlan(studySessionId: string, body: AdaptivePlanApply): Promise<StudySessionView> {
    if (!isUuid(studySessionId)
      || body.schema !== "adaptive-plan-apply/v1"
      || !isRevision(body.adaptive_plan_revision, "adaptive-plan")
      || Object.keys(body).length !== 2) {
      throw new ApiClientError("input", "調整學習步驟的請求無效。", { reasonCode: "REQUEST_INPUT_INVALID" });
    }
    const session = await this.json(
      `/v1/study-sessions/${studySessionId}/adaptive-plan/apply`,
      {
        method: "POST",
        headers: { Origin: requestOrigin(), "Content-Type": "application/json" },
        body: JSON.stringify(body),
      },
      isStudySession,
    );
    if (session.study_session_id !== studySessionId) {
      throw new ApiClientError("schema", "伺服器回應格式不符。", { reasonCode: "RESPONSE_SCHEMA_MISMATCH" });
    }
    return session;
  }

  sourceArtifactUrl(artifactId: string, pageNumber?: number): string {
    if (!isUuid(artifactId)) throw new ApiClientError("input", "教材檔案編號無效。", { reasonCode: "REQUEST_INPUT_INVALID" });
    if (pageNumber !== undefined && (!Number.isInteger(pageNumber) || pageNumber < 1)) {
      throw new ApiClientError("input", "教材頁碼無效。", { reasonCode: "REQUEST_INPUT_INVALID" });
    }
    return `/v1/artifacts/${artifactId}${pageNumber === undefined ? "" : `#page=${pageNumber}`}`;
  }
}

export function errorMessage(error: unknown): string {
  return error instanceof ApiClientError ? error.message : "目前無法完成操作，請稍後再試。";
}
