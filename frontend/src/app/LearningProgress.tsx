import { useEffect, useState } from "react";
import type { StudydyApiClient } from "../api/client";
import type { LearningActivityView } from "../api/contracts";
import { BadgeEarned, useNewBadges } from "../features/badges/Badges";
import { Icon } from "../ui/Icon";
import { writeRoute } from "./routes";

// 頁首與首頁共用的學習活動；換頁或回到視窗時讀取（client 短時間內共用同一次結果）。
// 讀取中回傳 undefined（可顯示骨架）；讀取失敗時回傳 null，畫面隱藏相關數字，不顯示推測值。
export function useLearningActivity(apiClient: StudydyApiClient, options: { fresh?: boolean } = {}): LearningActivityView | null | undefined {
  const [activity, setActivity] = useState<LearningActivityView | null | undefined>(undefined);
  const fresh = !!options.fresh;
  useEffect(() => {
    let cancelled = false;
    const load = (force: boolean) => void apiClient.getLearningActivity({ fresh: force }).then(
      (value) => { if (!cancelled) setActivity(value); },
      () => { if (!cancelled) setActivity(null); },
    );
    load(fresh);
    const unsubscribe = apiClient.onLearningActivityChange((value) => { if (!cancelled) setActivity(value); });
    const refresh = () => load(false);
    window.addEventListener("popstate", refresh);
    window.addEventListener("focus", refresh);
    return () => {
      cancelled = true;
      unsubscribe();
      window.removeEventListener("popstate", refresh);
      window.removeEventListener("focus", refresh);
    };
  }, [apiClient, fresh]);
  return activity;
}

// 「上次看到的等級」只用來決定是否播放升級動畫，不是學習資料。
function seenLevel(key: string): number | null {
  try {
    const value = Number(localStorage.getItem(key));
    return Number.isInteger(value) && value >= 1 ? value : null;
  } catch { return null; }
}
function rememberLevel(key: string, level: number) {
  try { localStorage.setItem(key, String(level)); } catch { /* 無法保存時只是不再判斷升級。 */ }
}

function LevelUp({ level, onClose }: { level: number; onClose: () => void }) {
  useEffect(() => {
    const timer = window.setTimeout(onClose, 8000);
    const onKey = (event: KeyboardEvent) => { if (event.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => { window.clearTimeout(timer); window.removeEventListener("keydown", onKey); };
  }, [onClose]);
  return <div className="level-up" role="status" aria-live="polite">
    <div className="level-up-card">
      <span className="level-up-burst" aria-hidden="true" />
      <img src="/assets/studydy/success-jump.png" alt="" />
      <p className="level-up-eyebrow">升級了！</p>
      <strong>第 {level} 級</strong>
      <p>持續作答、複習與收聽，累積經驗值。</p>
      <button className="primary-button" type="button" onClick={onClose}>太好了</button>
    </div>
  </div>;
}

export function LearningProgressPill({ apiClient, learnerId }: { apiClient: StudydyApiClient; learnerId: string }) {
  const activity = useLearningActivity(apiClient);
  const [celebrate, setCelebrate] = useState<number | null>(null);
  const key = `studydy.level-seen:${learnerId}`;
  const level = activity?.xp.level;
  const [newBadges, clearBadges] = useNewBadges(activity ?? null, learnerId);
  useEffect(() => {
    if (level === undefined) return;
    const seen = seenLevel(key);
    // 第一次在此瀏覽器看到等級時只記下，不把既有等級當成剛升級。
    if (seen !== null && level > seen) setCelebrate(level);
    if (seen === null || level > seen) rememberLevel(key, level);
  }, [key, level]);
  if (!activity) return null;
  const { streak_days: streak, xp } = activity;
  return <>
    <button className={`header-progress${streak ? "" : " is-idle"}`} type="button"
      aria-label={`連續學習 ${streak} 天，第 ${xp.level} 級，累計 ${xp.total} XP`}
      onClick={() => writeRoute({ name: "home" })}>
      <span className="header-streak"><Icon name="flame" size={17} /><b>{streak}</b><span className="header-progress-wide">天</span></span>
      <span className="header-level">Lv {xp.level}</span>
      <span className="header-xp"><Icon name="sparkle" size={15} />{xp.total} XP</span>
    </button>
    {celebrate !== null && <LevelUp level={celebrate} onClose={() => setCelebrate(null)} />}
    {/* 同時升級與取得徽章時，先看升級，關閉後再顯示徽章。 */}
    {celebrate === null && newBadges.length > 0 && <BadgeEarned badges={newBadges} onClose={clearBadges} />}
  </>;
}
