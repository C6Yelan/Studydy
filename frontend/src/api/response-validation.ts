import type {
  PodcastSummary, PodcastView, PodcastListView, PodcastDeletedView,
  AnswerFeedbackView,
  CardSetSummary,
  CardSetView,
  CardSetListView,
  CardSetDeletedView,
  ConceptCard,
  ApiErrorView,
  AssessmentCycleSummary,
  AssessmentPlanView,
  AssessmentSetListView,
  AssessmentSetSummary,
  AssessmentSetView,
  AssessmentView,
  EvidenceSourceView,
  KnowledgeStructureView,
  LearnerIdentity,
  LearnerProgressView,
  MaterialDiscardView,
  MaterialLibraryItem,
  MaterialLibraryView,
  MaterialProcessingRunView,
  SourceCapabilities,
  SourceListView,
  SourceView,
  StudyResumeView,
  StudySessionView,
} from "./contracts";

type Json = Record<string, unknown>;

const uuidPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

const sha = /^[0-9a-f]{64}$/;

function object(value: unknown): Json | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Json)
    : null;
}

function isUuid(value: unknown): value is string {
  return typeof value === "string" && uuidPattern.test(value);
}

export function voiceTurnReceipt(value: unknown): value is { turn_id: string } {
  const row = object(value);
  return !!row && isUuid(row.turn_id);
}

function revision(value: unknown, kind: string): value is string {
  return (
    typeof value === "string" &&
    value.startsWith(`${kind}:sha256:`) &&
    sha.test(value.slice(kind.length + 8))
  );
}

function strings(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === "string" && item.length > 0);
}

function timestamp(value: unknown): value is string {
  if (
    typeof value !== "string" ||
    !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/.test(value) ||
    !Number.isFinite(Date.parse(value))
  )
    return false;
  const date = value.slice(0, 10);
  return (
    new Date(`${date}T00:00:00Z`).toISOString().slice(0, 10) === date &&
    Number(value.slice(11, 13)) < 24 &&
    Number(value.slice(14, 16)) < 60 &&
    Number(value.slice(17, 19)) < 60
  );
}

function materialAttempt(value: unknown): boolean {
  const item = object(value);
  if (
    !item ||
    !isUuid(item.run_id) ||
    !["queued", "evidence", "semantics", "review", "publishing", "completed"].includes(
      String(item.progress_stage),
    ) ||
    !Number.isInteger(item.completed_pages) ||
    Number(item.completed_pages) < 0 ||
    !(
      item.total_pages === null ||
      (Number.isInteger(item.total_pages) && Number(item.total_pages) > 0)
    ) ||
    !((item.completed_units == null && item.total_units == null) ||
      (Number.isSafeInteger(item.completed_units) && Number.isSafeInteger(item.total_units) &&
       Number(item.completed_units) >= 0 && Number(item.completed_units) <= Number(item.total_units))) ||
    !(item.cancel_requested_at === null || timestamp(item.cancel_requested_at)) ||
    !timestamp(item.created_at)
  )
    return false;
  if (item.status === "pending")
    return (
      item.progress_stage === "queued" &&
      item.error_code === null &&
      item.cancel_requested_at === null
    );
  if (item.status === "running")
    return (
      item.progress_stage !== "completed" &&
      item.error_code === null &&
      (item.cancel_requested_at === null ||
        ["queued", "evidence", "semantics", "review", "publishing"].includes(String(item.progress_stage)))
    );
  if (item.status === "cancelled")
    return (
      item.progress_stage !== "completed" &&
      item.cancel_requested_at !== null &&
      item.error_code === null
    );
  if (item.status === "failed")
    return (
      item.progress_stage !== "completed" &&
      item.cancel_requested_at === null &&
      typeof item.error_code === "string" &&
      /^[A-Z][A-Z0-9_]{0,99}$/.test(item.error_code)
    );
  return (
    ["succeeded", "partial"].includes(String(item.status)) &&
    item.progress_stage === "completed" &&
    item.cancel_requested_at === null &&
    item.error_code === null &&
    item.total_pages !== null &&
    item.completed_pages === item.total_pages
  );
}

export function materialDiscard(value: unknown): value is MaterialDiscardView {
  const item = object(value);
  return (
    !!item &&
    Object.keys(item).length === 3 &&
    item.schema === "material-discard/v1" &&
    isUuid(item.material_id) &&
    (item.state === "removing" || item.state === "removed")
  );
}

export function materialRun(value: unknown): value is MaterialProcessingRunView {
  const item = object(value);
  if (!item || item.schema !== "material-processing-run/v1" || !materialAttempt(item)) return false;
  if (item.base_revision !== undefined && !revision(item.base_revision, "knowledge-structure"))
    return false;
  if (item.analysis_saved !== undefined && typeof item.analysis_saved !== "boolean") return false;
  if (item.source_names !== undefined && !strings(item.source_names)) return false;
  if (!isUuid(item.material_id) || !isUuid(item.source_artifact_id)) return false;
  if (!timestamp(item.updated_at) || !(item.completed_at === null || timestamp(item.completed_at)))
    return false;
  if (item.status === "succeeded" || item.status === "partial") {
    const binding = object(item.output_binding);
    return (
      !!binding &&
      binding.schema === "material-run-output-binding/v1" &&
      revision(binding.knowledge_structure_revision, "knowledge-structure") &&
      Number.isInteger(binding.page_count) &&
      binding.page_count === item.total_pages &&
      item.completed_at !== null
    );
  }
  return (
    item.output_binding === null &&
    (["pending", "running"].includes(String(item.status))
      ? item.completed_at === null
      : item.completed_at !== null)
  );
}

function sourceView(value: unknown): value is SourceView {
  const item = object(value);
  return (
    !!item &&
    [item.source_id, item.normalization_id, item.original_artifact_id].every(isUuid) &&
    (item.included === undefined || typeof item.included === "boolean") &&
    typeof item.original_name === "string" &&
    typeof item.media_type === "string" &&
    ["pending", "running", "ready", "failed"].includes(String(item.status)) &&
    (item.normalized_artifact_id === null || isUuid(item.normalized_artifact_id)) &&
    (item.page_count === null ||
      (Number.isInteger(item.page_count) && Number(item.page_count) > 0)) &&
    (item.error_code === null || typeof item.error_code === "string")
  );
}

export function sourceList(value: unknown): value is SourceListView {
  const item = object(value);
  return (
    !!item &&
    item.schema === "material-sources/v1" &&
    isUuid(item.material_id) &&
    (item.discard_requested === undefined || typeof item.discard_requested === "boolean") &&
    Array.isArray(item.sources) &&
    item.sources.every(sourceView)
  );
}

export function capabilities(value: unknown): value is SourceCapabilities {
  const item = object(value);
  return (
    !!item &&
    item.schema === "source-capabilities/v1" &&
    Array.isArray(item.formats) &&
    item.formats.every((v) => {
      const format = object(v);
      return (
        !!format &&
        typeof format.extension === "string" &&
        [".pdf", ".docx", ".pptx", ".doc", ".ppt", ".txt", ".md"].includes(format.extension) &&
        typeof format.media_type === "string" &&
        Number.isInteger(format.max_bytes) &&
        Number(format.max_bytes) > 0
      );
    })
  );
}

export function evidenceSource(value: unknown): value is EvidenceSourceView {
  const item = object(value);
  return (
    !!item &&
    item.schema === "evidence-source/v1" &&
    ["pdf", "docx", "pptx", "doc", "ppt", "txt", "md"].includes(String(item.format)) &&
    typeof item.original_name === "string" &&
    typeof item.label === "string" &&
    ["exact", "ambiguous", "unavailable"].includes(String(item.accuracy)) &&
    Number.isInteger(item.normalized_page) &&
    Number(item.normalized_page) > 0 &&
    Array.isArray(item.origin_locators) &&
    typeof item.original_url === "string" &&
    /^\/v1\/artifacts\/[0-9a-f-]+\/download$/.test(item.original_url) &&
    typeof item.preview_url === "string" &&
    /^\/v1\/artifacts\/[0-9a-f-]+#page=[1-9]\d*$/.test(item.preview_url)
  );
}

export function libraryItem(value: unknown): value is MaterialLibraryItem {
  const item = object(value);
  if (
    item &&
    ((item.head_revision !== undefined &&
      item.head_revision !== null &&
      !revision(item.head_revision, "knowledge-structure")) ||
      (item.source_count !== undefined &&
        (!Number.isInteger(item.source_count) || Number(item.source_count) < 0)))
  )
    return false;
  if (
    !item ||
    item.schema !== "material-library-item/v1" ||
    !isUuid(item.material_id) ||
    !(isUuid(item.source_artifact_id) || item.source_artifact_id === null) ||
    (item.source !== undefined && !sourceView(item.source)) ||
    typeof item.display_name !== "string" ||
    !item.display_name.trim() ||
    !Number.isInteger(item.size_bytes) ||
    Number(item.size_bytes) < 0 ||
    typeof item.created_at !== "string" ||
    !Number.isFinite(Date.parse(item.created_at)) ||
    !Array.isArray(item.available_structures) ||
    !Array.isArray(item.study_sessions)
  )
    return false;
  if (
    !item.study_sessions.every((value) => {
      const study = object(value);
      return (
        !!study &&
        isUuid(study.study_session_id) &&
        revision(study.knowledge_structure_revision, "knowledge-structure") &&
        isUuid(study.run_id) &&
        ["active", "no_safe", "completed"].includes(String(study.status)) &&
        typeof study.started_at === "string" &&
        (study.current_concept_id === null || revision(study.current_concept_id, "concept"))
      );
    })
  )
    return false;
  if (item.latest_attempt !== null && !materialAttempt(item.latest_attempt)) return false;
  return item.available_structures.every((value) => {
    const link = object(value);
    return (
      !!link &&
      isUuid(link.run_id) &&
      (link.base_revision === undefined || revision(link.base_revision, "knowledge-structure")) &&
      revision(link.knowledge_structure_revision, "knowledge-structure") &&
      ["succeeded", "partial"].includes(String(link.status)) &&
      typeof link.created_at === "string" &&
      Number.isFinite(Date.parse(link.created_at))
    );
  });
}

export function library(value: unknown): value is MaterialLibraryView {
  const item = object(value);
  return (
    !!item &&
    item.schema === "material-library/v1" &&
    Array.isArray(item.materials) &&
    item.materials.every(libraryItem)
  );
}

function locator(value: unknown): boolean {
  const item = object(value);
  return (
    !!item &&
    Number.isInteger(item.page) &&
    Number(item.page) >= 1 &&
    revision(item.block_id, "block") &&
    Array.isArray(item.region) &&
    item.region.length === 4 &&
    item.region.every((number) => typeof number === "number" && Number.isFinite(number))
  );
}

function conceptCard(value: unknown): value is ConceptCard {
  const item = object(value);
  return !!item && revision(item.concept_id, "concept") && typeof item.label === "string"
    && Array.isArray(item.claims) && item.claims.every((value) => {
      const claim = object(value);
      return !!claim && revision(claim.claim_id, "claim") && typeof claim.text === "string"
        && Array.isArray(claim.evidence) && claim.evidence.every((value) => {
          const evidence = object(value);
          return !!evidence && revision(evidence.evidence_id, "evidence")
            && Number.isInteger(evidence.page) && Number(evidence.page) > 0
            && (evidence.source_id === undefined || isUuid(evidence.source_id))
            && (evidence.source_name === undefined || typeof evidence.source_name === "string")
            && (evidence.normalized_page === undefined ||
              (Number.isInteger(evidence.normalized_page) && Number(evidence.normalized_page) > 0))
            && ["native_text", "unlimited_ocr"].includes(String(evidence.source))
            && typeof evidence.quote === "string" && locator(evidence.source_locator);
        });
    });
}

export function cardSetSummary(value: unknown): value is CardSetSummary {
  const item = object(value);
  return !!item && isUuid(item.card_set_id) && isUuid(item.material_id)
    && Number.isInteger(item.version) && Number(item.version) > 0
    && revision(item.knowledge_structure_revision, "knowledge-structure")
    && typeof item.name === "string" && item.name.trim().length > 0
    && typeof item.material_name === "string" && timestamp(item.created_at)
    && Number.isInteger(item.card_count) && Number(item.card_count) > 0
    && typeof item.is_current_revision === "boolean";
}

export function cardSetList(value: unknown): value is CardSetListView {
  const item = object(value);
  return !!item && item.schema === "card-set-list/v1"
    && Array.isArray(item.card_sets) && item.card_sets.every(cardSetSummary);
}

export function cardSet(value: unknown): value is CardSetView {
  const item = object(value);
  if (!item || !cardSetSummary(value) || item.schema !== "card-set/v1") return false;
  const status = object(item.status);
  return item.source_resolver === `/v1/materials/${item.material_id}/knowledge-structures/${item.knowledge_structure_revision}/evidence`
    && !!status && ["accepted", "needs_review"].includes(String(status.quality))
    && ["succeeded", "partial"].includes(String(status.processing))
    && ["retain", "review"].includes(String(status.decision)) && strings(status.reason_codes)
    && Array.isArray(item.excluded_pages) && item.excluded_pages.every((value) => {
      const page = object(value);
      return !!page && Number.isInteger(page.page) && Number(page.page) > 0
        && typeof page.reason_code === "string";
    })
    && Array.isArray(item.cards) && item.cards.length === item.card_count
    && item.cards.every(conceptCard)
    && item.cards.every((card) => card.claims.length > 0 && card.claims.every((claim) => claim.evidence.length > 0))
    && new Set(item.cards.map((card) => card.concept_id)).size === item.cards.length;
}

export function cardSetDeleted(value: unknown): value is CardSetDeletedView {
  const item = object(value);
  return !!item && item.schema === "card-set-deleted/v1" && isUuid(item.card_set_id);
}

export function knowledgeStructure(value: unknown): value is KnowledgeStructureView {
  const item = object(value);
  if (
    !item ||
    item.schema !== "knowledge-structure-view/v1" ||
    !revision(item.knowledge_structure_revision, "knowledge-structure")
  )
    return false;
  if (
    typeof item.source_resolver !== "string" ||
    !item.source_resolver.startsWith("/v1/materials/")
  )
    return false;
  if (
    !Array.isArray(item.concepts) ||
    !Array.isArray(item.relations) ||
    !Array.isArray(item.initial_learning_path)
  )
    return false;
  const concepts = item.concepts as unknown[];
  const conceptIds: string[] = [];
  for (const value of concepts) {
    if (!conceptCard(value)) return false;
    conceptIds.push(value.concept_id);
  }
  if (conceptIds.length !== new Set(conceptIds).size) return false;
  const known = new Set(conceptIds);
  const relationTypes = new Set(["prerequisite", "part_of", "application", "example", "contrast"]);
  if (
    !(item.relations as unknown[]).every((value) => {
      const relation = object(value);
      return (
        !!relation &&
        revision(relation.relation_id, "relation") &&
        known.has(String(relation.source_concept_id)) &&
        known.has(String(relation.target_concept_id)) &&
        relation.source_concept_id !== relation.target_concept_id &&
        relationTypes.has(String(relation.type)) &&
        typeof relation.learner_reason === "string"
      );
    })
  )
    return false;
  const pathIds = (item.initial_learning_path as unknown[]).map(
    (value) => object(value)?.concept_id,
  );
  return (
    pathIds.length === conceptIds.length &&
    pathIds.every((id) => typeof id === "string" && known.has(id)) &&
    new Set(pathIds).size === pathIds.length
  );
}

export function studySession(value: unknown): value is StudySessionView {
  const item = object(value);
  return (
    !!item &&
    item.schema === "study-session/v1" &&
    isUuid(item.study_session_id) &&
    isUuid(item.material_id) &&
    revision(item.knowledge_structure_revision, "knowledge-structure") &&
    (item.current_concept_id === null || revision(item.current_concept_id, "concept")) &&
    strings(item.deferred_concept_ids) &&
    strings(item.no_safe_claim_ids) &&
    Number.isInteger(item.event_watermark) &&
    ["active", "no_safe", "completed"].includes(String(item.status))
  );
}

function assessment(value: unknown): value is AssessmentView {
  const item = object(value);
  if (
    !item ||
    item.schema !== "single-choice-assessment/v1" ||
    !revision(item.assessment_revision, "assessment") ||
    !isUuid(item.study_session_id) ||
    !revision(item.knowledge_structure_revision, "knowledge-structure") ||
    !revision(item.question_id, "question") ||
    !revision(item.target_concept_id, "concept") ||
    !revision(item.target_claim_id, "claim") ||
    typeof item.prompt !== "string" ||
    !strings(item.source_evidence_ids) ||
    !Array.isArray(item.options) ||
    item.options.length !== 4
  )
    return false;
  const options = item.options as unknown[];
  const ids = options.map((value) => object(value)?.option_id);
  return (
    options.every((value) => {
      const option = object(value);
      return (
        !!option &&
        revision(option.option_id, "option") &&
        typeof option.text === "string" &&
        option.text.length > 0
      );
    }) &&
    new Set(ids).size === 4 &&
    !Object.hasOwn(item, "correct_option_id")
  );
}

function feedback(value: unknown): value is AnswerFeedbackView {
  const item = object(value);
  return (
    !!item &&
    item.schema === "answer-feedback/v1" &&
    isUuid(item.answer_event_id) &&
    isUuid(item.study_session_id) &&
    revision(item.assessment_revision, "assessment") &&
    revision(item.question_id, "question") &&
    revision(item.selected_option_id, "option") &&
    typeof item.is_correct === "boolean" &&
    typeof item.rationale === "string" &&
    strings(item.source_evidence_ids) &&
    Number.isInteger(item.event_number)
  );
}

export function progress(value: unknown): value is LearnerProgressView {
  const item = object(value);
  if (
    !item ||
    item.schema !== "learner-progress/v1" ||
    !isUuid(item.study_session_id) ||
    !revision(item.knowledge_structure_revision, "knowledge-structure") ||
    !Number.isInteger(item.event_watermark) ||
    !(item.current_concept_id === null || revision(item.current_concept_id, "concept")) ||
    !Array.isArray(item.assessment_cycles) ||
    !item.assessment_cycles.every(cycleSummary) ||
    !Array.isArray(item.concept_states) ||
    !Array.isArray(item.weaknesses) ||
    !object(item.next_action) ||
    !revision(item.guidance_revision, "learner-guidance")
  )
    return false;
  return item.concept_states.every((value) => {
    const state = object(value);
    return (
      !!state &&
      revision(state.concept_id, "concept") &&
      typeof state.label === "string" &&
      ["not_started", "in_progress", "needs_review", "completed"].includes(String(state.status))
    );
  });
}

export function assessmentPlan(value: unknown): value is AssessmentPlanView {
  const item = object(value);
  return (
    !!item &&
    item.schema === "assessment-plan/v1" &&
    isUuid(item.study_session_id) &&
    revision(item.knowledge_structure_revision, "knowledge-structure") &&
    revision(item.concept_id, "concept") &&
    item.policy === "single-concept-grounded-points/v1" &&
    Number.isSafeInteger(item.point_count) &&
    Number(item.point_count) >= 0 &&
    Number.isSafeInteger(item.requested_count) &&
    Array.isArray(item.targets) &&
    item.targets.length === item.requested_count &&
    item.targets.every((value) => {
      const row = object(value);
      return (
        !!row &&
        revision(row.claim_id, "claim") &&
        strings(row.covered_claim_ids) &&
        row.covered_claim_ids.every((id) => revision(id, "claim")) &&
        row.reason === "distinct_grounded_point"
      );
    }) &&
    Array.isArray(item.excluded) &&
    item.excluded.every((value) => {
      const row = object(value);
      return !!row && revision(row.claim_id, "claim") && row.reason === "no_content_evidence";
    })
  );
}

function cycleSummary(value: unknown): value is AssessmentCycleSummary & Json {
  const item = object(value);
  const source = object(item?.inherited_from);
  return (
    !!item &&
    (item.inherited_from == null ||
      (!!source && isUuid(source.study_session_id) && isUuid(source.run_id) &&
        revision(source.knowledge_structure_revision, "knowledge-structure") &&
        item.outcome === "passed" && item.active_set_id === null)) &&
    isUuid(item.diagnostic_set_id) &&
    revision(item.concept_id, "concept") &&
    Number.isSafeInteger(item.set_version) &&
    Number(item.set_version) > 0 &&
    ["in_progress", "needs_review", "passed", "incomplete"].includes(String(item.outcome)) &&
    (item.active_set_id === null || isUuid(item.active_set_id)) &&
    [
      "passed_count",
      "remediation_passed_count",
      "pending_count",
      "unanswered_count",
      "unavailable_count",
    ].every((key) => Number.isSafeInteger(item[key]) && Number(item[key]) >= 0) &&
    Number(item.remediation_passed_count) <= Number(item.passed_count)
  );
}

function assessmentSetSummary(value: unknown): value is AssessmentSetSummary & Json {
  const item = object(value);
  return (
    !!item &&
    ["diagnostic", "remediation"].includes(String(item.kind)) &&
    (item.kind === "diagnostic"
      ? item.diagnostic_set_id === null
      : isUuid(item.diagnostic_set_id) && item.diagnostic_set_id !== item.set_id) &&
    isUuid(item.set_id) &&
    revision(item.target_concept_id, "concept") &&
    [
      "preparing",
      "partial_ready",
      "failed",
      "ready",
      "in_progress",
      "completed",
      "cancelled",
    ].includes(String(item.status)) &&
    ["set_version", "requested_count", "published_count", "answered_count", "passed_count"].every(
      (key) => Number.isSafeInteger(item[key]) && Number(item[key]) >= 0,
    ) &&
    Number(item.set_version) > 0 &&
    Number(item.passed_count) <= Number(item.answered_count) &&
    Number(item.answered_count) <= Number(item.published_count) &&
    Number(item.published_count) <= Number(item.requested_count) &&
    strings(item.assessment_revisions) &&
    item.assessment_revisions.length === item.published_count &&
    item.assessment_revisions.every((id) => revision(id, "assessment")) &&
    new Set(item.assessment_revisions).size === item.assessment_revisions.length &&
    timestamp(item.created_at) &&
    (item.completed_at === null || timestamp(item.completed_at))
  );
}

export function assessmentSetList(value: unknown): value is AssessmentSetListView {
  const item = object(value);
  if (
    !item ||
    item.schema !== "assessment-set-list/v1" ||
    !isUuid(item.study_session_id) ||
    !revision(item.knowledge_structure_revision, "knowledge-structure") ||
    !Array.isArray(item.sets) ||
    !item.sets.every(assessmentSetSummary)
  )
    return false;
  const active = item.sets.filter((group) =>
    ["preparing", "partial_ready", "ready", "in_progress"].includes(group.status),
  );
  return (
    new Set(item.sets.map((group) => group.set_id)).size === item.sets.length &&
    strings(item.active_set_ids) &&
    item.active_set_ids.length === active.length &&
    new Set(item.active_set_ids).size === active.length &&
    new Set(active.map((group) => group.target_concept_id)).size === active.length &&
    active.every((group) => (item.active_set_ids as string[]).includes(group.set_id))
  );
}

export function assessmentSet(value: unknown): value is AssessmentSetView {
  if (!assessmentSetSummary(value)) return false;
  const item = value;
  if (
    item.schema !== "assessment-set/v1" ||
    !isUuid(item.study_session_id) ||
    !isUuid(item.material_id) ||
    !revision(item.knowledge_structure_revision, "knowledge-structure") ||
    item.selection_policy !==
      (item.kind === "diagnostic"
        ? "single-concept-grounded-points/v1"
        : "needs-review-points/v1") ||
    !["point_count", "excluded_count", "verified_count"].every(
      (key) => Number.isSafeInteger(item[key]) && Number(item[key]) >= 0,
    ) ||
    !["can_retry", "can_publish_partial", "can_complete"].every(
      (key) => typeof item[key] === "boolean",
    ) ||
    !Array.isArray(item.items) ||
    item.items.length !== item.requested_count ||
    Number(item.verified_count) > item.requested_count ||
    Number(item.point_count) < item.requested_count + Number(item.excluded_count) ||
    ["runtime_lock_document", "target_plan", "action_receipts"].some((key) =>
      Object.hasOwn(item, key),
    )
  )
    return false;
  const cycle = item.cycle;
  if (
    !cycleSummary(cycle) ||
    cycle.concept_id !== item.target_concept_id ||
    cycle.diagnostic_set_id !==
      (item.kind === "diagnostic" ? item.set_id : item.diagnostic_set_id) ||
    typeof cycle.can_create_remediation !== "boolean" ||
    !Array.isArray(cycle.points)
  )
    return false;
  const results = new Map<string, number>();
  const cycleClaims = new Set<string>();
  for (const point of cycle.points) {
    const row = object(point);
    if (
      !row ||
      !revision(row.claim_id, "claim") ||
      cycleClaims.has(String(row.claim_id)) ||
      ![
        "unavailable",
        "unanswered",
        "diagnostic_pass",
        "needs_review",
        "remediation_pass",
      ].includes(String(row.result)) ||
      !["latest_answer_event_id", "latest_set_id"].every(
        (key) => row[key] === null || isUuid(row[key]),
      )
    )
      return false;
    cycleClaims.add(String(row.claim_id));
    results.set(String(row.result), (results.get(String(row.result)) ?? 0) + 1);
  }
  const count = (name: string) => results.get(name) ?? 0;
  if (
    cycle.passed_count !== count("diagnostic_pass") + count("remediation_pass") ||
    cycle.remediation_passed_count !== count("remediation_pass") ||
    cycle.pending_count !== count("needs_review") ||
    cycle.unanswered_count !== count("unanswered") ||
    cycle.unavailable_count < count("unavailable")
  )
    return false;
  let published = 0,
    answered = 0,
    passed = 0,
    verified = 0;
  const claims = new Set<string>(),
    revisions = new Set<string>();
  const valid = item.items.every((value, index) => {
    const row = object(value);
    if (
      !row ||
      row.ordinal !== index + 1 ||
      !revision(row.target_claim_id, "claim") ||
      !["pending", "generating", "verified", "published", "failed", "omitted"].includes(
        String(row.state),
      ) ||
      !Number.isSafeInteger(row.attempts) ||
      Number(row.attempts) < 0 ||
      Number(row.attempts) > 2 ||
      typeof row.can_submit !== "boolean" ||
      !(row.failure_reason === null || typeof row.failure_reason === "string") ||
      [
        "prepared_document",
        "private_answer_document",
        "generation_provenance",
        "correct_option_id",
      ].some((key) => Object.hasOwn(row, key))
    )
      return false;
    if (claims.has(String(row.target_claim_id))) return false;
    claims.add(String(row.target_claim_id));
    if (row.state === "verified" || row.state === "published") verified += 1;
    if (row.assessment === null)
      return (
        row.state !== "published" &&
        row.feedback === null &&
        row.created_at === null &&
        !row.can_submit
      );
    if (
      row.state !== "published" ||
      !assessment(row.assessment) ||
      !timestamp(row.created_at) ||
      row.assessment.study_session_id !== item.study_session_id ||
      row.assessment.knowledge_structure_revision !== item.knowledge_structure_revision ||
      row.assessment.target_concept_id !== item.target_concept_id ||
      row.assessment.target_claim_id !== row.target_claim_id ||
      !item.assessment_revisions.includes(row.assessment.assessment_revision)
    )
      return false;
    if (revisions.has(row.assessment.assessment_revision)) return false;
    revisions.add(row.assessment.assessment_revision);
    published += 1;
    if (row.feedback === null)
      return !row.can_submit || ["ready", "in_progress"].includes(item.status);
    if (
      !feedback(row.feedback) ||
      row.can_submit ||
      row.feedback.assessment_revision !== row.assessment.assessment_revision ||
      row.feedback.study_session_id !== item.study_session_id ||
      row.feedback.question_id !== row.assessment.question_id
    )
      return false;
    answered += 1;
    if (row.feedback.is_correct) passed += 1;
    return true;
  });
  return (
    valid &&
    verified === item.verified_count &&
    published === item.published_count &&
    answered === item.answered_count &&
    passed === item.passed_count
  );
}

export function studyResume(value: unknown): value is StudyResumeView {
  const item = object(value);
  if (
    !item ||
    item.schema !== "study-resume/v1" ||
    !studySession(item.session) ||
    !knowledgeStructure(item.knowledge_structure) ||
    !progress(item.progress) ||
    !isUuid(item.run_id) ||
    !isUuid(item.source_artifact_id) ||
    !Array.isArray(item.assessment_sets) ||
    !item.assessment_sets.every(assessmentSetSummary) ||
    !(
      item.selected_set_id === null ||
      item.assessment_sets.some((group) => group.set_id === item.selected_set_id)
    )
  )
    return false;
  const session = item.session;
  return (
    item.progress.study_session_id === session.study_session_id &&
    item.progress.knowledge_structure_revision === session.knowledge_structure_revision &&
    item.knowledge_structure.knowledge_structure_revision ===
      session.knowledge_structure_revision &&
    item.progress.event_watermark === session.event_watermark
  );
}

export function apiError(value: unknown): value is ApiErrorView {
  const item = object(value);
  return (
    !!item &&
    item.schema === "api-error/v1" &&
    isUuid(item.request_id) &&
    typeof item.reason_code === "string" &&
    typeof item.retryable === "boolean" &&
    item.message === "Request could not be completed."
  );
}

export function identity(value: unknown): value is LearnerIdentity {
  const item = object(value);
  return !!item && item.schema === "learner-identity/v1" && isUuid(item.learner_id);
}

export function materialDraft(
  value: unknown,
): value is { schema: "material-draft/v1"; material_id: string } {
  const item = object(value);
  return !!item && item.schema === "material-draft/v1" && isUuid(item.material_id);
}

export function podcastSummary(value: unknown): value is PodcastSummary {
  const item = object(value);
  return !!item && isUuid(item.podcast_id) && isUuid(item.material_id)
    && revision(item.knowledge_structure_revision, "knowledge-structure")
    && typeof item.name === "string" && !!item.name.trim() && typeof item.material_name === "string"
    && ["solo", "dialogue"].includes(String(item.delivery))
    && strings(item.concept_ids) && item.concept_ids.length > 0
    && item.concept_ids.every((id) => revision(id, "concept"))
    && new Set(item.concept_ids).size === item.concept_ids.length
    && ["pending", "running", "ready", "failed", "cancelled"].includes(String(item.status))
    && (item.error_code === null || typeof item.error_code === "string")
    && Number.isInteger(item.version) && Number(item.version) > 0 && timestamp(item.created_at)
    && Number.isInteger(item.episode_count) && Number(item.episode_count) > 0
    && Number.isInteger(item.completed_episodes) && Number(item.completed_episodes) >= 0
    && Number(item.completed_episodes) <= Number(item.episode_count)
    && typeof item.is_current_revision === "boolean";
}

export function podcastList(value: unknown): value is PodcastListView {
  const item = object(value);
  return !!item && item.schema === "podcast-list/v1" && Array.isArray(item.podcasts)
    && item.podcasts.every(podcastSummary);
}

export function podcast(value: unknown): value is PodcastView {
  const item = object(value);
  if (!item || !podcastSummary(value) || item.schema !== "podcast/v1") return false;
  const status = object(item.source_status);
  if (item.source_resolver !== `/v1/materials/${item.material_id}/knowledge-structures/${item.knowledge_structure_revision}/evidence`
    || !status || !["accepted", "needs_review"].includes(String(status.quality))
    || !["succeeded", "partial"].includes(String(status.processing))
    || !["retain", "review"].includes(String(status.decision)) || !strings(status.reason_codes)
    || !Array.isArray(item.excluded_pages) || !item.excluded_pages.every((v) => {
      const p = object(v); return !!p && Number.isInteger(p.page) && Number(p.page) > 0 && typeof p.reason_code === "string";
    }) || !Array.isArray(item.episodes) || item.episodes.length !== item.episode_count) return false;
  const covered = new Set<string>();
  const valid = item.episodes.every((v) => {
    const episode = object(v);
    if (!episode || !Array.isArray(episode.claims) || !episode.claims.length
      || !["solo", "dialogue"].includes(String(episode.delivery))
      || episode.delivery !== item.delivery) return false;
    if (!episode.claims.every((v) => {
      const c = object(v);
      if (!c || !conceptCard({ concept_id: c.concept_id, label: c.label, claims: [c] })
        || !Array.isArray(c.evidence) || !c.evidence.length || !(item.concept_ids as string[]).includes(String(c.concept_id))) return false;
      covered.add(String(c.concept_id)); return true;
    })) return false;
    if (episode.script !== null) {
      const script = object(episode.script);
      if (!script || typeof script.provider !== "string" || !script.provider || !Array.isArray(script.segments)) return false;
      if (script.schema === 'podcast-script/v2') {
        const review=object(script.review), covered=new Set<number>();let totalText=0;
        if (!review || !['correctness','teaching_quality'].every(k=>{const v=object(review[k]);return v?.passed===true&&typeof v.reason==='string';})
          || script.segments.length<1 || script.segments.length>12) return false;
        if (!script.segments.every((value,i)=>{
          const beat=object(value);
          if (!beat || beat.beat_id!==`beat-${i}` || typeof beat.title!=='string' || !beat.title || !Array.isArray(beat.turns) || !beat.turns.length || beat.turns.length>12) return false;
          return beat.turns.every(value=>{
            const turn=object(value);
            if (!turn || !['host',...(episode.delivery==='dialogue'?['guest']:[])].includes(String(turn.speaker))
              || typeof turn.text!=='string' || !turn.text.trim() || !Array.isArray(turn.parts) || !turn.parts.length || turn.parts.length>24) return false;
            let text='';
            for (const value of turn.parts) {
              const part=object(value);
              if (!part || typeof part.text!=='string' || !part.text || !Array.isArray(part.source_refs)) return false;
              text+=part.text;
              const seen=new Set<number>();
              for (const value of part.source_refs) {
                const ref=object(value), index=ref?.source_index;
                if (!Number.isInteger(index) || typeof index!=='number' || index<0 || index>=(episode.claims as unknown[]).length || seen.has(index)) return false;
                const claim=object((episode.claims as unknown[])[index]);
                if (!ref || !strings(ref.evidence_ids) || !ref.evidence_ids.length || new Set(ref.evidence_ids).size!==ref.evidence_ids.length
                  || !ref.evidence_ids.every(id=>(claim?.evidence as unknown[]).some(e=>object(e)?.evidence_id===id))) return false;
                seen.add(index);covered.add(index);
              }
            }
            totalText+=Array.from(text).length;
            return text===turn.text&&Array.from(text).length<=1600;
          });
        }) || covered.size!==episode.claims.length || totalText>9600) return false;
      } else if (script.schema !== undefined || script.segments.length !== episode.claims.length
        || !script.segments.every((v,i)=>{
          const s=object(v), c=object((episode.claims as unknown[])[i]);
          return !!s && s.claim_id===c?.claim_id && Array.isArray(s.turns) && s.turns.length>=1 && s.turns.length<=(episode.delivery==='dialogue'?8:3)
            && s.turns.every(v=>{const t=object(v);return !!t && (episode.delivery==='dialogue'?['host','guest']:['host']).includes(String(t.speaker)) && typeof t.text==='string' && !!t.text.trim();});
        })) return false;
      const speakers = new Set(script.segments.flatMap(v => {
        const turns = object(v)?.turns;
        return Array.isArray(turns) ? turns.map(t => object(t)?.speaker) : [];
      }));
      if (episode.delivery === "dialogue" && speakers.size !== 2) return false;
    }
    if (episode.audio !== null) {
      const a = object(episode.audio);
      if (episode.script === null || !a || !isUuid(a.artifact_id) || typeof a.sha256 !== "string"
        || !/^[0-9a-f]{64}$/.test(a.sha256) || typeof a.duration_seconds !== "number"
        || !Number.isFinite(a.duration_seconds) || a.duration_seconds <= 0 || typeof a.provider !== "string") return false;
    }
    return true;
  });
  return valid && covered.size === (item.concept_ids as string[]).length
    && item.episodes.filter((v) => object(v)?.audio !== null).length === item.completed_episodes
    && (item.status !== "ready" || item.completed_episodes === item.episode_count);
}

export function podcastDeleted(value: unknown): value is PodcastDeletedView {
  const item = object(value);
  return !!item && item.schema === "podcast-deleted/v1" && isUuid(item.podcast_id);
}
