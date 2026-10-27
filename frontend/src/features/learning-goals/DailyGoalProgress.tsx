import "./styles.css";
import type { GoalStage } from "../../api/contracts";
import { Progress } from "../../ui/Progress";

export function DailyGoalProgress({ stage }: { stage: GoalStage }) {
  return <div className="daily-goal-progress">
    <p>今天學 {stage.quota_points} 個重點 · 已完成 {stage.completed_points}</p>
    {stage.quota_points > 0 && <Progress value={stage.completed_points} max={stage.quota_points} tone={stage.done ? "complete" : "default"} aria-label="今日重點進度" aria-valuetext={`已完成 ${stage.completed_points}／${stage.quota_points} 個重點`} />}
    {stage.scheduled_points !== stage.remaining_points && <small>為保留完整概念題組，接下來安排 {stage.scheduled_points} 個重點（今日尚需 {stage.remaining_points} 個）。</small>}
    {stage.done && <small>今日關卡已完成</small>}
  </div>;
}
