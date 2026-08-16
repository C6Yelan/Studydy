import type {
  ApiErrorView,
  ApiReasonCode,
  AssessmentRequest,
  AssessmentView,
  KnowledgeMapRequest,
  KnowledgeMapView,
  KnownApiReasonCode,
  LearningResourceRequest,
  LearningResourceResultView,
  LearningStateRequest,
  LearningStateView,
  LearningUpdateCreate,
  MaterialOutputBinding,
  MaterialProcessingCreate,
  MaterialProcessingRunView,
  MaterialView,
} from "./contracts";

type FetchRequest = (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;
type JsonReader<T> = (value: unknown) => value is T;
type JsonObject = Record<string, unknown>;

const knownApiReasons = new Set<KnownApiReasonCode>([
  "REQUEST_INVALID",
  "SESSION_REQUIRED",
  "ORIGIN_NOT_ALLOWED",
  "RESOURCE_NOT_FOUND",
  "IDEMPOTENCY_CONFLICT",
  "MATERIAL_TOO_LARGE",
  "UNSUPPORTED_MEDIA_TYPE",
  "STORAGE_UNAVAILABLE",
  "INTERNAL_ERROR",
]);

const uuidPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const sha256Pattern = /^[0-9a-f]{64}$/;
const maximumPdfBytes = 100 * 1024 * 1024;

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

export type LearningStateSubmission = {
  state: LearningStateView;
  replayed: boolean;
};

function readJsonObject(value: unknown): JsonObject | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  return value as JsonObject;
}

function readClosedObject(
  value: unknown,
  requiredKeys: readonly string[],
  optionalKeys: readonly string[] = [],
): JsonObject | null {
  const object = readJsonObject(value);
  if (!object) return null;
  const allowedKeys = new Set([...requiredKeys, ...optionalKeys]);
  if (Object.keys(object).some((key) => !allowedKeys.has(key))) return null;
  if (requiredKeys.some((key) => !Object.hasOwn(object, key))) return null;
  return object;
}

function isString(value: unknown): value is string {
  return typeof value === "string";
}

function isUuid(value: unknown): value is string {
  return isString(value) && uuidPattern.test(value);
}

function isRevision(value: unknown, prefix: string): value is string {
  return isString(value) && value.startsWith(`${prefix}:sha256:`) && sha256Pattern.test(value.slice(prefix.length + 8));
}

function isPublicResourceLocator(value: unknown): value is string {
  if (!isString(value) || !value || /\s/.test(value)) return false;
  if (isRevision(value, "artifact")) return true;
  try {
    const locator = new URL(value);
    return (locator.protocol === "http:" || locator.protocol === "https:")
      && !!locator.hostname
      && !locator.username
      && !locator.password;
  } catch {
    return false;
  }
}

function isMaterialView(value: unknown): value is MaterialView {
  const object = readJsonObject(value);
  return !!object
    && object.schema === "material/v1"
    && isUuid(object.material_id)
    && isUuid(object.source_artifact_id)
    && isString(object.source_sha256)
    && sha256Pattern.test(object.source_sha256)
    && typeof object.size_bytes === "number"
    && Number.isInteger(object.size_bytes);
}

function isMaterialOutputBinding(value: unknown): value is MaterialOutputBinding {
  const object = readJsonObject(value);
  return !!object
    && object.schema === "material-run-output-binding/v1"
    && isRevision(object.study_material_output_revision, "study-material-output")
    && isRevision(object.catalog_revision, "resource-catalog")
    && isRevision(object.learning_resource_result_revision, "learning-resource-result")
    && isRevision(object.knowledge_map_revision, "knowledge-map")
    && isRevision(object.learning_path_revision, "initial-learning-path")
    && isRevision(object.assessment_revision, "assessment")
    && (object.processing === "succeeded" || object.processing === "partial");
}

function isMaterialRun(value: unknown): value is MaterialProcessingRunView {
  const object = readJsonObject(value);
  if (!object || object.schema !== "material-processing-run/v1") return false;
  if (!isUuid(object.run_id) || !isUuid(object.material_id) || !isUuid(object.source_artifact_id)) return false;
  if (!isString(object.created_at) || !isString(object.updated_at)) return false;
  if (object.completed_at !== null && !isString(object.completed_at)) return false;
  if (object.error_code !== null && !isString(object.error_code)) return false;
  if (
    object.catalog_revision !== undefined
    && object.catalog_revision !== null
    && !isRevision(object.catalog_revision, "resource-catalog")
  ) return false;
  if (object.output_binding !== null && !isMaterialOutputBinding(object.output_binding)) return false;
  const binding = object.output_binding as MaterialOutputBinding | null;
  if (binding && object.catalog_revision !== binding.catalog_revision) return false;
  if (object.status === "succeeded" || object.status === "partial") {
    return binding !== null
      && binding.processing === object.status
      && object.error_code === null
      && isString(object.completed_at);
  }
  if (object.status === "failed") {
    return binding === null
      && isString(object.error_code)
      && isString(object.completed_at);
  }
  if (object.status === "pending" || object.status === "running") {
    return binding === null && object.error_code === null && object.completed_at === null;
  }
  return false;
}

function hasMatchingMaterialEvidence(value: unknown, materialRef: string): boolean {
  const object = readJsonObject(value);
  return !!object
    && Array.isArray(object.evidence)
    && object.evidence.every((evidence) => readJsonObject(evidence)?.material_ref === materialRef);
}

function isKnowledgeMap(value: unknown): value is KnowledgeMapView {
  const object = readJsonObject(value);
  if (!object || object.schema !== "knowledge-map-view/v1" || !isString(object.material_ref)) return false;
  if (!isRevision(object.knowledge_map_revision, "knowledge-map")) return false;
  if (!isRevision(object.learning_path_revision, "initial-learning-path")) return false;
  if (!Array.isArray(object.concepts) || !Array.isArray(object.relations) || !Array.isArray(object.review_items)) {
    return false;
  }
  return [...object.concepts, ...object.relations, ...object.review_items]
    .every((item) => hasMatchingMaterialEvidence(item, object.material_ref as string));
}

function hasPrivateResourceLocator(object: JsonObject): boolean {
  return Object.keys(object).some((key) =>
    key.endsWith("_path") || key.startsWith("private_") || key === "storage_locator"
  );
}

function containsPrivateResourceLocator(value: unknown): boolean {
  if (Array.isArray(value)) return value.some(containsPrivateResourceLocator);
  const object = readJsonObject(value);
  if (!object) return false;
  if (hasPrivateResourceLocator(object)) return true;
  if (Object.hasOwn(object, "source_locator") && !isPublicResourceLocator(object.source_locator)) return true;
  return Object.values(object).some(containsPrivateResourceLocator);
}

function isLearningResourceResult(value: unknown): value is LearningResourceResultView {
  const object = readJsonObject(value);
  return !!object
    && object.schema === "learning-resource-result-view/v1"
    && isRevision(object.result_revision, "learning-resource-result")
    && isRevision(object.source_study_material_output_revision, "study-material-output")
    && isRevision(object.catalog_revision, "resource-catalog")
    && isUuid(object.run_id)
    && Array.isArray(object.resources)
    && !containsPrivateResourceLocator(object);
}

function containsAssessmentAnswerKey(value: unknown): boolean {
  if (Array.isArray(value)) return value.some(containsAssessmentAnswerKey);
  const object = readJsonObject(value);
  if (!object) return false;
  if (Object.keys(object).some((key) =>
    key.includes("answer_key") || key.startsWith("correct_answer") || key.startsWith("correct_option")
  )) return true;
  return Object.values(object).some(containsAssessmentAnswerKey);
}

function isAssessment(value: unknown): value is AssessmentView {
  const object = readJsonObject(value);
  return !!object
    && object.schema === "assessment-view/v1"
    && isRevision(object.assessment_view_id, "assessment-view")
    && isRevision(object.knowledge_map_revision, "knowledge-map")
    && isRevision(object.learning_path_revision, "initial-learning-path")
    && Array.isArray(object.questions)
    && Array.isArray(object.practice_sets)
    && !containsAssessmentAnswerKey(object);
}

function containsPrivateLearningField(value: unknown): boolean {
  if (Array.isArray(value)) return value.some(containsPrivateLearningField);
  const object = readJsonObject(value);
  if (!object) return false;
  if (Object.keys(object).some((key) =>
    key.startsWith("learner_")
    || key === "user_id"
    || key === "assessment_score"
    || key.includes("answer_key")
  )) return true;
  return Object.values(object).some(containsPrivateLearningField);
}

function isLearningState(value: unknown): value is LearningStateView {
  const object = readJsonObject(value);
  return !!object
    && object.schema === "learning-state-view/v1"
    && isRevision(object.state_revision, "learning-state")
    && isRevision(object.knowledge_map_revision, "knowledge-map")
    && isRevision(object.learning_path_revision, "initial-learning-path")
    && isRevision(object.assessment_id, "assessment")
    && isRevision(object.assessment_revision, "assessment")
    && Array.isArray(object.mastery)
    && Array.isArray(object.weaknesses)
    && !!readJsonObject(object.suggestion)
    && !containsPrivateLearningField(object);
}

function isApiError(value: unknown): value is ApiErrorView {
  const object = readJsonObject(value);
  return !!object
    && object.schema === "api-error/v1"
    && isUuid(object.request_id)
    && isString(object.reason_code)
    && typeof object.retryable === "boolean"
    && object.message === "Request could not be completed.";
}

function safeErrorMessage(reasonCode: ApiReasonCode): string {
  if (reasonCode === "SESSION_REQUIRED") return "工作階段已失效，請重新整理後再試。";
  if (reasonCode === "RESOURCE_NOT_FOUND") return "找不到這筆資料，或你沒有權限讀取。";
  if (reasonCode === "MATERIAL_TOO_LARGE") return "PDF 不可超過 100 MiB。";
  if (reasonCode === "UNSUPPORTED_MEDIA_TYPE") return "只接受 PDF 教材。";
  if (reasonCode === "STORAGE_UNAVAILABLE") return "資料服務暫時無法使用，請稍後再試。";
  return "目前無法完成請求，請稍後再試。";
}

async function readApiError(response: Response): Promise<ApiClientError> {
  let body: unknown;
  try {
    body = await response.json();
  } catch {
    return new ApiClientError("schema", "伺服器回應格式不符。", {
      status: response.status,
      reasonCode: "RESPONSE_SCHEMA_MISMATCH",
    });
  }
  if (!isApiError(body)) {
    return new ApiClientError("schema", "伺服器回應格式不符。", {
      status: response.status,
      reasonCode: "RESPONSE_SCHEMA_MISMATCH",
    });
  }
  const reasonCode = knownApiReasons.has(body.reason_code as KnownApiReasonCode)
    ? body.reason_code as KnownApiReasonCode
    : "UNKNOWN_API_ERROR";
  return new ApiClientError("api", safeErrorMessage(reasonCode), {
    status: response.status,
    reasonCode,
    requestId: body.request_id,
    retryable: body.retryable,
  });
}

function assertApiPath(path: string): void {
  if (!path.startsWith("/v1/") || path.startsWith("//") || path.includes("://")) {
    throw new ApiClientError("input", "請求路徑無效。", {
      reasonCode: "REQUEST_INPUT_INVALID",
    });
  }
}

export class StudydyApiClient {
  private readonly fetchRequest: FetchRequest;
  private sessionStart: Promise<void> | null = null;

  constructor(fetchRequest: FetchRequest = globalThis.fetch.bind(globalThis)) {
    this.fetchRequest = fetchRequest;
  }

  ensureSession(): Promise<void> {
    if (!this.sessionStart) {
      this.sessionStart = this.refreshOrCreateSession().catch((error: unknown) => {
        this.sessionStart = null;
        throw error;
      });
    }
    return this.sessionStart;
  }

  async createMaterial(pdf: Blob, userIntentKey: string = crypto.randomUUID()): Promise<MaterialView> {
    if (pdf.type !== "application/pdf") {
      throw new ApiClientError("input", "只接受 PDF 教材。", {
        reasonCode: "REQUEST_INPUT_INVALID",
      });
    }
    if (pdf.size === 0 || pdf.size > maximumPdfBytes) {
      throw new ApiClientError("input", pdf.size === 0 ? "PDF 不可為空白檔案。" : "PDF 不可超過 100 MiB。", {
        reasonCode: "REQUEST_INPUT_INVALID",
      });
    }
    return this.sendProtectedJson(
      "/v1/materials",
      {
        method: "POST",
        headers: {
          "Content-Type": "application/pdf",
          "Idempotency-Key": userIntentKey,
        },
        body: pdf,
      },
      201,
      isMaterialView,
    );
  }

  async createMaterialRun(
    request: MaterialProcessingCreate,
    userIntentKey: string = crypto.randomUUID(),
  ): Promise<MaterialProcessingRunView> {
    return this.sendProtectedJson(
      "/v1/material-processing-runs",
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Idempotency-Key": userIntentKey,
        },
        body: JSON.stringify(request),
      },
      202,
      isMaterialRun,
    );
  }

  async getMaterialRun(runId: string, signal?: AbortSignal): Promise<MaterialProcessingRunView> {
    return this.sendProtectedJson(
      `/v1/material-processing-runs/${encodeURIComponent(runId)}`,
      { method: "GET", signal },
      200,
      isMaterialRun,
    );
  }

  async getKnowledgeMap(request: KnowledgeMapRequest): Promise<KnowledgeMapView> {
    const path = [
      "/v1/materials",
      encodeURIComponent(request.materialId),
      "knowledge-map-views",
      encodeURIComponent(request.mapRevision),
      encodeURIComponent(request.pathRevision),
    ].join("/");
    const query = new URLSearchParams({ run_id: request.runId });
    const map = await this.sendProtectedJson(`${path}?${query}`, { method: "GET" }, 200, isKnowledgeMap);
    if (
      map.knowledge_map_revision !== request.mapRevision
      || map.learning_path_revision !== request.pathRevision
    ) {
      throw new ApiClientError("schema", "知識地圖版本與回應不一致。", {
        status: 200,
        reasonCode: "RESPONSE_SCHEMA_MISMATCH",
      });
    }
    return map;
  }

  async getLearningResourceResult(request: LearningResourceRequest): Promise<LearningResourceResultView> {
    const path = [
      "/v1/materials",
      encodeURIComponent(request.materialId),
      "learning-resource-results",
      encodeURIComponent(request.resultRevision),
    ].join("/");
    const query = new URLSearchParams({ run_id: request.runId });
    return this.sendProtectedJson(`${path}?${query}`, { method: "GET" }, 200, isLearningResourceResult);
  }

  sourceArtifactUrl(sourceArtifactId: string): string {
    if (!isUuid(sourceArtifactId)) {
      throw new ApiClientError("input", "教材來源識別碼無效。", {
        reasonCode: "REQUEST_INPUT_INVALID",
      });
    }
    return `/v1/artifacts/${encodeURIComponent(sourceArtifactId)}`;
  }

  async getSourceArtifact(sourceArtifactId: string, signal?: AbortSignal): Promise<Blob> {
    const path = this.sourceArtifactUrl(sourceArtifactId);
    await this.ensureSession();
    let response = await this.send(path, { method: "GET", signal });
    if (response.status === 401) {
      await this.recoverSession();
      response = await this.send(path, { method: "GET", signal });
    }
    if (response.status !== 200) throw await readApiError(response);
    const contentType = response.headers.get("Content-Type")?.split(";", 1)[0].trim().toLowerCase();
    if (contentType !== "application/pdf") {
      throw new ApiClientError("schema", "教材來源不是可安全開啟的 PDF。", {
        status: response.status,
        reasonCode: "RESPONSE_SCHEMA_MISMATCH",
      });
    }
    const pdf = await response.blob();
    if (pdf.size === 0) {
      throw new ApiClientError("schema", "來源 PDF 是空白內容。", {
        status: response.status,
        reasonCode: "RESPONSE_SCHEMA_MISMATCH",
      });
    }
    return pdf;
  }

  async getAssessment(request: AssessmentRequest): Promise<AssessmentView> {
    if (
      !isUuid(request.materialId)
      || !isRevision(request.outputRevision, "study-material-output")
      || !isRevision(request.mapRevision, "knowledge-map")
      || !isRevision(request.pathRevision, "initial-learning-path")
      || !isRevision(request.assessmentRevision, "assessment")
    ) {
      throw new ApiClientError("input", "評量識別資訊無效。", {
        reasonCode: "REQUEST_INPUT_INVALID",
      });
    }
    const path = [
      "/v1/materials",
      encodeURIComponent(request.materialId),
      "assessments",
      encodeURIComponent(request.assessmentRevision),
    ].join("/");
    const query = new URLSearchParams({
      output_revision: request.outputRevision,
      map_revision: request.mapRevision,
      path_revision: request.pathRevision,
    });
    const assessment = await this.sendProtectedJson(`${path}?${query}`, { method: "GET" }, 200, isAssessment);
    if (
      assessment.knowledge_map_revision !== request.mapRevision
      || assessment.learning_path_revision !== request.pathRevision
    ) {
      throw new ApiClientError("schema", "評量版本與回應不一致。", {
        status: 200,
        reasonCode: "RESPONSE_SCHEMA_MISMATCH",
      });
    }
    return assessment;
  }

  async submitLearningUpdate(
    update: LearningUpdateCreate,
    userIntentKey: string = crypto.randomUUID(),
  ): Promise<LearningStateSubmission> {
    const object = readClosedObject(update, [
      "schema",
      "material_id",
      "map_revision",
      "path_revision",
      "assessment_revision",
      "responses",
    ]);
    const responses = object?.responses;
    const hasValidResponses = Array.isArray(responses)
      && responses.length >= 1
      && responses.length <= 200
      && responses.every((response) => {
        const item = readClosedObject(response, ["question_id", "selected_option_id"]);
        return !!item && isString(item.question_id) && !!item.question_id
          && isString(item.selected_option_id) && !!item.selected_option_id;
      });
    if (
      !object
      || object.schema !== "learning-update-create/v1"
      || !isUuid(object.material_id)
      || !isRevision(object.map_revision, "knowledge-map")
      || !isRevision(object.path_revision, "initial-learning-path")
      || !isRevision(object.assessment_revision, "assessment")
      || !hasValidResponses
      || new Set((responses as Array<{ question_id: string }>).map((item) => item.question_id)).size !== responses.length
    ) {
      throw new ApiClientError("input", "作答內容或版本資訊無效。", {
        reasonCode: "REQUEST_INPUT_INVALID",
      });
    }
    const closedUpdate: LearningUpdateCreate = {
      schema: "learning-update-create/v1",
      material_id: object.material_id,
      map_revision: object.map_revision,
      path_revision: object.path_revision,
      assessment_revision: object.assessment_revision,
      responses: (responses as Array<{ question_id: string; selected_option_id: string }>).map((response) => ({
        question_id: response.question_id,
        selected_option_id: response.selected_option_id,
      })),
    };
    const response = await this.sendProtectedJsonResponse(
      `/v1/materials/${encodeURIComponent(closedUpdate.material_id)}/learning-states`,
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Idempotency-Key": userIntentKey,
        },
        body: JSON.stringify(closedUpdate),
      },
      [200, 201],
      isLearningState,
    );
    const state = response.body;
    if (
      state.knowledge_map_revision !== closedUpdate.map_revision
      || state.learning_path_revision !== closedUpdate.path_revision
      || state.assessment_revision !== closedUpdate.assessment_revision
    ) {
      throw new ApiClientError("schema", "學習狀態版本與目前流程不一致。", {
        status: response.status,
        reasonCode: "RESPONSE_SCHEMA_MISMATCH",
      });
    }
    return { state, replayed: response.status === 200 };
  }

  async getLearningState(request: LearningStateRequest): Promise<LearningStateView> {
    if (!isUuid(request.materialId) || !isRevision(request.stateRevision, "learning-state")) {
      throw new ApiClientError("input", "學習狀態識別資訊無效。", {
        reasonCode: "REQUEST_INPUT_INVALID",
      });
    }
    const state = await this.sendProtectedJson(
      `/v1/materials/${encodeURIComponent(request.materialId)}/learning-states/${encodeURIComponent(request.stateRevision)}`,
      { method: "GET" },
      200,
      isLearningState,
    );
    if (state.state_revision !== request.stateRevision) {
      throw new ApiClientError("schema", "學習狀態版本與網址不一致。", {
        status: 200,
        reasonCode: "RESPONSE_SCHEMA_MISMATCH",
      });
    }
    return state;
  }

  private async refreshOrCreateSession(): Promise<void> {
    try {
      await this.sendEmpty("/v1/session/refresh", { method: "POST" }, 204);
    } catch (error) {
      if (!(error instanceof ApiClientError) || error.status !== 401) throw error;
      await this.sendEmpty("/v1/session", { method: "POST" }, 204);
    }
  }

  private async recoverSession(): Promise<void> {
    this.sessionStart = null;
    await this.ensureSession();
  }

  private async sendProtectedJson<T>(
    path: string,
    init: RequestInit,
    expectedStatus: number,
    readJson: JsonReader<T>,
  ): Promise<T> {
    const response = await this.sendProtectedJsonResponse(path, init, [expectedStatus], readJson);
    return response.body;
  }

  private async sendProtectedJsonResponse<T>(
    path: string,
    init: RequestInit,
    expectedStatuses: readonly number[],
    readJson: JsonReader<T>,
  ): Promise<{ body: T; status: number }> {
    await this.ensureSession();
    try {
      return await this.sendJsonResponse(path, init, expectedStatuses, readJson);
    } catch (error) {
      if (!(error instanceof ApiClientError) || error.status !== 401) throw error;
      await this.recoverSession();
      return this.sendJsonResponse(path, init, expectedStatuses, readJson);
    }
  }

  private async sendEmpty(path: string, init: RequestInit, expectedStatus: number): Promise<void> {
    const response = await this.send(path, init);
    if (response.status === expectedStatus) return;
    throw await readApiError(response);
  }

  private async sendJsonResponse<T>(
    path: string,
    init: RequestInit,
    expectedStatuses: readonly number[],
    readJson: JsonReader<T>,
  ): Promise<{ body: T; status: number }> {
    const response = await this.send(path, init);
    if (!expectedStatuses.includes(response.status)) throw await readApiError(response);
    let body: unknown;
    try {
      body = await response.json();
    } catch {
      throw new ApiClientError("schema", "伺服器回應格式不符。", {
        status: response.status,
        reasonCode: "RESPONSE_SCHEMA_MISMATCH",
      });
    }
    if (!readJson(body)) {
      throw new ApiClientError("schema", "伺服器回應格式不符。", {
        status: response.status,
        reasonCode: "RESPONSE_SCHEMA_MISMATCH",
      });
    }
    return { body, status: response.status };
  }

  private async send(path: string, init: RequestInit): Promise<Response> {
    assertApiPath(path);
    const request = { ...init, credentials: "same-origin" as const };
    const method = request.method?.toUpperCase() ?? "GET";
    const hasIdempotencyKey = new Headers(request.headers).has("Idempotency-Key");
    const canRetryNetwork = method === "GET"
      || path === "/v1/session/refresh"
      || hasIdempotencyKey;
    try {
      return await this.fetchRequest(path, request);
    } catch {
      if (request.signal?.aborted) {
        throw new ApiClientError("network", "請求已取消或超過等待時間。", {
          reasonCode: "NETWORK_ERROR",
          retryable: true,
        });
      }
      if (!canRetryNetwork) {
        throw new ApiClientError("network", "無法連線到 Studydy，請檢查網路後再試。", {
          reasonCode: "NETWORK_ERROR",
          retryable: true,
        });
      }
      try {
        // 同一次送出的網路重試沿用原本的 Idempotency-Key，避免後端重複建立資料。
        return await this.fetchRequest(path, request);
      } catch {
        if (request.signal?.aborted) {
          throw new ApiClientError("network", "請求已取消或超過等待時間。", {
            reasonCode: "NETWORK_ERROR",
            retryable: true,
          });
        }
        throw new ApiClientError("network", "無法連線到 Studydy，請檢查網路後再試。", {
          reasonCode: "NETWORK_ERROR",
          retryable: true,
        });
      }
    }
  }
}
