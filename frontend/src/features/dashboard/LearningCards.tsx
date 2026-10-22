import type { LearningQuest } from "../../api/contracts";
import { Icon, type IconName } from "../../ui/Icon";

// 首頁的今日任務；數字全部來自學習活動 API。

const questText: Record<LearningQuest["kind"], { icon: IconName; title: (quest: LearningQuest) => string; action: string }> = {
  mark_cards: { icon: "cards", title: (q) => `標記 ${q.target} 張概念卡`, action: "去標記" },
  continue_study: { icon: "quiz", title: () => "完成 1 次題組作答", action: "繼續學習" },
  finish_podcast_episode: { icon: "headphones", title: () => "聽完 1 集還沒聽完的 Podcast", action: "去收聽" },
};

export function DailyQuests({ quests, onOpen }: { quests: LearningQuest[]; onOpen: (quest: LearningQuest) => void }) {
  const done = quests.filter((quest) => quest.done).length;
  return <section className="dashboard-quests surface" aria-labelledby="dashboard-quests-title">
    <header>
      <h2 id="dashboard-quests-title">今日任務</h2>
      <span className={done === quests.length ? "quests-count is-done" : "quests-count"}>{done} / {quests.length}</span>
    </header>
    <ul>
      {quests.map((quest) => {
        const text = questText[quest.kind];
        const title = text.title(quest);
        // 有入口才顯示按鈕；已完成的任務只顯示勾選。
        const actionable = !quest.done && (quest.kind !== "continue_study" || quest.study_session_id)
          && (quest.kind !== "finish_podcast_episode" || quest.podcast_id);
        return <li key={quest.kind} className={quest.done ? "is-done" : undefined}>
          <span className="quest-icon" aria-hidden="true"><Icon name={quest.done ? "check" : text.icon} size={18} /></span>
          <span className="quest-text">
            <b>{title}</b>
            <small>{quest.done ? "今天已完成" : quest.target > 1 ? `已完成 ${quest.progress} / ${quest.target}` : "今天還沒完成"}</small>
          </span>
          {actionable && <button className="text-button" type="button" aria-label={`${text.action}：${title}`} onClick={() => onOpen(quest)}>
            {text.action}<Icon name="chevron-right" size={16} />
          </button>}
        </li>;
      })}
    </ul>
  </section>;
}
