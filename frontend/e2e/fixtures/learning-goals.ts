import type { GoalStage, LearningGoalView, LearningGoalSummary } from "../../src/api/contracts";

export const goalToday = "2026-10-24";

export function emptyGoal(materialId: string, revision: string, runId: string): LearningGoalView {
  return { schema: "learning-goal/v1", material_id: materialId, knowledge_structure_revision: revision, run_id: runId, today: goalToday, goal: null };
}

export function goalStage(conceptId: string, label = "Stack"): GoalStage {
  return { date: goalToday, quota_points: 2, completed_points: 0, remaining_points: 2, scheduled_points: 3, done: false,
    concepts: [{ concept_id: conceptId, label, remaining_points: 3 }] };
}

export function goalView(materialId: string, revision: string, runId: string, conceptId: string): LearningGoalView {
  return { ...emptyGoal(materialId, revision, runId), goal: {
    target_date: "2026-10-30", target_percent: 80, assessable_points: 20, completed_points: 6, target_points: 16, remaining_points: 10,
    achieved: false, days_remaining: 7, overdue_days: 0, behind_points: 2, daily_limit: 20, quota_over_limit: false, scheduled_over_limit: false,
    today_stage: goalStage(conceptId), future_stages: [{ ...goalStage(conceptId), date: "2026-10-25" }], preview_offset: 0, preview_has_more: false,
    manual_review: [], update_notice: null,
  } };
}

export function goalSummary(view: LearningGoalView, name = "資料結構"): LearningGoalSummary {
  const { future_stages, preview_offset, preview_has_more, manual_review, update_notice, ...metrics } = view.goal!;
  return { ...metrics, material_id: view.material_id, material_name: name, knowledge_structure_revision: view.knowledge_structure_revision, run_id: view.run_id };
}
