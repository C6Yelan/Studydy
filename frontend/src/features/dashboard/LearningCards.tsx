import type { LearningGoalSummary, LearningQuest } from "../../api/contracts";
import { Icon, type IconName } from "../../ui/Icon";
import { Progress } from "../../ui/Progress";

// 首頁的「今天的小事」；數字全部來自學習活動 API。

const questText: Record<LearningQuest["kind"], { icon: IconName; title: (quest: LearningQuest) => string; action: string }> = {
  mark_cards: { icon: "cards", title: (q) => `標記 ${q.target} 張概念卡`, action: "去標記" },
  continue_study: { icon: "quiz", title: () => "完成 1 次題組作答", action: "繼續學習" },
  finish_podcast_episode: { icon: "headphones", title: () => "聽完 1 集還沒聽完的 Podcast", action: "去收聽" },
};

/** 今天的小事的完成數：每日任務加上每份教材當天的關卡；首頁和知識地圖的「今天」共用。 */
export function dailyTaskCount(quests: LearningQuest[], goals: LearningGoalSummary[]) {
  return {
    done: quests.filter((quest) => quest.done).length + goals.filter(goal => goal.today_stage.done).length,
    total: quests.length + goals.length,
  };
}

export function DailyQuests({ quests, goals, onOpen, onOpenGoal, onContinue }: {
  quests: LearningQuest[]; goals: LearningGoalSummary[];
  onOpen: (quest: LearningQuest) => void; onOpenGoal: (goal: LearningGoalSummary) => void;
  /** 全部完成時顯示「繼續學習」；沒有可接續的教材就不傳。 */
  onContinue?: () => void;
}) {
  const { done, total } = dailyTaskCount(quests, goals);
  return <section className="dashboard-quests surface" aria-labelledby="dashboard-quests-title">
    <header>
      <h2 id="dashboard-quests-title">今天的小事</h2>
      <span className={done === total ? "quests-count is-done" : "quests-count"}>{done} / {total}</span>
    </header>
    <ul>
      {quests.map((quest) => {
        const text = questText[quest.kind];
        const title = text.title(quest);
        return <li key={`${quest.kind}:${quest.material_id ?? ""}`} className={quest.done ? "is-done" : undefined}>
          <button className="quest-row" type="button" aria-label={`${text.action}：${title}`} onClick={() => onOpen(quest)}>
          <span className="quest-icon" aria-hidden="true"><Icon name={quest.done ? "check" : text.icon} size={18} /></span>
          <span className="quest-text"><b title={title}>{quest.kind === "continue_study" ? "完成題組作答" : quest.kind === "finish_podcast_episode" ? "聽完 1 集 Podcast" : title}</b>
            <Progress value={quest.progress} max={quest.target} tone={quest.done ? "complete" : "default"} aria-label="完成進度" aria-valuetext={`${quest.progress} / ${quest.target}`} /></span>
          <span className="quest-progress" aria-label={`已完成 ${quest.progress}／${quest.target}`}>{quest.progress}/{quest.target}</span>
          <Icon name="chevron-right" size={16} />
          </button>
        </li>;
      })}
      {goals.map(goal => <li key={`goal_stage:${goal.material_id}`} className={`goal-quest${goal.today_stage.done ? " is-done" : ""}`}>
        <button className="quest-row" type="button" aria-label={`查看關卡：${goal.material_name}`} onClick={() => onOpenGoal(goal)}>
        <span className="quest-icon" aria-hidden="true"><Icon name={goal.today_stage.done ? "check" : "learning"} size={18} /></span>
        <span className="quest-text"><b>今天的關卡</b><small className="quest-material">{goal.material_name}</small>
          <Progress value={goal.today_stage.completed_points} max={goal.today_stage.quota_points || 1} tone={goal.today_stage.done ? "complete" : "default"} aria-label="關卡進度"
            aria-valuetext={`${goal.today_stage.completed_points} / ${goal.today_stage.quota_points} 個重點`} /></span>
        <span className="quest-progress" aria-label={`已完成 ${goal.today_stage.completed_points}／${goal.today_stage.quota_points} 個重點`}>{goal.today_stage.completed_points}/{goal.today_stage.quota_points} 重點</span>
        <Icon name="chevron-right" size={16} />
        </button>
      </li>)}
    </ul>
    {total > 0 && done === total && <div className="quests-cheer">
      <p>今天的小事都完成了，做得很好！想再多學一點也可以。</p>
      {onContinue && <button className="primary-button" type="button" onClick={onContinue}>繼續學習</button>}
    </div>}
  </section>;
}
