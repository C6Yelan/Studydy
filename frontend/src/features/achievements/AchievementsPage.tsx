import type { StudydyApiClient } from "../../api/client";
import { useLearningActivity } from "../../app/LearningProgress";
import { Icon, type IconName } from "../../ui/Icon";
import { StateView } from "../../ui/StateView";
import { BadgeCollection } from "../badges/Badges";
import { islandState, useMaterialProgress } from "../dashboard/islands";
import { LevelCard, WeekRhythm } from "../dashboard/LearningCards";
import "../dashboard/styles.css";
import "./styles.css";

// 成就頁：等級、學習總覽、本週節奏與徽章；數字全部來自學習活動 API 與教材進度摘要。
export function AchievementsPage({ apiClient }: { apiClient: StudydyApiClient }) {
  const activity = useLearningActivity(apiClient, { fresh: true });
  const { materials, progress, error } = useMaterialProgress(apiClient);
  if (activity === undefined) return <StateView title="正在讀取學習成就" description="正在整理等級、徽章與本週紀錄。" tone="loading" live />;
  if (activity === null) return <StateView title="無法讀取學習成就" description="學習活動暫時無法讀取，請稍後再試。" tone="failure" />;
  const completedConcepts = [...progress.values()].reduce((sum, item) => sum + item.completed, 0);
  // 教材清單讀取失敗或還沒讀完時，教材相關數字顯示「—」。
  const fromMaterials = (value: (items: NonNullable<typeof materials>) => number) => error || !materials ? "—" : value(materials);
  const stats: [IconName, string, string | number, string][] = [
    ["book", "is-blue", fromMaterials((items) => items.length), "份教材"],
    ["learning", "is-amber", fromMaterials((items) => items.filter((item) => islandState(item) === "learning").length), "學習中的教材"],
    ["check", "is-green", fromMaterials(() => completedConcepts), "已完成概念"],
    ["quiz", "is-blue", activity.total_answers, "累計作答"],
    ["check", "is-green", activity.total_answers ? `${Math.round((activity.total_correct / activity.total_answers) * 100)}%` : "—", "答對率"],
  ];
  return <section className="achievements" aria-labelledby="achievements-title">
    <header className="library-header collection-head">
      <div className="collection-head-text">
        <p className="eyebrow">學習成就</p>
        <h1 id="achievements-title">我的成就</h1>
        <p className="library-subtitle">作答、完成概念、複習卡片與收聽 Podcast 都會累積經驗值與徽章。</p>
      </div>
    </header>
    <div className="achievements-grid">
      <LevelCard activity={activity} />
      <section className="dashboard-overview surface" aria-labelledby="achievements-overview-title">
        <h2 id="achievements-overview-title">學習總覽</h2>
        <div className="dashboard-stats" aria-busy={materials === null && !error}>
          {stats.map(([icon, tone, value, label]) => <div key={label} className="dashboard-stat">
            <span className={`stat-icon ${tone}`}><Icon name={icon} size={18} /></span>
            <strong>{value}</strong>
            <small>{label}</small>
          </div>)}
        </div>
      </section>
      <WeekRhythm activity={activity} />
      <BadgeCollection activity={activity} />
    </div>
  </section>;
}
