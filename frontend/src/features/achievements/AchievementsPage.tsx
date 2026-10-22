import type { StudydyApiClient } from "../../api/client";
import type { LearningActivityView } from "../../api/contracts";
import { useLearningActivity } from "../../app/LearningProgress";
import { Icon, type IconName } from "../../ui/Icon";
import { StateView } from "../../ui/StateView";
import { BadgeCollection } from "../badges/Badges";
import "../dashboard/styles.css";
import "./styles.css";

// 經驗值規則 xp-rules/v3 的圖解；數值須與後端 runtime/learning_activity.py 一致（見 docs/learning.md）。
type Source = keyof LearningActivityView["xp"]["sources"];
const xpRules: { icon: IconName; source: Source; tone: string; title: string; gains: [string, string][]; note: string }[] = [
  { icon: "quiz", source: "answers", tone: "is-answers", title: "作答", gains: [["+10", "答對"], ["+3", "答錯"]], note: "同一題只算第一次作答" },
  { icon: "learning", source: "concepts", tone: "is-concepts", title: "完成概念", gains: [["+30", "每個概念"]], note: "重點全部完成時計入，同一概念只算一次" },
  { icon: "cards", source: "cards", tone: "is-cards", title: "標記概念卡", gains: [["+2", "每張"]], note: "同一張卡每天只算一次，連續狂點不計，每天最多 +40" },
  { icon: "headphones", source: "podcast", tone: "is-podcast", title: "收聽 Podcast", gains: [["+5", "每 5 分鐘"]], note: "實際收聽時間，每天最多 +60" },
];

// 等級與經驗值：目前等級與本級進度（升級所需 XP 由後端分段曲線決定），下方圖解經驗值來源與各來源規則。
function LevelAndXp({ activity }: { activity: LearningActivityView }) {
  const { xp } = activity;
  const total = Math.max(1, xp.total);
  const ratio = xp.level_target ? xp.level_xp / xp.level_target : 0;
  return <section className="level-xp dashboard-level surface" aria-labelledby="level-xp-title">
    <header>
      <img src="/assets/studydy/brand-idle.png" alt="" />
      <div>
        <h2 id="level-xp-title">第 {xp.level} 級</h2>
        <small>累計 {xp.total} XP{xp.today > 0 && <> · 今天 <b>+{xp.today}</b></>}</small>
      </div>
    </header>
    <div className="level-bar" role="progressbar" aria-label={`第 ${xp.level} 級經驗值`}
      aria-valuemin={0} aria-valuemax={xp.level_target} aria-valuenow={xp.level_xp}
      aria-valuetext={`${xp.level_xp} / ${xp.level_target} XP`}>
      <i style={{ width: `${Math.max(ratio > 0 ? 3 : 0, Math.min(100, ratio * 100))}%` }} />
    </div>
    <p className="level-next"><span>{xp.level_xp} / {xp.level_target} XP</span><span>再 {xp.level_target - xp.level_xp} XP 升到第 {xp.level + 1} 級</span></p>
    <section className="xp-rules" aria-labelledby="xp-rules-title">
      <h3 id="xp-rules-title">經驗值怎麼算</h3>
      <div className="xp-mix">
        <p><b>{xp.total} XP</b> 從哪裡來</p>
        <div className="xp-mix-bar" role="img" aria-label={xpRules.map((rule) => `${rule.title} ${xp.sources[rule.source]} XP`).join("、")}>
          {xpRules.map((rule) => xp.sources[rule.source] > 0 && <i key={rule.source} className={rule.tone} style={{ width: `${(xp.sources[rule.source] / total) * 100}%` }} />)}
        </div>
      </div>
      <ul className="xp-sources">
        {xpRules.map((rule) => <li key={rule.source} className={rule.tone}>
          <span className="xp-source-icon"><Icon name={rule.icon} size={20} /></span>
          <span className="xp-source-text">
            <b className="xp-source-title">{rule.title}</b>
            <small className="xp-source-note">{rule.note}</small>
          </span>
          <span className="xp-gains">{rule.gains.map(([value, label]) => <span key={label}><strong>{value}</strong><small>{label}</small></span>)}</span>
          <span className="xp-source-earned"><b>{xp.sources[rule.source]}</b><small>已獲得</small></span>
        </li>)}
      </ul>
    </section>
  </section>;
}

// 成就：左欄等級與經驗值（捲動時固定），右欄成就總覽。
export function AchievementsPage({ apiClient }: { apiClient: StudydyApiClient }) {
  const activity = useLearningActivity(apiClient, { fresh: true });
  return <section className="achievements" aria-labelledby="achievements-title">
    <header className="library-header collection-head">
      <div className="collection-head-text">
        <h1 id="achievements-title">成就</h1>
        <p className="library-subtitle">作答、完成概念、標記概念卡與收聽 Podcast 都會累積經驗值，達到門檻就能取得各系列的下一層徽章。</p>
      </div>
    </header>
    {activity === undefined ? <StateView title="正在讀取學習成就" description="正在整理等級與徽章。" tone="loading" live />
      // 頁面標題已是 h1，錯誤區塊用 h2。
      : activity === null ? <section className="section-failure surface" role="alert">
        <h2>無法讀取學習成就</h2>
        <p>學習活動暫時無法讀取，請稍後再試。</p>
      </section>
      : <div className="achievements-layout">
        <LevelAndXp activity={activity} />
        <BadgeCollection activity={activity} />
      </div>}
  </section>;
}
