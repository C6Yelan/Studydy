import type { StudydyApiClient } from "../../api/client";
import type { LearningActivityView } from "../../api/contracts";
import { useLearningActivity } from "../../app/LearningProgress";
import { Icon, type IconName } from "../../ui/Icon";
import { StateView } from "../../ui/StateView";
import { BadgeCollection } from "../badges/Badges";
import { LevelCard } from "../dashboard/LearningCards";
import "../dashboard/styles.css";
import "./styles.css";

// 經驗值規則 xp-rules/v1 的說明；數值須與後端 runtime/storage/learning_activity.py 一致（見 docs/learning.md）。
const xpRules: { icon: IconName; source: keyof LearningActivityView["xp"]["sources"]; title: string; rule: string; note: string }[] = [
  { icon: "quiz", source: "answers", title: "作答", rule: "答對 +10、答錯 +3", note: "同一題只算第一次作答，重做不重複計分。" },
  { icon: "learning", source: "concepts", title: "完成概念", rule: "每個概念 +30", note: "概念的重點全部完成時計入；同一概念只算一次。" },
  { icon: "cards", source: "cards", title: "複習概念卡", rule: "每張 +2", note: "同一張卡每天只算一次。" },
  { icon: "headphones", source: "podcast", title: "收聽 Podcast", rule: "每 5 分鐘 +5", note: "以實際收聽時間計算，每天最多 +60。" },
];

function XpRules({ activity }: { activity: LearningActivityView }) {
  const { xp } = activity;
  return <section className="xp-rules surface" aria-labelledby="xp-rules-title">
    <h2 id="xp-rules-title">經驗值怎麼算</h2>
    <ul>
      {xpRules.map((item) => <li key={item.source}>
        <span className="stat-icon is-blue"><Icon name={item.icon} size={18} /></span>
        <span className="xp-rule-text"><b>{item.title}<em>{item.rule}</em></b><small>{item.note}</small></span>
        <span className="xp-rule-earned"><b>{xp.sources[item.source]}</b><small>已獲得 XP</small></span>
      </li>)}
    </ul>
    <p className="xp-level-rule">
      <b>升級</b>第 n 級升到下一級需要 100×n XP（1→2 需 100、2→3 需 200…）。目前第 {xp.level} 級，再 {xp.level_target - xp.level_xp} XP 升到第 {xp.level + 1} 級。
    </p>
  </section>;
}

// 成就：等級、經驗值計算方式與各系列徽章。
export function AchievementsPage({ apiClient }: { apiClient: StudydyApiClient }) {
  const activity = useLearningActivity(apiClient, { fresh: true });
  return <section className="achievements" aria-labelledby="achievements-title">
    <header className="library-header collection-head">
      <div className="collection-head-text">
        <p className="eyebrow">學習成就</p>
        <h1 id="achievements-title">成就</h1>
        <p className="library-subtitle">作答、完成概念、複習卡片與收聽 Podcast 都會累積經驗值，達到門檻就能取得各系列的下一層徽章。</p>
      </div>
    </header>
    {activity === undefined ? <StateView title="正在讀取學習成就" description="正在整理等級與徽章。" tone="loading" live />
      // 頁面標題已是 h1，錯誤區塊用 h2。
      : activity === null ? <section className="section-failure surface" role="alert">
        <h2>無法讀取學習成就</h2>
        <p>學習活動暫時無法讀取，請稍後再試。</p>
      </section>
      : <div className="achievements-layout">
        <LevelCard activity={activity} />
        <XpRules activity={activity} />
        <BadgeCollection activity={activity} />
      </div>}
  </section>;
}
