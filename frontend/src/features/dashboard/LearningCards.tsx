import type { LearningActivityView, LearningQuest } from "../../api/contracts";
import { Icon, type IconName } from "../../ui/Icon";

// 首頁的今日任務與個人檔案的本週節奏；數字全部來自學習活動 API（規則 xp-rules/v2）。

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

const weekdays = ["一", "二", "三", "四", "五", "六", "日"];
const minutes = (seconds: number) => Math.floor(seconds / 60);

// 本週節奏分項顯示每天作答題數、標記的概念卡張數與實際收聽分鐘數。
export function WeekRhythm({ activity }: { activity: LearningActivityView }) {
  const totals = activity.week.reduce((sum, day) => ({
    answers: sum.answers + day.answers, correct: sum.correct + day.correct,
    cards: sum.cards + day.card_reviews, listened: sum.listened + day.listened_seconds,
  }), { answers: 0, correct: 0, cards: 0, listened: 0 });
  const peak = {
    answers: Math.max(1, ...activity.week.map((day) => day.answers)),
    cards: Math.max(1, ...activity.week.map((day) => day.card_reviews)),
    listened: Math.max(60, ...activity.week.map((day) => day.listened_seconds)),
  };
  const height = (value: number, max: number) => `${value ? Math.max(12, (value / max) * 100) : 0}%`;
  const parts = [
    totals.answers && `作答 ${totals.answers} 題（答對率 ${Math.round((totals.correct / totals.answers) * 100)}%）`,
    totals.cards && `標記 ${totals.cards} 張卡`,
    minutes(totals.listened) && `收聽 ${minutes(totals.listened)} 分鐘`,
  ].filter(Boolean);
  return <section className="dashboard-week surface" aria-labelledby="dashboard-week-title">
    <header>
      <h2 id="dashboard-week-title">本週學習節奏</h2>
      <span className={`dashboard-streak${activity.streak_days ? "" : " is-idle"}`}>
        <Icon name="flame" size={16} />{activity.streak_days ? `連續 ${activity.streak_days} 天` : "尚未連續"}
      </span>
    </header>
    <ol className="week-days">
      {activity.week.map((day, index) => {
        const today = day.date === activity.today;
        const future = day.date > activity.today;
        const detail = [
          day.answers && `作答 ${day.answers} 題，答對 ${day.correct} 題`,
          day.card_reviews && `標記 ${day.card_reviews} 張卡`,
          minutes(day.listened_seconds) && `收聽 ${minutes(day.listened_seconds)} 分鐘`,
        ].filter(Boolean).join("，");
        return <li key={day.date} className={[today && "is-today", day.active && "is-active", future && "is-future"].filter(Boolean).join(" ")}
          aria-label={`星期${weekdays[index]} ${Number(day.date.slice(5, 7))}/${Number(day.date.slice(8))}${today ? "（今天）" : ""}：${future ? "尚未到" : detail || "沒有學習紀錄"}`}>
          <span className="week-bars" aria-hidden="true">
            <i className="is-answers" style={{ height: height(day.answers, peak.answers) }} />
            <i className="is-cards" style={{ height: height(day.card_reviews, peak.cards) }} />
            <i className="is-listen" style={{ height: height(minutes(day.listened_seconds) ? day.listened_seconds : 0, peak.listened) }} />
          </span>
          <span className="week-active" aria-hidden="true">{day.active ? <Icon name="flame" size={13} /> : future ? "" : "–"}</span>
          <small aria-hidden="true">{weekdays[index]}</small>
        </li>;
      })}
    </ol>
    <ul className="week-legend" aria-hidden="true">
      <li className="is-answers">作答</li><li className="is-cards">概念卡</li><li className="is-listen">Podcast</li>
    </ul>
    <p className="week-summary">
      {parts.length ? <>本週{parts.join(" · ")}</>
        : activity.streak_days ? "今天作答、標記卡片或收聽 Podcast，就能延續連續紀錄。" : "本週還沒有學習紀錄，作答、標記卡片或收聽都會出現在這裡。"}
    </p>
    <small className="week-note">依作答、概念卡標記與實際收聽紀錄計算，以台灣時間為準；收聽滿 1 分鐘才算當天有學習。</small>
  </section>;
}
