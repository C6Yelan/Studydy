import { useEffect, useId, useState } from "react";
import { Progress } from "../../ui/Progress";
import type { StudydyApiClient } from "../../api/client";
import type { LearningActivityView, LearningRhythmView } from "../../api/contracts";
import { useLearningActivity } from "../../app/LearningProgress";
import { Icon, type IconName } from "../../ui/Icon";
import { StateView } from "../../ui/StateView";
import { BadgeCollection, BadgeSeriesWall, NearBadges, shownBadges } from "../badges/Badges";
import "../dashboard/styles.css";
import "./styles.css";

// 經驗值規則 xp-rules/v5 的圖解；數值須與後端 runtime/learning_activity.py 一致（見 docs/learning.md）。
type Source = keyof LearningActivityView["xp"]["sources"];
const xpRules: { icon: IconName; source: Source; tone: string; title: string; gains: [string, string][]; note: string }[] = [
  { icon: "quiz", source: "answers", tone: "is-answers", title: "作答", gains: [["+10", "答對"], ["+3", "答錯"]], note: "同一題只算第一次作答" },
  { icon: "learning", source: "concepts", tone: "is-concepts", title: "完成概念", gains: [["+30", "每個概念"]], note: "重點全部完成時計入，同一概念只算一次" },
  { icon: "cards", source: "cards", tone: "is-cards", title: "標記概念卡", gains: [["+2", "每張"]], note: "同一張卡每天只算一次，連續狂點不計，每天最多 +40" },
  { icon: "headphones", source: "podcast", tone: "is-podcast", title: "收聽 Podcast", gains: [["+5", "每 5 分鐘"]], note: "實際收聽時間，每天最多 +60" },
  { icon: "route", source: "goals", tone: "is-goals", title: "目標與關卡", gains: [["+15", "每關"], ["+50", "準時達標"]], note: "每份教材每天一關，每天最多 +45；達標每份教材一次" },
  { icon: "check", source: "difficulties", tone: "is-difficulties", title: "克服難點", gains: [["+20", "每個難點"]], note: "錯誤率降到一半以下時計入，同一概念只算一次" },
];

// 等級卡：目前等級、累計 XP 與本級進度（升級所需 XP 由後端分段曲線決定）。
function LevelCard({ activity }: { activity: LearningActivityView }) {
  const { xp } = activity;
  return <section className="level-card" aria-labelledby="level-xp-title">
    <small>我的等級</small>
    <h2 id="level-xp-title">第 {xp.level} 級</h2>
    <span className="level-total">累計 {xp.total} XP{xp.today > 0 && <> · 今天 <b>+{xp.today}</b></>}</span>
    <Progress value={xp.level_xp} max={xp.level_target} aria-label={`第 ${xp.level} 級經驗值`} aria-valuetext={`${xp.level_xp} / ${xp.level_target} XP`} />
    <p className="level-next"><span>{xp.level_xp} / {xp.level_target} XP</span><span>再 {xp.level_target - xp.level_xp} XP 升到第 {xp.level + 1} 級</span></p>
    <img src="/assets/studydy/brand-guide.png" alt="" />
  </section>;
}

const weekdays = ["日", "一", "二", "三", "四", "五", "六"];

// 這週 XP 長條圖：每天的 XP 來自每週紀錄（rhythm）每一天的 xp；這週 0 XP 時只顯示一句怎麼開始累積；
// 讀不到只顯示一句說明，不影響其他區塊。
function WeekXp({ apiClient, today, onRules }: { apiClient: StudydyApiClient; today: number; onRules: () => void }) {
  const [week, setWeek] = useState<LearningRhythmView | null | undefined>(undefined);
  useEffect(() => {
    let cancelled = false;
    void apiClient.getLearningRhythm("week", 0).then(
      (value) => { if (!cancelled) setWeek(value); },
      () => { if (!cancelled) setWeek(null); },
    );
    return () => { cancelled = true; };
  }, [apiClient]);
  const total = week?.days.reduce((sum, day) => sum + day.xp, 0) ?? 0;
  const peak = Math.max(1, ...(week?.days.map((day) => day.xp) ?? []));
  const rules = <button className="text-button" type="button" onClick={onRules}>經驗值怎麼算？<Icon name="chevron-right" size={16} /></button>;
  if (week && total === 0) return <section className="week-xp surface">
    <p>這週還沒拿到經驗值。答一題、翻一張卡或聽一段 Podcast 就會開始累積。</p>
    {rules}
  </section>;
  return <section className="week-xp surface" aria-labelledby="week-xp-title" aria-busy={week === undefined}>
    <header>
      <h2 id="week-xp-title">{week ? `這週拿到 ${total} XP` : "這週 XP"}</h2>
      {today > 0 && <small>今天 {today}</small>}
    </header>
    {week === null && <p role="status">這週的 XP 暫時無法讀取。</p>}
    {week && <ol className="week-xp-bars">
      {week.days.map((day) => {
        const label = weekdays[new Date(`${day.date}T00:00:00Z`).getUTCDay()];
        return <li key={day.date} aria-label={`週${label}${day.date === week.today ? "（今天）" : ""}：${day.xp} XP`}>
          <span className="week-xp-bar" aria-hidden="true"><i className={day.date === week.today ? "is-today" : undefined} style={{ height: `${day.xp ? Math.max(6, day.xp / peak * 100) : 2}%` }} /></span>
          <small aria-hidden="true">{label}</small>
        </li>;
      })}
    </ol>}
    {rules}
  </section>;
}

// 「全部徽章和規則」：全部徽章（含特殊成就）與各來源 XP 的算法，平常收合。
// 入口按鈕的數量和總覽同一個計數，不含尚未揭曉的成就鏈步驟。
function AllBadgesAndRules({ activity, open, onToggle }: { activity: LearningActivityView; open: boolean; onToggle: () => void }) {
  const { xp } = activity;
  const total = Math.max(1, xp.total);
  const panelId = useId();
  return <section className="achievements-all" aria-label="全部徽章和規則">
    <button className="secondary-button achievements-all-toggle" type="button" aria-expanded={open} aria-controls={panelId} onClick={onToggle}>
      看全部 {shownBadges(activity).length} 個徽章和特別成就<span className="achievements-all-chevron" aria-hidden="true"><Icon name="chevron-right" size={18} /></span>
    </button>
    {open && <div id={panelId} className="achievements-all-panel">
      <BadgeCollection activity={activity} />
      <section className="xp-rules surface" id="xp-rules" aria-labelledby="xp-rules-title" tabIndex={-1}>
        <h2 id="xp-rules-title">經驗值怎麼算</h2>
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
    </div>}
  </section>;
}

// 成就：上方等級與這週 XP、快拿到了（寬度不夠時上下排），下方 5 區系列卡，最後是收合的全部徽章和規則。
export function AchievementsPage({ apiClient }: { apiClient: StudydyApiClient }) {
  const activity = useLearningActivity(apiClient, { fresh: true });
  const [allOpen, setAllOpen] = useState(false);
  // 「經驗值怎麼算？」展開全部徽章和規則，並把焦點移到規則區。
  const [showRules, setShowRules] = useState(0);
  useEffect(() => {
    if (!showRules) return;
    const frame = requestAnimationFrame(() => {
      const rules = document.getElementById("xp-rules");
      rules?.scrollIntoView({ behavior: "smooth", block: "start" });
      rules?.focus({ preventScroll: true });
    });
    return () => cancelAnimationFrame(frame);
  }, [showRules]);
  return <section className="achievements" aria-labelledby="achievements-title">
    <header className="library-header collection-head">
      <div className="collection-head-text">
        <h1 id="achievements-title">成就</h1>
        <p className="library-subtitle">學習時做的每件事都會累積進度。每一種徽章有好幾層：銅、銀、金、鑽石、星耀，越往上越難。</p>
      </div>
    </header>
    {activity === undefined ? <StateView title="正在讀取學習成就" description="正在整理等級與徽章。" tone="loading" live />
      // 頁面標題已是 h1，錯誤區塊用 h2。
      : activity === null ? <section className="section-failure surface" role="alert">
        <h2>無法讀取學習成就</h2>
        <p>學習活動暫時無法讀取，請稍後再試。</p>
      </section>
      : <>
        <div className="achievements-top">
          <div className="achievements-side">
            <LevelCard activity={activity} />
            <WeekXp apiClient={apiClient} today={activity.xp.today} onRules={() => { setAllOpen(true); setShowRules((value) => value + 1); }} />
          </div>
          <NearBadges activity={activity} />
        </div>
        <BadgeSeriesWall activity={activity} />
        <AllBadgesAndRules activity={activity} open={allOpen} onToggle={() => setAllOpen((value) => !value)} />
      </>}
  </section>;
}
