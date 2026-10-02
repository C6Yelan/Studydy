import type { KnowledgeStructureView, LearnerProgressView } from "../../api/contracts";

export type CardRecommendation = { conceptId: string; reason: string };
export type RecommendationMode = "weakness" | "path";
export type CardRecommendations = { items: CardRecommendation[]; message: string };

export function recommendCards(
  view: KnowledgeStructureView,
  progress: LearnerProgressView | null,
  mode: RecommendationMode,
  count: number,
): CardRecommendations {
  if (!Number.isInteger(count) || count < 1 || count > view.concepts.length)
    return { items: [], message: `請選擇 1–${view.concepts.length} 張卡片。` };
  if (progress && progress.knowledge_structure_revision !== view.knowledge_structure_revision)
    return { items: [], message: "學習進度與教材版本不一致，請重新讀取。" };

  const concepts = new Map(view.concepts.map((concept) => [concept.concept_id, concept]));
  const path = [...view.initial_learning_path]
    .sort((a, b) => a.position - b.position)
    .map((step) => step.concept_id)
    .filter((id) => concepts.has(id));
  if (!progress) {
    return {
      items: path.slice(0, count).map((conceptId) => ({ conceptId, reason: "依教材學習路徑推薦" })),
      message: "此版本尚無學習紀錄，先從教材學習路徑的起點推薦。",
    };
  }

  const states = new Map(progress.concept_states.map((state) => [state.concept_id, state]));
  if (mode === "weakness") {
    const weak = new Set(progress.weaknesses.filter((finding) => {
      const claims = concepts.get(finding.concept_id)?.claims ?? [];
      return claims.some((claim) => finding.claim_ids.includes(claim.claim_id));
    }).map((finding) => finding.concept_id));
    const items = path.filter((id) => weak.has(id) || states.get(id)?.status === "needs_review")
      .slice(0, count).map((conceptId) => ({ conceptId, reason: "有待複習重點" }));
    return {
      items,
      message: items.length === 0
        ? "目前沒有待複習的弱點，可改用「依學習路徑」或手動選取。"
        : `推薦 ${items.length} 張弱點卡，依學習路徑排列。${items.length < count ? "弱點數量少於指定張數，不另外補入其他概念。" : ""}`,
    };
  }

  const start = Math.max(0, path.indexOf(progress.current_concept_id ?? ""));
  // 從目前位置接續；前段仍未掌握的概念排在後面，不因移動學習位置而遺漏。
  const ordered = [...path.slice(start), ...path.slice(0, start)];
  const items = ordered.filter((id) => states.get(id)?.status !== "mastered").slice(0, count)
    .map((conceptId) => ({
      conceptId,
      reason: conceptId === progress.current_concept_id ? "目前正在學習" : "依學習路徑推薦，尚未掌握",
    }));
  return {
    items,
    message: items.length === 0
      ? "此版本的概念都已掌握，可手動挑選想回顧的內容。"
      : "從目前學習位置依序推薦尚未掌握的概念；不足時接續前段尚未掌握的概念。",
  };
}
