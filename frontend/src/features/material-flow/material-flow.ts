import type { MaterialSubject, MaterialProcessingRunView } from "../../api/contracts";

export const maximumPdfBytes = 100 * 1024 * 1024;
export const automaticPollLimit = 80;
export const automaticPollIntervalMs = 1_500;
export const materialRunRequestTimeoutMs = 10_000;

const uuidPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

type PdfFileDetails = Pick<File, "size" | "type">;

type RunRecovery = {
  schema: "material-run-recovery/v1";
  subject: MaterialSubject;
};

export function validatePdfFile(file: PdfFileDetails | null): string | null {
  if (!file) return "請先選擇 PDF 教材。";
  if (file.type !== "application/pdf") return "只接受 application/pdf 格式的教材。";
  if (file.size === 0) return "PDF 不可為空白檔案。";
  if (file.size > maximumPdfBytes) return "PDF 不可超過 100 MiB。";
  return null;
}

export function formatFileSize(sizeBytes: number): string {
  if (sizeBytes < 1024 * 1024) return `${Math.max(1, Math.round(sizeBytes / 1024))} KiB`;
  return `${(sizeBytes / (1024 * 1024)).toFixed(1)} MiB`;
}

// 這裡只保存同一分頁重新處理時需要的科目，不是教材或處理結果的正式資料來源。
export function saveRunSubject(storage: Storage, runId: string, subject: MaterialSubject): boolean {
  if (!uuidPattern.test(runId)) return false;
  const recovery: RunRecovery = { schema: "material-run-recovery/v1", subject };
  try {
    storage.setItem(`studydy.material-run.${runId}`, JSON.stringify(recovery));
    return true;
  } catch {
    return false;
  }
}

export function readRunSubject(storage: Storage, runId: string): MaterialSubject | null {
  // 編號或保存內容不符合目前格式時就放棄恢復，避免用不完整資料建立新的處理作業。
  if (!uuidPattern.test(runId)) return null;
  try {
    const saved = storage.getItem(`studydy.material-run.${runId}`);
    if (!saved) return null;
    const recovery: unknown = JSON.parse(saved);
    if (!recovery || typeof recovery !== "object" || Array.isArray(recovery)) return null;
    const keys = Object.keys(recovery);
    if (keys.length !== 2 || !keys.includes("schema") || !keys.includes("subject")) return null;
    const candidate = recovery as Record<string, unknown>;
    if (candidate.schema !== "material-run-recovery/v1") return null;
    return candidate.subject === "data_structures" || candidate.subject === "economics"
      ? candidate.subject
      : null;
  } catch {
    return null;
  }
}

export function materialRunLabel(status: MaterialProcessingRunView["status"]): string {
  if (status === "pending") return "等待開始處理";
  if (status === "running") return "正在分析教材";
  if (status === "succeeded") return "處理完成";
  if (status === "partial") return "部分完成，需要複核";
  return "處理失敗";
}

export function materialFailureMessage(errorCode: NonNullable<MaterialProcessingRunView["error_code"]>): string {
  if (errorCode === "RESTART_INTERRUPTED") return "服務重新啟動時中斷了這次處理。";
  if (errorCode === "LOCAL_PROVIDER_TIMEOUT") return "頁面分析服務超過等待時間。";
  if (errorCode === "LOCAL_PROVIDER_RATE_LIMITED") return "頁面分析服務目前請求過多。";
  if (errorCode === "LOCAL_PROVIDER_TRANSIENT_ERROR") return "頁面分析服務暫時無法使用。";
  if (errorCode === "MATERIAL_CONFIGURATION_INVALID") return "教材處理設定無法使用。";
  if (errorCode === "MATERIAL_ANALYSIS_FAILED") return "教材頁面分析未能產生有效結果。";
  if (errorCode === "CONTROLLED_RESOURCE_INVALID") return "科目的學習資源目前無法使用。";
  return "教材輸出未能安全完成。";
}
