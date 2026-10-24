import type { LearningGoalSummary } from "../../api/contracts";

export function goalSummaryText(goal: LearningGoalSummary): string {
  if (goal.assessable_points === 0) return `目標 ${goal.target_percent}% · 暫無可檢測重點`;
  if (goal.achieved) return `目標 ${goal.target_percent}% 已達成`;
  if (goal.overdue_days > 0) return `目標 ${goal.target_percent}% · 逾期 ${goal.overdue_days} 天`;
  return `目標 ${goal.target_percent}% · ${goal.days_remaining === 1 ? "今天到期" : `剩 ${goal.days_remaining} 天`}`;
}
