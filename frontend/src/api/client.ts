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
  MaterialView,
  StudySessionCreate,
  StudySessionView,
} from "./contracts";

type FetchRequest = (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;
type Json = Record<string, unknown>;

const knownReasons = new Set<KnownApiReasonCode>([
  "REQUEST_INVALID", "SESSION_REQUIRED", "ORIGIN_NOT_ALLOWED", "RESOURCE_NOT_FOUND",
  "IDEMPOTENCY_CONFLICT", "NO_SAFE_ASSESSMENT", "MATERIAL_TOO_LARGE",
  "MATERIAL_PDF_INVALID", "UNSUPPORTED_MEDIA_TYPE", "STORAGE_UNAVAILABLE", "INTERNAL_ERROR",
]);
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const sha = /^[0-9a-f]{64}$/;
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

function materialRun(value: unknown): value is MaterialProcessingRunView {
  const item = object(value);
  if (!item || item.schema !== "material-processing-run/v4" || typeof item.run_id !== "string" || !uuid.test(item.run_id)) return false;
  if (typeof item.material_id !== "string" || !uuid.test(item.material_id) || typeof item.source_artifact_id !== "string" || !uuid.test(item.source_artifact_id)) return false;
  const statuses = ["pending", "running", "succeeded", "partial", "failed"];
  const stages = ["queued", "evidence", "semantics", "publishing", "completed"];
  if (!statuses.includes(String(item.status)) || !stages.includes(String(item.progress_stage)) || !Number.isInteger(item.completed_pages)) return false;
  if (!(item.total_pages === null || Number.isInteger(item.total_pages)) || !(item.error_code === null || typeof item.error_code === "string")) return false;
  if (item.status === "succeeded" || item.status === "partial") {
    const binding = object(item.output_binding);
    return !!binding && binding.schema === "material-run-output-binding/v4"
      && revision(binding.knowledge_structure_revision, "knowledge-structure")
      && typeof binding.runtime_lock_sha256 === "string" && sha.test(binding.runtime_lock_sha256)
      && Number.isInteger(binding.page_count) && Number(binding.page_count) >= 1
      && Number.isInteger(binding.ocr_calls) && Number.isInteger(binding.semantic_calls)
      && item.progress_stage === "completed" && typeof item.completed_at === "string";
  }
  return item.output_binding === null;
}

function locator(value: unknown): boolean {
  const item = object(value);
  return !!item && Number.isInteger(item.page) && Number(item.page) >= 1 && revision(item.block_id, "block")
    && Array.isArray(item.region) && item.region.length === 4 && item.region.every((number) => typeof number === "number" && Number.isFinite(number));
}

function knowledgeStructure(value: unknown): value is KnowledgeStructureView {
  const item = object(value);
  if (!item || item.schema !== "knowledge-structure-view/v1" || !revision(item.knowledge_structure_revision, "knowledge-structure")) return false;
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
    && strings(item.deferred_concept_ids) && Number.isInteger(item.event_watermark)
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

function apiError(value: unknown): value is ApiErrorView {
  const item = object(value);
  return !!item && item.schema === "api-error/v1" && typeof item.request_id === "string" && uuid.test(item.request_id)
    && typeof item.reason_code === "string" && typeof item.retryable === "boolean" && item.message === "Request could not be completed.";
}

function safeMessage(reason: ApiReasonCode): string {
  if (reason === "SESSION_REQUIRED") return "工作階段已失效，請重新整理後再試。";
  if (reason === "RESOURCE_NOT_FOUND") return "找不到這筆資料，或你沒有權限讀取。";
  if (reason === "NO_SAFE_ASSESSMENT") return "目前沒有可安全提供的新題目。";
  if (reason === "MATERIAL_TOO_LARGE") return "PDF 不可超過 100 MiB。";
  if (reason === "MATERIAL_PDF_INVALID") return "這份 PDF 已損毀、加密或無法開啟。";
  if (reason === "UNSUPPORTED_MEDIA_TYPE") return "只接受 PDF 教材。";
  if (reason === "STORAGE_UNAVAILABLE") return "資料服務暫時無法使用，請稍後再試。";
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

export class StudydyApiClient {
  private sessionReady: Promise<void> | null = null;
  private readonly fetchRequest: FetchRequest;

  constructor(fetchRequest: FetchRequest = fetch.bind(globalThis)) {
    this.fetchRequest = fetchRequest;
  }

  async ensureSession(): Promise<void> {
    if (!this.sessionReady) {
      this.sessionReady = this.request("/v1/session/refresh", { method: "POST", headers: { Origin: origin() } })
        .catch((error) => {
          if (error instanceof ApiClientError && error.reasonCode === "SESSION_REQUIRED") {
            return this.request("/v1/session", { method: "POST", headers: { Origin: origin() } });
          }
          throw error;
        })
        .then(() => undefined)
        .catch((error) => { this.sessionReady = null; throw error; });
    }
    return this.sessionReady;
  }

  private async request(path: string, init: RequestInit, retry = true): Promise<Response> {
    let response: Response;
    try {
      response = await this.fetchRequest(path, { ...init, credentials: "same-origin" });
    } catch {
      throw new ApiClientError("network", "無法連線到 Studydy。", { reasonCode: "NETWORK_ERROR", retryable: true });
    }
    if (response.ok) return response;
    let body: unknown;
    try { body = await response.json(); } catch { body = null; }
    if (response.status === 401 && retry && path !== "/v1/session" && path !== "/v1/session/refresh") {
      this.sessionReady = null;
      await this.ensureSession();
      return this.request(path, init, false);
    }
    if (!apiError(body)) throw new ApiClientError("schema", "伺服器回應格式無法辨識。", { status: response.status, reasonCode: "RESPONSE_SCHEMA_MISMATCH" });
    const reason = knownReasons.has(body.reason_code as KnownApiReasonCode) ? body.reason_code as KnownApiReasonCode : "UNKNOWN_API_ERROR";
    throw new ApiClientError("api", safeMessage(reason), { status: response.status, reasonCode: reason, requestId: body.request_id, retryable: body.retryable });
  }

  private async json<T>(path: string, init: RequestInit, guard: (value: unknown) => value is T): Promise<T> {
    const response = await this.request(path, init);
    let value: unknown;
    try { value = await response.json(); } catch { value = null; }
    if (!guard(value)) throw new ApiClientError("schema", "伺服器回應格式無法辨識。", { status: response.status, reasonCode: "RESPONSE_SCHEMA_MISMATCH" });
    return value;
  }

  private post<T>(path: string, body: unknown, key: string, guard: (value: unknown) => value is T): Promise<T> {
    return this.json(path, {
      method: "POST",
      headers: { "Content-Type": "application/json", Origin: origin(), "Idempotency-Key": key },
      body: JSON.stringify(body),
    }, guard);
  }

  async createMaterial(pdf: Blob, key: string = crypto.randomUUID()): Promise<MaterialView> {
    if (pdf.type !== "application/pdf" || pdf.size < 1 || pdf.size > maximumPdfBytes) throw new ApiClientError("input", "請選擇有效且不超過 100 MiB 的 PDF。", { reasonCode: "REQUEST_INPUT_INVALID" });
    return this.json("/v1/materials", { method: "POST", headers: { "Content-Type": "application/pdf", Origin: origin(), "Idempotency-Key": key }, body: pdf }, material);
  }

  createMaterialRun(body: MaterialProcessingCreate, key: string = crypto.randomUUID()): Promise<MaterialProcessingRunView> {
    return this.post("/v1/material-processing-runs", body, key, materialRun);
  }

  getMaterialRun(runId: string): Promise<MaterialProcessingRunView> {
    return this.json(`/v1/material-processing-runs/${encodeURIComponent(runId)}`, { method: "GET" }, materialRun);
  }

  async getKnowledgeStructure(request: KnowledgeStructureRequest): Promise<KnowledgeStructureView> {
    const view = await this.json(`/v1/materials/${encodeURIComponent(request.materialId)}/knowledge-structures/${encodeURIComponent(request.structureRevision)}`, { method: "GET" }, knowledgeStructure);
    if (view.knowledge_structure_revision !== request.structureRevision) throw new ApiClientError("schema", "教材結構版本不一致。", { reasonCode: "RESPONSE_SCHEMA_MISMATCH" });
    return view;
  }

  createStudySession(body: StudySessionCreate, key: string = crypto.randomUUID()): Promise<StudySessionView> {
    return this.post("/v1/study-sessions", body, key, studySession);
  }

  getStudySession(id: string): Promise<StudySessionView> {
    return this.json(`/v1/study-sessions/${encodeURIComponent(id)}`, { method: "GET" }, studySession);
  }

  completeStudySession(id: string): Promise<StudySessionView> {
    return this.json(`/v1/study-sessions/${encodeURIComponent(id)}/complete`, { method: "POST", headers: { Origin: origin() } }, studySession);
  }

  createAssessment(id: string, body: AssessmentCreate, key: string = crypto.randomUUID()): Promise<AssessmentView> {
    return this.post(`/v1/study-sessions/${encodeURIComponent(id)}/assessments`, body, key, assessment);
  }

  getAssessment(id: string, revision: string): Promise<AssessmentView> {
    return this.json(`/v1/study-sessions/${encodeURIComponent(id)}/assessments/${encodeURIComponent(revision)}`, { method: "GET" }, assessment);
  }

  submitAssessmentAnswer(id: string, revision: string, body: AnswerSubmissionCreate, key: string = crypto.randomUUID()): Promise<AnswerFeedbackView> {
    return this.post(`/v1/study-sessions/${encodeURIComponent(id)}/assessments/${encodeURIComponent(revision)}/submissions`, body, key, feedback);
  }

  getLearnerProgress(id: string): Promise<LearnerProgressView> {
    return this.json(`/v1/study-sessions/${encodeURIComponent(id)}/progress`, { method: "GET" }, progress);
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
