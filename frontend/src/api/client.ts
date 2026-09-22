import type { SourceView, SourceListView, SourceCapabilities, EvidenceSourceView } from "./contracts";
import type { AssessmentCycleSummary, AssessmentPlanView, AssessmentSetSummary, AssessmentSetListView, AssessmentSetAnswer, AssessmentSetView, AssessmentSetAction } from "./contracts";
import type {
  AnswerFeedbackView,
  ApiErrorView,
  ApiReasonCode,
  AssessmentView,
  KnowledgeStructureRequest,
  KnowledgeStructureView,
  KnownApiReasonCode,
  LearnerProgressView,
  MaterialProcessingRunView,
  MaterialDiscardView,
  MaterialLibraryItem,
  MaterialRename,
  MaterialLibraryView,
  StudySessionCreate,
  StudySessionFocus,
  StudySessionView,
  StudyResumeView,
} from "./contracts";

type FetchRequest = (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;
type Json = Record<string, unknown>;

const knownReasons = new Set<KnownApiReasonCode>([
  "INVALID_EMAIL",
  "INVALID_CREDENTIALS", "ACCOUNT_UNAVAILABLE", "REQUEST_INVALID", "SESSION_REQUIRED", "ORIGIN_NOT_ALLOWED", "RESOURCE_NOT_FOUND",
  "IDEMPOTENCY_CONFLICT", "ASSESSMENT_SET_CONFLICT", "ASSESSMENT_SET_ACTIVE", "NO_SAFE_ASSESSMENT", "MATERIAL_TOO_LARGE",
  "MATERIAL_NOT_DISCARDABLE",
  "SOURCE_NOT_READY", "NORMALIZER_UNAVAILABLE", "DUPLICATE_SOURCE", "REVISION_CONFLICT", "REVISION_IN_PROGRESS", "SOURCE_IN_USE", "SOURCE_BUSY",
  "MATERIAL_PDF_INVALID", "UNSUPPORTED_MEDIA_TYPE", "STORAGE_UNAVAILABLE", "INTERNAL_ERROR",
]);
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const sha = /^[0-9a-f]{64}$/;
const authTimeoutMs = 10_000;

function origin(): string {
  return globalThis.location?.origin ?? "http://127.0.0.1:4173";
}

function object(value: unknown): Json | null {
  return value !== null && typeof value === "object" && !Array.isArray(value) ? value as Json : null;
}

function revision(value: unknown, kind: string): value is string {
  return typeof value === "string" && value.startsWith(`${kind}:sha256:`) && sha.test(value.slice(kind.length + 8));
}

function strings(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === "string" && item.length > 0);
}


function timestamp(value: unknown): value is string {
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/.test(value) || !Number.isFinite(Date.parse(value))) return false;
  const date = value.slice(0, 10);
  return new Date(`${date}T00:00:00Z`).toISOString().slice(0, 10) === date
    && Number(value.slice(11, 13)) < 24 && Number(value.slice(14, 16)) < 60 && Number(value.slice(17, 19)) < 60;
}

function materialAttempt(value: unknown): boolean {
  const item = object(value);
  if (!item || typeof item.run_id !== "string" || !uuid.test(item.run_id)
    || !["queued", "evidence", "semantics", "publishing", "completed"].includes(String(item.progress_stage))
    || !Number.isInteger(item.completed_pages) || Number(item.completed_pages) < 0
    || !(item.total_pages === null || (Number.isInteger(item.total_pages) && Number(item.total_pages) > 0))
    || !(item.cancel_requested_at === null || timestamp(item.cancel_requested_at)) || !timestamp(item.created_at)) return false;
  if (item.status === "pending") return item.progress_stage === "queued" && item.error_code === null && item.cancel_requested_at === null;
  if (item.status === "running") return item.progress_stage !== "completed" && item.error_code === null
    && (item.cancel_requested_at === null || ["queued", "evidence", "semantics"].includes(String(item.progress_stage)));
  if (item.status === "cancelled") return item.progress_stage !== "completed" && item.cancel_requested_at !== null && item.error_code === null;
  if (item.status === "failed") return item.progress_stage !== "completed" && item.cancel_requested_at === null
    && typeof item.error_code === "string" && /^[A-Z][A-Z0-9_]{0,99}$/.test(item.error_code);
  return ["succeeded", "partial"].includes(String(item.status)) && item.progress_stage === "completed"
    && item.cancel_requested_at === null && item.error_code === null && item.total_pages !== null && item.completed_pages === item.total_pages;
}

function materialDiscard(value: unknown): value is MaterialDiscardView {
  const item = object(value);
  return !!item && Object.keys(item).length === 3 && item.schema === "material-discard/v1"
    && typeof item.material_id === "string" && uuid.test(item.material_id)
    && (item.state === "removing" || item.state === "removed");
}

function materialRun(value: unknown): value is MaterialProcessingRunView {
  const item = object(value);
  if (!item || item.schema !== "material-processing-run/v6" || !materialAttempt(item)) return false;
  if (item.base_revision !== undefined && !revision(item.base_revision, "knowledge-structure")) return false;
  if (item.analysis_saved !== undefined && typeof item.analysis_saved !== "boolean") return false;
  if (item.source_names !== undefined && !strings(item.source_names)) return false;
  if (typeof item.material_id !== "string" || !uuid.test(item.material_id) || typeof item.source_artifact_id !== "string" || !uuid.test(item.source_artifact_id)) return false;
  if (!timestamp(item.updated_at) || !(item.completed_at === null || timestamp(item.completed_at))) return false;
  if (item.status === "succeeded" || item.status === "partial") {
    const binding = object(item.output_binding);
    return !!binding && binding.schema === "material-run-output-binding/v4"
      && revision(binding.knowledge_structure_revision, "knowledge-structure")
      && typeof binding.runtime_lock_sha256 === "string" && sha.test(binding.runtime_lock_sha256)
      && Number.isInteger(binding.page_count) && binding.page_count === item.total_pages
      && binding.processing === item.status && ["accepted", "needs_review"].includes(String(binding.quality))
      && ["retain", "review"].includes(String(binding.decision)) && strings(binding.reason_codes)
      && Number.isInteger(binding.ocr_calls) && Number(binding.ocr_calls) >= 0 && Number(binding.ocr_calls) <= Number(binding.page_count)
      && Number.isInteger(binding.semantic_calls) && Number(binding.semantic_calls) >= 1 && item.completed_at !== null;
  }
  return item.output_binding === null && (["pending", "running"].includes(String(item.status)) ? item.completed_at === null : item.completed_at !== null);
}

function sourceView(value: unknown): value is SourceView {
  const item=object(value);
  return !!item && [item.source_id,item.normalization_id,item.original_artifact_id].every(v=>typeof v==="string" && uuid.test(v))
    && (item.included===undefined || typeof item.included==="boolean")
    && typeof item.original_name==="string" && typeof item.media_type==="string"
    && ["pending","running","ready","failed"].includes(String(item.status))
    && (item.normalized_artifact_id===null || typeof item.normalized_artifact_id==="string" && uuid.test(item.normalized_artifact_id))
    && (item.page_count===null || Number.isInteger(item.page_count) && Number(item.page_count)>0)
    && (item.error_code===null || typeof item.error_code==="string");
}
function sourceList(value: unknown): value is SourceListView {
  const item=object(value); return !!item && item.schema==="material-sources/v1" && typeof item.material_id==="string" && uuid.test(item.material_id)
    && (item.discard_requested===undefined || typeof item.discard_requested==="boolean") && Array.isArray(item.sources) && item.sources.every(sourceView);
}
function capabilities(value: unknown): value is SourceCapabilities {
  const item=object(value); return !!item && item.schema==="source-capabilities/v1" && typeof item.quality_notice==="string"
    && Array.isArray(item.formats) && item.formats.every(v=>{ const f=object(v); return !!f && typeof f.extension==="string"
      && [".pdf",".docx",".pptx",".doc",".ppt",".txt",".md"].includes(f.extension) && typeof f.media_type==="string" && Number.isInteger(f.max_bytes) && Number(f.max_bytes)>0; });
}
function evidenceSource(value:unknown): value is EvidenceSourceView {
  const item=object(value);return !!item && item.schema==="evidence-source/v1" && ["pdf","docx","pptx","doc","ppt","txt","md"].includes(String(item.format))
    && typeof item.original_name==="string" && typeof item.label==="string" && ["exact","ambiguous","unavailable"].includes(String(item.accuracy))
    && Number.isInteger(item.normalized_page) && Number(item.normalized_page)>0 && Array.isArray(item.origin_locators)
    && [item.original_url,item.preview_url].every(v=>typeof v==="string" && /^\/v[12]\/artifacts\/[0-9a-f-]+(?:#page=\d+)?$/.test(v));
}

function libraryItem(value: unknown): value is MaterialLibraryItem {
  const item = object(value);
  if (item && ((item.head_revision !== undefined && item.head_revision !== null && !revision(item.head_revision,"knowledge-structure")) || (item.source_count !== undefined && (!Number.isInteger(item.source_count) || Number(item.source_count)<0)))) return false;
  if (!item || item.schema !== "material-library-item/v3"
    || typeof item.material_id !== "string" || !uuid.test(item.material_id)
    || !(typeof item.source_artifact_id === "string" && uuid.test(item.source_artifact_id) || item.schema === "material-library-item/v3" && item.source_artifact_id === null)
    || (item.source !== undefined && !sourceView(item.source))
    || typeof item.display_name !== "string" || !item.display_name.trim()
    || !Number.isInteger(item.size_bytes) || Number(item.size_bytes) < (item.schema === "material-library-item/v3" ? 0 : 1)
    || typeof item.created_at !== "string" || !Number.isFinite(Date.parse(item.created_at))
    || !Array.isArray(item.available_structures) || !Array.isArray(item.study_sessions)) return false;
  if (!item.study_sessions.every((value) => {
    const study = object(value);
    return !!study && typeof study.study_session_id === "string" && uuid.test(study.study_session_id)
      && revision(study.knowledge_structure_revision, "knowledge-structure")
      && typeof study.run_id === "string" && uuid.test(study.run_id)
      && ["active", "no_safe", "completed"].includes(String(study.status))
      && typeof study.started_at === "string"
      && (study.current_concept_id === null || revision(study.current_concept_id, "concept"));
  })) return false;
  if (item.latest_attempt !== null && !materialAttempt(item.latest_attempt)) return false;
  return item.available_structures.every((value) => {
    const link = object(value);
    return !!link && typeof link.run_id === "string" && uuid.test(link.run_id)
      && (link.base_revision === undefined || revision(link.base_revision, "knowledge-structure"))
      && revision(link.knowledge_structure_revision, "knowledge-structure")
      && ["succeeded", "partial"].includes(String(link.status))
      && typeof link.created_at === "string" && Number.isFinite(Date.parse(link.created_at));
  });
}

function library(value: unknown): value is MaterialLibraryView {
  const item = object(value);
  return !!item && item.schema === "material-library/v2" && Array.isArray(item.materials) && item.materials.every(libraryItem);
}

function locator(value: unknown): boolean {
  const item = object(value);
  return !!item && Number.isInteger(item.page) && Number(item.page) >= 1 && revision(item.block_id, "block")
    && Array.isArray(item.region) && item.region.length === 4 && item.region.every((number) => typeof number === "number" && Number.isFinite(number));
}

function knowledgeStructure(value: unknown): value is KnowledgeStructureView {
  const item = object(value);
  if (!item || item.schema !== "knowledge-structure-view/v3" || !revision(item.knowledge_structure_revision, "knowledge-structure")) return false;
  if (item.schema === "knowledge-structure-view/v3" && (typeof item.source_resolver !== "string" || !item.source_resolver.startsWith("/v2/materials/"))) return false;
  if (!Array.isArray(item.concepts) || !Array.isArray(item.relations) || !Array.isArray(item.initial_learning_path)) return false;
  const concepts = item.concepts as unknown[];
  const conceptIds: string[] = [];
  for (const value of concepts) {
    const concept = object(value);
    if (!concept || !revision(concept.concept_id, "concept") || typeof concept.label !== "string" || !Array.isArray(concept.claims)) return false;
    conceptIds.push(concept.concept_id);
    for (const claimValue of concept.claims) {
      const claim = object(claimValue);
      if (!claim || !revision(claim.claim_id, "claim") || typeof claim.text !== "string" || !Array.isArray(claim.evidence)) return false;
      if (!claim.evidence.every((value) => {
        const evidence = object(value);
        return !!evidence && revision(evidence.evidence_id, "evidence") && Number.isInteger(evidence.page)
          && (evidence.source_id === undefined || typeof evidence.source_id === "string" && uuid.test(evidence.source_id))
          && (evidence.source_name === undefined || typeof evidence.source_name === "string")
          && (evidence.normalized_page === undefined || Number.isInteger(evidence.normalized_page) && Number(evidence.normalized_page)>0)
          && (evidence.source === "native_text" || evidence.source === "unlimited_ocr")
          && typeof evidence.quote === "string" && locator(evidence.source_locator);
      })) return false;
    }
  }
  if (conceptIds.length !== new Set(conceptIds).size) return false;
  const known = new Set(conceptIds);
  const relationTypes = new Set(["prerequisite", "part_of", "application", "example", "contrast"]);
  if (!(item.relations as unknown[]).every((value) => {
    const relation = object(value);
    return !!relation && revision(relation.relation_id, "relation")
      && known.has(String(relation.source_concept_id)) && known.has(String(relation.target_concept_id))
      && relation.source_concept_id !== relation.target_concept_id && relationTypes.has(String(relation.type))
      && typeof relation.learner_reason === "string";
  })) return false;
  const pathIds = (item.initial_learning_path as unknown[]).map((value) => object(value)?.concept_id);
  return pathIds.length === conceptIds.length && pathIds.every((id) => typeof id === "string" && known.has(id))
    && new Set(pathIds).size === pathIds.length;
}

function studySession(value: unknown): value is StudySessionView {
  const item = object(value);
  return !!item && item.schema === "study-session/v2" && typeof item.study_session_id === "string" && uuid.test(item.study_session_id)
    && typeof item.material_id === "string" && uuid.test(item.material_id)
    && revision(item.knowledge_structure_revision, "knowledge-structure")
    && (item.current_concept_id === null || revision(item.current_concept_id, "concept"))
    && strings(item.deferred_concept_ids) && strings(item.no_safe_claim_ids) && Number.isInteger(item.event_watermark)
    && ["active", "no_safe", "completed"].includes(String(item.status));
}

function assessment(value: unknown): value is AssessmentView {
  const item = object(value);
  if (!item || item.schema !== "single-choice-assessment/v2" || !revision(item.assessment_revision, "assessment")
    || typeof item.study_session_id !== "string" || !uuid.test(item.study_session_id)
    || !revision(item.knowledge_structure_revision, "knowledge-structure") || !revision(item.question_id, "question")
    || !revision(item.target_concept_id, "concept") || !revision(item.target_claim_id, "claim")
    || typeof item.prompt !== "string" || !strings(item.source_evidence_ids) || !Array.isArray(item.options) || item.options.length !== 4) return false;
  const options = item.options as unknown[];
  const ids = options.map((value) => object(value)?.option_id);
  return options.every((value) => {
    const option = object(value);
    return !!option && revision(option.option_id, "option") && typeof option.text === "string" && option.text.length > 0;
  }) && new Set(ids).size === 4 && !Object.hasOwn(item, "correct_option_id");
}

function feedback(value: unknown): value is AnswerFeedbackView {
  const item = object(value);
  return !!item && item.schema === "answer-feedback/v2" && typeof item.answer_event_id === "string" && uuid.test(item.answer_event_id)
    && typeof item.study_session_id === "string" && uuid.test(item.study_session_id)
    && revision(item.assessment_revision, "assessment") && revision(item.question_id, "question")
    && revision(item.selected_option_id, "option") && typeof item.is_correct === "boolean"
    && typeof item.rationale === "string" && strings(item.source_evidence_ids) && Number.isInteger(item.event_number);
}

function progress(value: unknown): value is LearnerProgressView {
  const item = object(value);
  if (!item || item.schema !== "learner-progress/v3" || typeof item.study_session_id !== "string" || !uuid.test(item.study_session_id)
    || !revision(item.knowledge_structure_revision, "knowledge-structure") || !Number.isInteger(item.event_watermark)
    || !(item.current_concept_id === null || revision(item.current_concept_id, "concept"))
    || !Array.isArray(item.assessment_cycles) || !item.assessment_cycles.every(cycleSummary)
    || !Array.isArray(item.concept_states) || !Array.isArray(item.weaknesses) || !object(item.next_action)
    || !revision(item.guidance_revision, "learner-guidance")) return false;
  return item.concept_states.every((value) => {
    const state = object(value);
    return !!state && revision(state.concept_id, "concept") && typeof state.label === "string"
      && ["not_started", "learning", "needs_review", "mastered"].includes(String(state.status));
  });
}


function assessmentPlan(value: unknown): value is AssessmentPlanView {
  const item = object(value);
  return !!item && item.schema === "assessment-plan/v1" && typeof item.study_session_id === "string" && uuid.test(item.study_session_id)
    && revision(item.knowledge_structure_revision, "knowledge-structure") && revision(item.concept_id, "concept")
    && item.policy === "single-concept-grounded-points/v1" && Number.isSafeInteger(item.point_count) && Number(item.point_count) >= 0
    && Number.isSafeInteger(item.requested_count) && Array.isArray(item.targets) && item.targets.length === item.requested_count
    && item.targets.every(value => { const row = object(value); return !!row && revision(row.claim_id, "claim")
      && strings(row.covered_claim_ids) && row.covered_claim_ids.every(id => revision(id, "claim")) && row.reason === "distinct_grounded_point"; })
    && Array.isArray(item.excluded) && item.excluded.every(value => { const row = object(value);
      return !!row && revision(row.claim_id, "claim") && row.reason === "no_content_evidence"; });
}

function cycleSummary(value: unknown): value is AssessmentCycleSummary {
  const item = object(value);
  return !!item && typeof item.diagnostic_set_id === "string" && uuid.test(item.diagnostic_set_id)
    && revision(item.concept_id, "concept") && Number.isSafeInteger(item.set_version) && Number(item.set_version) > 0
    && ["in_progress", "needs_review", "ready_for_remediation", "passed", "incomplete", "deferred"].includes(String(item.outcome))
    && (item.closed_at === null || timestamp(item.closed_at))
    && (item.active_set_id === null || typeof item.active_set_id === "string" && uuid.test(item.active_set_id))
    && ["passed_count", "remediation_passed_count", "pending_count", "unanswered_count", "unavailable_count"].every(key => Number.isSafeInteger(item[key]) && Number(item[key]) >= 0)
    && Number(item.remediation_passed_count) <= Number(item.passed_count);
}

function assessmentSetSummary(value: unknown): value is AssessmentSetSummary {
  const item = object(value);
  return !!item && ["diagnostic", "remediation"].includes(String(item.kind))
    && (item.kind === "diagnostic" ? item.diagnostic_set_id === null : typeof item.diagnostic_set_id === "string" && uuid.test(item.diagnostic_set_id) && item.diagnostic_set_id !== item.set_id)
    && typeof item.set_id === "string" && uuid.test(item.set_id) && revision(item.target_concept_id, "concept")
    && ["preparing", "partial_ready", "failed", "ready", "in_progress", "completed", "cancelled"].includes(String(item.status))
    && ["set_version", "requested_count", "published_count", "answered_count", "passed_count"].every(key => Number.isSafeInteger(item[key]) && Number(item[key]) >= 0)
    && Number(item.set_version) > 0 && Number(item.passed_count) <= Number(item.answered_count)
    && Number(item.answered_count) <= Number(item.published_count) && Number(item.published_count) <= Number(item.requested_count)
    && strings(item.assessment_revisions) && item.assessment_revisions.length === item.published_count
    && item.assessment_revisions.every(id => revision(id, "assessment")) && new Set(item.assessment_revisions).size === item.assessment_revisions.length
    && timestamp(item.created_at) && (item.completed_at === null || timestamp(item.completed_at));
}

function assessmentSetList(value: unknown): value is AssessmentSetListView {
  const item = object(value);
  if (!item || item.schema !== "assessment-set-list/v3" || typeof item.study_session_id !== "string" || !uuid.test(item.study_session_id)
    || !revision(item.knowledge_structure_revision, "knowledge-structure") || !Array.isArray(item.sets) || !item.sets.every(assessmentSetSummary)) return false;
  const active = item.sets.filter(group => ["preparing", "partial_ready", "ready", "in_progress"].includes(group.status));
  return new Set(item.sets.map(group => group.set_id)).size === item.sets.length
    && strings(item.active_set_ids) && item.active_set_ids.length === active.length
    && new Set(item.active_set_ids).size === active.length
    && new Set(active.map(group => group.target_concept_id)).size === active.length
    && active.every(group => (item.active_set_ids as string[]).includes(group.set_id));
}

function assessmentSet(value: unknown): value is AssessmentSetView {
  if (!assessmentSetSummary(value)) return false;
  const summary = value;
  const item = object(value);
  if (!item || item.schema !== "assessment-set/v2"
    || typeof item.study_session_id !== "string" || !uuid.test(item.study_session_id)
    || typeof item.material_id !== "string" || !uuid.test(item.material_id)
    || !revision(item.knowledge_structure_revision, "knowledge-structure")
    || item.selection_policy !== (item.kind === "diagnostic" ? "single-concept-grounded-points/v1" : "reviewed-wrong-points/v1")
    || !["point_count", "excluded_count", "verified_count"].every(key => Number.isSafeInteger(item[key]) && Number(item[key]) >= 0)
    || !["can_retry", "can_publish_partial", "can_complete", "can_cancel"].every(key => typeof item[key] === "boolean")
    || !Array.isArray(item.items) || item.items.length !== item.requested_count
    || Number(item.verified_count) > Number(item.requested_count)
    || Number(item.point_count) < Number(item.requested_count) + Number(item.excluded_count)
    || ["runtime_lock_document", "execution_identity", "target_plan", "action_receipts", "review_actions"].some(key => Object.hasOwn(item, key))) return false;
  const cycle = object(item.cycle);
  if (!cycleSummary(item.cycle) || !cycle || cycle.concept_id !== item.target_concept_id
    || cycle.diagnostic_set_id !== (item.kind === "diagnostic" ? item.set_id : item.diagnostic_set_id)
    || !["can_create_remediation", "can_close", "can_review"].every(key => typeof cycle[key] === "boolean")
    || !Array.isArray(cycle.points)) return false;
  const results = new Map<string, number>();
  const cycleClaims = new Set<string>();
  for (const point of cycle.points) {
    const row = object(point);
    if (!row || !revision(row.claim_id, "claim") || cycleClaims.has(String(row.claim_id))
      || !["unavailable", "unanswered", "diagnostic_pass", "needs_review", "reviewed", "remediation_pass", "deferred"].includes(String(row.result))
      || !["latest_answer_event_id", "latest_set_id"].every(key => row[key] === null || typeof row[key] === "string" && uuid.test(row[key] as string))) return false;
    cycleClaims.add(String(row.claim_id)); results.set(String(row.result), (results.get(String(row.result)) ?? 0) + 1);
  }
  const count = (name: string) => results.get(name) ?? 0;
  if (cycle.passed_count !== count("diagnostic_pass") + count("remediation_pass")
    || cycle.remediation_passed_count !== count("remediation_pass")
    || cycle.pending_count !== count("needs_review") + count("reviewed") + count("deferred")
    || cycle.unanswered_count !== count("unanswered") || Number(cycle.unavailable_count) < count("unavailable")) return false;
  let published = 0, answered = 0, passed = 0, verified = 0;
  const claims = new Set<string>(), revisions = new Set<string>();
  const valid = item.items.every((value, index) => {
    const row = object(value);
    if (!row || row.ordinal !== index + 1 || !revision(row.target_claim_id, "claim")
      || !["pending", "generating", "verified", "published", "failed", "omitted"].includes(String(row.state))
      || !Number.isSafeInteger(row.attempts) || Number(row.attempts) < 0 || Number(row.attempts) > 2
      || typeof row.can_submit !== "boolean" || !(row.failure_reason === null || typeof row.failure_reason === "string")
      || ["prepared_document", "private_answer_document", "generation_provenance", "correct_option_id"].some(key => Object.hasOwn(row, key))) return false;
    if (claims.has(String(row.target_claim_id))) return false;
    claims.add(String(row.target_claim_id));
    if (row.state === "verified" || row.state === "published") verified += 1;
    if (row.assessment === null) return row.state !== "published" && row.feedback === null && row.created_at === null && !row.can_submit;
    if (row.state !== "published" || !assessment(row.assessment) || !timestamp(row.created_at)
      || row.assessment.study_session_id !== item.study_session_id || row.assessment.knowledge_structure_revision !== item.knowledge_structure_revision
      || row.assessment.target_concept_id !== item.target_concept_id || row.assessment.target_claim_id !== row.target_claim_id
      || !summary.assessment_revisions.includes(row.assessment.assessment_revision)) return false;
    if (revisions.has(row.assessment.assessment_revision)) return false;
    revisions.add(row.assessment.assessment_revision);
    published += 1;
    if (row.feedback === null) return !row.can_submit || ["ready", "in_progress"].includes(summary.status);
    if (!feedback(row.feedback) || row.can_submit || row.feedback.assessment_revision !== row.assessment.assessment_revision
      || row.feedback.study_session_id !== item.study_session_id || row.feedback.question_id !== row.assessment.question_id) return false;
    answered += 1; if (row.feedback.is_correct) passed += 1;
    return true;
  });
  return valid && verified === item.verified_count && published === item.published_count && answered === item.answered_count && passed === item.passed_count;
}

function studyResume(value: unknown): value is StudyResumeView {
  const item = object(value);
  if (!item || item.schema !== "study-resume/v4" || !studySession(item.session)
    || !knowledgeStructure(item.knowledge_structure) || !progress(item.progress)
    || typeof item.run_id !== "string" || !uuid.test(item.run_id)
    || typeof item.source_artifact_id !== "string" || !uuid.test(item.source_artifact_id)
    || !Array.isArray(item.assessment_sets)
    || !item.assessment_sets.every(assessmentSetSummary)
    || !(item.selected_set_id === null || item.assessment_sets.some(group => group.set_id === item.selected_set_id))) return false;
  const session = item.session;
  if (item.progress.study_session_id !== session.study_session_id
    || item.progress.knowledge_structure_revision !== session.knowledge_structure_revision
    || item.knowledge_structure.knowledge_structure_revision !== session.knowledge_structure_revision
    || item.progress.event_watermark !== session.event_watermark) return false;
  return true;
}

function apiError(value: unknown): value is ApiErrorView {
  const item = object(value);
  return !!item && item.schema === "api-error/v1" && typeof item.request_id === "string" && uuid.test(item.request_id)
    && typeof item.reason_code === "string" && typeof item.retryable === "boolean" && item.message === "Request could not be completed.";
}

function safeMessage(reason: ApiReasonCode): string {
  if (reason === "INVALID_EMAIL") return "請輸入有效的 Email 格式。";
  if (reason === "SESSION_REQUIRED") return "工作階段已失效，請重新登入。";
  if (reason === "INVALID_CREDENTIALS") return "Email 或密碼不正確。";
  if (reason === "ACCOUNT_UNAVAILABLE") return "這個 Email 已被使用，請使用其他 Email。";
  if (reason === "RESOURCE_NOT_FOUND") return "找不到這筆資料，或你沒有權限讀取。";
  if (reason === "SOURCE_NOT_READY") return "教材尚未完成轉換，請稍後再開始分析。";
  if (reason === "DUPLICATE_SOURCE") return "這份檔案已在教材來源清單中，請直接選取既有來源。";
  if (reason === "REVISION_CONFLICT") return "教材已更新，請重新讀取目前地圖與來源清單。";
  if (reason === "REVISION_IN_PROGRESS") return "這份教材已有更新正在處理，請先查看該次處理。";
  if (reason === "SOURCE_IN_USE") return "這份來源已被分析引用，無法刪除；可取消勾選，不加入這次更新。";
  if (reason === "SOURCE_BUSY") return "教材仍在轉換中，完成後才可移除。";
  if (reason === "NORMALIZER_UNAVAILABLE") return "轉換工具目前不可用，仍可使用 PDF 上傳。";
  if (reason === "ASSESSMENT_SET_ACTIVE") return "已有尚未完成的題組，可從題組紀錄接續。";
  if (reason === "ASSESSMENT_SET_CONFLICT") return "題組狀態已更新，請重新讀取後繼續。";
  if (reason === "NO_SAFE_ASSESSMENT") return "目前沒有可安全提供的新題目。";
  if (reason === "MATERIAL_TOO_LARGE") return "每個檔案不可超過 100 MiB。";
  if (reason === "MATERIAL_PDF_INVALID") return "這份 PDF 已損毀、加密或無法開啟。";
  if (reason === "UNSUPPORTED_MEDIA_TYPE") return "此檔案格式目前不支援，請優先使用 PDF。";
  if (reason === "STORAGE_UNAVAILABLE") return "資料服務暫時無法使用，請稍後再試。";
  if (reason === "MATERIAL_NOT_DISCARDABLE") return "這份教材正在刪除，無法進行這項操作。";
  return "請求無法完成，請稍後再試。";
}

export class ApiClientError extends Error {
  readonly kind: "api" | "network" | "schema" | "input";
  readonly details: { status?: number; reasonCode: string; requestId?: string; retryable?: boolean };

  constructor(
    kind: "api" | "network" | "schema" | "input",
    message: string,
    details: { status?: number; reasonCode: string; requestId?: string; retryable?: boolean },
  ) {
    super(message);
    this.name = "ApiClientError";
    this.kind = kind;
    this.details = details;
  }

  get status(): number | null { return this.details.status ?? null; }
  get reasonCode(): string { return this.details.reasonCode; }
  get requestId(): string | null { return this.details.requestId ?? null; }
  get retryable(): boolean { return this.details.retryable ?? false; }
}

export type LearnerIdentity = { schema: "learner-identity/v1"; learner_id: string };

function identity(value: unknown): value is LearnerIdentity {
  const item = object(value);
  return !!item && item.schema === "learner-identity/v1" && typeof item.learner_id === "string" && uuid.test(item.learner_id);
}

export class StudydyApiClient {
  private sessionReady: Promise<LearnerIdentity> | null = null;
  private active = true;
  private readonly pending = new Set<AbortController>();
  private readonly fetchRequest: FetchRequest;
  onSessionExpired: (() => void) | null = null;

  constructor(fetchRequest: FetchRequest = fetch.bind(globalThis)) {
    this.fetchRequest = fetchRequest;
  }

  invalidate(): void {
    this.active = false;
    this.sessionReady = null;
    for (const controller of this.pending) controller.abort();
    this.pending.clear();
  }

  private requireActive(): void {
    if (!this.active) throw new ApiClientError("api", "工作階段已結束，請重新登入。", { reasonCode: "SESSION_REQUIRED" });
  }

  async ensureSession(): Promise<LearnerIdentity> {
    this.requireActive();
    if (!this.sessionReady) {
      this.sessionReady = this.request("/v1/session/refresh", { method: "POST", headers: { Origin: origin() } }, authTimeoutMs)
        .then(() => this.currentIdentity())
        .finally(() => { this.sessionReady = null; });
    }
    return this.sessionReady;
  }

  currentIdentity(): Promise<LearnerIdentity> {
    return this.json("/v1/session", { method: "GET" }, identity, authTimeoutMs);
  }

  authenticate(mode: "login" | "register", email: string, password: string): Promise<LearnerIdentity> {
    return this.json(mode === "register" ? "/v1/accounts" : "/v1/session/login", {
      method: "POST", headers: { "Content-Type": "application/json", Origin: origin() },
      body: JSON.stringify({ email, password }),
    }, identity, authTimeoutMs);
  }

  async logout(): Promise<void> {
    await this.request("/v1/session", { method: "DELETE", headers: { Origin: origin() } }, authTimeoutMs);
  }

  private async request(path: string, init: RequestInit, timeoutMs?: number): Promise<{ status: number; value: unknown }> {
    this.requireActive();
    const controller = new AbortController();
    this.pending.add(controller);
    let timedOut = false;
    const cancellationError = () => timedOut
      ? new ApiClientError("network", "連線逾時，請再試一次。", { reasonCode: "REQUEST_TIMEOUT", retryable: true })
      : new ApiClientError("api", "工作階段已結束，請重新登入。", { reasonCode: "SESSION_REQUIRED" });
    let onAbort!: () => void;
    const cancelled = new Promise<never>((_resolve, reject) => {
      onAbort = () => reject(cancellationError());
      controller.signal.addEventListener("abort", onAbort, { once: true });
    });
    const timer = timeoutMs === undefined ? undefined : setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, timeoutMs);
    const checkActive = () => {
      this.requireActive();
      if (controller.signal.aborted) throw cancellationError();
    };
    const read = async () => {
      let response: Response;
      try {
        response = await this.fetchRequest(path, { ...init, credentials: "same-origin", cache: "no-store", signal: controller.signal });
      } catch {
        checkActive();
        throw new ApiClientError("network", "無法連線到 Studydy。", { reasonCode: "NETWORK_ERROR", retryable: true });
      }
      checkActive();
      let value: unknown = null;
      if (response.status !== 204) {
        try { value = await response.json(); } catch { /* 依 HTTP 狀態區分格式錯誤與服務故障。 */ }
      }
      checkActive();
      if (response.ok) return { status: response.status, value };
      if (!apiError(value)) {
        if (response.status >= 500) throw new ApiClientError("network", "Studydy 服務暫時無法使用，請稍後再試。", { status: response.status, reasonCode: "SERVICE_UNAVAILABLE", retryable: true });
        throw new ApiClientError("schema", "伺服器回應格式無法辨識。", { status: response.status, reasonCode: "RESPONSE_SCHEMA_MISMATCH" });
      }
      if (response.status === 401 && value.reason_code === "SESSION_REQUIRED") {
        // 取消其他請求，保留這次回應的正式錯誤資訊。
        this.pending.delete(controller);
        this.invalidate();
        this.onSessionExpired?.();
      }
      const reason = knownReasons.has(value.reason_code as KnownApiReasonCode) ? value.reason_code as KnownApiReasonCode : "UNKNOWN_API_ERROR";
      throw new ApiClientError("api", safeMessage(reason), { status: response.status, reasonCode: reason, requestId: value.request_id, retryable: value.retryable });
    };
    try {
      return await Promise.race([read(), cancelled]);
    } finally {
      clearTimeout(timer);
      controller.signal.removeEventListener("abort", onAbort);
      this.pending.delete(controller);
    }
  }

  private async json<T>(path: string, init: RequestInit, guard: (value: unknown) => value is T, timeoutMs?: number): Promise<T> {
    const { status, value } = await this.request(path, init, timeoutMs);
    this.requireActive();
    if (!guard(value)) throw new ApiClientError("schema", "伺服器回應格式無法辨識。", { status, reasonCode: "RESPONSE_SCHEMA_MISMATCH" });
    return value;
  }

  private post<T>(path: string, body: unknown, key: string, guard: (value: unknown) => value is T): Promise<T> {
    return this.json(path, {
      method: "POST",
      headers: { "Content-Type": "application/json", Origin: origin(), "Idempotency-Key": key },
      body: JSON.stringify(body),
    }, guard);
  }


  sourceCapabilities(): Promise<SourceCapabilities> { return this.json("/v2/source-capabilities",{method:"GET"},capabilities); }
  createDraft(name:string,key:string): Promise<{schema:"material-draft/v1";material_id:string}> {
    return this.post("/v2/materials",{schema:"material-draft-create/v1",display_name:name},key,
      (value): value is {schema:"material-draft/v1";material_id:string} => {const item=object(value);return !!item && item.schema==="material-draft/v1" && typeof item.material_id==="string" && uuid.test(item.material_id);});
  }
  uploadSource(materialId:string,file:File,mediaType:string,key:string): Promise<SourceListView> {
    return this.json(`/v2/materials/${encodeURIComponent(materialId)}/sources`,{method:"POST",body:file,
      headers:{"Content-Type":mediaType,Origin:origin(),"Idempotency-Key":key,"X-Material-Name":encodeURIComponent(file.name)}},sourceList);
  }
  getSources(materialId:string): Promise<SourceListView> {return this.json(`/v2/materials/${encodeURIComponent(materialId)}/sources`,{method:"GET"},sourceList);}
  removeStagedSource(materialId:string,sourceId:string): Promise<SourceListView> {return this.json(`/v2/materials/${encodeURIComponent(materialId)}/sources/${encodeURIComponent(sourceId)}`,{method:"DELETE",headers:{Origin:origin()}},sourceList);}
  retryNormalization(materialId:string,id:string): Promise<SourceListView> {return this.json(`/v2/materials/${encodeURIComponent(materialId)}/sources/${encodeURIComponent(id)}/retry`,{method:"POST",headers:{Origin:origin()}},sourceList);}
  createRevision(materialId:string,normalizationIds:string[],key:string,baseRevision:string|null=null): Promise<MaterialProcessingRunView> {
    return this.post(`/v2/materials/${encodeURIComponent(materialId)}/revisions`,{schema:"material-revision-create/v1",base_revision:baseRevision,normalization_ids:normalizationIds},key,materialRun);
  }
  cancelRevision(runId:string,baseRevision:string): Promise<MaterialProcessingRunView> {
    return this.post(`/v2/material-processing-runs/${encodeURIComponent(runId)}/cancel`,{schema:"material-revision-cancel/v1",base_revision:baseRevision},crypto.randomUUID(),materialRun);
  }
  retryRevision(runId:string,key:string): Promise<MaterialProcessingRunView> {
    return this.json(`/v2/material-processing-runs/${encodeURIComponent(runId)}/retry`,{method:"POST",headers:{Origin:origin(),"Idempotency-Key":key}},materialRun);
  }
  resolveEvidence(base:string,evidenceId:string): Promise<EvidenceSourceView> {
    if (!base.startsWith("/v2/materials/")) throw new Error("SOURCE_ROUTE_INVALID");
    return this.json(`${base}/${encodeURIComponent(evidenceId)}/source`,{method:"GET"},evidenceSource);
  }

  listMaterials(): Promise<MaterialLibraryView> {
    return this.json("/v1/materials", { method: "GET" }, library);
  }

  async getMaterial(materialId: string): Promise<MaterialLibraryItem> {
    const item = await this.json(`/v1/materials/${encodeURIComponent(materialId)}`, { method: "GET" }, libraryItem);
    if (item.material_id !== materialId) throw new ApiClientError("schema", "教材身分不一致。", { reasonCode: "RESPONSE_SCHEMA_MISMATCH" });
    return item;
  }


  getMaterialRun(runId: string): Promise<MaterialProcessingRunView> {
    return this.json(`/v1/material-processing-runs/${encodeURIComponent(runId)}`, { method: "GET" }, materialRun);
  }

  async renameMaterial(materialId: string, displayName: string): Promise<MaterialLibraryItem> {
    const item = await this.json(`/v1/materials/${encodeURIComponent(materialId)}/rename`, {
      method: "POST", headers: { "Content-Type": "application/json", Origin: origin() },
      body: JSON.stringify({ schema: "material-rename/v1", display_name: displayName } satisfies MaterialRename),
    }, libraryItem);
    if (item.material_id !== materialId) throw new ApiClientError("schema", "教材身分不一致。", { reasonCode: "RESPONSE_SCHEMA_MISMATCH" });
    return item;
  }

  async discardMaterial(materialId: string): Promise<MaterialDiscardView> {
    const view = await this.json(`/v1/materials/${encodeURIComponent(materialId)}`, {
      method: "DELETE", headers: { Origin: origin() },
    }, materialDiscard, 10_000);
    if (view.material_id !== materialId) throw new ApiClientError("schema", "教材身分不一致。", { reasonCode: "RESPONSE_SCHEMA_MISMATCH" });
    return view;
  }

  async getKnowledgeStructure(request: KnowledgeStructureRequest): Promise<KnowledgeStructureView> {
    const view = await this.json(`/v1/materials/${encodeURIComponent(request.materialId)}/knowledge-structures/${encodeURIComponent(request.structureRevision)}`, { method: "GET" }, knowledgeStructure);
    if (view.knowledge_structure_revision !== request.structureRevision) throw new ApiClientError("schema", "教材結構版本不一致。", { reasonCode: "RESPONSE_SCHEMA_MISMATCH" });
    return view;
  }

  async focusStudySession(studySessionId: string, currentConceptId: string): Promise<StudySessionView> {
    const state = await this.json(`/v1/study-sessions/${encodeURIComponent(studySessionId)}/focus`, {
      method: "POST", headers: { "Content-Type": "application/json", Origin: origin() },
      body: JSON.stringify({ schema: "study-session-focus/v1", current_concept_id: currentConceptId } satisfies StudySessionFocus),
    }, studySession);
    if (state.study_session_id !== studySessionId) throw new ApiClientError("schema", "學習進度身分不一致。", { reasonCode: "RESPONSE_SCHEMA_MISMATCH" });
    return state;
  }

  createStudySession(body: StudySessionCreate, key: string = crypto.randomUUID()): Promise<StudySessionView> {
    return this.post("/v1/study-sessions", body, key, studySession);
  }

  async resumeStudy(request: { materialId: string; structureRevision: string; studySessionId: string; runId: string; assessmentSetId?: string }): Promise<StudyResumeView> {
    const query = new URLSearchParams({ run_id: request.runId });
    if (request.assessmentSetId) query.set("set_id", request.assessmentSetId);
    let restored: StudyResumeView;
    // Worker 可能恰好發布題目；僅對 snapshot 衝突補讀，絕不重播建立或作答。
    for (let attempt = 0; ; attempt += 1) {
      try {
        restored = await this.json(`/v1/materials/${encodeURIComponent(request.materialId)}/knowledge-structures/${encodeURIComponent(request.structureRevision)}/study-sessions/${encodeURIComponent(request.studySessionId)}/resume?${query}`, { method: "GET" }, studyResume);
        break;
      } catch (error) {
        if (!(error instanceof ApiClientError) || error.reasonCode !== "IDEMPOTENCY_CONFLICT" || attempt >= 2) throw error;
        await new Promise(resolve => setTimeout(resolve, 100 * (attempt + 1)));
      }
    }
    if (restored.session.material_id !== request.materialId || restored.session.study_session_id !== request.studySessionId
      || restored.session.knowledge_structure_revision !== request.structureRevision || restored.run_id !== request.runId
      || (request.assessmentSetId !== undefined && restored.selected_set_id !== request.assessmentSetId)) {
      throw new ApiClientError("schema", "學習紀錄與教材版本不一致。", { reasonCode: "RESPONSE_SCHEMA_MISMATCH" });
    }
    return restored;
  }

  async readAssessmentPlan(id: string, conceptId: string): Promise<AssessmentPlanView> {
    const value = await this.json(`/v1/study-sessions/${encodeURIComponent(id)}/assessment-plan?concept_id=${encodeURIComponent(conceptId)}`, { method: "GET" }, assessmentPlan);
    if (value.study_session_id !== id || value.concept_id !== conceptId) throw new ApiClientError("schema", "題組範圍不一致。", { reasonCode: "RESPONSE_SCHEMA_MISMATCH" });
    return value;
  }

  async listAssessmentSets(id: string): Promise<AssessmentSetListView> {
    const value = await this.json(`/v1/study-sessions/${encodeURIComponent(id)}/assessment-sets`, { method: "GET" }, assessmentSetList);
    if (value.study_session_id !== id) throw new ApiClientError("schema", "題組範圍不一致。", { reasonCode: "RESPONSE_SCHEMA_MISMATCH" });
    return value;
  }

  async readAssessmentSet(id: string, setId: string): Promise<AssessmentSetView> {
    const value = await this.json(`/v1/study-sessions/${encodeURIComponent(id)}/assessment-sets/${encodeURIComponent(setId)}`, { method: "GET" }, assessmentSet);
    if (value.study_session_id !== id || value.set_id !== setId) throw new ApiClientError("schema", "題組範圍不一致。", { reasonCode: "RESPONSE_SCHEMA_MISMATCH" });
    return value;
  }

  async createAssessmentSet(id: string, conceptId: string, key: string): Promise<AssessmentSetView> {
    const value = await this.post(`/v1/study-sessions/${encodeURIComponent(id)}/assessment-sets`,
      { schema: "assessment-set-create/v1", target_concept_id: conceptId }, key, assessmentSet);
    if (value.study_session_id !== id || value.target_concept_id !== conceptId) throw new ApiClientError("schema", "題組範圍不一致。", { reasonCode: "RESPONSE_SCHEMA_MISMATCH" });
    return value;
  }

  async changeAssessmentSet(id: string, setId: string, action: AssessmentSetAction, version: number, key: string): Promise<AssessmentSetView> {
    const value = await this.post(`/v1/study-sessions/${encodeURIComponent(id)}/assessment-sets/${encodeURIComponent(setId)}/${action}`,
      { schema: "assessment-set-action/v1", expected_set_version: version }, key, assessmentSet);
    if (value.study_session_id !== id || value.set_id !== setId) throw new ApiClientError("schema", "題組範圍不一致。", { reasonCode: "RESPONSE_SCHEMA_MISMATCH" });
    return value;
  }

  async submitAssessmentSet(id: string, setId: string, answers: AssessmentSetAnswer[], version: number, key: string): Promise<AssessmentSetView> {
    const value = await this.post(`/v1/study-sessions/${encodeURIComponent(id)}/assessment-sets/${encodeURIComponent(setId)}/submissions`,
      { schema: "assessment-set-submission/v1", expected_set_version: version, answers }, key, assessmentSet);
    const expected = new Map(answers.map(answer => [answer.assessment_revision, answer]));
    if (value.study_session_id !== id || value.set_id !== setId || value.status !== "completed"
      || value.answered_count !== value.published_count || value.published_count !== answers.length
      || value.items.some(item => item.assessment && (item.feedback === null
        || item.feedback.question_id !== expected.get(item.assessment.assessment_revision)?.question_id
        || item.feedback.selected_option_id !== expected.get(item.assessment.assessment_revision)?.selected_option_id))) throw new ApiClientError("schema", "題組範圍不一致。", { reasonCode: "RESPONSE_SCHEMA_MISMATCH" });
    return value;
  }

  async reviewAssessmentPoint(id: string, rootId: string, claimId: string, action: "review" | "defer", version: number, key: string): Promise<AssessmentSetView> {
    const value = await this.post(`/v1/study-sessions/${encodeURIComponent(id)}/assessment-sets/${encodeURIComponent(rootId)}/reviews`,
      { schema: "assessment-review/v1", target_claim_id: claimId, action, expected_set_version: version }, key, assessmentSet);
    if (value.study_session_id !== id || value.set_id !== rootId) throw new ApiClientError("schema", "題組範圍不一致。", { reasonCode: "RESPONSE_SCHEMA_MISMATCH" });
    return value;
  }

  async createRemediationSet(id: string, rootId: string, version: number, key: string): Promise<AssessmentSetView> {
    const value = await this.post(`/v1/study-sessions/${encodeURIComponent(id)}/assessment-sets/${encodeURIComponent(rootId)}/remediation`,
      { schema: "assessment-set-action/v1", expected_set_version: version }, key, assessmentSet);
    if (value.study_session_id !== id || value.diagnostic_set_id !== rootId || value.kind !== "remediation") throw new ApiClientError("schema", "題組範圍不一致。", { reasonCode: "RESPONSE_SCHEMA_MISMATCH" });
    return value;
  }




  sourceArtifactUrl(id: string, page?: number): string {
    return `/v1/artifacts/${encodeURIComponent(id)}${page ? `#page=${page}` : ""}`;
  }
}

export function errorMessage(error: unknown): string {
  return error instanceof ApiClientError ? error.message : "發生未預期的錯誤，請稍後再試。";
}
