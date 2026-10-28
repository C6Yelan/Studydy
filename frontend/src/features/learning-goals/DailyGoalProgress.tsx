import "./styles.css";
import { useEffect, useState } from "react";
import type { GoalStage } from "../../api/contracts";
import { playCue } from "../../ui/feedback-sound";
import { Progress } from "../../ui/Progress";

// 記住各教材今天的關卡是否已完成；回到畫面時由「未完成」變成「已完成」才算剛完成，重新整理後不重播。
const seenDone = new Map<string, boolean>();

export function DailyGoalProgress({ stage, stageKey }: { stage: GoalStage; stageKey: string }) {
  const [justDone, setJustDone] = useState(false);
  useEffect(() => {
    const key = `${stageKey}:${stage.date}`;
    const fresh = stage.done && seenDone.get(key) === false;
    seenDone.set(key, stage.done);
    setJustDone(fresh);
    if (fresh) playCue("stage");
  }, [stageKey, stage.date, stage.done]);
  return <div className="daily-goal-progress">
    <span>今天 {stage.completed_points}/{stage.quota_points} 個重點{stage.done && <small className={justDone ? "stage-complete-pop" : undefined}> · 今日關卡已完成</small>}</span>
    {stage.quota_points > 0 && <Progress value={stage.completed_points} max={stage.quota_points} tone={stage.done ? "complete" : "default"} aria-label="今日重點進度" aria-valuetext={`已完成 ${stage.completed_points}／${stage.quota_points} 個重點`} />}
  </div>;
}
