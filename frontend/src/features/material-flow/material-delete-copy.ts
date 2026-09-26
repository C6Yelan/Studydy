import type { MaterialLibraryItem, SourceView } from "../../api/contracts";

export function materialDeleteCopy(material?: MaterialLibraryItem | null, sources?: SourceView[]) {
  const hasMap = (material?.available_structures.length ?? 0) > 0;
  const hasHistory = (material?.study_sessions.length ?? 0) > 0;
  const knownSources = sources ?? (material?.source ? [material.source] : []);
  const processing = [material?.latest_attempt, ...knownSources].some(
    (value) => value?.status === "pending" || value?.status === "running",
  );
  const notice = processing
    ? `目前的教材${hasMap ? "更新" : "處理"}會先停止，再刪除這份教材。`
    : undefined;
  let scope = "將刪除這份教材及其相關內容。此操作無法復原。";
  if (hasHistory) {
    scope = `將刪除這份教材${hasMap ? "、知識地圖" : ""}，以及相關的學習紀錄、題目與作答。此操作無法復原。`;
  } else if (hasMap) {
    scope = "將刪除這份教材及已建立的知識地圖。此操作無法復原。";
  } else if (material) {
    const files = sources?.length ? ` ${sources.length} 份教材` : "教材檔案";
    const converted = knownSources.some(
      (source) => source.media_type !== "application/pdf" && source.normalized_artifact_id,
    );
    const conversion = converted ? "，以及已產生的轉換內容" : "";
    scope = `將刪除目前已上傳的${files}${conversion}。此操作無法復原。`;
  }
  return { notice, scope };
}
