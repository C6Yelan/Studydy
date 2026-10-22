import type { StudydyApiClient } from "../../api/client";
import type { LearningActivityView } from "../../api/contracts";
import { useLearningActivity } from "../../app/LearningProgress";
import { Icon, type IconName } from "../../ui/Icon";
import { StateView } from "../../ui/StateView";
import { BadgeCollection } from "../badges/Badges";
import "../dashboard/styles.css";
import "./styles.css";

// 經驗值規則 xp-rules/v1 的圖解；數值須與後端 runtime/storage/learning_activity.py 一致（見 docs/learning.md）。
type Source = keyof LearningActivityView["xp"]["sources"];
const xpRules: { icon: IconName; source: Source; tone: string; title: string; gains: [string, string][]; note: string }[] = [
  { icon: "quiz", source: "answers", tone: "is-answers", title: "作答", gains: [["+10", "答對"], ["+3", "答錯"]], note: "同一題只算第一次作答" },
  { icon: "learning", source: "concepts", tone: "is-concepts", title: "完成概念", gains: [["+30", "每個概念"]], note: "重點全部完成時計入，同一概念只算一次" },
  { icon: "cards", source: "cards", tone: "is-cards", title: "複習概念卡", gains: [["+2", "每張"]], note: "同一張卡每天只算一次" },
  { icon: "headphones", source: "podcast", tone: "is-podcast", title: "收聽 Podcast", gains: [["+5", "每 5 分鐘"]], note: "實際收聽時間，每天最多 +60" },
];

// 升級階梯：第 n 級升到 n+1 需要 100×n XP，台階越來越高；標出目前位置與還差多少。
function LevelLadder({ level, levelXp, levelTarget }: { level: number; levelXp: number; levelTarget: number }) {
  const first = Math.max(1, level - 1);
  const steps = Array.from({ length: 5 }, (_, index) => first + index);
  const tallest = 100 * steps[steps.length - 1];
  return <ol className="xp-ladder" aria-label={`升級階梯：第 ${level} 級升到第 ${level + 1} 級需要 ${levelTarget} XP，目前已得 ${levelXp} XP`}>
    {steps.map((step) => {
      const need = 100 * step;
      const state = step < level ? "is-done" : step === level ? "is-current" : "is-next";
      const fill = step < level ? 1 : step === level ? levelXp / levelTarget : 0;
      return <li key={step} className={state} aria-hidden="true">
        {step === level && <span className="xp-ladder-you">你在這裡</span>}
        <span className="xp-ladder-step" style={{ height: `${Math.max(18, (need / tallest) * 100)}%` }}>
          <i style={{ height: `${fill * 100}%` }} />
        </span>
        <b>Lv {step}→{step + 1}</b>
        <small>{need} XP</small>
      </li>;
    })}
  </ol>;
}

// 等級與經驗值：目前等級與本級進度，下方圖解經驗值來源、各來源規則與升級階梯。
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
      <div className="xp-level">
        <p><b>升級</b>每一級比上一級多需要 100 XP（第 n 級升到下一級需 100×n）。</p>
        <LevelLadder level={xp.level} levelXp={xp.level_xp} levelTarget={xp.level_target} />
      </div>
    </section>
  </section>;
}

// 成就：左欄等級與經驗值（捲動時固定），右欄成就總覽。
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
        <LevelAndXp activity={activity} />
        <BadgeCollection activity={activity} />
      </div>}
  </section>;
}
