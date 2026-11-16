import type { MaterialProcessingRunView } from "../../api/contracts";

export const automaticPollIntervalMs = 1_500;
export const materialProgressStages = [
  "queued",
  "evidence",
  "semantics",
  "review",
  "indexing",
  "publishing",
  "completed",
] as const;

export function materialProgressStageLabel(
  stage: MaterialProcessingRunView["progress_stage"],
): string {
  if (stage === "queued") return "等待處理資源";
  if (stage === "evidence") return "整理頁面與教材來源";
  if (stage === "semantics") return "建立概念、關係與學習順序";
  if (stage === "review") return "檢查與修正知識結構";
  if (stage === "indexing") return "建立教材檢索索引";
  if (stage === "publishing") return "組裝與發布知識地圖";
  return "處理完成";
}

type ProcessingProgress = Pick<
  MaterialProcessingRunView,
  "base_revision" | "status" | "progress_stage" | "completed_pages" | "total_pages" | "completed_units" | "total_units"
>;

export function materialStageCount(run: ProcessingProgress): { completed: number; total: number } | null {
  const stage = run.progress_stage;
  if (!["evidence", "semantics", "review", "indexing"].includes(stage)) return null;
  // 舊工作的區塊／檢核分母無法從頁數重建，保持未定量。
  const completed = run.completed_units ?? (stage === "evidence" && !run.base_revision ? run.completed_pages : null);
  const total = run.total_units ?? (stage === "evidence" && !run.base_revision ? run.total_pages : null);
  if (completed == null || total == null || !Number.isSafeInteger(completed) ||
      !Number.isSafeInteger(total) || total <= 0 || completed < 0 || completed > total) return null;
  return { completed, total };
}

export function materialCurrentStagePercent(run: ProcessingProgress): number | null {
  const count = materialStageCount(run);
  return count ? Math.floor(count.completed / count.total * 100) : null;
}

export function materialOverallProgressPercent(run: ProcessingProgress): number | null {
  return run.progress_stage === "completed" && ["succeeded", "partial"].includes(run.status) ? 100 : null;
}

export function materialStageCountLabel(run: ProcessingProgress): string {
  const count = materialStageCount(run);
  if (!count) return "";
  return run.progress_stage === "evidence" && !run.base_revision
    ? `已完成 ${count.completed} / ${count.total} 頁` : `已完成 ${Math.floor(count.completed / count.total * 100)}%`;
}

export function materialElapsedLabel(createdAt: string, now: number): string {
  const startedAt = Date.parse(createdAt);
  if (!Number.isFinite(startedAt) || now < startedAt) return "剛剛開始";
  const totalSeconds = Math.floor((now - startedAt) / 1000);
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return minutes > 0 ? `${minutes} 分 ${seconds} 秒` : `${seconds} 秒`;
}

export function formatFileSize(sizeBytes: number): string {
  if (sizeBytes < 1024 * 1024) return `${Math.max(1, Math.round(sizeBytes / 1024))} KiB`;
  if (sizeBytes >= 1024 ** 3) return `${(sizeBytes / 1024 ** 3).toFixed(1)} GiB`;
  return `${(sizeBytes / (1024 * 1024)).toFixed(1)} MiB`;
}

// 失敗畫面依使用者能做的事分組說明，不顯示錯誤代碼；代碼只留在後端失敗紀錄供查問題。
const BUSY_FAILURES = new Set(["SEMANTIC_SERVICE_TIMEOUT", "SEMANTIC_SERVICE_UNAVAILABLE", "RESTART_INTERRUPTED"]);
const UNRELIABLE_RESULT_FAILURES = new Set([
  "SEMANTIC_OUTPUT_TRUNCATED", "SEMANTIC_GENERATION_FAILED", "SEMANTIC_RESPONSE_INVALID",
  "SEMANTIC_OUTPUT_INVALID", "SEMANTIC_OUTPUT_TOO_LARGE", "NO_CANONICAL_CONCEPT", "NO_USABLE_EVIDENCE",
  "DOCUMENT_EVIDENCE_INVALID", "KNOWLEDGE_STRUCTURE_INVALID",
]);
const SAVE_FAILURES = new Set([
  "KNOWLEDGE_STRUCTURE_CONFLICT", "KNOWLEDGE_STRUCTURE_STORE_FAILED", "KNOWLEDGE_STRUCTURE_UNAVAILABLE",
  "REVISION_CONFLICT", "MATERIAL_RUN_STORAGE_FAILED", "ANALYSIS_ARTIFACT_WRITE_FAILED", "ANALYSIS_CHECKPOINT_INVALID",
]);

export function materialFailureMessage(errorCode: string): string {
  if (BUSY_FAILURES.has(errorCode)) return "分析服務暫時忙碌，這次分析沒有完成。請稍後重新分析。";
  if (errorCode === "MATERIAL_MODEL_BUDGET_EXHAUSTED")
    return "這次重新分析沒有完成，沒有發布新版知識地圖。目前的地圖和學習紀錄維持原樣，可以照常使用。";
  if (errorCode === "NO_USABLE_ADDED_CONTENT")
    return "新增的教材沒有整理出可用的內容，目前的地圖與學習紀錄已保留。";
  if (errorCode === "SEMANTIC_INPUT_TOO_LARGE")
    return "這批教材的內容太多，一次處理不完，沒有發布新的知識地圖。可以減少來源後再重新分析。";
  if (UNRELIABLE_RESULT_FAILURES.has(errorCode) || errorCode.startsWith("REVIEW_") || errorCode.startsWith("CONSOLIDATION_"))
    return "這次沒能從教材整理出可靠的知識地圖，沒有發布新版。可以重新分析一次；若仍失敗，請檢查教材內容是否完整清楚。";
  if (SAVE_FAILURES.has(errorCode)) return "分析結果沒能完整保存，沒有發布新版知識地圖。請稍後重新分析。";
  return "教材分析沒能完成，沒有發布知識地圖。可以重新分析；若一再失敗，請稍後再試。";
}
