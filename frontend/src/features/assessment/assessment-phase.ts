import type { AssessmentSetSummary } from "../../api/contracts";

export type AssessmentPhase = "preparation" | "preparing" | "intervention" | "question" | "result";

// 僅投影既有題組狀態為版面；resume 摘要與完整題組共用，避免讀取期間閃回教材。
export function assessmentPhase(group?: Pick<AssessmentSetSummary, "status" | "published_count" | "answered_count"> | null): AssessmentPhase {
  if (!group) return "preparation";
  if (group.status === "preparing") return "preparing";
  if (group.status === "partial_ready" || group.status === "failed") return "intervention";
  if (["ready", "in_progress"].includes(group.status) && group.answered_count < group.published_count) return "question";
  return "result";
}
