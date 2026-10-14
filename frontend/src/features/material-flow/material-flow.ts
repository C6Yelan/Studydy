import type { FormatCapability, MaterialProcessingRunView } from "../../api/contracts";

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
  "status" | "progress_stage" | "completed_pages" | "total_pages" | "completed_units" | "total_units"
>;

export function materialStageCount(run: ProcessingProgress): { completed: number; total: number; unit: string } | null {
  const stage = run.progress_stage;
  if (!["evidence", "semantics", "review", "indexing"].includes(stage)) return null;
  // 舊工作的區塊／檢核分母無法從頁數重建，保持未定量。
  const completed = run.completed_units ?? (stage === "evidence" ? run.completed_pages : null);
  const total = run.total_units ?? (stage === "evidence" ? run.total_pages : null);
  if (completed == null || total == null || !Number.isSafeInteger(completed) ||
      !Number.isSafeInteger(total) || total <= 0 || completed < 0 || completed > total) return null;
  return { completed, total, unit: stage === "evidence" ? "頁" : stage === "semantics" ? "個來源區塊" : stage === "indexing" ? "個索引來源" : "個檢核批次" };
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
  return count ? `已完成 ${count.completed} / ${count.total} ${count.unit}` : "";
}

export function materialElapsedLabel(createdAt: string, now: number): string {
  const startedAt = Date.parse(createdAt);
  if (!Number.isFinite(startedAt) || now < startedAt) return "剛剛開始";
  const totalSeconds = Math.floor((now - startedAt) / 1000);
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return minutes > 0 ? `${minutes} 分 ${seconds} 秒` : `${seconds} 秒`;
}

export function validateSourceFile(
  file: Pick<File, "name" | "size" | "type">,
  formats: FormatCapability[],
): string | null {
  const format = formats.find((item) => file.name.toLowerCase().endsWith(item.extension));
  if (!format) return "目前不支援這個教材格式。";
  if (file.type && file.type !== "application/octet-stream" && file.type !== format.media_type)
    return "副檔名與檔案類型不一致。";
  if (file.size === 0) return "教材不可為空白檔案。";
  if (file.size > format.max_bytes) return `每份檔案最多 ${format.max_bytes / (1024 * 1024)} MiB。`;
  return null;
}

export function formatFileSize(sizeBytes: number): string {
  if (sizeBytes < 1024 * 1024) return `${Math.max(1, Math.round(sizeBytes / 1024))} KiB`;
  return `${(sizeBytes / (1024 * 1024)).toFixed(1)} MiB`;
}

export function materialFailureMessage(errorCode: string): string {
  const reviewFailures: Record<string, string> = {
    REVIEW_RESPONSE_INVALID: "模型的審查回應格式不完整或不符合要求。",
    REVIEW_CONCEPT_COVERAGE_INVALID: "模型的審查結果漏列或重複列出概念。",
    REVIEW_CONCEPT_SUPPORT_INVALID: "模型為概念提供的引用不屬於該概念的來源。",
    REVIEW_EVIDENCE_INVALID: "模型使用了不存在的來源編號，或沒有提供修改理由。",
    REVIEW_TARGET_INVALID: "模型提出的概念歸屬對象不存在或不允許使用。",
    REVIEW_METADATA_REASON_INVALID: "模型將概念標為非教學內容，但沒有提供允許的分類理由（作者、版面資訊或表格格值）。",
    REVIEW_GROUPING_CYCLE: "模型提出的概念歸屬互相循環，無法建立有效結構。",
    REVIEW_EDIT_ID_INVALID: "模型重複修改同一項目，或使用了不屬於本批的項目編號。",
    REVIEW_ALIAS_EDIT_INVALID: "模型要刪除的別名不存在、重複，或缺少該概念的來源支持；大小寫也須與原別名一致。",
    REVIEW_CLAIM_EDIT_INVALID: "模型提出的主張修正文句為空白。",
    REVIEW_RELATION_EDIT_INVALID: "模型提出的關係修改，其類型或來源引用不符合要求。",
    REVIEW_SOURCE_CHANGED: "審查使用的來源與目前待整理的版本不一致。",
    REVIEW_SCOPES_OVERLAP: "不同審查批次重複處理了同一個概念，無法安全合併。",
    REVIEW_PREREQUISITE_CYCLE: "合併後的先備關係形成循環，無法建立學習順序。",
    REVIEW_CONTENT_LOST: "合併審查結果時發現知識內容缺漏。",
    REVIEW_STRUCTURE_INVALID: "審查結果合併後未通過知識結構檢查。",
  };
  if (reviewFailures[errorCode])
    return `${reviewFailures[errorCode]}這次審查未通過，尚未發布新版地圖。`;
  if (errorCode === "DOCUMENT_EVIDENCE_INVALID")
    return "教材來源文字或章節結構未通過檢查，尚未發布知識地圖。請檢查來源後重試。";
  if (errorCode === "KNOWLEDGE_STRUCTURE_INVALID")
    return "分析結果在組裝地圖時未通過結構檢查，尚未發布地圖。";
  if (errorCode === "ANALYSIS_ARTIFACT_WRITE_FAILED")
    return "分析產物無法寫入本機儲存空間，處理已停止。";
  if (errorCode === "ANALYSIS_CHECKPOINT_INVALID")
    return "已保存的分析資料未通過完整性檢查，處理已停止，沒有自動重新分析。";
  if (errorCode === "ANALYSIS_RUNTIME_CHANGED")
    return "已保存的分析進度與目前的教材分析設定不一致，已停止接續，沒有自動重新分析教材。";
  if (errorCode === "NO_USABLE_ADDED_CONTENT")
    return "新增教材沒有產生可用的知識內容，目前地圖與學習紀錄已保留。";
  if (errorCode === "SEMANTIC_INPUT_TOO_LARGE")
    return "目前批次的完整來源與處理內容超過單次模型容量，尚未發布新版地圖。需調整分批方式後接續已保存的進度。";
  if (errorCode === "SEMANTIC_BUDGET_EXHAUSTED")
    return "目前開發測試的 AI 呼叫額度已用完，沒有發布知識地圖。請先確認測試額度，再重試。";
  if (errorCode === "RESTART_INTERRUPTED") return "服務重新啟動時中斷了這次處理。";
  if (errorCode === "MATERIAL_CONFIGURATION_INVALID" || errorCode === "RUNTIME_BINDING_INVALID") {
    return "本機教材處理環境未通過安全檢查。";
  }
  if (errorCode === "NO_USABLE_EVIDENCE" || errorCode === "NO_USABLE_CONCEPT") {
    return "教材沒有產生可安全回查的概念與依據。";
  }
  return "教材分析未能安全完成，沒有發布知識地圖。";
}
