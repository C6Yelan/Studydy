import type { StudydyApiClient } from "../../api/client";
import { useProfile } from "../../app/HeaderTools";
import { useLearningActivity } from "../../app/LearningProgress";
import { Icon, type IconName } from "../../ui/Icon";
import { StateView } from "../../ui/StateView";
import { islandState, useMaterialProgress } from "../dashboard/islands";
import { AccountSettings, DevicesCard, useAccountSessions } from "./AccountSettings";
import { LearningRhythm } from "./LearningRhythm";
import { StorageCard } from "./StorageCard";
import "./styles.css";

// 個人檔案：左欄由上而下是學習總覽（一列小數字）、學習節奏、登入裝置與帳號空間，右欄是帳號設定（名稱與密碼）；
// 等級與徽章在「成就」頁。學習活動讀不到時只有總覽顯示錯誤，節奏、登入裝置與帳號設定各自讀取。
export function ProfilePage({ apiClient }: { apiClient: StudydyApiClient }) {
  const name = useProfile(apiClient)?.display_name ?? null;
  const activity = useLearningActivity(apiClient, { fresh: true });
  const { materials, progress, error } = useMaterialProgress(apiClient);
  const [sessions, setSessions] = useAccountSessions(apiClient);
  const completedConcepts = [...progress.values()].reduce((sum, item) => sum + item.completed, 0);
  // 教材清單讀取失敗或還沒讀完時，教材相關數字顯示「—」。
  const fromMaterials = (value: (items: NonNullable<typeof materials>) => number) => error || !materials ? "—" : value(materials);
  const stats: [IconName, string, string | number, string][] = activity ? [
    ["book", "is-blue", fromMaterials((items) => items.length), "份教材"],
    ["learning", "is-amber", fromMaterials((items) => items.filter((item) => islandState(item) === "learning").length), "學習中的教材"],
    ["check", "is-green", fromMaterials(() => completedConcepts), "已完成概念"],
    ["quiz", "is-blue", activity.total_answers, "累計作答"],
    ["check", "is-green", activity.total_answers ? `${Math.round((activity.total_correct / activity.total_answers) * 100)}%` : "—", "累計答對率"],
  ] : [];
  return <section className="profile" aria-labelledby="profile-title">
    <header className="profile-head">
      <span className="profile-avatar" aria-hidden="true">{name ? [...name][0] : <Icon name="user" size={32} />}</span>
      <h1 id="profile-title">{name ?? "尚未設定名稱"}</h1>
      {/* 窄螢幕上帳號設定排在學習節奏之後，提供直接跳過去的入口。 */}
      <button className="secondary-button profile-settings-jump" type="button"
        onClick={() => document.getElementById("account-settings-title")?.scrollIntoView({ behavior: "smooth", block: "start" })}>
        <Icon name="user" size={18} />帳號設定
      </button>
    </header>
    <div className="profile-layout">
      <div className="profile-overview-slot">
        {activity === undefined ? <StateView title="正在讀取學習統計" description="正在整理學習總覽。" tone="loading" live />
          // 頁內區塊的錯誤用 h2，頁面標題仍是名稱。
          : activity === null ? <section className="section-failure surface" role="alert">
            <h2>無法讀取學習統計</h2>
            <p>學習活動暫時無法讀取，請稍後再試；帳號設定仍可使用。</p>
          </section>
          : <section className="profile-overview surface" aria-labelledby="profile-overview-title">
            <h2 id="profile-overview-title">學習總覽</h2>
            <div className="profile-stat-list" aria-busy={materials === null && !error}>
              {stats.map(([icon, tone, value, label]) => <div key={label} className="profile-stat">
                <span className={`stat-icon ${tone}`}><Icon name={icon} size={16} /></span>
                <strong>{value}</strong>
                <small>{label}</small>
              </div>)}
            </div>
          </section>}
      </div>
      <LearningRhythm apiClient={apiClient} />
      <AccountSettings apiClient={apiClient} onSessionsChanged={setSessions} />
      <DevicesCard apiClient={apiClient} sessions={sessions} onChanged={setSessions} />
      <StorageCard apiClient={apiClient} />
    </div>
  </section>;
}
