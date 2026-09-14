import type {
  AnswerFeedbackView,
  AnswerSubmissionCreate,
  ApiErrorView,
  ApiReasonCode,
  AssessmentCreate,
  AssessmentView,
  GuidanceApply,
  KnowledgeStructureRequest,
  KnowledgeStructureView,
  KnownApiReasonCode,
  LearnerProgressView,
  MaterialProcessingCreate,
  MaterialProcessingRunView,
  MaterialDiscardView,
  MaterialView,
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
  "IDEMPOTENCY_CONFLICT", "NO_SAFE_ASSESSMENT", "MATERIAL_TOO_LARGE",
  "MATERIAL_NOT_DISCARDABLE",
  "MATERIAL_PDF_INVALID", "UNSUPPORTED_MEDIA_TYPE", "STORAGE_UNAVAILABLE", "INTERNAL_ERROR",
]);
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const sha = /^[0-9a-f]{64}$/;
const authTimeoutMs = 10_000;
const maximumPdfBytes = 100 * 1024 * 1024;

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

function material(value: unknown): value is MaterialView {
  const item = object(value);
  return !!item && item.schema === "material/v1" && typeof item.material_id === "string" && uuid.test(item.material_id)
    && typeof item.source_artifact_id === "string" && uuid.test(item.source_artifact_id)
    && typeof item.source_sha256 === "string" && sha.test(item.source_sha256)
    && Number.isInteger(item.size_bytes) && Number(item.size_bytes) > 0;
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
  if (!item || item.schema !== "material-processing-run/v5" || !materialAttempt(item)) return false;
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

function libraryItem(value: unknown): value is MaterialLibraryItem {
  const item = object(value);
  if (!item || item.schema !== "material-library-item/v2"
    || typeof item.material_id !== "string" || !uuid.test(item.material_id)
    || typeof item.source_artifact_id !== "string" || !uuid.test(item.source_artifact_id)
    || typeof item.display_name !== "string" || !item.display_name.trim()
    || !Number.isInteger(item.size_bytes) || Number(item.size_bytes) < 1
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
  if (!item || item.schema !== "knowledge-structure-view/v2" || !revision(item.knowledge_structure_revision, "knowledge-structure")) return false;
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
  if (!item || item.schema !== "learner-progress/v2" || typeof item.study_session_id !== "string" || !uuid.test(item.study_session_id)
    || !revision(item.knowledge_structure_revision, "knowledge-structure") || !Number.isInteger(item.event_watermark)
    || !(item.current_concept_id === null || revision(item.current_concept_id, "concept"))
    || !Array.isArray(item.concept_states) || !Array.isArray(item.weaknesses) || !object(item.next_action)
    || !revision(item.guidance_revision, "learner-guidance")) return false;
  return item.concept_states.every((value) => {
    const state = object(value);
    return !!state && revision(state.concept_id, "concept") && typeof state.label === "string"
      && ["not_started", "learning", "needs_review", "mastered"].includes(String(state.status));
  });
}

function studyResume(value: unknown): value is StudyResumeView {
  const item = object(value);
  if (!item || item.schema !== "study-resume/v1" || !studySession(item.session)
    || !knowledgeStructure(item.knowledge_structure) || !progress(item.progress)
    || typeof item.run_id !== "string" || !uuid.test(item.run_id)
    || typeof item.source_artifact_id !== "string" || !uuid.test(item.source_artifact_id)
    || !Array.isArray(item.assessments)) return false;
  const session = item.session;
  if (item.progress.study_session_id !== session.study_session_id
    || item.progress.knowledge_structure_revision !== session.knowledge_structure_revision
    || item.knowledge_structure.knowledge_structure_revision !== session.knowledge_structure_revision
    || item.progress.event_watermark !== session.event_watermark) return false;
  if (!item.assessments.every((value) => {
    const record = object(value);
    if (!record || !assessment(record.assessment) || typeof record.created_at !== "string" || typeof record.can_submit !== "boolean") return false;
    const question = record.assessment;
    if (question.study_session_id !== session.study_session_id || question.knowledge_structure_revision !== session.knowledge_structure_revision) return false;
    if (record.feedback !== null && (!feedback(record.feedback)
      || record.feedback.study_session_id !== session.study_session_id
      || record.feedback.assessment_revision !== question.assessment_revision
      || record.feedback.question_id !== question.question_id
      || !question.options.some(option => option.option_id === (record.feedback as AnswerFeedbackView).selected_option_id))) return false;
    return !record.can_submit || (record.feedback === null && session.status !== "completed" && question.target_concept_id === session.current_concept_id);
  })) return false;
  return item.selected_assessment_revision === null || item.assessments.some(value => object(object(value)?.assessment)?.assessment_revision === item.selected_assessment_revision);
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
  if (reason === "NO_SAFE_ASSESSMENT") return "目前沒有可安全提供的新題目。";
  if (reason === "MATERIAL_TOO_LARGE") return "PDF 不可超過 100 MiB。";
  if (reason === "MATERIAL_PDF_INVALID") return "這份 PDF 已損毀、加密或無法開啟。";
  if (reason === "UNSUPPORTED_MEDIA_TYPE") return "只接受 PDF 教材。";
  if (reason === "STORAGE_UNAVAILABLE") return "資料服務暫時無法使用，請稍後再試。";
  if (reason === "MATERIAL_NOT_DISCARDABLE") return "這份教材已有可使用的學習資料，目前無法直接移除。";
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

  async createMaterial(pdf: Blob, key: string = crypto.randomUUID(), displayName?: string): Promise<MaterialView> {
    if (pdf.type !== "application/pdf" || pdf.size < 1 || pdf.size > maximumPdfBytes) throw new ApiClientError("input", "請選擇有效且不超過 100 MiB 的 PDF。", { reasonCode: "REQUEST_INPUT_INVALID" });
    const headers: Record<string, string> = { "Content-Type": "application/pdf", Origin: origin(), "Idempotency-Key": key };
    if (displayName !== undefined) headers["X-Material-Name"] = encodeURIComponent(displayName);
    return this.json("/v1/materials", { method: "POST", headers, body: pdf }, material);
  }

  listMaterials(): Promise<MaterialLibraryView> {
    return this.json("/v1/materials", { method: "GET" }, library);
  }

  async getMaterial(materialId: string): Promise<MaterialLibraryItem> {
    const item = await this.json(`/v1/materials/${encodeURIComponent(materialId)}`, { method: "GET" }, libraryItem);
    if (item.material_id !== materialId) throw new ApiClientError("schema", "教材身分不一致。", { reasonCode: "RESPONSE_SCHEMA_MISMATCH" });
    return item;
  }

  createMaterialRun(body: MaterialProcessingCreate, key: string = crypto.randomUUID()): Promise<MaterialProcessingRunView> {
    return this.post("/v1/material-processing-runs", body, key, materialRun);
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

  async resumeStudy(request: { materialId: string; structureRevision: string; studySessionId: string; runId: string; assessmentRevision?: string }): Promise<StudyResumeView> {
    const query = new URLSearchParams({ run_id: request.runId });
    if (request.assessmentRevision) query.set("assessment_revision", request.assessmentRevision);
    const restored = await this.json(`/v1/materials/${encodeURIComponent(request.materialId)}/knowledge-structures/${encodeURIComponent(request.structureRevision)}/study-sessions/${encodeURIComponent(request.studySessionId)}/resume?${query}`, { method: "GET" }, studyResume);
    if (restored.session.material_id !== request.materialId || restored.session.study_session_id !== request.studySessionId
      || restored.session.knowledge_structure_revision !== request.structureRevision || restored.run_id !== request.runId
      || (request.assessmentRevision !== undefined && restored.selected_assessment_revision !== request.assessmentRevision)) {
      throw new ApiClientError("schema", "學習紀錄與教材版本不一致。", { reasonCode: "RESPONSE_SCHEMA_MISMATCH" });
    }
    return restored;
  }

  completeStudySession(id: string): Promise<StudySessionView> {
    return this.json(`/v1/study-sessions/${encodeURIComponent(id)}/complete`, { method: "POST", headers: { Origin: origin() } }, studySession);
  }

  createAssessment(id: string, body: AssessmentCreate, key: string = crypto.randomUUID()): Promise<AssessmentView> {
    return this.post(`/v1/study-sessions/${encodeURIComponent(id)}/assessments`, body, key, assessment);
  }

  submitAssessmentAnswer(id: string, revision: string, body: AnswerSubmissionCreate, key: string = crypto.randomUUID()): Promise<AnswerFeedbackView> {
    return this.post(`/v1/study-sessions/${encodeURIComponent(id)}/assessments/${encodeURIComponent(revision)}/submissions`, body, key, feedback);
  }

  applyGuidance(id: string, body: GuidanceApply): Promise<LearnerProgressView> {
    return this.json(`/v1/study-sessions/${encodeURIComponent(id)}/guidance/apply`, { method: "POST", headers: { "Content-Type": "application/json", Origin: origin() }, body: JSON.stringify(body) }, progress);
  }

  sourceArtifactUrl(id: string, page?: number): string {
    return `/v1/artifacts/${encodeURIComponent(id)}${page ? `#page=${page}` : ""}`;
  }
}

export function errorMessage(error: unknown): string {
  return error instanceof ApiClientError ? error.message : "發生未預期的錯誤，請稍後再試。";
}
