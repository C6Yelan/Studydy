import type {
  ApiErrorView,
  ApiReasonCode,
  KnowledgeMapRequest,
  KnowledgeMapView,
  KnownApiReasonCode,
  MaterialProcessingCreate,
  MaterialProcessingRunView,
  MaterialView,
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
    "error_code", "created_at", "updated_at", "completed_at",
  ]);
  if (!item || item.schema !== "material-processing-run/v2") return false;
  if (!isUuid(item.run_id) || !isUuid(item.material_id) || !isUuid(item.source_artifact_id)) return false;
  if (typeof item.created_at !== "string" || typeof item.updated_at !== "string") return false;
  const hasBinding = isBinding(item.output_binding);
  if (item.status === "succeeded" || item.status === "partial") {
    return hasBinding
      && object(item.output_binding)?.processing === item.status
      && item.error_code === null
      && typeof item.completed_at === "string";
  }
  if (item.status === "failed") {
    return item.output_binding === null
      && typeof item.error_code === "string"
      && typeof item.completed_at === "string";
  }
  return (item.status === "pending" || item.status === "running")
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
    "verifier_calls", "verifier_rejected", "verifier_unsupported", "accepted_relations",
  ]);
  if (!diagnostics) return false;
  const countNames = [
    "possible_pairs", "candidate_pairs", "selected_pairs", "evidence_gated_pairs",
    "rejected_no_evidence", "direction_conflicts", "verifier_calls", "verifier_rejected",
    "verifier_unsupported", "accepted_relations",
  ];
  const signals = object(diagnostics.selected_signal_counts);
  const allowedSignals = new Set([
    "adjacent", "same_group", "same_page", "explicit_relation", "cross_reference",
    "shared_evidence", "shared_formula",
  ]);
  return countNames.every((name) => Number.isInteger(diagnostics[name]) && Number(diagnostics[name]) >= 0)
    && !!signals
    && Object.entries(signals).every(([name, count]) => allowedSignals.has(name)
      && Number.isInteger(count) && Number(count) >= 0)
    && Number(diagnostics.selected_pairs) <= Number(diagnostics.candidate_pairs)
    && Number(diagnostics.candidate_pairs) <= Number(diagnostics.possible_pairs);
}

function isKnowledgeMap(value: unknown): value is KnowledgeMapView {
  const item = closed(value, [
    "schema", "material_ref", "knowledge_map_revision", "source_output_id", "status",
    "concepts", "relations", "relation_diagnostics", "initial_learning_path", "excluded_pages",
  ]);
  if (!item
    || item.schema !== "knowledge-map-view/v4"
    || !isRevision(item.material_ref, "material")
    || !isRevision(item.knowledge_map_revision, "knowledge-map")
    || !isRevision(item.source_output_id, "study-material-output")
    || !Array.isArray(item.concepts)
    || !Array.isArray(item.relations)
    || !isRelationDiagnostics(item.relation_diagnostics)
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
      "quality", "decision", "reason_codes",
    ]);
    return !!concept
      && isRevision(concept.formal_concept_id, "formal-concept")
      && typeof concept.label === "string" && concept.label.length >= 1
      && isStringArray(concept.source_concept_ids, 1)
      && Array.isArray(concept.source_page_numbers)
      && concept.source_page_numbers.length > 0
      && concept.source_page_numbers.every((page) => Number.isInteger(page) && Number(page) >= 1)
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
  const conceptIds = item.concepts.map((entry) => object(entry)?.formal_concept_id);
  const evidenceByConcept = new Map(
    (item.concepts as unknown as KnowledgeMapView["concepts"]).map((concept) => [
      concept.formal_concept_id,
      new Set(
        concept.claims.flatMap((claim) =>
          claim.evidence.map((evidence) => evidence.evidence_id)),
      ),
    ]),
  );
  const relationsAreValid = item.relations.every((value) => {
    const relation = closed(value, [
      "relation_id", "type", "source_formal_concept_id", "target_formal_concept_id",
      "source_evidence_ids", "target_evidence_ids", "quality", "decision",
      "reason_codes", "is_in_prerequisite_cycle",
    ]);
    return !!relation
      && isRevision(relation.relation_id, "formal-relation")
      && ["prerequisite", "contains", "related"].includes(String(relation.type))
      && conceptIds.includes(relation.source_formal_concept_id)
      && conceptIds.includes(relation.target_formal_concept_id)
      && relation.source_formal_concept_id !== relation.target_formal_concept_id
      && isStringArray(relation.source_evidence_ids, 1)
      && isStringArray(relation.target_evidence_ids, 1)
      && (relation.source_evidence_ids as unknown[]).every((evidenceId) =>
        evidenceByConcept.get(String(relation.source_formal_concept_id))
          ?.has(String(evidenceId)) === true)
      && (relation.target_evidence_ids as unknown[]).every((evidenceId) =>
        evidenceByConcept.get(String(relation.target_formal_concept_id))
          ?.has(String(evidenceId)) === true)
      && relation.quality === "needs_review"
      && relation.decision === "review"
      && isSortedUniqueStrings(relation.reason_codes)
      && typeof relation.is_in_prerequisite_cycle === "boolean";
  });
  const excludedRefs = item.excluded_pages.map((entry) => object(entry)?.page_ref);
  const excludedNumbers = item.excluded_pages.map((entry) => object(entry)?.page_number);
  return new Set(conceptIds).size === conceptIds.length
    && relationsAreValid
    && new Set(item.relations.map((entry) => object(entry)?.relation_id)).size === item.relations.length
    && isStringArray(item.initial_learning_path)
    && item.initial_learning_path.length === conceptIds.length
    && new Set(item.initial_learning_path).size === conceptIds.length
    && item.initial_learning_path.every((id) => conceptIds.includes(id))
    && new Set(excludedRefs).size === excludedRefs.length
    && new Set(excludedNumbers).size === excludedNumbers.length
    && (item.excluded_pages.length === 0 || status.processing === "partial");
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
  private readonly fetchRequest: FetchRequest;

  constructor(fetchRequest: FetchRequest = fetch.bind(globalThis)) {
    this.fetchRequest = fetchRequest;
  }

  async ensureSession(): Promise<void> {
    if (this.sessionChecked) return;
    await this.refreshSession();
    this.sessionChecked = true;
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

  async createMaterial(pdf: Blob, idempotencyKey = crypto.randomUUID()): Promise<MaterialView> {
    if (pdf.type !== "application/pdf" || pdf.size < 1 || pdf.size > maximumPdfBytes) {
      throw new ApiClientError("input", "請選擇 100 MiB 以內的 PDF。", { reasonCode: "REQUEST_INPUT_INVALID" });
    }
    const request = () => this.json(
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
    try {
      return await request();
    } catch (error) {
      if (error instanceof ApiClientError && error.kind === "network") return request();
      throw error;
    }
  }

  async createMaterialRun(body: MaterialProcessingCreate, idempotencyKey = crypto.randomUUID()): Promise<MaterialProcessingRunView> {
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
