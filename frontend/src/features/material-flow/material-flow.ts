import type { FormatCapability, MaterialProcessingRunView } from "../../api/contracts";

export const maximumPdfBytes = 100 * 1024 * 1024;
export const automaticPollIntervalMs = 1_500;
export const materialProgressStages = [
  "queued",
  "evidence",
  "semantics",
  "publishing",
  "completed",
] as const;

export function materialProgressStageLabel(stage: MaterialProcessingRunView["progress_stage"]): string {
  if (stage === "queued") return "等待處理資源";
  if (stage === "evidence") return "整理頁面與教材來源";
  if (stage === "semantics") return "建立概念、關係與學習順序";
  if (stage === "publishing") return "發布知識地圖";
  return "處理完成";
}

type ProcessingProgress = Pick<MaterialProcessingRunView, "status" | "progress_stage" | "completed_pages" | "total_pages">;

export function materialCurrentStagePercent(run: ProcessingProgress): number | null {
  if (run.status === "cancelled") return null;
  if (run.progress_stage === "completed" && (run.status === "succeeded" || run.status === "partial")) return 100;
  if (run.progress_stage !== "evidence" && run.progress_stage !== "semantics") return null;
  const total = run.total_pages;
  if (total === null || !Number.isSafeInteger(total) || total <= 0 || !Number.isFinite(run.completed_pages)) return null;
  return Math.round(Math.max(0, Math.min(total, run.completed_pages)) / total * 100);
}

export function materialOverallProgressPercent(run: ProcessingProgress): number | null {
  if (run.status === "cancelled") return null;
  if (run.progress_stage === "completed" && (run.status === "succeeded" || run.status === "partial")) return 100;
  if (run.progress_stage === "queued") return 0;
  const total = run.total_pages;
  if (total === null || !Number.isSafeInteger(total) || total <= 0 || !Number.isFinite(run.completed_pages)) return null;
  const completed = Math.max(0, Math.min(total, run.completed_pages));
  let done: number;
  if (run.progress_stage === "evidence") done = completed;
  else if (run.progress_stage === "semantics") done = total + completed;
  else if (run.progress_stage === "publishing") done = total * 2;
  else return null;
  // 頁面整理與語意整理各計一輪，發布計最後一單位；這不是耗時比例。
  return Math.min(99, Math.round(done / (total * 2 + 1) * 100));
}

export function materialElapsedLabel(createdAt: string, now: number): string {
  const startedAt = Date.parse(createdAt);
  if (!Number.isFinite(startedAt) || now < startedAt) return "剛剛開始";
  const totalSeconds = Math.floor((now - startedAt) / 1000);
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return minutes > 0 ? `${minutes} 分 ${seconds} 秒` : `${seconds} 秒`;
}

export function validateSourceFile(file: Pick<File, "name" | "size" | "type">, formats: FormatCapability[]): string | null {
  const format = formats.find(item => file.name.toLowerCase().endsWith(item.extension));
  if (!format) return "目前不支援這個教材格式。";
  if (file.type && file.type !== "application/octet-stream" && file.type !== format.media_type) return "副檔名與檔案類型不一致。";
  if (file.size === 0) return "教材不可為空白檔案。";
  if (file.size > format.max_bytes) return "每份檔案最多 100 MiB。";
  return null;
}

export function formatFileSize(sizeBytes: number): string {
  if (sizeBytes < 1024 * 1024) return `${Math.max(1, Math.round(sizeBytes / 1024))} KiB`;
  return `${(sizeBytes / (1024 * 1024)).toFixed(1)} MiB`;
}

export function materialRunLabel(status: MaterialProcessingRunView["status"], cancelRequestedAt: string | null, isUpdate = false): string {
  if (isUpdate) {
    if (status === "running" && cancelRequestedAt !== null) return "正在取消這次更新";
    return { pending: "等待更新教材", running: "正在更新教材", succeeded: "更新完成", partial: "更新完成（部分內容待複核）", failed: "更新失敗", cancelled: "已取消這次更新" }[status];
  }
  if (status === "cancelled") return "已取消處理";
  if (status === "running" && cancelRequestedAt !== null) return "正在取消並刪除教材";
  if (status === "pending") return "等待開始處理";
  if (status === "running") return "正在分析完整教材";
  if (status === "succeeded") return "處理完成，等待複核";
  if (status === "partial") return "部分內容需要複核";
  return "處理失敗";
}

export function materialFailureMessage(errorCode: string): string {
  if (errorCode === "KNOWLEDGE_STRUCTURE_INVALID") return "分析結果在組裝地圖時未通過結構檢查，尚未發布地圖。";
  if (errorCode === "ANALYSIS_ARTIFACT_WRITE_FAILED") return "分析產物無法寫入本機儲存空間，處理已停止。";
  if (errorCode === "ANALYSIS_CHECKPOINT_INVALID") return "已保存的分析資料未通過完整性檢查，處理已停止，沒有自動重新分析。";
  if (errorCode === "ANALYSIS_RUNTIME_CHANGED") return "已保存的分析進度與目前的教材分析設定不一致，已停止接續，沒有自動重新分析教材。";
  if (errorCode === "NO_USABLE_ADDED_CONTENT") return "新增教材沒有產生可用的知識內容，目前地圖與學習紀錄已保留。";
  if (errorCode === "SEMANTIC_INPUT_TOO_LARGE") return "教材內容與累積概念超過目前分析輸入限制，沒有發布知識地圖。請先調整分析設定，再重試。";
  if (errorCode === "SEMANTIC_BUDGET_EXHAUSTED") return "目前開發測試的 AI 呼叫額度已用完，沒有發布知識地圖。請先確認測試額度，再重試。";
  if (errorCode === "RESTART_INTERRUPTED") return "服務重新啟動時中斷了這次處理。";
  if (errorCode === "MATERIAL_CONFIGURATION_INVALID" || errorCode === "RUNTIME_BINDING_INVALID") {
    return "本機教材處理環境未通過安全檢查。";
  }
  if (errorCode === "NO_USABLE_EVIDENCE" || errorCode === "NO_USABLE_CONCEPT") {
    return "教材沒有產生可安全回查的概念與依據。";
  }
  return "教材分析未能安全完成，沒有發布知識地圖。";
}

export function materialRunHasUsableMap(run: MaterialProcessingRunView): boolean {
  return run.output_binding !== null;
}
