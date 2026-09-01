import type {
  AnswerFeedbackView,
  AnswerSubmissionCreate,
  GuidanceApply,
  ApiErrorView,
  ApiReasonCode,
  AssessmentCreate,
  AssessmentView,
  KnowledgeMapRequest,
  KnowledgeMapView,
  LearnerProgressView,
  KnownApiReasonCode,
  MaterialProcessingCreate,
  MaterialProcessingRunView,
  MaterialView,
  StudySessionCreate,
  StudySessionView,
} from "./contracts";

type FetchRequest = (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;
type JsonObject = Record<string, unknown>;

const knownApiReasons = new Set<KnownApiReasonCode>([
  "REQUEST_INVALID",
  "SESSION_REQUIRED",
  "ORIGIN_NOT_ALLOWED",
  "RESOURCE_NOT_FOUND",
  "IDEMPOTENCY_CONFLICT",
  "NO_SAFE_ASSESSMENT",
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
    "concepts", "concept_diagnostics", "document_tree", "initial_learning_path",
    "supplementary_resources", "excluded_pages",
  ]);
  if (!item
    || item.schema !== "knowledge-map-view/v11"
    || !isRevision(item.material_ref, "material")
    || !isRevision(item.knowledge_map_revision, "knowledge-map")
    || !isRevision(item.source_output_id, "study-material-output")
    || !Array.isArray(item.concepts)
    || !isConceptDiagnostics(item.concept_diagnostics)
    || !Array.isArray(item.initial_learning_path)
    || !Array.isArray(item.excluded_pages)) return false;
  const status = closed(item.status, ["processing", "quality", "decision", "reason_codes"]);
  if (!status
    || !["succeeded", "partial", "failed"].includes(String(status.processing))
    || status.quality !== "needs_review"
    || !["review", "reject"].includes(String(status.decision))
    || !isSortedUniqueStrings(status.reason_codes)) return false;

  const concepts = item.concepts as unknown as KnowledgeMapView["concepts"];
  if (!concepts.every((concept) => {
    const closedConcept = closed(concept, [
      "formal_concept_id", "label", "aliases", "claims", "source_concept_ids",
      "source_page_numbers", "supplementary_resources", "quality", "decision",
      "reason_codes",
    ]);
    return !!closedConcept
      && isRevision(concept.formal_concept_id, "formal-concept")
      && typeof concept.label === "string" && concept.label.length > 0
      && isSortedUniqueStrings(concept.aliases, 0)
      && isStringArray(concept.source_concept_ids, 1)
      && Array.isArray(concept.source_page_numbers) && concept.source_page_numbers.length > 0
      && concept.source_page_numbers.every((page) => Number.isInteger(page) && page >= 1)
      && Array.isArray(concept.claims) && concept.claims.length > 0
      && concept.claims.every((claim) => {
        const closedClaim = closed(claim, ["claim_id", "text", "evidence"]);
        return !!closedClaim
          && isRevision(claim.claim_id, "claim")
          && typeof claim.text === "string" && claim.text.length > 0
          && Array.isArray(claim.evidence) && claim.evidence.length > 0
          && claim.evidence.every((evidence) => isEvidence(evidence)
            && concept.source_page_numbers.includes(evidence.page_number));
      })
      && Array.isArray(concept.supplementary_resources)
      && concept.supplementary_resources.every((resource) =>
        isRevision(resource.promotion_id, "resource-promotion")
        && isRevision(resource.resource_concept_id, "resource-concept")
        && isRevision(resource.resource_id, "resource")
        && isHttpUrl(resource.source_url) && isHttpUrl(resource.license_url))
      && concept.quality === "needs_review" && concept.decision === "review"
      && isSortedUniqueStrings(concept.reason_codes);
  })) return false;
  const conceptIds = concepts.map((concept) => concept.formal_concept_id);
  if (new Set(conceptIds).size !== conceptIds.length) return false;

  const tree = closed(item.document_tree, ["root", "sections"]);
  const root = tree && closed(tree.root, ["material_ref", "section_ids"]);
  if (!tree || !root || root.material_ref !== item.material_ref
    || !isStringArray(root.section_ids)
    || !Array.isArray(tree.sections)) return false;
  const sections = tree.sections.map((section) => closed(section, [
    "section_id", "label", "label_source", "heading_evidence_id", "source_order",
    "concept_ids",
  ]));
  const treeConceptIds = sections.flatMap((section) =>
    Array.isArray(section?.concept_ids) ? section.concept_ids : []);
  if (sections.some((section) => {
    const source = section && closed(section.source_order, [
      "page_ref", "page_number", "reading_order", "evidence_id",
    ]);
    return !section || !source
      || !isRevision(section.section_id, "document-section")
      || typeof section.label !== "string" || section.label.length < 1
      || !["heading", "unheaded_fallback"].includes(String(section.label_source))
      || !(section.heading_evidence_id === null
        || isRevision(section.heading_evidence_id, "evidence"))
      || !isStringArray(section.concept_ids, 1)
      || !isRevision(source.page_ref, "page")
      || !isRevision(source.evidence_id, "evidence")
      || !Number.isInteger(source.page_number) || Number(source.page_number) < 1
      || !Number.isInteger(source.reading_order) || Number(source.reading_order) < 0;
  })
    || JSON.stringify(root.section_ids) !== JSON.stringify(
      sections.map((section) => section?.section_id),
    )
    || treeConceptIds.length !== conceptIds.length
    || new Set(treeConceptIds).size !== conceptIds.length
    || treeConceptIds.some((conceptId) => !conceptIds.includes(String(conceptId)))) return false;

  const path = item.initial_learning_path.map((step) => closed(step, [
    "step_number", "formal_concept_id", "placement_reason", "order_basis",
  ]));
  const pathIds = path.map((step) => step?.formal_concept_id);
  if (pathIds.length !== conceptIds.length
    || new Set(pathIds).size !== conceptIds.length
    || pathIds.some((conceptId) => !conceptIds.includes(String(conceptId)))
    || path.some((step, index) => {
      const basis = step && closed(step.order_basis, [
        "section_id", "page_ref", "page_number",
        "reading_order", "evidence_id",
      ]);
      const concept = concepts.find((candidate) =>
        candidate.formal_concept_id === step?.formal_concept_id);
      const anchor = concept?.claims.flatMap((claim) => claim.evidence)
        .find((evidence) => evidence.evidence_id === basis?.evidence_id);
      return !step || !basis || !anchor || step.step_number !== index + 1
        || typeof step.placement_reason !== "string" || step.placement_reason.length < 1
        || !isRevision(basis.section_id, "document-section")
        || !isRevision(basis.page_ref, "page")
        || !isRevision(basis.evidence_id, "evidence")
        || !Number.isInteger(basis.page_number) || Number(basis.page_number) < 1
        || !Number.isInteger(basis.reading_order) || Number(basis.reading_order) < 0
        || anchor.page_ref !== basis.page_ref
        || anchor.page_number !== basis.page_number
        || !sections.some((section) => section?.section_id === basis.section_id
          && (section?.concept_ids as unknown[]).includes(step.formal_concept_id));
    })) return false;

  const resources = closed(item.supplementary_resources, [
    "processing", "quality", "decision", "reason_codes", "binding", "diagnostics",
  ]);
  const diagnostics = resources && closed(resources.diagnostics, [
    "matches", "promoted_matches", "promoted_resources",
  ]);
  if (!resources || !diagnostics
    || !["succeeded", "partial"].includes(String(resources.processing))
    || resources.quality !== "needs_review" || resources.decision !== "review"
    || !isSortedUniqueStrings(resources.reason_codes, 0)
    || !Object.values(diagnostics).every((count) => Number.isInteger(count) && Number(count) >= 0)
    || diagnostics.matches !== diagnostics.promoted_matches
    || Number(diagnostics.promoted_resources) > Number(diagnostics.promoted_matches)
    || !(resources.binding === null || !!closed(resources.binding, [
      "context_revision", "library_revision", "matching_policy", "promotion_policy",
    ]))) return false;
  return item.excluded_pages.every(isExcludedPage)
    && (item.excluded_pages.length === 0 || status.processing === "partial");
}

function isDateTime(value: unknown): value is string {
  return typeof value === "string" && value.length > 0 && Number.isFinite(Date.parse(value));
}

function isStudySession(value: unknown): value is StudySessionView {
  const item = closed(value, [
    "schema", "study_session_id", "material_id", "knowledge_map_revision",
    "current_formal_concept_id",
    "no_safe_deferred_formal_concept_ids", "status",
    "started_at", "completed_at", "event_watermark",
  ]);
  if (!item
    || item.schema !== "study-session/v1"
    || !isUuid(item.study_session_id)
    || !isUuid(item.material_id)
    || !isRevision(item.knowledge_map_revision, "knowledge-map")
    || !(item.current_formal_concept_id === null || isRevision(item.current_formal_concept_id, "formal-concept"))
    || !isStringArray(item.no_safe_deferred_formal_concept_ids)
    || !(item.no_safe_deferred_formal_concept_ids as string[])
      .every((id) => isRevision(id, "formal-concept"))
    || new Set(item.no_safe_deferred_formal_concept_ids as string[]).size
      !== (item.no_safe_deferred_formal_concept_ids as string[]).length
    || !isDateTime(item.started_at)
    || !Number.isInteger(item.event_watermark)
    || Number(item.event_watermark) < 0) return false;
  if (item.status === "active" || item.status === "no_safe") return item.completed_at === null;
  return item.status === "completed" && isDateTime(item.completed_at);
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
const guidanceActions = new Set([
  "start", "continue", "practice", "review",
  "use_resource", "follow_path", "collect_more_data", "defer", "resume", "no_action",
]);

function readConceptState(value: unknown): JsonObject | null {
  const state = closed(value, [
    "formal_concept_id", "status", "mastery_band", "confidence", "needs_more_data",
    "required_claim_ids", "attempted_claim_ids", "latest_correct_claim_ids",
    "claim_coverage_complete", "required_evidence_ids", "observed_evidence_ids",
    "evidence_coverage_complete", "valid_attempts", "correct_attempts",
    "qualified_distinct_correct_items", "recent_result", "repeated_error",
    "post_error_improvement", "explanation",
  ]);
  if (!state
    || !isRevision(state.formal_concept_id, "formal-concept")
    || !learningStatuses.has(String(state.status))
    || !["no_evidence", "developing", "demonstrated"].includes(String(state.mastery_band))
    || !learningConfidences.has(String(state.confidence))
    || typeof state.needs_more_data !== "boolean"
    || typeof state.claim_coverage_complete !== "boolean"
    || typeof state.evidence_coverage_complete !== "boolean"
    || !Number.isInteger(state.valid_attempts) || Number(state.valid_attempts) < 0
    || !Number.isInteger(state.correct_attempts) || Number(state.correct_attempts) < 0
    || Number(state.correct_attempts) > Number(state.valid_attempts)
    || !Number.isInteger(state.qualified_distinct_correct_items)
    || Number(state.qualified_distinct_correct_items) < 0
    || Number(state.qualified_distinct_correct_items) > Number(state.correct_attempts)
    || !(state.recent_result === null || state.recent_result === "correct" || state.recent_result === "incorrect")
    || typeof state.repeated_error !== "boolean"
    || typeof state.post_error_improvement !== "boolean"
    || typeof state.explanation !== "string" || state.explanation.length < 1) return null;
  const idGroups = [
    [state.required_claim_ids, "claim"],
    [state.attempted_claim_ids, "claim"],
    [state.latest_correct_claim_ids, "claim"],
    [state.required_evidence_ids, "evidence"],
    [state.observed_evidence_ids, "evidence"],
  ] as [unknown, string][];
  return idGroups.every(([ids, kind]) => isStringArray(ids)
    && (ids as string[]).every((id) => isRevision(id, kind))
    && new Set(ids as string[]).size === (ids as string[]).length) ? state : null;
}

function readWeaknessFinding(value: unknown): JsonObject | null {
  const finding = closed(value, [
    "target_formal_concept_id", "target_label", "category", "confidence",
    "claim_coverage_complete", "remediation_intent", "reason",
  ]);
  return finding
    && isRevision(finding.target_formal_concept_id, "formal-concept")
    && typeof finding.target_label === "string" && finding.target_label.length > 0
    && ["observed_weak", "needs_review", "not_enough_data"].includes(String(finding.category))
    && learningConfidences.has(String(finding.confidence))
    && typeof finding.claim_coverage_complete === "boolean"
    && ["practice", "review", "collect_more_data"].includes(String(finding.remediation_intent))
    && typeof finding.reason === "string" && finding.reason.length > 0
    ? finding : null;
}

function readNextAction(value: unknown, studySessionId: string): JsonObject | null {
  const action = closed(value, [
    "action", "target_formal_concept_id", "target_label", "reason", "confidence",
    "claim_coverage_complete", "route",
  ]);
  const route = action && closed(action.route, [
    "study_session_id", "formal_concept_id", "resource_promotion_id",
  ]);
  return action && route
    && guidanceActions.has(String(action.action))
    && (action.target_formal_concept_id === null || isRevision(action.target_formal_concept_id, "formal-concept"))
    && (action.target_label === null || (typeof action.target_label === "string" && action.target_label.length > 0))
    && typeof action.reason === "string" && action.reason.length > 0
    && learningConfidences.has(String(action.confidence))
    && typeof action.claim_coverage_complete === "boolean"
    && route.study_session_id === studySessionId
    && route.formal_concept_id === action.target_formal_concept_id
    && (route.resource_promotion_id === null || isRevision(route.resource_promotion_id, "resource-promotion"))
    ? action : null;
}

function isLearnerProgress(value: unknown): value is LearnerProgressView {
  const item = closed(value, [
    "schema", "study_session_id", "material_id", "base_knowledge_map_revision",
    "inline_initial_learning_path_sha256", "event_watermark", "status",
    "current_formal_concept_id", "no_safe_deferred_formal_concept_ids",
    "concept_states", "weakness_findings", "next_action", "guidance_revision",
  ]);
  if (!item || item.schema !== "learner-progress/v1"
    || !isUuid(item.study_session_id) || !isUuid(item.material_id)
    || !isRevision(item.base_knowledge_map_revision, "knowledge-map")
    || typeof item.inline_initial_learning_path_sha256 !== "string"
    || !sha256Pattern.test(item.inline_initial_learning_path_sha256)
    || !Number.isInteger(item.event_watermark) || Number(item.event_watermark) < 0
    || !["active", "completed", "no_safe"].includes(String(item.status))
    || !(item.current_formal_concept_id === null || isRevision(item.current_formal_concept_id, "formal-concept"))
    || !isStringArray(item.no_safe_deferred_formal_concept_ids)
    || !Array.isArray(item.concept_states) || item.concept_states.length < 1
    || !Array.isArray(item.weakness_findings)
    || !isRevision(item.guidance_revision, "learner-guidance")) return false;
  const states = item.concept_states.map(readConceptState);
  const conceptIds = states.map((state) => state?.formal_concept_id as string | undefined);
  const findings = item.weakness_findings.map(readWeaknessFinding);
  const findingIds = findings.map((finding) => finding?.target_formal_concept_id as string | undefined);
  const deferredIds = item.no_safe_deferred_formal_concept_ids as string[];
  const nextAction = readNextAction(item.next_action, String(item.study_session_id));
  return !conceptIds.includes(undefined) && new Set(conceptIds).size === conceptIds.length
    && !findingIds.includes(undefined) && new Set(findingIds).size === findingIds.length
    && findingIds.every((id) => conceptIds.includes(id))
    && (item.current_formal_concept_id === null || conceptIds.includes(item.current_formal_concept_id))
    && deferredIds.every((id) => conceptIds.includes(id))
    && new Set(deferredIds).size === deferredIds.length
    && !!nextAction
    && (nextAction.target_formal_concept_id === null
      || conceptIds.includes(nextAction.target_formal_concept_id as string));
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
  if (reasonCode === "NO_SAFE_ASSESSMENT") return "目前沒有可安全提供的新題目。";
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

  async getLearnerProgress(studySessionId: string): Promise<LearnerProgressView> {
    if (!isUuid(studySessionId)) {
      throw new ApiClientError("input", "本次學習識別資訊無效。", { reasonCode: "REQUEST_INPUT_INVALID" });
    }
    const progress = await this.json(
      `/v1/study-sessions/${studySessionId}/progress`,
      { method: "GET" },
      isLearnerProgress,
    );
    if (progress.study_session_id !== studySessionId) {
      throw new ApiClientError("schema", "伺服器回應格式不符。", { reasonCode: "RESPONSE_SCHEMA_MISMATCH" });
    }
    return progress;
  }

  async applyGuidance(studySessionId: string, body: GuidanceApply): Promise<LearnerProgressView> {
    if (!isUuid(studySessionId)
      || body.schema !== "guidance-apply/v1"
      || !isRevision(body.guidance_revision, "learner-guidance")
      || Object.keys(body).length !== 2) {
      throw new ApiClientError("input", "調整學習步驟的請求無效。", { reasonCode: "REQUEST_INPUT_INVALID" });
    }
    const progress = await this.json(
      `/v1/study-sessions/${studySessionId}/guidance/apply`,
      {
        method: "POST",
        headers: { Origin: requestOrigin(), "Content-Type": "application/json" },
        body: JSON.stringify(body),
      },
      isLearnerProgress,
    );
    if (progress.study_session_id !== studySessionId) {
      throw new ApiClientError("schema", "伺服器回應格式不符。", { reasonCode: "RESPONSE_SCHEMA_MISMATCH" });
    }
    return progress;
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
