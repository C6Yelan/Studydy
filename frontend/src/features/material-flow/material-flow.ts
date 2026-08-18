import type { MaterialProcessingRunView } from "../../api/contracts";

export const maximumPdfBytes = 100 * 1024 * 1024;
export const automaticPollLimit = 240;
export const automaticPollIntervalMs = 1_500;

type PdfFileDetails = Pick<File, "size" | "type">;

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

export function materialRunLabel(status: MaterialProcessingRunView["status"]): string {
  if (status === "pending") return "等待開始處理";
  if (status === "running") return "正在分析完整教材";
  if (status === "succeeded") return "處理完成，等待複核";
  if (status === "partial") return "部分頁面已排除，等待複核";
  return "處理失敗";
}

export function materialFailureMessage(errorCode: string): string {
  if (errorCode === "RESTART_INTERRUPTED") return "服務重新啟動時中斷了這次處理。";
  if (errorCode === "MATERIAL_PAGE_LIMIT_EXCEEDED") return "目前一次最多處理 32 頁 PDF。";
  if (errorCode === "MATERIAL_CONFIGURATION_INVALID" || errorCode === "RUNTIME_BINDING_INVALID") {
    return "本機教材處理環境未通過安全檢查。";
  }
  if (errorCode === "NO_USABLE_EVIDENCE" || errorCode === "NO_USABLE_CONCEPT") {
    return "教材沒有產生可安全回查的概念與依據。";
  }
  return "教材分析未能安全完成，沒有發布知識地圖。";
}
