import * as validate from "./response-validation.ts";
import type {
  ApiReasonCode,
  AssessmentPlanView,
  AssessmentSetAction,
  AssessmentSetAnswer,
  AssessmentSetListView,
  AssessmentSetView,
  EvidenceSourceView,
  GuidanceApply,
  KnowledgeStructureRequest,
  KnowledgeStructureView,
  KnownApiReasonCode,
  LearnerIdentity,
  LearnerProgressView,
  MaterialDiscardView,
  MaterialLibraryItem,
  MaterialLibraryView,
  MaterialProcessingRunView,
  MaterialRename,
  SourceCapabilities,
  SourceListView,
  StudyResumeView,
  StudySessionCreate,
  StudySessionFocus,
  StudySessionView,
} from "./contracts";

type FetchRequest = (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;

const genericApiMessage = "請求無法完成，請稍後再試。";
const apiErrorMessages: Record<KnownApiReasonCode, string> = {
  INVALID_EMAIL: "請輸入有效的 Email 格式。",
  INVALID_CREDENTIALS: "Email 或密碼不正確。",
  ACCOUNT_UNAVAILABLE: "這個 Email 已被使用，請使用其他 Email。",
  REQUEST_INVALID: genericApiMessage,
  SESSION_REQUIRED: "工作階段已失效，請重新登入。",
  ORIGIN_NOT_ALLOWED: genericApiMessage,
  RESOURCE_NOT_FOUND: "找不到這筆資料，或你沒有權限讀取。",
  LEARNER_GUIDANCE_STALE: "學習進度已更新，請重新確認下一步。",
  IDEMPOTENCY_CONFLICT: genericApiMessage,
  ASSESSMENT_SET_CONFLICT: "題組狀態已更新，請重新讀取後繼續。",
  ASSESSMENT_SET_ACTIVE: "已有尚未完成的題組，可從題組紀錄接續。",
  MATERIAL_TOO_LARGE: "每個檔案不可超過 100 MiB。",
  MATERIAL_NOT_DISCARDABLE: "這份教材正在刪除，無法進行這項操作。",
  SOURCE_NOT_READY: "教材尚未完成轉換，請稍後再開始分析。",
  NORMALIZER_UNAVAILABLE: "轉換工具目前不可用，仍可使用 PDF 上傳。",
  DUPLICATE_SOURCE: "這份檔案已在教材來源清單中，請直接選取既有來源。",
  REVISION_CONFLICT: "教材已更新，請重新讀取目前地圖與來源清單。",
  REVISION_IN_PROGRESS: "這份教材已有更新正在處理，請先查看該次處理。",
  SOURCE_IN_USE: "這份來源已被分析引用，無法刪除；可取消勾選，不加入這次更新。",
  SOURCE_BUSY: "教材仍在轉換中，完成後才可移除。",
  UNSUPPORTED_MEDIA_TYPE: "此檔案格式目前不支援，請優先使用 PDF。",
  STORAGE_UNAVAILABLE: "資料服務暫時無法使用，請稍後再試。",
  INTERNAL_ERROR: genericApiMessage,
};

const authTimeoutMs = 10_000;

function origin(): string {
  return globalThis.location?.origin ?? "http://127.0.0.1:4173";
}

function safeMessage(reason: ApiReasonCode): string {
  return reason === "UNKNOWN_API_ERROR" ? genericApiMessage : apiErrorMessages[reason];
}

export class ApiClientError extends Error {
  readonly kind: "api" | "network" | "schema" | "input";
  readonly details: {
    status?: number;
    reasonCode: string;
    requestId?: string;
    retryable?: boolean;
  };

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

  get status(): number | null {
    return this.details.status ?? null;
  }
  get reasonCode(): string {
    return this.details.reasonCode;
  }
  get requestId(): string | null {
    return this.details.requestId ?? null;
  }
  get retryable(): boolean {
    return this.details.retryable ?? false;
  }
}

function schemaMismatch(message: string, details: { status?: number } = {}): ApiClientError {
  return new ApiClientError("schema", message, {
    ...details,
    reasonCode: "RESPONSE_SCHEMA_MISMATCH",
  });
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
    if (!this.active)
      throw new ApiClientError("api", "工作階段已結束，請重新登入。", {
        reasonCode: "SESSION_REQUIRED",
      });
  }

  async ensureSession(): Promise<LearnerIdentity> {
    this.requireActive();
    if (!this.sessionReady) {
      this.sessionReady = this.json(
        "/v1/session/refresh",
        { method: "POST", headers: { Origin: origin() } },
        validate.identity,
        authTimeoutMs,
      ).finally(() => {
        this.sessionReady = null;
      });
    }
    return this.sessionReady;
  }

  authenticate(
    mode: "login" | "register",
    email: string,
    password: string,
  ): Promise<LearnerIdentity> {
    return this.json(
      mode === "register" ? "/v1/accounts" : "/v1/session/login",
      {
        method: "POST",
        headers: { "Content-Type": "application/json", Origin: origin() },
        body: JSON.stringify({ email, password }),
      },
      validate.identity,
      authTimeoutMs,
    );
  }

  async logout(): Promise<void> {
    await this.request(
      "/v1/session",
      { method: "DELETE", headers: { Origin: origin() } },
      authTimeoutMs,
    );
  }

  private async request(
    path: string,
    init: RequestInit,
    timeoutMs?: number,
  ): Promise<{ status: number; value: unknown }> {
    this.requireActive();
    const controller = new AbortController();
    this.pending.add(controller);
    let timedOut = false;
    const cancellationError = () =>
      timedOut
        ? new ApiClientError("network", "連線逾時，請再試一次。", {
            reasonCode: "REQUEST_TIMEOUT",
            retryable: true,
          })
        : new ApiClientError("api", "工作階段已結束，請重新登入。", {
            reasonCode: "SESSION_REQUIRED",
          });
    let onAbort!: () => void;
    const cancelled = new Promise<never>((_resolve, reject) => {
      onAbort = () => reject(cancellationError());
      controller.signal.addEventListener("abort", onAbort, { once: true });
    });
    const timer =
      timeoutMs === undefined
        ? undefined
        : setTimeout(() => {
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
        response = await this.fetchRequest(path, {
          ...init,
          credentials: "same-origin",
          cache: "no-store",
          signal: controller.signal,
        });
      } catch {
        checkActive();
        throw new ApiClientError("network", "無法連線到 Studydy。", {
          reasonCode: "NETWORK_ERROR",
          retryable: true,
        });
      }
      checkActive();
      let value: unknown = null;
      if (response.status !== 204) {
        try {
          value = await response.json();
        } catch {
          /* 依 HTTP 狀態區分格式錯誤與服務故障。 */
        }
      }
      checkActive();
      if (response.ok) return { status: response.status, value };
      if (!validate.apiError(value)) {
        if (response.status >= 500)
          throw new ApiClientError("network", "Studydy 服務暫時無法使用，請稍後再試。", {
            status: response.status,
            reasonCode: "SERVICE_UNAVAILABLE",
            retryable: true,
          });
        throw schemaMismatch("伺服器回應格式無法辨識。", { status: response.status });
      }
      if (response.status === 401 && value.reason_code === "SESSION_REQUIRED") {
        // 取消其他請求，保留這次回應的正式錯誤資訊。
        this.pending.delete(controller);
        this.invalidate();
        this.onSessionExpired?.();
      }
      const reason = Object.hasOwn(apiErrorMessages, value.reason_code)
        ? (value.reason_code as KnownApiReasonCode)
        : "UNKNOWN_API_ERROR";
      throw new ApiClientError("api", safeMessage(reason), {
        status: response.status,
        reasonCode: reason,
        requestId: value.request_id,
        retryable: value.retryable,
      });
    };
    try {
      return await Promise.race([read(), cancelled]);
    } finally {
      clearTimeout(timer);
      controller.signal.removeEventListener("abort", onAbort);
      this.pending.delete(controller);
    }
  }

  private async json<T>(
    path: string,
    init: RequestInit,
    guard: (value: unknown) => value is T,
    timeoutMs?: number,
  ): Promise<T> {
    const { status, value } = await this.request(path, init, timeoutMs);
    this.requireActive();
    if (!guard(value)) throw schemaMismatch("伺服器回應格式無法辨識。", { status });
    return value;
  }

  private post<T>(
    path: string,
    body: unknown,
    key: string,
    guard: (value: unknown) => value is T,
  ): Promise<T> {
    return this.json(
      path,
      {
        method: "POST",
        headers: { "Content-Type": "application/json", Origin: origin(), "Idempotency-Key": key },
        body: JSON.stringify(body),
      },
      guard,
    );
  }

  sourceCapabilities(): Promise<SourceCapabilities> {
    return this.json("/v1/source-capabilities", { method: "GET" }, validate.capabilities);
  }
  createDraft(
    name: string,
    key: string,
  ): Promise<{ schema: "material-draft/v1"; material_id: string }> {
    return this.post(
      "/v1/materials",
      { schema: "material-draft-create/v1", display_name: name },
      key,
      validate.materialDraft,
    );
  }
  uploadSource(
    materialId: string,
    file: File,
    mediaType: string,
    key: string,
  ): Promise<SourceListView> {
    return this.json(
      `/v1/materials/${encodeURIComponent(materialId)}/sources`,
      {
        method: "POST",
        body: file,
        headers: {
          "Content-Type": mediaType,
          Origin: origin(),
          "Idempotency-Key": key,
          "X-Material-Name": encodeURIComponent(file.name),
        },
      },
      validate.sourceList,
    );
  }
  getSources(materialId: string): Promise<SourceListView> {
    return this.json(
      `/v1/materials/${encodeURIComponent(materialId)}/sources`,
      { method: "GET" },
      validate.sourceList,
    );
  }
  removeStagedSource(materialId: string, sourceId: string): Promise<SourceListView> {
    return this.json(
      `/v1/materials/${encodeURIComponent(materialId)}/sources/${encodeURIComponent(sourceId)}`,
      { method: "DELETE", headers: { Origin: origin() } },
      validate.sourceList,
    );
  }
  retryNormalization(materialId: string, id: string): Promise<SourceListView> {
    return this.json(
      `/v1/materials/${encodeURIComponent(materialId)}/sources/${encodeURIComponent(id)}/retry`,
      { method: "POST", headers: { Origin: origin() } },
      validate.sourceList,
    );
  }
  createRevision(
    materialId: string,
    normalizationIds: string[],
    key: string,
    baseRevision: string | null = null,
  ): Promise<MaterialProcessingRunView> {
    return this.post(
      `/v1/materials/${encodeURIComponent(materialId)}/revisions`,
      {
        schema: "material-revision-create/v1",
        base_revision: baseRevision,
        normalization_ids: normalizationIds,
      },
      key,
      validate.materialRun,
    );
  }
  cancelRevision(runId: string, baseRevision: string): Promise<MaterialProcessingRunView> {
    return this.post(
      `/v1/material-processing-runs/${encodeURIComponent(runId)}/cancel`,
      { schema: "material-revision-cancel/v1", base_revision: baseRevision },
      crypto.randomUUID(),
      validate.materialRun,
    );
  }
  retryRevision(runId: string, key: string): Promise<MaterialProcessingRunView> {
    return this.json(
      `/v1/material-processing-runs/${encodeURIComponent(runId)}/retry`,
      { method: "POST", headers: { Origin: origin(), "Idempotency-Key": key } },
      validate.materialRun,
    );
  }
  resolveEvidence(base: string, evidenceId: string): Promise<EvidenceSourceView> {
    if (!base.startsWith("/v1/materials/")) throw new Error("SOURCE_ROUTE_INVALID");
    return this.json(
      `${base}/${encodeURIComponent(evidenceId)}/source`,
      { method: "GET" },
      validate.evidenceSource,
    );
  }

  listMaterials(): Promise<MaterialLibraryView> {
    return this.json("/v1/materials", { method: "GET" }, validate.library);
  }

  async getMaterial(materialId: string): Promise<MaterialLibraryItem> {
    const item = await this.json(
      `/v1/materials/${encodeURIComponent(materialId)}`,
      { method: "GET" },
      validate.libraryItem,
    );
    if (item.material_id !== materialId) throw schemaMismatch("教材身分不一致。");
    return item;
  }

  getMaterialRun(runId: string): Promise<MaterialProcessingRunView> {
    return this.json(
      `/v1/material-processing-runs/${encodeURIComponent(runId)}`,
      { method: "GET" },
      validate.materialRun,
    );
  }

  async renameMaterial(materialId: string, displayName: string): Promise<MaterialLibraryItem> {
    const item = await this.json(
      `/v1/materials/${encodeURIComponent(materialId)}/rename`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json", Origin: origin() },
        body: JSON.stringify({
          schema: "material-rename/v1",
          display_name: displayName,
        } satisfies MaterialRename),
      },
      validate.libraryItem,
    );
    if (item.material_id !== materialId) throw schemaMismatch("教材身分不一致。");
    return item;
  }

  async discardMaterial(materialId: string): Promise<MaterialDiscardView> {
    const view = await this.json(
      `/v1/materials/${encodeURIComponent(materialId)}`,
      {
        method: "DELETE",
        headers: { Origin: origin() },
      },
      validate.materialDiscard,
      10_000,
    );
    if (view.material_id !== materialId) throw schemaMismatch("教材身分不一致。");
    return view;
  }

  async getKnowledgeStructure(request: KnowledgeStructureRequest): Promise<KnowledgeStructureView> {
    const view = await this.json(
      `/v1/materials/${encodeURIComponent(request.materialId)}/knowledge-structures/${encodeURIComponent(request.structureRevision)}`,
      { method: "GET" },
      validate.knowledgeStructure,
    );
    if (view.knowledge_structure_revision !== request.structureRevision)
      throw schemaMismatch("教材結構版本不一致。");
    return view;
  }

  async readProgress(id: string, structureRevision: string): Promise<LearnerProgressView> {
    const value = await this.json(
      `/v1/study-sessions/${encodeURIComponent(id)}/progress`,
      { method: "GET" },
      validate.progress,
    );
    if (value.study_session_id !== id || value.knowledge_structure_revision !== structureRevision) {
      throw schemaMismatch("學習進度與教材版本不一致。");
    }
    return value;
  }

  async applyGuidance(id: string, body: GuidanceApply): Promise<LearnerProgressView> {
    const value = await this.json(
      `/v1/study-sessions/${encodeURIComponent(id)}/guidance/apply`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json", Origin: origin() },
        body: JSON.stringify(body),
      },
      validate.progress,
    );
    if (value.study_session_id !== id) throw schemaMismatch("學習進度身分不一致。");
    return value;
  }

  async focusStudySession(
    studySessionId: string,
    currentConceptId: string,
  ): Promise<StudySessionView> {
    const state = await this.json(
      `/v1/study-sessions/${encodeURIComponent(studySessionId)}/focus`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json", Origin: origin() },
        body: JSON.stringify({
          schema: "study-session-focus/v1",
          current_concept_id: currentConceptId,
        } satisfies StudySessionFocus),
      },
      validate.studySession,
    );
    if (state.study_session_id !== studySessionId) throw schemaMismatch("學習進度身分不一致。");
    return state;
  }

  createStudySession(
    body: StudySessionCreate,
    key: string = crypto.randomUUID(),
  ): Promise<StudySessionView> {
    return this.post("/v1/study-sessions", body, key, validate.studySession);
  }

  async resumeStudy(request: {
    materialId: string;
    structureRevision: string;
    studySessionId: string;
    runId: string;
    assessmentSetId?: string;
  }): Promise<StudyResumeView> {
    const query = new URLSearchParams({ run_id: request.runId });
    if (request.assessmentSetId) query.set("set_id", request.assessmentSetId);
    let restored: StudyResumeView;
    // Worker 可能恰好發布題目；僅對 snapshot 衝突補讀，絕不重播建立或作答。
    for (let attempt = 0; ; attempt += 1) {
      try {
        restored = await this.json(
          `/v1/materials/${encodeURIComponent(request.materialId)}/knowledge-structures/${encodeURIComponent(request.structureRevision)}/study-sessions/${encodeURIComponent(request.studySessionId)}/resume?${query}`,
          { method: "GET" },
          validate.studyResume,
        );
        break;
      } catch (error) {
        if (
          !(error instanceof ApiClientError) ||
          error.reasonCode !== "IDEMPOTENCY_CONFLICT" ||
          attempt >= 2
        )
          throw error;
        await new Promise((resolve) => setTimeout(resolve, 100 * (attempt + 1)));
      }
    }
    if (
      restored.session.material_id !== request.materialId ||
      restored.session.study_session_id !== request.studySessionId ||
      restored.session.knowledge_structure_revision !== request.structureRevision ||
      restored.run_id !== request.runId ||
      (request.assessmentSetId !== undefined &&
        restored.selected_set_id !== request.assessmentSetId)
    ) {
      throw schemaMismatch("學習紀錄與教材版本不一致。");
    }
    return restored;
  }

  async readAssessmentPlan(id: string, conceptId: string): Promise<AssessmentPlanView> {
    const value = await this.json(
      `/v1/study-sessions/${encodeURIComponent(id)}/assessment-plan?concept_id=${encodeURIComponent(conceptId)}`,
      { method: "GET" },
      validate.assessmentPlan,
    );
    if (value.study_session_id !== id || value.concept_id !== conceptId)
      throw schemaMismatch("題組範圍不一致。");
    return value;
  }

  async listAssessmentSets(id: string): Promise<AssessmentSetListView> {
    const value = await this.json(
      `/v1/study-sessions/${encodeURIComponent(id)}/assessment-sets`,
      { method: "GET" },
      validate.assessmentSetList,
    );
    if (value.study_session_id !== id) throw schemaMismatch("題組範圍不一致。");
    return value;
  }

  async readAssessmentSet(id: string, setId: string): Promise<AssessmentSetView> {
    const value = await this.json(
      `/v1/study-sessions/${encodeURIComponent(id)}/assessment-sets/${encodeURIComponent(setId)}`,
      { method: "GET" },
      validate.assessmentSet,
    );
    if (value.study_session_id !== id || value.set_id !== setId)
      throw schemaMismatch("題組範圍不一致。");
    return value;
  }

  async createAssessmentSet(
    id: string,
    conceptId: string,
    key: string,
  ): Promise<AssessmentSetView> {
    const value = await this.post(
      `/v1/study-sessions/${encodeURIComponent(id)}/assessment-sets`,
      { schema: "assessment-set-create/v1", target_concept_id: conceptId },
      key,
      validate.assessmentSet,
    );
    if (value.study_session_id !== id || value.target_concept_id !== conceptId)
      throw schemaMismatch("題組範圍不一致。");
    return value;
  }

  async changeAssessmentSet(
    id: string,
    setId: string,
    action: AssessmentSetAction,
    version: number,
    key: string,
  ): Promise<AssessmentSetView> {
    const value = await this.post(
      `/v1/study-sessions/${encodeURIComponent(id)}/assessment-sets/${encodeURIComponent(setId)}/${action}`,
      { schema: "assessment-set-action/v1", expected_set_version: version },
      key,
      validate.assessmentSet,
    );
    if (value.study_session_id !== id || value.set_id !== setId)
      throw schemaMismatch("題組範圍不一致。");
    return value;
  }

  async submitAssessmentSet(
    id: string,
    setId: string,
    answers: AssessmentSetAnswer[],
    version: number,
    key: string,
  ): Promise<AssessmentSetView> {
    const value = await this.post(
      `/v1/study-sessions/${encodeURIComponent(id)}/assessment-sets/${encodeURIComponent(setId)}/submissions`,
      { schema: "assessment-set-submission/v1", expected_set_version: version, answers },
      key,
      validate.assessmentSet,
    );
    const expected = new Map(answers.map((answer) => [answer.assessment_revision, answer]));
    if (
      value.study_session_id !== id ||
      value.set_id !== setId ||
      value.status !== "completed" ||
      value.answered_count !== value.published_count ||
      value.published_count !== answers.length ||
      value.items.some(
        (item) =>
          item.assessment &&
          (item.feedback === null ||
            item.feedback.question_id !==
              expected.get(item.assessment.assessment_revision)?.question_id ||
            item.feedback.selected_option_id !==
              expected.get(item.assessment.assessment_revision)?.selected_option_id),
      )
    )
      throw schemaMismatch("題組範圍不一致。");
    return value;
  }

  async createRemediationSet(
    id: string,
    rootId: string,
    version: number,
    key: string,
  ): Promise<AssessmentSetView> {
    const value = await this.post(
      `/v1/study-sessions/${encodeURIComponent(id)}/assessment-sets/${encodeURIComponent(rootId)}/remediation`,
      { schema: "assessment-set-action/v1", expected_set_version: version },
      key,
      validate.assessmentSet,
    );
    if (
      value.study_session_id !== id ||
      value.diagnostic_set_id !== rootId ||
      value.kind !== "remediation"
    )
      throw schemaMismatch("題組範圍不一致。");
    return value;
  }
}

export function errorMessage(error: unknown): string {
  return error instanceof ApiClientError ? error.message : "發生未預期的錯誤，請稍後再試。";
}
