import type {
  MaterialNotesView,
  LearningGoalView, LearningGoalsView,
  StartingLevel, StartingLevelView, StudyStartView,
  LearningDiagnosisView, ConceptDifficultyView,
  KnowledgeOutlineView, StudyNavigationView, KnowledgeConceptsView, KnowledgeRegionsView, KnowledgeSearchView, LearningActivityView, LearningRhythmView, MapProgressView, ProgressSummaryView, PodcastProgressView, StudyWorkspaceView,
  PodcastSummary, PodcastView, PodcastListView, PodcastDeletedView,
  AnswerFeedbackView,
  CardSetSummary,
  CardSetView, CardStudyView, CardBatch,
  CardGeneration,
  CardSetListView,
  CardSetDeletedView,
  CardReviewsView,
  CardReviewView,
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
  LearnerProfile,
  AccountSessions,
  LibrarySearchView,
  LearnerProgressView,
  MaterialDiscardView,
  MaterialLibraryItem,
  MaterialLibraryView,
  MaterialProcessingRunView,
  SourceCapabilities,
  SourceUploadProgress,
  SourceListView,
  SourceView,
  StudyResumeView,
  StudySessionView,
  VersionMovePreview,
  VersionMoveResult,
} from "./contracts";

type Json = Record<string, unknown>;

export function startingLevel(value: unknown): value is StartingLevel {
  return ["beginner", "some_knowledge", "familiar", "gaps"].includes(String(value));
}

export function startingLevelView(value: unknown): value is StartingLevelView {
  const item = object(value);
  return !!item && item.schema === "starting-level/v1" && isUuid(item.material_id)
    && (item.starting_level === null || startingLevel(item.starting_level));
}

export function studyStart(value: unknown): value is StudyStartView {
  const item = object(value);
  return !!item && item.schema === "study-start/v1" && studySession(item.session)
    && startingLevel(item.starting_level) && (item.selected_set_id === null || isUuid(item.selected_set_id));
}

function goalConcepts(value: unknown): boolean {
  return Array.isArray(value) && value.every(value => {
    const concept = object(value);
    return !!concept && revision(concept.concept_id, "concept") && typeof concept.label === "string" && count(concept.remaining_points);
  });
}

function goalStage(value: unknown): boolean {
  const stage = object(value);
  return !!stage && typeof stage.date === "string" && isoDate.test(stage.date)
    && [stage.quota_points, stage.completed_points, stage.remaining_points, stage.scheduled_points].every(count)
    && typeof stage.done === "boolean" && goalConcepts(stage.concepts);
}

function goalMetrics(value: unknown): boolean {
  const goal = object(value);
  return !!goal && typeof goal.target_date === "string" && isoDate.test(goal.target_date)
    && typeof goal.target_percent === "number" && [60, 80, 100].includes(goal.target_percent)
    && [goal.assessable_points, goal.completed_points, goal.target_points, goal.remaining_points, goal.days_remaining, goal.overdue_days, goal.behind_points].every(count)
    && goal.daily_limit === 20 && typeof goal.achieved === "boolean"
    && typeof goal.quota_over_limit === "boolean" && typeof goal.scheduled_over_limit === "boolean"
    && goalStage(goal.today_stage)
    && (goal.assessable_points !== 0 || !goal.achieved);
}

export function learningGoal(value: unknown): value is LearningGoalView {
  const item = object(value), goal = object(item?.goal);
  return !!item && item.schema === "learning-goal/v1" && isUuid(item.material_id)
    && revision(item.knowledge_structure_revision, "knowledge-structure") && isUuid(item.run_id)
    && typeof item.today === "string" && isoDate.test(item.today)
    && (item.goal === null || (!!goal && goalMetrics(goal)
      && Array.isArray(goal.future_stages) && goal.future_stages.every(goalStage)
      && count(goal.preview_offset) && typeof goal.preview_has_more === "boolean" && goalConcepts(goal.manual_review)
      && (goal.update_notice === null || (!!object(goal.update_notice)
        && revision(object(goal.update_notice)!.knowledge_structure_revision, "knowledge-structure")
        && count(object(goal.update_notice)!.previous_points) && count(object(goal.update_notice)!.current_points)))));
}

export function learningGoals(value: unknown): value is LearningGoalsView {
  const item = object(value);
  return !!item && item.schema === "learning-goals/v1" && typeof item.today === "string" && isoDate.test(item.today)
    && Array.isArray(item.goals) && item.goals.every(value => {
      const goal = object(value);
      return !!goal && goalMetrics(goal) && isUuid(goal.material_id) && typeof goal.material_name === "string"
        && revision(goal.knowledge_structure_revision, "knowledge-structure") && isUuid(goal.run_id);
    }) && new Set(item.goals.map(goal => goal.material_id)).size === item.goals.length;
}

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
    !["queued", "evidence", "semantics", "review", "indexing", "publishing", "completed"].includes(
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
        ["queued", "evidence", "semantics", "review", "indexing", "publishing"].includes(String(item.progress_stage)))
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
  const positive = (number: unknown) => Number.isSafeInteger(number) && Number(number) > 0;
  return (
    !!item &&
    item.schema === "source-capabilities/v2" &&
    positive(item.max_request_bytes) &&
    positive(item.max_images) &&
    positive(item.max_media_seconds) &&
    typeof item.youtube === "boolean" &&
    Array.isArray(item.formats) &&
    item.formats.every((v) => {
      const format = object(v);
      return (
        !!format &&
        typeof format.extension === "string" &&
        /^\.[a-z0-9]+$/.test(format.extension) &&
        typeof format.media_type === "string" &&
        ["document", "image", "audio", "video"].includes(String(format.kind)) &&
        positive(format.max_bytes)
      );
    })
  );
}

export function sourceUploadProgress(value: unknown): value is SourceUploadProgress {
  const item = object(value);
  return (
    !!item &&
    item.schema === "source-upload-progress/v1" &&
    Number.isSafeInteger(item.received_bytes) &&
    Number(item.received_bytes) > 0 &&
    (item.source_id === null || isUuid(item.source_id))
  );
}

function playback(value: unknown): boolean {
  const item = object(value);
  if (!item) return false;
  const seconds = [item.start_seconds, item.end_seconds].every(
    (number) => typeof number === "number" && Number.isFinite(number) && number >= 0,
  );
  return (
    seconds &&
    ((item.kind === "audio" &&
      typeof item.url === "string" &&
      /^\/v1\/artifacts\/[0-9a-f-]+\/media$/.test(item.url)) ||
      (item.kind === "youtube" &&
        typeof item.video_id === "string" &&
        /^[A-Za-z0-9_-]{11}$/.test(item.video_id)))
  );
}

export function evidenceSource(value: unknown): value is EvidenceSourceView {
  const item = object(value);
  return (
    !!item &&
    item.schema === "evidence-source/v1" &&
    ["pdf", "docx", "pptx", "doc", "ppt", "txt", "md", "images", "audio", "video", "youtube"].includes(String(item.format)) &&
    typeof item.original_name === "string" &&
    typeof item.label === "string" &&
    ["exact", "ambiguous", "unavailable"].includes(String(item.accuracy)) &&
    Number.isInteger(item.normalized_page) &&
    Number(item.normalized_page) > 0 &&
    Array.isArray(item.origin_locators) &&
    typeof item.original_url === "string" &&
    /^\/v1\/artifacts\/[0-9a-f-]+\/download$/.test(item.original_url) &&
    typeof item.preview_url === "string" &&
    /^\/v1\/artifacts\/[0-9a-f-]+#page=[1-9]\d*$/.test(item.preview_url) &&
    (item.playback === undefined || playback(item.playback)) &&
    (["audio", "video", "youtube"].includes(String(item.format)) === (item.playback !== undefined))
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

function cardEvidence(value: unknown): boolean {
          const evidence = object(value);
          return !!evidence && revision(evidence.evidence_id, "evidence")
            && Number.isInteger(evidence.page) && Number(evidence.page) > 0
            && (evidence.source_id === undefined || isUuid(evidence.source_id))
            && (evidence.source_name === undefined || typeof evidence.source_name === "string")
            && (evidence.normalized_page === undefined ||
              (Number.isInteger(evidence.normalized_page) && Number(evidence.normalized_page) > 0))
            && ["native_text", "unlimited_ocr"].includes(String(evidence.source))
            && typeof evidence.quote === "string" && locator(evidence.source_locator);
}

function conceptCard(value: unknown): value is ConceptCard {
  const item = object(value);
  return !!item && revision(item.concept_id, "concept") && typeof item.label === "string"
    && Array.isArray(item.claims) && item.claims.every((value) => {
      const claim = object(value);
      return !!claim && revision(claim.claim_id, "claim") && typeof claim.text === "string"
        && Array.isArray(claim.evidence) && claim.evidence.every(cardEvidence);
    });
}

export function cardSetSummary(value: unknown): value is CardSetSummary {
  const item = object(value);
  return !!item && isUuid(item.card_set_id) && isUuid(item.material_id)
    && Number.isInteger(item.version) && Number(item.version) > 0
    && revision(item.knowledge_structure_revision, "knowledge-structure")
    && typeof item.name === "string" && item.name.trim().length > 0
    && typeof item.material_name === "string" && timestamp(item.created_at)
    && (item.card_count === null || Number.isInteger(item.card_count) && Number(item.card_count) > 0)
    && cardGeneration(item.generation)
    && typeof item.is_current_revision === "boolean";
}

const unmappedReasons = ["split", "ambiguous", "not_found", "merged_unfinished"];

function versionMoveUnmapped(value: unknown): boolean {
  const item = object(value);
  return !!item && revision(item.concept_id, "concept") && typeof item.label === "string"
    && unmappedReasons.includes(String(item.reason));
}

export function versionMovePreview(value: unknown): value is VersionMovePreview {
  const item = object(value);
  if (!item || item.schema !== "version-move-preview/v1" || !isUuid(item.material_id)
    || !(item.to_revision === null || revision(item.to_revision, "knowledge-structure"))
    || !(item.preview_digest === null || revision(item.preview_digest, "version-move"))
    || !count(item.kept_podcasts) || !Array.isArray(item.card_sets)) return false;
  const sets = item.card_sets.every((value) => {
    const set = object(value);
    return !!set && isUuid(set.card_set_id) && typeof set.name === "string"
      && revision(set.from_revision, "knowledge-structure") && typeof set.movable === "boolean"
      && [set.concept_count, set.target_count, set.carried_reviews, set.reused_designs].every(count)
      && Array.isArray(set.unmapped) && set.unmapped.every(versionMoveUnmapped)
      && Array.isArray(set.mapped) && set.mapped.every((value) => {
        const row = object(value);
        return !!row && revision(row.from_concept_id, "concept") && revision(row.to_concept_id, "concept")
          && typeof row.from_label === "string" && typeof row.to_label === "string"
          && (row.basis === "evidence" || row.basis === "label");
      });
  });
  if (!sets) return false;
  if (item.study === null) return true;
  const study = object(item.study);
  const focus = study && (study.current_concept === null ? true : object(study.current_concept));
  return !!study && isUuid(study.study_session_id) && revision(study.from_revision, "knowledge-structure")
    && (study.target_study_session_id === null || isUuid(study.target_study_session_id))
    && count(study.completed_count) && !!focus
    && (study.current_concept === null || revision((focus as Json).concept_id, "concept") && typeof (focus as Json).label === "string")
    && Array.isArray(study.not_carried) && study.not_carried.every(versionMoveUnmapped)
    && Array.isArray(study.carried) && study.carried.every((value) => {
      const row = object(value);
      return !!row && revision(row.concept_id, "concept") && typeof row.label === "string"
        && Array.isArray(row.from_labels) && row.from_labels.every((label) => typeof label === "string");
    });
}

export function versionMoveResult(value: unknown): value is VersionMoveResult {
  const item = object(value);
  return !!item && item.schema === "version-move/v1" && isUuid(item.move_id) && isUuid(item.material_id)
    && revision(item.to_revision, "knowledge-structure") && timestamp(item.created_at)
    && (item.study_session_id === null || isUuid(item.study_session_id))
    && Array.isArray(item.card_sets) && item.card_sets.every((value) => {
      const row = object(value);
      return !!row && isUuid(row.from_card_set_id) && isUuid(row.card_set_id)
        && (row.status === "pending" || row.status === "ready");
    });
}

export function cardSetList(value: unknown): value is CardSetListView {
  const item = object(value);
  return !!item && item.schema === "card-set-list/v1"
    && Array.isArray(item.card_sets) && item.card_sets.every(cardSetSummary);
}

const ratings = ["forgot", "remembered"];

export function cardReviews(value: unknown): value is CardReviewsView {
  const item = object(value);
  const count = (n: unknown) => Number.isInteger(n) && (n as number) >= 0;
  if (!item || item.schema !== "card-reviews/v2" || !count(item.forgot_count)
    || !Array.isArray(item.sets) || !Array.isArray(item.marks) || !Array.isArray(item.cards)) return false;
  const sets = item.sets.map(object);
  if (!sets.every(s => !!s && isUuid(s.card_set_id)
    && [s.card_count, s.forgot_count, s.remembered_count].every(count)
    && (s.forgot_count as number) + (s.remembered_count as number) <= (s.card_count as number))) return false;
  if (!item.marks.every(value => {
    const m = object(value);
    return !!m && isUuid(m.card_set_id) && revision(m.concept_id, "concept") && ratings.includes(String(m.rating))
      && Number.isInteger(m.review_count) && (m.review_count as number) >= 1;
  })) return false;
  const seen = new Set<string>();
  return item.cards.every(value => {
    const c = object(value);
    if (!c || !isUuid(c.card_set_id) || !isUuid(c.material_id) || typeof c.card_set_name !== "string"
      || typeof c.material_name !== "string" || !revision(c.knowledge_structure_revision, "knowledge-structure")
      || c.source_resolver !== `/v1/materials/${c.material_id}/knowledge-structures/${c.knowledge_structure_revision}/evidence`
      || !Number.isInteger(c.review_count) || (c.review_count as number) < 1
      || !conceptCard(c.card) || !c.card.claims.length) return false;
    // 同一張卡不會在清單重複出現。
    const key = `${c.card_set_id}:${c.card.concept_id}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

export function cardReview(value: unknown): value is CardReviewView {
  const item = object(value);
  return !!item && item.schema === "card-review/v2" && isUuid(item.card_set_id) && revision(item.concept_id, "concept")
    && ratings.includes(String(item.rating)) && Number.isInteger(item.review_count) && (item.review_count as number) >= 1
    && timestamp(item.reviewed_at);
}

export function cardSet(value: unknown): value is CardSetView {
  const item = object(value);
  if (!item || !cardSetSummary(value) || item.schema !== "card-set/v1" || !cardGeneration(item.generation)) return false;
  const edits = object(item.display_edits);
  if (!edits || !Object.values(edits).every(list => Array.isArray(list) && list.every(edit => {
    const entry = object(edit);
    return entry && typeof entry.source === "string" && typeof entry.text === "string";
  }))) return false;
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
    && Array.isArray(item.cards) && item.cards.length === item.generation.total
    && item.cards.every(conceptCard)
    && item.cards.every((card) => card.claims.length > 0 && card.claims.every((claim) => claim.evidence.length > 0))
    && new Set(item.cards.map((card) => card.concept_id)).size === item.cards.length;
}

function cardEdits(value: unknown): boolean {
  return Array.isArray(value) && value.every(edit => {
    const item = object(edit);
    return item && typeof item.source === "string" && typeof item.text === "string";
  });
}

function cardDesign(value: unknown, card: ConceptCard, context: CardBatch["context"]): boolean {
  if (value === undefined || value === null) return true;
  const design = object(value), provider = object(design?.provider);
  if (!design || design.schema !== "concept-card-design/v1" || !revision(design.revision, "card-design")
    || !provider || ![provider.model_id, provider.revision, provider.api_protocol].every(v => typeof v === "string" && v.length > 0)
    || !Array.isArray(design.pages) || !design.pages.length || design.pages.length > 3
    || !strings(design.details_claim_ids)) return false;
  const claims = new Map(card.claims.map(c => [c.claim_id, c]));
  const relations = new Map(context.relations.filter(r => [r.source_concept_id, r.target_concept_id].includes(card.concept_id)).map(r => [r.relation_id, r]));
  const refs = (value: Json) => {
    if (!strings(value.claim_ids) || !strings(value.relation_ids) || !strings(value.evidence_ids) || !value.evidence_ids.length
      || !value.claim_ids.every(id => claims.has(id)) || !value.relation_ids.every(id => relations.has(id))) return false;
    const evidence = new Set([...value.claim_ids.flatMap(id => claims.get(id)!.evidence.map(e => e.evidence_id)), ...value.relation_ids.flatMap(id => relations.get(id)!.evidence_refs)]);
    return value.evidence_ids.every(id => evidence.has(id));
  };
  return design.details_claim_ids.every(id => claims.has(id)) && design.pages.every(value => {
    const page = object(value), takeaway = object(page?.takeaway);
    if (!page || typeof page.title !== "string" || !["focus", "compare", "flow", "grid"].includes(String(page.layout))
      || !takeaway || typeof takeaway.text !== "string" || !refs(takeaway)
      || !Array.isArray(page.blocks) || !page.blocks.length || page.blocks.length > 4
      || !page.blocks.every(value => {
        const block = object(value);
        return block && typeof block.heading === "string" && typeof block.text === "string"
          && ["none", "book", "bulb", "database", "cpu", "network", "arrows-exchange", "shield", "code", "list-check", "hierarchy", "clock"].includes(String(block.icon)) && refs(block);
      }) || !Array.isArray(page.links)) return false;
    const blocks = page.blocks;
    return page.links.every(value => {
      const link = object(value);
      return link && Number.isInteger(link.source) && Number.isInteger(link.target)
        && Number(link.source) >= 0 && Number(link.target) >= 0 && Number(link.source) < blocks.length && Number(link.target) < blocks.length
        && typeof link.label === "string" && refs(link);
    });
  });
}

export function cardBatch(value: unknown): value is CardBatch {
  const item = object(value), context = object(item?.context), status = object(context?.status);
  if (!item || item.schema !== "card-batch/v1" || !isUuid(item.card_set_id)
    || !Number.isInteger(item.version) || Number(item.version) < 1
    || !Number.isInteger(item.offset) || Number(item.offset) < 0 || Number(item.offset) % 8 !== 0
    || !Number.isInteger(item.total) || Number(item.total) <= Number(item.offset)
    || !Array.isArray(item.cards) || item.cards.length !== Math.min(8, Number(item.total) - Number(item.offset))
    || !item.cards.every(card => conceptCard(card) && card.claims.length > 0 && cardEdits(object(card)?.display_edits))
    || new Set(item.cards.map(c => c.concept_id)).size !== item.cards.length
    || !context || typeof context.source_resolver !== "string"
    || !status || !["accepted", "needs_review"].includes(String(status.quality))
    || !["succeeded", "partial"].includes(String(status.processing))
    || !["retain", "review"].includes(String(status.decision)) || !strings(status.reason_codes)
    || !Array.isArray(context.concepts) || !context.concepts.every(conceptCard)
    || !Array.isArray(context.relation_evidence) || !context.relation_evidence.every(cardEvidence)
    || !Array.isArray(context.relations) || !context.relations.every(value => {
      const relation = object(value);
      return relation && revision(relation.relation_id, "relation")
        && revision(relation.source_concept_id, "concept") && revision(relation.target_concept_id, "concept")
        && ["prerequisite", "part_of", "application", "example", "contrast"].includes(String(relation.type))
        && typeof relation.learner_reason === "string" && strings(relation.evidence_refs)
        && relation.evidence_refs.every(id => revision(id, "evidence"));
    })) return false;
  const concepts = new Set(context.concepts.map(c => c.concept_id));
  return concepts.size === context.concepts.length && item.cards.every(c => concepts.has(c.concept_id) && cardDesign(c.design, c, context as unknown as CardBatch["context"]));
}

export function cardStudy(value: unknown): value is CardStudyView {
  const item = object(value);
  if (!item || !cardSetSummary(value)) return false;
  const generation = value.generation;
  if (item.schema !== "card-study/v1"
    || item.source_resolver !== `/v1/materials/${item.material_id}/knowledge-structures/${item.knowledge_structure_revision}/evidence`
    || !Array.isArray(item.navigation) || item.navigation.length !== generation.total
    || !item.navigation.every(value => {
      const entry = object(value);
      return entry && revision(entry.concept_id, "concept") && typeof entry.label === "string"
        && (entry.page_count === null || Number.isInteger(entry.page_count) && Number(entry.page_count) > 0);
    }) || new Set(item.navigation.map(c => c.concept_id)).size !== item.navigation.length) return false;
  const navigation = item.navigation;
  const status = object(item.status);
  if (!status) return false;
  if (generation.status !== "ready") return item.batch === null;
  const batch = item.batch;
  return cardBatch(batch) && batch.card_set_id === item.card_set_id && batch.version === item.version
    && batch.offset === 0 && batch.total === item.navigation.length
    && batch.context.source_resolver === item.source_resolver
    && batch.context.status.processing === status.processing && batch.context.status.quality === status.quality && batch.context.status.decision === status.decision
    && batch.cards.every((c, i) => c.concept_id === navigation[i].concept_id);
}

export function cardGeneration(value: unknown): value is CardGeneration {
  const item = object(value);
  return !!item && ["pending", "running", "ready", "failed"].includes(String(item.status))
    && ["preparing", "compacting", "designing", "checking", "complete"].includes(String(item.stage))
    && [item.completed, item.total, item.retained_original, item.cached].every(n => Number.isInteger(n) && Number(n) >= 0)
    && Number(item.completed) <= Number(item.total)
    && (item.error_code === null || typeof item.error_code === "string");
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

function knowledgeStatus(value: unknown): boolean {
  const s = object(value);
  return !!s && ["succeeded", "partial", "failed"].includes(String(s.processing))
    && ["accepted", "needs_review"].includes(String(s.quality))
    && ["retain", "review", "reject"].includes(String(s.decision)) && strings(s.reason_codes);
}

function relationSummary(value: unknown): boolean {
  const r = object(value);
  return !!r && revision(r.relation_id, "relation") && revision(r.source_concept_id, "concept")
    && revision(r.target_concept_id, "concept") && r.source_concept_id !== r.target_concept_id
    && ["prerequisite", "part_of", "application", "example", "contrast"].includes(String(r.type));
}

function knowledgeRelation(value: unknown): boolean {
  const r = object(value);
  return !!r && relationSummary(r) && typeof r.learner_reason === "string" && strings(r.evidence_refs)
    && r.evidence_refs.every(id => revision(id, "evidence"));
}

export function knowledgeOutline(value: unknown): value is KnowledgeOutlineView {
  const item = object(value);
  if (!item || item.schema !== "knowledge-outline/v1" || !isUuid(item.material_id) || !isUuid(item.run_id)
    || !revision(item.knowledge_structure_revision, "knowledge-structure") || !knowledgeStatus(item.status)
    || item.source_resolver !== `/v1/materials/${item.material_id}/knowledge-structures/${item.knowledge_structure_revision}/evidence`
    || !Number.isSafeInteger(item.section_count) || Number(item.section_count) < 0
    || !Array.isArray(item.concepts) || !Array.isArray(item.relations) || !Array.isArray(item.excluded_pages)
    || !Array.isArray(item.initial_learning_path)) return false;
  const ids = new Set<string>();
  for (const value of item.concepts) {
    const c = object(value);
    if (!c || !revision(c.concept_id, "concept") || ids.has(c.concept_id) || typeof c.label !== "string"
      || !strings(c.aliases) || !Number.isSafeInteger(c.claim_count)
      || Number(c.claim_count) < 0 || Object.hasOwn(c, "claims")) return false;
    ids.add(c.concept_id);
  }
  const path = item.initial_learning_path;
  return item.relations.every(r => relationSummary(r) && ids.has(r.source_concept_id) && ids.has(r.target_concept_id))
    && new Set(item.relations.map(r => r.relation_id)).size === item.relations.length
    && path.length === ids.size && new Set(path.map(s => s.concept_id)).size === ids.size
    && path.every((s, i) => object(s) && ids.has(s.concept_id) && s.position === i + 1
      && ["document_order", "prerequisite"].includes(s.reason));
}

export function knowledgeConcepts(value: unknown): value is KnowledgeConceptsView {
  const item = object(value);
  if (!item || item.schema !== "knowledge-concepts/v1" || !isUuid(item.material_id)
    || !revision(item.knowledge_structure_revision, "knowledge-structure") || !knowledgeStatus(item.status)
    || item.source_resolver !== `/v1/materials/${item.material_id}/knowledge-structures/${item.knowledge_structure_revision}/evidence`
    || !strings(item.requested_concept_ids) || item.requested_concept_ids.length > 32
    || !Array.isArray(item.concepts) || !item.concepts.every(c => conceptCard(c) && strings(object(c)?.aliases))
    || !Array.isArray(item.relations) || !item.relations.every(knowledgeRelation)
    || !Array.isArray(item.relation_evidence) || !item.relation_evidence.every(cardEvidence)) return false;
  const ids = new Set(item.concepts.map(c => c.concept_id));
  return ids.size === item.concepts.length && new Set(item.requested_concept_ids).size === item.requested_concept_ids.length
    && item.requested_concept_ids.every(id => ids.has(id));
}

export function knowledgeSearch(value: unknown): value is KnowledgeSearchView {
  const item = object(value);
  return !!item && item.schema === "knowledge-search/v1" && isUuid(item.material_id)
    && revision(item.knowledge_structure_revision, "knowledge-structure") && strings(item.concept_ids)
    && item.concept_ids.every(id => revision(id, "concept")) && new Set(item.concept_ids).size === item.concept_ids.length;
}

export function learnerProfile(value: unknown): value is LearnerProfile {
  const item = object(value);
  return !!item && item.schema === "learner-profile/v1"
    && (item.display_name === null || (typeof item.display_name === "string" && item.display_name.length > 0 && [...item.display_name].length <= 20));
}

function accountSession(value: unknown): boolean {
  const item = object(value);
  return !!item && isUuid(item.session_id) && typeof item.current === "boolean"
    && (item.device_label === null || (typeof item.device_label === "string" && item.device_label.length > 0 && item.device_label.length <= 64))
    && timestamp(item.created_at) && timestamp(item.last_used_at);
}

// 目前這個登入一定在清單裡且排第一，只有一個。
export function accountSessions(value: unknown): value is AccountSessions {
  const item = object(value);
  if (!item || item.schema !== "account-sessions/v2" || !Array.isArray(item.sessions) || !item.sessions.every(accountSession))
    return false;
  const sessions = item.sessions as { current: boolean }[];
  return sessions.length > 0 && sessions[0].current && sessions.filter((session) => session.current).length === 1;
}

export function librarySearch(value: unknown): value is LibrarySearchView {
  const item = object(value);
  return !!item && item.schema === "library-search/v1" && typeof item.query === "string"
    && typeof item.concepts_truncated === "boolean"
    && Array.isArray(item.materials) && item.materials.every(value => {
      const m = object(value);
      return !!m && isUuid(m.material_id) && typeof m.display_name === "string"
        && ((m.run_id === null && m.knowledge_structure_revision === null)
          || (isUuid(m.run_id) && revision(m.knowledge_structure_revision, "knowledge-structure")));
    })
    && Array.isArray(item.concepts) && item.concepts.every(value => {
      const c = object(value);
      return !!c && isUuid(c.material_id) && typeof c.material_name === "string" && isUuid(c.run_id)
        && revision(c.knowledge_structure_revision, "knowledge-structure") && revision(c.concept_id, "concept")
        && typeof c.label === "string" && c.label.length > 0 && count(c.claim_count);
    });
}

export function knowledgeRegions(value: unknown): value is KnowledgeRegionsView {
  const item = object(value);
  if (!item || item.schema !== "knowledge-regions/v1" || !isUuid(item.material_id)
    || !revision(item.knowledge_structure_revision, "knowledge-structure")
    || !["chapters", "source", "learning_order"].includes(String(item.basis)) || !Array.isArray(item.regions)
    || (item.groups !== undefined && !Array.isArray(item.groups))
    || (item.partial !== undefined && typeof item.partial !== "boolean")) return false;
  const groupIds = new Set<unknown>();
  for (const value of (item.groups ?? []) as unknown[]) {
    const group = object(value);
    if (!group || typeof group.group_id !== "string" || typeof group.label !== "string" || !group.label
      || !(group.source_id === null || isUuid(group.source_id)) || groupIds.has(group.group_id)) return false;
    groupIds.add(group.group_id);
  }
  const ids = item.regions.flatMap(value => {
    const region = object(value);
    return region && typeof region.region_id === "string" && typeof region.label === "string" && region.label
      && (region.source_id === null || isUuid(region.source_id)) && strings(region.concept_ids)
      && region.concept_ids.length > 0 && region.concept_ids.every(id => revision(id, "concept"))
      // 有島群時每章都要指向存在的島群；沒有島群時不得帶 group_id。
      && (groupIds.size ? groupIds.has(region.group_id) : region.group_id === undefined || region.group_id === null)
      ? region.concept_ids : [null];
  });
  // 每個概念只能屬於一個區域。
  return !ids.includes(null) && new Set(ids).size === ids.length;
}

const isoDate = /^\d{4}-\d{2}-\d{2}$/;
const count = (value: unknown): value is number => Number.isInteger(value) && (value as number) >= 0;

const questKinds = ["mark_cards", "continue_study", "finish_podcast_episode"];
const nullableUuid = (value: unknown) => value === null || isUuid(value);

function learningQuest(value: unknown): boolean {
  const quest = object(value);
  return !!quest && questKinds.includes(String(quest.kind)) && count(quest.progress) && count(quest.target)
    && Number(quest.target) > 0 && Number(quest.progress) <= Number(quest.target) && typeof quest.done === "boolean"
    && nullableUuid(quest.material_id) && nullableUuid(quest.study_session_id) && nullableUuid(quest.podcast_id)
    && (quest.episode_index === null || count(quest.episode_index));
}

function learningBadge(value: unknown): boolean {
  const badge = object(value);
  return !!badge && typeof badge.badge_id === "string" && typeof badge.earned === "boolean"
    && count(badge.progress) && count(badge.target) && Number(badge.target) > 0 && Number(badge.progress) <= Number(badge.target)
    && (badge.earned_on === null || (typeof badge.earned_on === "string" && isoDate.test(badge.earned_on)))
    // 取得與否須和進度一致；未取得的徽章不帶日期。
    && badge.earned === (badge.progress === badge.target) && (badge.earned || badge.earned_on === null);
}

export function learningActivity(value: unknown): value is LearningActivityView {
  const item = object(value);
  const xp = object(item?.xp);
  const sources = object(xp?.sources);
  const badges = object(item?.badges);
  return !!item && item.schema === "learning-activity/v3" && item.timezone === "Asia/Taipei"
    && item.rule_version === "xp-rules/v3"
    && typeof item.today === "string" && isoDate.test(item.today)
    && count(item.streak_days) && typeof item.active_today === "boolean"
    && count(item.total_answers) && count(item.total_correct) && item.total_correct <= item.total_answers
    && !!xp && !!sources && [xp.total, xp.today, xp.level_xp, xp.level_target].every(count)
    && Number.isInteger(xp.level) && Number(xp.level) >= 1 && Number(xp.level_xp) < Number(xp.level_target)
    && Number(xp.today) <= Number(xp.total)
    && ["answers", "concepts", "cards", "podcast"].every(key => count(sources[key]))
    && Array.isArray(item.quests) && item.quests.every(learningQuest)
    && !!badges && badges.rule_version === "badge-rules/v6" && Array.isArray(badges.items) && badges.items.every(learningBadge);
}

const rhythmPeriods = ["week", "month", "quarter", "year"];

// 學習節奏：每天一筆、日期連續涵蓋 start～end（ISO 日期字串可直接比較先後）。
export function learningRhythm(value: unknown): value is LearningRhythmView {
  const item = object(value);
  if (!item || item.schema !== "learning-rhythm/v1" || item.timezone !== "Asia/Taipei"
    || !rhythmPeriods.includes(String(item.period)) || !count(item.offset) || !count(item.streak_days)
    || ![item.today, item.start, item.end].every(day => typeof day === "string" && isoDate.test(day))
    || !(item.first_day === null || (typeof item.first_day === "string" && isoDate.test(item.first_day)))
    || !Array.isArray(item.days) || item.days.length === 0 || item.days.length > 364) return false;
  const days = item.days.map(object);
  return days.every(day => !!day && typeof day.date === "string" && isoDate.test(day.date)
      && count(day.answers) && count(day.correct) && day.correct <= day.answers
      && count(day.card_reviews) && count(day.listened_seconds) && typeof day.active === "boolean")
    && days[0]!.date === item.start && days.at(-1)!.date === item.end
    && days.every((day, index) => index === 0 || String(day!.date) > String(days[index - 1]!.date));
}

export function podcastProgress(value: unknown): value is PodcastProgressView {
  const item = object(value);
  const current = item?.current === null ? null : object(item?.current);
  const position = (value: unknown) => typeof value === "number" && Number.isFinite(value) && value >= 0;
  return !!item && item.schema === "podcast-progress/v1" && isUuid(item.podcast_id)
    && (item.current === null || (!!current && count(current.episode_index) && position(current.position_seconds)
      && typeof current.updated_at === "string"))
    && Array.isArray(item.episodes) && item.episodes.every(value => {
      const episode = object(value);
      return !!episode && count(episode.episode_index) && position(episode.position_seconds) && typeof episode.completed === "boolean";
    }) && count(item.credited_seconds);
}

export function progressSummary(value: unknown): value is ProgressSummaryView {
  const item = object(value);
  const count = (value: unknown) => Number.isInteger(value) && (value as number) >= 0;
  return !!item && item.schema === "progress-summary/v1" && isUuid(item.material_id)
    && revision(item.knowledge_structure_revision, "knowledge-structure")
    && (item.study_session_id === null || isUuid(item.study_session_id))
    && count(item.concept_count) && count(item.completed_count) && (item.completed_count as number) <= (item.concept_count as number)
    && (item.current_concept_id === null || revision(item.current_concept_id, "concept"))
    && (item.current_concept_label === null || typeof item.current_concept_label === "string");
}

export function learningDiagnosis(value: unknown): value is LearningDiagnosisView {
  const item = object(value);
  const findings = (value: unknown) => Array.isArray(value) && value.every(value => {
    const finding = object(value);
    return !!finding && revision(finding.concept_id, "concept") && typeof finding.label === "string"
      && Array.isArray(finding.points) && finding.points.every(value => {
        const point = object(value);
        return !!point && revision(point.claim_id, "claim") && typeof point.title === "string"
          && Number.isSafeInteger(point.answer_count) && Number(point.answer_count) >= 0
          && Number.isSafeInteger(point.correct_count) && Number(point.correct_count) >= 0 && Number(point.correct_count) <= Number(point.answer_count)
          && (point.latest_is_correct === null || typeof point.latest_is_correct === "boolean")
          && Array.isArray(point.evidence) && point.evidence.every(value => {
            const source = object(value);
            return !!source && revision(source.evidence_id, "evidence") && Number.isSafeInteger(source.page) && Number(source.page) >= 1
              && (source.source_name == null || typeof source.source_name === "string")
              && (source.normalized_page == null || Number.isSafeInteger(source.normalized_page) && Number(source.normalized_page) >= 1);
          });
      });
  });
  return !!item && item.schema === "learning-diagnosis/v1" && isUuid(item.material_id)
    && revision(item.knowledge_structure_revision, "knowledge-structure")
    && item.source_resolver === `/v1/materials/${item.material_id}/knowledge-structures/${item.knowledge_structure_revision}/evidence`
    && (item.scope === "material" ? item.set_id === null : item.scope === "set" && isUuid(item.set_id))
    && findings(item.strengths) && findings(item.weaknesses)
    && Array.isArray(item.recommendations) && item.recommendations.length <= 3 && item.recommendations.every(value => {
      const recommendation = object(value);
      return !!recommendation && revision(recommendation.concept_id, "concept")
        && typeof recommendation.label === "string" && typeof recommendation.reason === "string";
    });
}

export function conceptDifficulty(value: unknown): value is ConceptDifficultyView {
  const item = object(value);
  return !!item && item.schema === "concept-difficulty/v1" && isUuid(item.material_id)
    && revision(item.knowledge_structure_revision, "knowledge-structure")
    && item.mode === "structure"
    && Array.isArray(item.concepts) && item.concepts.every(value => {
      const concept = object(value);
      return !!concept && revision(concept.concept_id, "concept") && typeof concept.label === "string"
        && ["standard", "hard"].includes(String(concept.level)) && typeof concept.reason === "string"
        && typeof concept.personal_difficulty === "boolean"
        && Number.isSafeInteger(concept.incorrect_count) && Number(concept.incorrect_count) >= 0
        && Number.isSafeInteger(concept.answer_count) && Number(concept.answer_count) >= Number(concept.incorrect_count);
    }) && new Set(item.concepts.map(concept => concept.concept_id)).size === item.concepts.length;
}

export function mapProgress(value: unknown): value is MapProgressView {
  const item = object(value);
  return !!item && item.schema === "map-progress/v1" && isUuid(item.study_session_id)
    && (item.starting_level === null || startingLevel(item.starting_level))
    && strings(item.learning_path) && item.learning_path.every(id => revision(id, "concept"))
    && revision(item.knowledge_structure_revision, "knowledge-structure")
    && (item.current_concept_id === null || revision(item.current_concept_id, "concept"))
    && !!object(item.next_action)
    && Array.isArray(item.assessment_cycles) && item.assessment_cycles.every(cycleSummary)
    && Array.isArray(item.concept_states) && item.concept_states.every(value => {
      const s = object(value);
      return !!s && revision(s.concept_id, "concept") && typeof s.assessable === "boolean"
        && ["not_started", "in_progress", "needs_review", "completed"].includes(String(s.status))
        && Number.isSafeInteger(s.attempts) && Number(s.attempts) >= 0;
    }) && new Set(item.concept_states.map(s => s.concept_id)).size === item.concept_states.length;
}

function studyNavigation(value: unknown): value is StudyNavigationView {
  const item = object(value);
  if (!item || item.schema !== "study-navigation/v1" || !isUuid(item.material_id) || !isUuid(item.run_id)
    || !revision(item.knowledge_structure_revision, "knowledge-structure") || !knowledgeStatus(item.status)
    || item.source_resolver !== `/v1/materials/${item.material_id}/knowledge-structures/${item.knowledge_structure_revision}/evidence`
    || !Number.isSafeInteger(item.concept_count) || Number(item.concept_count) < 0
    || !Array.isArray(item.concepts) || item.concepts.length > Number(item.concept_count)
    || !item.concepts.every(c => object(c) && revision(c.concept_id, "concept") && typeof c.label === "string")
    || !Array.isArray(item.initial_learning_path)) return false;
  const ids = new Set(item.concepts.map(c => c.concept_id));
  return ids.size === item.concepts.length && item.initial_learning_path.length === ids.size
    && new Set(item.initial_learning_path.map(s => s.concept_id)).size === ids.size
    && item.initial_learning_path.every(s => object(s) && ids.has(s.concept_id)
      && Number.isSafeInteger(s.position) && s.position > 0 && s.position <= Number(item.concept_count)
      && ["document_order", "prerequisite"].includes(s.reason));
}

export function studyWorkspace(value: unknown): value is StudyWorkspaceView {
  const item = object(value), p = object(item?.progress);
  if (!item || item.schema !== "study-workspace/v1" || !studySession(item.session)
    || !(item.starting_level === null || startingLevel(item.starting_level))
    || !studyNavigation(item.outline) || !isUuid(item.run_id) || !isUuid(item.source_artifact_id)
    || !p || p.schema !== "study-progress/v1" || !progress({ ...p, schema: "learner-progress/v1" })
    || p.concept_count !== item.outline.concept_count
    || !(item.context === null || knowledgeConcepts(item.context))
    || !Array.isArray(item.assessment_sets) || !item.assessment_sets.every(assessmentSetSummary)) return false;
  const session = item.session, outline = item.outline, context = item.context;
  const ids = new Set(outline.concepts.map(c => c.concept_id));
  const selected = item.selected_set;
  if (item.selected_set_id === null ? selected !== null : (
    !assessmentSet(selected) || selected.set_id !== item.selected_set_id || selected.study_session_id !== session.study_session_id
    || selected.material_id !== session.material_id || selected.knowledge_structure_revision !== session.knowledge_structure_revision
    || !item.assessment_sets.some(s => s.set_id === selected.set_id && s.set_version === selected.set_version && s.target_concept_id === selected.target_concept_id)
  )) return false;
  const needed = new Set([session.current_concept_id, object(selected)?.target_concept_id, object(p.next_action)?.target_concept_id,
    ...(object(selected)?.kind === "material_diagnostic" ? (object(selected)!.items as Json[]).map(row => row.target_concept_id) : []),
  ].filter(v => v != null));
  return outline.material_id === session.material_id && outline.run_id === item.run_id
    && outline.knowledge_structure_revision === session.knowledge_structure_revision
    && p.study_session_id === session.study_session_id && p.event_watermark === session.event_watermark
    && p.knowledge_structure_revision === session.knowledge_structure_revision
    && p.current_concept_id === session.current_concept_id
    && (context === null ? needed.size === 0 : context.material_id === session.material_id
      && context.knowledge_structure_revision === session.knowledge_structure_revision
      && context.concepts.length === needed.size && context.concepts.every(c => ids.has(c.concept_id))
      && [...needed].every(id => context.concepts.some(c => c.concept_id === id)))
    && (p.concept_states as Json[]).every(s => ids.has(String(s.concept_id)))
    && [...needed].every(id => (p.concept_states as Json[]).some(s => s.concept_id === id))
    && item.assessment_sets.every(s => s.kind === "material_diagnostic" || ids.has(s.target_concept_id));
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
    !(item.starting_level === null || startingLevel(item.starting_level)) ||
    !strings(item.learning_path) || !item.learning_path.every(id => revision(id, "concept")) ||
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
    ["diagnostic", "remediation", "material_diagnostic"].includes(String(item.kind)) &&
    (item.kind !== "remediation"
      ? item.diagnostic_set_id === null
      : isUuid(item.diagnostic_set_id) && item.diagnostic_set_id !== item.set_id) &&
    isUuid(item.set_id) &&
    (item.kind === "material_diagnostic" ? item.target_concept_id === null : revision(item.target_concept_id, "concept")) &&
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
      (item.kind === "material_diagnostic" ? "material-diagnostic/v1" : item.kind === "diagnostic"
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
  if (item.kind === "material_diagnostic") {
    if (item.cycle !== null || item.requested_count > 15
      || new Set(item.items.map((row: Json) => row.target_concept_id)).size !== item.items.length) return false;
  } else {
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
  }
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
      !revision(row.target_concept_id, "concept") ||
      (item.kind !== "material_diagnostic" && row.target_concept_id !== item.target_concept_id) ||
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
      row.assessment.target_concept_id !== row.target_concept_id ||
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
  const selected = item.selected_set;
  if (item.selected_set_id === null ? selected !== null : (
    !assessmentSet(selected) || selected.set_id !== item.selected_set_id ||
    selected.study_session_id !== session.study_session_id ||
    selected.material_id !== session.material_id ||
    selected.knowledge_structure_revision !== session.knowledge_structure_revision ||
    !item.assessment_sets.some(group => group.set_id === selected.set_id &&
      group.target_concept_id === selected.target_concept_id && group.set_version === selected.set_version)
  )) return false;
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
  if (item?.generation !== undefined) {
    const generation = object(item.generation);
    if (!generation || typeof generation.active !== 'boolean'
      || ['preview_ready','retryable_videos'].some(key => generation[key] !== undefined && typeof generation[key] !== 'boolean')
      || !['completed_videos','failed_videos'].every(key => Number.isInteger(generation[key]) && Number(generation[key]) >= 0)
      || Number(generation.completed_videos) + Number(generation.failed_videos) > Number(item.episode_count)) return false;
  }
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
    // 總時長只在音訊全部完成（ready）時提供。
    && (item.duration_seconds === null || typeof item.duration_seconds === "number" && Number.isFinite(item.duration_seconds)
      && item.duration_seconds >= 0 && item.status === "ready" && item.completed_episodes === item.episode_count)
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
      || !(episode.title === undefined || episode.title === null || (typeof episode.title === "string" && !!episode.title.trim()))
      || !["solo", "dialogue"].includes(String(episode.delivery))
      || episode.delivery !== item.delivery) return false;
    if (episode.hosts !== undefined && episode.hosts !== null) {
      const hosts = object(episode.hosts);
      const roles = episode.delivery === "dialogue" ? ["host", "guest"] : ["host"];
      if (!hosts || Object.keys(hosts).length !== roles.length
        || !roles.every(role => typeof hosts[role] === "string" && !!(hosts[role] as string).trim())) return false;
    }
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
        let totalText=0;
        if (script.segments.length<1 || script.segments.length>12) return false;
        if (!script.segments.every((value,i)=>{
          const beat=object(value);
          if (!beat || beat.beat_id!==`beat-${i}` || typeof beat.title!=='string' || !beat.title || !Array.isArray(beat.turns) || !beat.turns.length) return false;
          return beat.turns.every(value=>{
            const turn=object(value);
            if (!turn || !['host',...(episode.delivery==='dialogue'?['guest']:[])].includes(String(turn.speaker))
              || typeof turn.text!=='string' || !turn.text.trim() || !Array.isArray(turn.parts) || !turn.parts.length) return false;
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
                seen.add(index);
              }
            }
            totalText+=Array.from(text).length;
            return text===turn.text&&Array.from(text).length<=1600;
          });
        }) || totalText>9600) return false;
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
      if (script.schema !== "podcast-script/v2" && episode.delivery === "dialogue" && speakers.size !== 2) return false;
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


function noteItems(value: unknown, depth = 1): boolean {
  return Array.isArray(value) && value.length > 0 && value.every(v => {
    const item = object(v);
    return !!item && typeof item.text === "string"
      && (item.children === undefined || (depth < 3 && (Array.isArray(item.children) && (item.children.length === 0 || noteItems(item.children, depth + 1)))));
  });
}

function noteTable(value: Record<string, unknown> | null): boolean {
  return !!value && strings(value.headers) && Array.isArray(value.rows) && value.rows.every(strings);
}

function noteBlock(value: unknown): boolean {
  const b = object(value);
  if (!b) return false;
  switch (b.type) {
    case "text": return typeof b.text === "string";
    case "bullets": return noteItems(b.items);
    case "table": return (b.caption === null || typeof b.caption === "string") && noteTable(b);
    case "formula": return typeof b.latex === "string";
    case "callout": return ["case_study", "discussion", "reminder", "lesson"].includes(String(b.style))
      && (b.title === null || typeof b.title === "string") && noteItems(b.items);
    default: return false;
  }
}

function noteParagraph(value: unknown): boolean {
  const p = object(value);
  if (!p || typeof p.id !== "string" || typeof p.anchor !== "string" || typeof p.heading !== "string"
    || !strings(p.claim_ids) || !strings(p.evidence_ids)
    || !Array.isArray(p.concepts) || !p.concepts.every(c => { const x = object(c); return x && typeof x.concept_id === "string" && typeof x.label === "string"; })
    || !Array.isArray(p.evidence) || !p.evidence.length || !p.evidence.every(cardEvidence)) return false;
  if ("blocks" in p) return (p.level === 1 || p.level === 2) && Array.isArray(p.blocks) && p.blocks.length > 0 && p.blocks.every(noteBlock);
  // v11 以前的段落格式。
  return typeof p.text === "string" && (p.formula === null || typeof p.formula === "string")
    && (p.table === null || noteTable(object(p.table)));
}

function noteOutline(value: unknown): boolean {
  return Array.isArray(value) && value.every(v => {
    const o = object(v);
    return !!o && typeof o.paragraph_id === "string" && (o.level === 1 || o.level === 2)
      && typeof o.number === "string" && typeof o.heading === "string";
  });
}

function noteProgress(value: unknown): boolean {
  if (value === null) return true;
  const p = object(value);
  return !!p && ["generating", "validating", "completed"].includes(String(p.stage))
    && [p.completed_sections, p.total_sections, p.completed_parts, p.total_parts].every(v => Number.isInteger(v) && Number(v) >= 0)
    && Number(p.completed_sections) <= Number(p.total_sections) && Number(p.completed_parts) <= Number(p.total_parts)
    && (p.current_section === null || typeof p.current_section === "string");
}

export function materialNotes(value: unknown): value is MaterialNotesView {
  const v = object(value);
  return !!v && v.schema === "material-notes/v2" && isUuid(v.material_id) && isUuid(v.run_id)
    && revision(v.knowledge_structure_revision, "knowledge-structure") && ["summary", "full"].includes(String(v.kind))
    && ["not_generated", "pending", "running", "ready", "failed"].includes(String(v.status))
    && Number.isInteger(v.generation) && Number(v.generation) >= 0 && typeof v.source_resolver === "string"
    && (v.error_code === null || typeof v.error_code === "string")
    && knowledgeStatus(v.source_status) && noteProgress(v.progress) && Array.isArray(v.paragraphs) && v.paragraphs.every(noteParagraph)
    && noteOutline(v.outline)
    && Array.isArray(v.annotations) && v.annotations.every(a => { const x = object(a); return !!x
      && isUuid(x.annotation_id) && typeof x.anchor === "string" && typeof x.body === "string"
      && revision(x.original_revision, "knowledge-structure") && noteParagraph(x.original_paragraph)
      && (x.paragraph_id === null || typeof x.paragraph_id === "string") && timestamp(x.updated_at); });
}
