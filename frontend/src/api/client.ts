import { browserId } from "./browser-id.ts";
import * as validate from "./response-validation.ts";
import type {
  MaterialNotesView, NoteKind, AssessmentPresence, CelebrationClaim,
  LearningGoalView, LearningGoalsView,
  StartingLevel, StartingLevelView, StudyStartView,
  LearningDiagnosisView, ConceptDifficultyView,
  KnowledgeOutlineView, KnowledgeConceptsView, KnowledgeRegionsView, KnowledgeSearchView, LearningActivityView, LearningRhythmPeriod, LearningRhythmView, MapProgressView, ProgressSummaryView, PodcastProgressView, PodcastProgressUpdate, StudyWorkspaceView,
  PodcastContext, PodcastSummary, PodcastView, PodcastCreate, PodcastAction, PodcastListView, PodcastDeletedView,
  ApiReasonCode,
  CardSetCreate,
  CardSetUpdate,
  CardSetSummary,
  CardSetView,
  CardStudyView, CardBatch,
  CardGeneration,
  CardSetListView,
  CardSetDeletedView,
  CardReviewsView,
  CardReviewRating,
  CardReviewView,
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
  LearnerProfile,
  AccountStorage,
  AccountSessions,
  LibrarySearchView,
  LearnerProgressView,
  MaterialDiscardView,
  MaterialLibraryItem,
  MaterialLibraryView,
  MaterialProcessingRunView,
  MaterialRename,
  SourceCapabilities,
  SourceListView,
  SourceUploadProgress,
  StudyResumeView,
  StudySessionCreate,
  StudySessionFocus,
  StudySessionView,
  VersionMoveCreate,
  VersionMovePreview, VersionMoveNoticeView, VersionMoveReturn,
  VersionMoveResult,
} from "./contracts";

type FetchRequest = (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;

const genericApiMessage = "請求無法完成，請稍後再試。";
const apiErrorMessages: Record<KnownApiReasonCode, string> = {
  LEARNING_GOAL_PUBLISHED: "教材已建立，請回到教材頁更改學習目標。",
  LEARNING_GOAL_STALE: "教材或目標已更新，請重新讀取學習目標。",
  STARTING_LEVEL_REQUIRED: "請先選擇這份教材的起始程度。",
  VOICE_PODCAST_CONTEXT_INVALID: "這段 Podcast 已不可用，請重新選段或改用教材問答。",
  PODCAST_CONFLICT: "Podcast 狀態已更新，請重新讀取後操作。",
  PODCAST_SOURCE_INSUFFICIENT: "所選概念缺少可回查的重點來源，請重新選擇。",
  PODCAST_SOURCE_TOO_LARGE: "單一來源重點超出本集容量，請先選擇其他概念。",
  EXPORT_SIZE_LIMIT: "這份卡組的卡面總量超過 512 MiB，請減少概念後再匯出。",
  EXPORT_IN_PROGRESS: "這份卡組匯出仍在處理或下載，請稍後重試。",
  EXPORT_ACTIVE_LIMIT: "同時最多保留 2 份卡組匯出，請完成現有下載，或等暫存逾時清理後再試。",
  EXPORT_PAGE_LIMIT: "卡面超過匯出上限：每個概念最多 16 張、整份最多 2000 張。",
  CARD_SET_CONFLICT: "卡組已在其他頁面更新，請重新讀取後再編輯。",
  CARD_REVIEW_CONFLICT: "這張卡已在其他頁面標記過，已重新讀取最新標記。",
  VERSION_MOVE_PREVIEW_STALE: "可返回的內容剛有變動，已重新讀取，請再選一次。",
  VERSION_MOVE_PREVIOUS_UNAVAILABLE: "目前沒有可返回的更新前內容。",
  VERSION_MOVE_STALE: "卡組或學習進度剛有變動，已重新整理可更新的項目，請再確認一次。",
  INVALID_EMAIL: "請輸入有效的 Email 格式。",
  INVALID_CREDENTIALS: "Email 或密碼不正確。",
  ACCOUNT_UNAVAILABLE: "這個 Email 已被使用，請使用其他 Email。",
  CURRENT_PASSWORD_INVALID: "目前密碼不正確。",
  TOO_MANY_ATTEMPTS: "嘗試次數過多，請 15 分鐘後再試。",
  REQUEST_INVALID: genericApiMessage,
  SCENE_CONFLICT: "同步圖卡的狀態已變更，請重新讀取。",
  SCENE_ALIGNMENT_INVALID: "尚無法可靠對齊音訊，請重試。",
  SCENE_ALIGNMENT_FAILED: "同步圖卡準備未完成，原音訊仍可播放。",
  SCENE_SOURCE_CHANGED: "講稿或音訊已變更，請重新準備同步圖卡。",
  PODCAST_TIMELINE_NOT_READY: "時間軸處理中，請稍候。",
  PODCAST_TIMELINE_UNAVAILABLE: "這一集的時間軸無法使用。",
  PODCAST_TIMELINE_SOURCE_MISMATCH: "時間軸與本集原稿、音訊或來源不一致。",
  TOPIC_CONFLICT: "這個規劃的狀態已改變，請重新讀取後再操作。",
  TOPIC_PROVIDER_FAILED: "目前無法產生學習範圍，請重試。",
  ASSESSMENT_IN_PROGRESS: "作答期間暫停教材問答，請交卷後再試。",
  VOICE_TURN_IN_PROGRESS: "請等目前提問完成，或先取消它。",
  VOICE_PROVIDER_UNAVAILABLE: "語音服務暫時無法連線，已保存的對話仍可查看。",
  VOICE_PROVIDER_FAILED: "這次問答處理失敗，請重試。",
  VOICE_TRANSCRIPT_INVALID: "沒有辨識到清楚的語音，請再錄一次或改用文字。",
  VOICE_CONTEXT_TOO_LARGE: "這次問題與引用內容超過回答容量，請聚焦一個問題再試。",
  RETRIEVAL_INDEX_REQUIRED: "這份教材尚未具備新版檢索索引，請使用原始來源重新建立教材。",
  RETRIEVAL_INDEX_INVALID: "教材檢索索引無法核對，請重新建立教材。",
  MATERIAL_FORMAT_UNSUPPORTED: "這份舊版教材或對話不支援目前格式，請重新建立。",
  SOURCE_SCOPE_EMPTY: "尚未取得能加入教材的原文段落。",
  SOURCE_SCOPE_INVALID: "選讀範圍與來源版本不一致，請重新搜尋核對。",
  SOURCE_SCOPE_UNRESOLVED: "選讀原文無法定位回來源，尚未加入教材。",
  RESEARCH_CONTEXT_TOO_LARGE: "這次研究的單批內容超過處理容量，已保留完成部分。",
  RESEARCH_SEARCH_FAILED: "搜尋暫時無法完成，請重試。",
  RESEARCH_SCOPE_REQUIRED: "請先確認要查找的觀念與細節，再開始搜尋。",
  RESEARCH_SNAPSHOT_UNAVAILABLE: "無法讀取這次研究保留的原文，請重新研究。",
  RESEARCH_DOWNLOAD_FAILED: "來源目前無法下載，其他結果已保留。",
  RESEARCH_LICENSE_UNCONFIRMED: "無法確認這份來源的使用授權。",
  RESEARCH_URL_REJECTED: "這個來源網址無法安全取得。",
  SESSION_REQUIRED: "工作階段已失效，請重新登入。",
  ORIGIN_NOT_ALLOWED: genericApiMessage,
  RESOURCE_NOT_FOUND: "找不到這筆資料，或你沒有權限讀取。",
  LEARNER_GUIDANCE_STALE: "學習進度已更新，請重新確認下一步。",
  IDEMPOTENCY_CONFLICT: genericApiMessage,
  ASSESSMENT_SET_CONFLICT: "題組狀態已更新，請重新讀取後繼續。",
  ASSESSMENT_SET_ACTIVE: "已有尚未完成的題組，可從題組紀錄接續。",
  MATERIAL_TOO_LARGE: "檔案超過目前上傳上限，請依選檔區顯示的限制縮小檔案。",
  STORAGE_QUOTA_EXCEEDED: "帳號空間不足，請先刪除不需要的教材、Podcast 或影片。",
  STORAGE_BUSY: "同一帳號的其他新增正在處理，請稍後再試。",
  MATERIAL_NOT_DISCARDABLE: "這份教材正在刪除，無法進行這項操作。",
  SOURCE_NOT_READY: "教材尚未完成轉換，請稍後再開始分析。",
  NORMALIZER_UNAVAILABLE: "轉換工具目前不可用，仍可使用 PDF 上傳。",
  DUPLICATE_SOURCE: "這份檔案已在教材來源清單中，請直接選取既有來源。",
  REVISION_CONFLICT: "教材已更新，請重新讀取目前地圖與來源清單。",
  REVISION_IN_PROGRESS: "這份教材已有更新正在處理，請先查看該次處理。",
  SOURCE_IN_USE: "這份來源已被分析引用，無法刪除；可取消勾選，不加入這次更新。",
  SOURCE_BUSY: "教材仍在轉換中，完成後才可移除。",
  UNSUPPORTED_MEDIA_TYPE: "此檔案格式目前不支援，請優先使用 PDF。",
  UPLOAD_OFFSET_MISMATCH: "上傳中斷，請重試上傳。",
  MEDIA_TOO_LONG: "音訊或影片超過 2 小時上限，請剪短後再上傳。",
  MEDIA_UNREADABLE: "無法讀取這個音訊或影片檔，請確認檔案完整。",
  MEDIA_NO_AUDIO: "這個影片沒有聲音軌，無法轉成逐字稿。",
  YOUTUBE_URL_INVALID: "請貼上單支 YouTube 影片的網址。",
  YOUTUBE_PLAYLIST_REJECTED: "不支援播放清單，請貼上單支影片的網址。",
  YOUTUBE_LIVE: "不支援直播或尚未播出的影片，請等直播結束、影片處理完成後再加入。",
  YOUTUBE_MEMBERS_ONLY: "這是會員限定或需要登入才能觀看的影片，無法加入。",
  YOUTUBE_PRIVATE: "這是私人影片，無法加入。",
  YOUTUBE_AGE_RESTRICTED: "這部影片有年齡限制，無法加入。",
  YOUTUBE_UNAVAILABLE: "目前無法讀取這部 YouTube 影片，請確認網址或稍後再試。",
  STORAGE_UNAVAILABLE: "資料服務暫時無法使用，請稍後再試。",
  INTERNAL_ERROR: genericApiMessage,
};

const authTimeoutMs = 10_000;

const activityReuseMs = 30_000;

function origin(): string {
  return globalThis.location?.origin ?? "http://127.0.0.1:4173";
}

/** 以 1024 進位顯示容量（介面標示 MB／GB）。 */
export function formatBytes(bytes: number): string {
  const units = ["B", "KB", "MB", "GB"];
  let value = bytes, unit = 0;
  while (value >= 1024 && unit < units.length - 1) { value /= 1024; unit += 1; }
  return `${unit === 0 ? value : value.toFixed(value >= 100 ? 0 : 1).replace(/\.0$/, "")} ${units[unit]}`;
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
  private readonly structures = new Map<string, Promise<KnowledgeStructureView>>();
  private readonly outlines = new Map<string, Promise<KnowledgeOutlineView>>();
  private readonly conceptContexts = new Map<string, Promise<KnowledgeConceptsView>>();
  // 名稱同時顯示在頁首與首頁；同一登入共用一次讀取，儲存後通知訂閱者。
  private profile: Promise<LearnerProfile> | null = null;
  private readonly profileListeners = new Set<(profile: LearnerProfile) => void>();
  // 學習活動同時供頁首與首頁使用；短時間內共用一次讀取，作答、標記概念卡或收聽後標記過期。
  private activity: { task: Promise<LearningActivityView>; at: number } | null = null;
  private readonly activityListeners = new Set<(activity: LearningActivityView) => void>();
  private readonly pending = new Set<AbortController>();
  private readonly fetchRequest: FetchRequest;
  onSessionExpired: (() => void) | null = null;

  constructor(fetchRequest: FetchRequest = fetch.bind(globalThis)) {
    this.fetchRequest = fetchRequest;
  }

  invalidate(): void {
    this.active = false;
    this.structures.clear();
    this.outlines.clear();
    this.conceptContexts.clear();
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
    this.structures.clear();
    this.outlines.clear();
    this.conceptContexts.clear();
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
    download = false,
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
      if (download && response.ok && !response.redirected) {
        const disposition = response.headers.get("Content-Disposition") ?? "";
        const encoded = /filename\*=UTF-8''([^;]+)/i.exec(disposition)?.[1];
        const mediaType = (response.headers.get("Content-Type") ?? "").split(";")[0].trim();
        if (!/^attachment;/i.test(disposition) || !encoded || ![
          "application/pdf", "application/zip", "application/octet-stream", "text/markdown",
          "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "image/png", "audio/mpeg", "video/mp4",
        ].includes(mediaType)) throw schemaMismatch("下載回應格式無法辨識。");
        let filename: string;
        try { filename = decodeURIComponent(encoded); } catch { throw schemaMismatch("下載檔名無法辨識。"); }
        const blob = await response.blob();
        checkActive();
        return { status: response.status, value: { blob, filename } };
      }
      let value: unknown = null;
      if (response.status !== 204) {
        try {
          value = await response.json();
        } catch {
          /* 依 HTTP 狀態區分格式錯誤與服務故障。 */
        }
      }
      checkActive();
      if (response.ok && !response.redirected) return { status: response.status, value };
      // Edge 不保證產品 JSON；只顯示固定訊息，不渲染回應內容。
      if (response.redirected || (!validate.apiError(value) && [401, 403].includes(response.status)))
        throw new ApiClientError("api", "入口授權已失效或遭拒絕，請重新開啟網站並確認存取權限。", {
          status: response.status, reasonCode: "EDGE_ACCESS_REQUIRED",
        });
      if ([413, 429].includes(response.status) && !validate.apiError(value))
        throw new ApiClientError("api", response.status === 413 ? apiErrorMessages.MATERIAL_TOO_LARGE : "請求過於頻繁，請稍後再試。", {
          status: response.status,
          reasonCode: response.status === 413 ? "MATERIAL_TOO_LARGE" : "RATE_LIMITED",
          retryable: response.status === 429,
        });
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
      throw new ApiClientError("api", reason === "STORAGE_QUOTA_EXCEEDED" && value.storage
        ? `帳號空間不足（已用 ${formatBytes(value.storage.used_bytes)}／上限 ${formatBytes(value.storage.limit_bytes)}），這次會新增檔案而超過上限。請先刪除不需要的教材、Podcast 或影片。`
        : safeMessage(reason), {
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

  async downloadExport(path: string, body?: unknown): Promise<void> {
    const result = await this.request(path, {
      method: body === undefined ? "GET" : "POST",
      ...(body === undefined ? {} : { headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }),
    }, undefined, true);
    this.requireActive();
    const { blob, filename } = result.value as { blob: Blob; filename: string };
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = filename.replace(/[\\/:*?"<>|\u0000-\u001f]/g, "_");
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  async studyTools<T>(path: string, body?: unknown, key?: string, method?: string): Promise<T> {
    const verb = method ?? (body === undefined ? "GET" : "POST");
    const guarded = verb === "POST" && (/^\/v1\/voice-conversations\/[^/]+\/turns$/.test(path)
      || /^\/v1\/voice-conversations\/[^/]+\/turns\/[^/]+\/actions$/.test(path) && (body as { action?: string } | undefined)?.action === "retry");
    const result = await this.request(path, {method: verb,
      headers: {"Content-Type":"application/json", ...(key ? {"Idempotency-Key":key} : {}), ...(guarded ? {"X-Studydy-Browser-Id":browserId()} : {})},
      ...(body === undefined ? {} : {body:JSON.stringify(body)})}, 30000);
    if (!result.value || typeof result.value !== "object") throw schemaMismatch("無法讀取教材工具回應。");
    return result.value as T;
  }

  async voiceRecording(id: string, data: Blob, key: string, context?: PodcastContext): Promise<string> {
    const result = await this.request(`/v1/voice-conversations/${id}/recordings`, {method:"POST",
      headers:{"Content-Type":data.type,"Idempotency-Key":key,"X-Studydy-Browser-Id":browserId(),...(context?{"X-Studydy-Podcast-Context":JSON.stringify(context)}:{})},body:data},30000);
    if (!validate.voiceTurnReceipt(result.value)) throw schemaMismatch("無法確認錄音提問的身分。");
    return result.value.turn_id;
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

  listPodcasts(): Promise<PodcastListView> {
    return this.json("/v1/podcasts", { method: "GET" }, validate.podcastList);
  }

  getPodcast(id: string): Promise<PodcastView> {
    return this.json(`/v1/podcasts/${encodeURIComponent(id)}`, { method: "GET" },
      (value): value is PodcastView => validate.podcast(value) && value.podcast_id === id);
  }

  createPodcast(materialId: string, body: PodcastCreate, key: string): Promise<PodcastSummary> {
    return this.post(`/v1/materials/${encodeURIComponent(materialId)}/podcasts`, body, key,
      (value): value is PodcastSummary => validate.podcastSummary(value) && value.material_id === materialId
        && value.knowledge_structure_revision === body.knowledge_structure_revision);
  }

  podcastAction(id: string, body: PodcastAction): Promise<PodcastSummary | PodcastDeletedView> {
    return this.json(`/v1/podcasts/${encodeURIComponent(id)}/actions`, {
      method: "POST", headers: { "Content-Type": "application/json", Origin: origin() }, body: JSON.stringify(body),
    }, (value): value is PodcastSummary | PodcastDeletedView => body.action === "cancel" ? validate.podcastDeleted(value) && value.podcast_id === id : validate.podcastSummary(value) && value.podcast_id === id);
  }

  deletePodcast(id: string): Promise<PodcastDeletedView> {
    return this.json(`/v1/podcasts/${encodeURIComponent(id)}`, { method: "DELETE", headers: { Origin: origin() } },
      (value): value is PodcastDeletedView => validate.podcastDeleted(value) && value.podcast_id === id);
  }

  listCardSets(): Promise<CardSetListView> {
    return this.json("/v1/card-sets", { method: "GET" }, validate.cardSetList);
  }

  getCardSet(id: string): Promise<CardSetView> {
    return this.json(`/v1/card-sets/${encodeURIComponent(id)}`, { method: "GET" },
      (value): value is CardSetView => validate.cardSet(value) && value.card_set_id === id);
  }

  getCardGeneration(id: string): Promise<CardGeneration> {
    return this.json(`/v1/card-sets/${encodeURIComponent(id)}/generation`, { method: "GET" }, validate.cardGeneration);
  }

  getCardStudy(id: string): Promise<CardStudyView> {
    return this.json(`/v1/card-sets/${encodeURIComponent(id)}/study`, { method: "GET" },
      (value): value is CardStudyView => validate.cardStudy(value) && value.card_set_id === id);
  }

  getCardBatch(id: string, version: number, offset: number): Promise<CardBatch> {
    return this.json(`/v1/card-sets/${encodeURIComponent(id)}/cards?offset=${offset}&version=${version}`, { method: "GET" },
      (value): value is CardBatch => validate.cardBatch(value) && value.card_set_id === id && value.version === version && value.offset === offset);
  }

  saveConceptCounts(id: string, version: number, counts: Record<string, number>): Promise<CardSetSummary> {
    return this.json(`/v1/card-sets/${encodeURIComponent(id)}/count`, {
      method: "POST", headers: { "Content-Type": "application/json", Origin: origin() },
      body: JSON.stringify({ expected_version: version, concept_counts: counts, layout_policy: "visual-concept-card/v4" }),
    }, (value): value is CardSetSummary => validate.cardSetSummary(value) && value.card_set_id === id && value.version === version);
  }

  saveCardCount(id: string, version: number, count: number): Promise<CardSetSummary> {
    return this.json(`/v1/card-sets/${encodeURIComponent(id)}/count`, {
      method: "POST", headers: { "Content-Type": "application/json", Origin: origin() },
      body: JSON.stringify({ expected_version: version, card_count: count, layout_policy: "visual-concept-card/v4" }),
    }, (value): value is CardSetSummary => validate.cardSetSummary(value) && value.card_set_id === id
      && value.version === version && value.card_count !== null);
  }

  retryCardGeneration(id: string): Promise<CardGeneration> {
    return this.json(`/v1/card-sets/${encodeURIComponent(id)}/generation/retry`, {
      method: "POST", headers: { Origin: origin() },
    }, validate.cardGeneration);
  }

  createCardSet(materialId: string, body: CardSetCreate, key: string): Promise<CardSetSummary> {
    return this.post(`/v1/materials/${encodeURIComponent(materialId)}/card-sets`, body, key,
      (value): value is CardSetSummary => validate.cardSetSummary(value)
        && value.material_id === materialId
        && value.knowledge_structure_revision === body.knowledge_structure_revision);
  }

  updateCardSet(id: string, body: CardSetUpdate): Promise<CardSetSummary> {
    return this.json(`/v1/card-sets/${encodeURIComponent(id)}/update`, {
      method: "POST", headers: { "Content-Type": "application/json", Origin: origin() },
      body: JSON.stringify(body),
    }, (value): value is CardSetSummary => validate.cardSetSummary(value) && value.card_set_id === id);
  }

  /** 需要加強：指定卡組時另含該卡組的標記；指定教材時只計這份教材的卡組。 */
  getCardReviews(scope: { cardSetId?: string; materialId?: string } = {}): Promise<CardReviewsView> {
    const { cardSetId, materialId } = scope;
    const params = new URLSearchParams({ ...(cardSetId ? { card_set_id: cardSetId } : {}), ...(materialId ? { material_id: materialId } : {}) });
    const query = params.size ? `?${params}` : "";
    return this.json(`/v1/card-reviews${query}`, { method: "GET" },
      (value): value is CardReviewsView => validate.cardReviews(value)
        && (cardSetId ? [...value.cards, ...value.marks].every((item) => item.card_set_id === cardSetId) : !value.marks.length)
        && (!materialId || value.cards.every((item) => item.material_id === materialId)));
  }

  // key 由呼叫端在第一次標記時產生，網路失敗重送沿用同一個鍵，不會重複計次。
  async submitCardReview(cardSetId: string, conceptId: string, rating: CardReviewRating, expectedReviewCount: number, key: string): Promise<CardReviewView> {
    const value = await this.json(`/v1/card-sets/${encodeURIComponent(cardSetId)}/reviews`, {
      method: "POST", headers: { "Content-Type": "application/json", Origin: origin(), "Idempotency-Key": key },
      body: JSON.stringify({ schema: "card-review-submit/v1", concept_id: conceptId, rating, expected_review_count: expectedReviewCount }),
    }, (value): value is CardReviewView => validate.cardReview(value)
      && value.card_set_id === cardSetId && value.concept_id === conceptId && value.rating === rating
      && value.review_count === expectedReviewCount + 1);
    this.markActivityStale();
    return value;
  }

  deleteCardSet(id: string): Promise<CardSetDeletedView> {
    return this.json(`/v1/card-sets/${encodeURIComponent(id)}`, {
      method: "DELETE", headers: { Origin: origin() },
    }, (value): value is CardSetDeletedView => validate.cardSetDeleted(value) && value.card_set_id === id);
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
  /** 超過單次請求上限的檔案依序分段上傳；最後一段收齊後後端才建立來源。 */
  uploadSourcePart(
    materialId: string,
    file: Blob,
    name: string,
    mediaType: string,
    key: string,
    offset: number,
    total: number,
  ): Promise<SourceUploadProgress> {
    return this.json(
      `/v1/materials/${encodeURIComponent(materialId)}/source-parts`,
      {
        method: "POST",
        body: file,
        headers: {
          "Content-Type": mediaType,
          Origin: origin(),
          "Idempotency-Key": key,
          "X-Material-Name": encodeURIComponent(name),
          "X-Upload-Offset": String(offset),
          "X-Upload-Total": String(total),
        },
      },
      validate.sourceUploadProgress,
    );
  }
  addYoutubeSource(materialId: string, url: string, key: string): Promise<SourceListView> {
    return this.post(
      `/v1/materials/${encodeURIComponent(materialId)}/youtube-sources`,
      { schema: "youtube-source-create/v1", url },
      key,
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
  getVersionMove(materialId: string): Promise<VersionMovePreview> {
    return this.json(`/v1/materials/${encodeURIComponent(materialId)}/version-move`, { method: "GET" },
      (value): value is VersionMovePreview => validate.versionMovePreview(value) && value.material_id === materialId);
  }

  setVersionMoveNotice(materialId: string, dismissed: boolean): Promise<VersionMoveNoticeView> {
    return this.json(`/v1/materials/${encodeURIComponent(materialId)}/version-move/notice`, {
      method: "PUT", headers: { "Content-Type": "application/json", Origin: origin() },
      body: JSON.stringify({ schema: "version-move-notice/v1", dismissed }),
    }, (value): value is VersionMoveNoticeView => validate.versionMoveNotice(value) && value.dismissed === dismissed);
  }

  returnToPreviousEntries(materialId: string, expectedMoveId: string): Promise<VersionMoveReturn> {
    return this.json(`/v1/materials/${encodeURIComponent(materialId)}/version-move/return`, {
      method: "POST", headers: { "Content-Type": "application/json", Origin: origin() },
      body: JSON.stringify({ schema: "version-move-return/v1", expected_move_id: expectedMoveId }),
    }, (value): value is VersionMoveReturn => validate.versionMoveReturn(value) && value.move_id === expectedMoveId);
  }

  // key 由確認時產生；回應遺失時以同一個鍵重送，取得同一筆承接結果。
  moveToNewVersion(materialId: string, body: VersionMoveCreate, key: string): Promise<VersionMoveResult> {
    return this.post(`/v1/materials/${encodeURIComponent(materialId)}/version-move`, body, key,
      (value): value is VersionMoveResult => validate.versionMoveResult(value)
        && value.material_id === materialId && value.to_revision === body.to_revision);
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
    this.structures.clear();
    this.outlines.clear();
    this.conceptContexts.clear();
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

  async getLearningDiagnosis(request: KnowledgeStructureRequest): Promise<LearningDiagnosisView> {
    return this.json(`/v1/materials/${request.materialId}/knowledge-structures/${encodeURIComponent(request.structureRevision)}/learning-diagnosis`,
      { method: "GET" }, (value): value is LearningDiagnosisView => validate.learningDiagnosis(value)
        && value.material_id === request.materialId && value.knowledge_structure_revision === request.structureRevision);
  }

  setAssessmentPresence(materialId: string, setId: string, active: boolean): Promise<AssessmentPresence> {
    return this.json(`/v1/materials/${materialId}/assessment-presence`, {
      method: "PUT", headers: { "Content-Type": "application/json", Origin: origin(), "X-Studydy-Browser-Id": browserId() },
      body: JSON.stringify({ schema: "assessment-presence/v1", assessment_set_id: setId, active }),
    }, (value): value is AssessmentPresence => validate.assessmentPresence(value) && value.active === active);
  }

  async getStartingLevel(materialId: string): Promise<StartingLevelView> {
    return this.json(`/v1/materials/${materialId}/starting-level`, { method: "GET" },
      (value): value is StartingLevelView => validate.startingLevelView(value) && value.material_id === materialId);
  }

  async getNotes(materialId: string, revision: string, kind: NoteKind): Promise<MaterialNotesView> {
    return this.json(`/v1/materials/${materialId}/knowledge-structures/${revision}/notes/${kind}`, { method: "GET" },
      (v): v is MaterialNotesView => validate.materialNotes(v) && v.material_id === materialId && v.knowledge_structure_revision === revision && v.kind === kind);
  }

  async generateNotes(materialId: string, revision: string, kind: NoteKind, generation: number): Promise<MaterialNotesView> {
    return this.json(`/v1/materials/${materialId}/knowledge-structures/${revision}/notes/${kind}`, {
      method: "POST", headers: { Origin: origin(), "Content-Type": "application/json" },
      body: JSON.stringify({ expected_generation: generation }),
    }, (v): v is MaterialNotesView => validate.materialNotes(v) && v.material_id === materialId && v.knowledge_structure_revision === revision && v.kind === kind);
  }

  async saveNoteAnnotation(view: MaterialNotesView, id: string, paragraphId: string | null, body: string): Promise<MaterialNotesView> {
    const saved = await this.json(`/v1/materials/${view.material_id}/knowledge-structures/${view.content_revision}/notes/${view.kind}/annotations/${id}`, {
      method: "PUT", headers: { Origin: origin(), "Content-Type": "application/json" },
      body: JSON.stringify({ expected_generation: view.content_generation, paragraph_id: paragraphId, body }),
    }, (v): v is MaterialNotesView => validate.materialNotes(v) && v.material_id === view.material_id && v.knowledge_structure_revision === view.content_revision && v.kind === view.kind);
    // 舊稿註記寫回舊內容後，重新讀取目前請求，避免把畫面切成舊教材。
    return view.content_revision === view.knowledge_structure_revision ? saved : this.getNotes(view.material_id, view.knowledge_structure_revision, view.kind);
  }

  async getLearningGoal(materialId: string, offset = 0): Promise<LearningGoalView> {
    return this.json(`/v1/materials/${materialId}/goal?preview_offset=${offset}&preview_days=7`, { method: "GET" },
      (value): value is LearningGoalView => validate.learningGoal(value) && value.material_id === materialId);
  }

  async clearDraftLearningGoal(materialId: string): Promise<LearningGoalView> {
    return this.json(`/v1/materials/${materialId}/goal`, { method: "DELETE", headers: { Origin: origin() } },
      (value): value is LearningGoalView => validate.learningGoal(value) && value.material_id === materialId
        && value.goal === null && value.pending_goal === null);
  }

  async setLearningGoal(materialId: string, date: string, percent: 60 | 80 | 100): Promise<LearningGoalView> {
    return this.json(`/v1/materials/${materialId}/goal`, {
      method: "PUT", headers: { Origin: origin(), "Content-Type": "application/json" },
      body: JSON.stringify({ schema: "learning-goal-update/v1", target_date: date, target_percent: percent }),
    }, (value): value is LearningGoalView => validate.learningGoal(value) && value.material_id === materialId
      && (value.goal ?? value.pending_goal)?.target_date === date && (value.goal ?? value.pending_goal)?.target_percent === percent);
  }

  async acknowledgeGoalUpdate(materialId: string, revision: string): Promise<LearningGoalView> {
    return this.json(`/v1/materials/${materialId}/goal/acknowledge-update`, {
      method: "POST", headers: { Origin: origin(), "Content-Type": "application/json" },
      body: JSON.stringify({ schema: "learning-goal-ack/v1", knowledge_structure_revision: revision }),
    }, (value): value is LearningGoalView => validate.learningGoal(value) && value.material_id === materialId);
  }

  getLearningGoals(): Promise<LearningGoalsView> {
    return this.json("/v1/learning-goals", { method: "GET" }, validate.learningGoals);
  }

  async setStartingLevel(materialId: string, level: StartingLevel): Promise<StartingLevelView> {
    return this.json(`/v1/materials/${materialId}/starting-level`, {
      method: "PUT", headers: { Origin: origin(), "Content-Type": "application/json" },
      body: JSON.stringify({ schema: "starting-level-update/v1", starting_level: level }),
    }, (value): value is StartingLevelView => validate.startingLevelView(value)
      && value.material_id === materialId && value.starting_level === level);
  }

  async startStudy(request: KnowledgeStructureRequest, key: string, conceptId?: string): Promise<StudyStartView> {
    return this.post(`/v1/materials/${request.materialId}/study-start`, {
      schema: "study-start/v1", knowledge_structure_revision: request.structureRevision,
      ...(conceptId ? { current_concept_id: conceptId } : {}),
    }, key, (value): value is StudyStartView => validate.studyStart(value)
      && value.session.material_id === request.materialId && value.session.knowledge_structure_revision === request.structureRevision);
  }

  async getConceptDifficulty(request: KnowledgeStructureRequest): Promise<ConceptDifficultyView> {
    return this.json(`/v1/materials/${request.materialId}/knowledge-structures/${encodeURIComponent(request.structureRevision)}/concept-difficulty`,
      { method: "GET" }, (value): value is ConceptDifficultyView => validate.conceptDifficulty(value)
        && value.material_id === request.materialId && value.knowledge_structure_revision === request.structureRevision);
  }

  async getKnowledgeOutline(request: KnowledgeStructureRequest): Promise<KnowledgeOutlineView> {
    this.requireActive();
    const key = `${request.materialId}:${request.structureRevision}`;
    const cached = this.outlines.get(key);
    if (cached) return cached;
    const task = this.json(`/v1/materials/${request.materialId}/knowledge-structures/${encodeURIComponent(request.structureRevision)}/outline`,
      { method: "GET" }, (value): value is KnowledgeOutlineView => validate.knowledgeOutline(value)
        && value.material_id === request.materialId && value.knowledge_structure_revision === request.structureRevision);
    this.outlines.set(key, task);
    if (this.outlines.size > 2) this.outlines.delete(this.outlines.keys().next().value!);
    try { return await task; } catch (error) { if (this.outlines.get(key) === task) this.outlines.delete(key); throw error; }
  }

  async getConceptContext(request: KnowledgeStructureRequest, ids: string[], neighbors = false): Promise<KnowledgeConceptsView> {
    this.requireActive();
    const key = `${request.materialId}:${request.structureRevision}:${neighbors}:${ids.join(",")}`;
    const cached = this.conceptContexts.get(key);
    if (cached) return cached;
    const query = new URLSearchParams({ ids: ids.join(","), neighbors: String(neighbors) });
    const task = this.json(`/v1/materials/${request.materialId}/knowledge-structures/${encodeURIComponent(request.structureRevision)}/concepts?${query}`,
      { method: "GET" }, (value): value is KnowledgeConceptsView => validate.knowledgeConcepts(value)
        && value.material_id === request.materialId && value.knowledge_structure_revision === request.structureRevision
        && value.requested_concept_ids.join(",") === ids.join(",")
        && (neighbors || value.concepts.length === ids.length));
    this.conceptContexts.set(key, task);
    if (this.conceptContexts.size > 8) this.conceptContexts.delete(this.conceptContexts.keys().next().value!);
    try { return await task; } catch (error) { if (this.conceptContexts.get(key) === task) this.conceptContexts.delete(key); throw error; }
  }

  searchConcepts(request: KnowledgeStructureRequest, text: string): Promise<KnowledgeSearchView> {
    const query = new URLSearchParams({ q: text });
    return this.json(`/v1/materials/${request.materialId}/knowledge-structures/${encodeURIComponent(request.structureRevision)}/search?${query}`,
      { method: "GET" }, (value): value is KnowledgeSearchView => validate.knowledgeSearch(value)
        && value.material_id === request.materialId && value.knowledge_structure_revision === request.structureRevision);
  }

  getKnowledgeRegions(request: KnowledgeStructureRequest): Promise<KnowledgeRegionsView> {
    return this.json(`/v1/materials/${request.materialId}/knowledge-structures/${encodeURIComponent(request.structureRevision)}/regions`,
      { method: "GET" }, (value): value is KnowledgeRegionsView => validate.knowledgeRegions(value)
        && value.material_id === request.materialId && value.knowledge_structure_revision === request.structureRevision);
  }

  getAccountStorage(): Promise<AccountStorage> {
    return this.json("/v1/account/storage", { method: "GET" }, validate.accountStorage);
  }

  getProfile(): Promise<LearnerProfile> {
    this.requireActive();
    if (!this.profile) {
      const task = this.json("/v1/profile", { method: "GET" }, validate.learnerProfile);
      this.profile = task;
      task.catch(() => { if (this.profile === task) this.profile = null; });
    }
    return this.profile;
  }

  async updateProfile(displayName: string | null): Promise<LearnerProfile> {
    const profile = await this.json("/v1/profile", {
      method: "POST", headers: { "Content-Type": "application/json", Origin: origin() },
      body: JSON.stringify({ schema: "learner-profile-update/v1", display_name: displayName }),
    }, validate.learnerProfile);
    this.profile = Promise.resolve(profile);
    for (const listener of this.profileListeners) listener(profile);
    return profile;
  }

  onProfileChange(listener: (profile: LearnerProfile) => void): () => void {
    this.profileListeners.add(listener);
    return () => { this.profileListeners.delete(listener); };
  }

  getAccountSessions(): Promise<AccountSessions> {
    return this.json("/v1/account/sessions", { method: "GET" }, validate.accountSessions);
  }

  // 撤銷其他裝置的登入，保留目前這個；回傳之後的有效登入。
  revokeOtherSessions(): Promise<AccountSessions> {
    return this.json("/v1/account/sessions/others", { method: "DELETE", headers: { Origin: origin() } },
      validate.accountSessions);
  }

  // 撤銷本人另一個登入（目前這個用登出）；回傳之後的有效登入。
  revokeSession(sessionId: string): Promise<AccountSessions> {
    return this.json(`/v1/account/sessions/${encodeURIComponent(sessionId)}`, { method: "DELETE", headers: { Origin: origin() } },
      validate.accountSessions);
  }

  // 成功後其他裝置的登入全部失效，目前這個裝置維持登入。
  changePassword(currentPassword: string, newPassword: string): Promise<AccountSessions> {
    return this.json("/v1/account/password", {
      method: "POST", headers: { "Content-Type": "application/json", Origin: origin() },
      body: JSON.stringify({ schema: "password-change/v1", current_password: currentPassword, new_password: newPassword }),
    }, validate.accountSessions);
  }

  searchLibrary(text: string): Promise<LibrarySearchView> {
    const query = new URLSearchParams({ q: text });
    return this.json(`/v1/search?${query}`, { method: "GET" },
      (value): value is LibrarySearchView => validate.librarySearch(value) && value.query === text.trim());
  }

  // async：登出後仍可能被 popstate／focus 觸發，失效時要回傳 rejected promise 讓呼叫端處理，不能同步拋錯。
  async getLearningActivity(options: { fresh?: boolean } = {}): Promise<LearningActivityView> {
    this.requireActive();
    // fresh 仍共用剛發出（2 秒內）的讀取，避免頁首與首頁同時載入時重複計算。
    if (!this.activity || Date.now() - this.activity.at > (options.fresh ? 2_000 : activityReuseMs)) {
      const task = this.json("/v1/learning-activity", { method: "GET" }, validate.learningActivity);
      this.activity = { task, at: Date.now() };
      task.then(
        (value) => { if (this.activity?.task === task) for (const listener of this.activityListeners) listener(value); },
        () => { if (this.activity?.task === task) this.activity = null; },
      );
    }
    return this.activity.task;
  }

  claimCelebrations(): Promise<CelebrationClaim> {
    return this.json("/v1/learning-activity/celebrations/claim", {
      method: "POST", headers: { "Content-Type": "application/json", Origin: origin() },
      body: JSON.stringify({ schema: "celebration-claim/v1" }),
    }, validate.celebrationClaim);
  }

  // 學習節奏不快取：切換區間或翻頁時才讀，每次最多一年的每日彙總。
  getLearningRhythm(period: LearningRhythmPeriod, offset: number): Promise<LearningRhythmView> {
    const query = new URLSearchParams({ period, offset: String(offset) });
    return this.json(`/v1/learning-activity/rhythm?${query}`, { method: "GET" },
      (value): value is LearningRhythmView => validate.learningRhythm(value) && value.period === period && value.offset === offset);
  }

  onLearningActivityChange(listener: (activity: LearningActivityView) => void): () => void {
    this.activityListeners.add(listener);
    return () => { this.activityListeners.delete(listener); };
  }

  // 下次讀取時重新計算；不立即重算，避免每次作答或標記都觸發彙總。
  private markActivityStale(): void {
    if (this.activity) this.activity.at = 0;
  }

  getPodcastProgress(id: string): Promise<PodcastProgressView> {
    return this.json(`/v1/podcasts/${encodeURIComponent(id)}/progress`, { method: "GET" },
      (value): value is PodcastProgressView => validate.podcastProgress(value) && value.podcast_id === id);
  }

  // keepalive 讓離開頁面（pagehide）時的最後一次回報仍能送出。
  async savePodcastProgress(id: string, body: PodcastProgressUpdate, options: { keepalive?: boolean } = {}): Promise<PodcastProgressView> {
    const value = await this.json(`/v1/podcasts/${encodeURIComponent(id)}/progress`, {
      method: "POST", headers: { "Content-Type": "application/json", Origin: origin() },
      body: JSON.stringify(body), keepalive: options.keepalive,
    }, (value): value is PodcastProgressView => validate.podcastProgress(value) && value.podcast_id === id);
    if (value.credited_seconds > 0 || body.ended) this.markActivityStale();
    return value;
  }

  readProgressSummary(request: KnowledgeStructureRequest, studySessionId?: string): Promise<ProgressSummaryView> {
    const query = studySessionId ? `?${new URLSearchParams({ study_session_id: studySessionId })}` : "";
    return this.json(`/v1/materials/${request.materialId}/knowledge-structures/${encodeURIComponent(request.structureRevision)}/progress-summary${query}`,
      { method: "GET" }, (value): value is ProgressSummaryView => validate.progressSummary(value)
        && value.material_id === request.materialId && value.knowledge_structure_revision === request.structureRevision
        && value.study_session_id === (studySessionId ?? null));
  }

  readMapProgress(id: string, revision: string): Promise<MapProgressView> {
    return this.json(`/v1/study-sessions/${id}/map-progress`, { method: "GET" },
      (value): value is MapProgressView => validate.mapProgress(value)
        && value.study_session_id === id && value.knowledge_structure_revision === revision);
  }

  async resumeWorkspace(request: { materialId: string; structureRevision: string; studySessionId: string; runId: string; assessmentSetId?: string }): Promise<StudyWorkspaceView> {
    const query = new URLSearchParams({ run_id: request.runId });
    if (request.assessmentSetId) query.set("set_id", request.assessmentSetId);
    const restored = await this.json(`/v1/materials/${request.materialId}/knowledge-structures/${encodeURIComponent(request.structureRevision)}/study-sessions/${request.studySessionId}/workspace?${query}`,
      { method: "GET" }, validate.studyWorkspace);
    if (restored.session.material_id !== request.materialId || restored.session.study_session_id !== request.studySessionId
      || restored.session.knowledge_structure_revision !== request.structureRevision || restored.run_id !== request.runId
      || (request.assessmentSetId !== undefined && restored.selected_set_id !== request.assessmentSetId))
      throw schemaMismatch("學習紀錄與教材版本不一致。");
    return restored;
  }

  async getKnowledgeStructure(request: KnowledgeStructureRequest): Promise<KnowledgeStructureView> {
    this.requireActive();
    const key = `${request.materialId}:${request.structureRevision}`;
    // 保留目前地圖與舊版卡組所用的兩份不可變結構，切換功能不必重傳大型教材。
    const cached = this.structures.get(key);
    if (cached) {
      this.structures.delete(key);
      this.structures.set(key, cached);
      return cached;
    }
    const value = this.json(
      `/v1/materials/${encodeURIComponent(request.materialId)}/knowledge-structures/${encodeURIComponent(request.structureRevision)}`,
      { method: "GET" },
      validate.knowledgeStructure,
    ).then(view => {
      if (view.knowledge_structure_revision !== request.structureRevision)
        throw schemaMismatch("教材結構版本不一致。");
      return view;
    });
    this.structures.set(key, value);
    if (this.structures.size > 2) this.structures.delete(this.structures.keys().next().value!);
    try { return await value; }
    catch (error) {
      if (this.structures.get(key) === value) this.structures.delete(key);
      throw error;
    }
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
    this.markActivityStale();
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

/** 資源不存在或不屬於目前帳號；頁面改顯示「找不到」，不提供永遠不會成功的重新讀取。 */
export function isNotFound(error: unknown): boolean {
  return error instanceof ApiClientError && error.reasonCode === "RESOURCE_NOT_FOUND";
}
