import { Progress } from "../ui/Progress";
import { useCallback, useEffect, useRef, useState } from "react";
import type { StudydyApiClient } from "../api/client";
import type { LearningActivityView } from "../api/contracts";
import { BadgeEarned, knownBadges } from "../features/badges/Badges";
import { CelebrationDialog } from "../ui/CelebrationDialog";
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

function LevelUp({ level, onClose }: { level: number; onClose: () => void }) {
  return <CelebrationDialog className="level-up" label={`升級了！第 ${level} 級`} onClose={onClose}>
    <div className="level-up-card">
      <span className="level-up-burst" aria-hidden="true" />
      <img src="/assets/studydy/success-jump.png" alt="" />
      <p className="level-up-eyebrow">升級了！</p>
      <strong>第 {level} 級</strong>
      <p>持續作答、標記概念卡與收聽，累積經驗值。</p>
      <button className="primary-button" type="button" onClick={onClose}>太好了</button>
    </div>
  </CelebrationDialog>;
}

export function LearningProgressPill({ apiClient, learnerId }: { apiClient: StudydyApiClient; learnerId: string }) {
  const activity = useLearningActivity(apiClient);
  const [celebrate, setCelebrate] = useState<number | null>(null);
  const [claimedBadgeIds, setClaimedBadgeIds] = useState<string[]>([]);
  const level = activity?.xp.level;
  const earnedIds = activity?.badges.items.filter((badge) => badge.earned).map((badge) => badge.badge_id).join(",");
  const newBadges = activity ? knownBadges(activity).filter((badge) => claimedBadgeIds.includes(badge.badge_id)) : [];
  const closeLevelUp = useCallback(() => setCelebrate(null), []);
  const clearBadges = useCallback(() => setClaimedBadgeIds([]), []);
  const session = useRef(0);
  useEffect(() => () => { session.current++; }, [apiClient, learnerId]);
  useEffect(() => {
    if (level === undefined) return;
    const claimedSession = session.current;
    void apiClient.claimCelebrations().then((claim) => {
      if (claimedSession !== session.current) return;
      if (claim.level !== null) setCelebrate(current => Math.max(current ?? 0, claim.level!));
      if (claim.badge_ids.length) setClaimedBadgeIds((ids) => [...new Set([...ids, ...claim.badge_ids])]);
    }, () => { /* 領取失敗不推測慶祝事件；下次學習活動更新時再向伺服器領取。 */ });
  }, [apiClient, learnerId, level, earnedIds]);
  if (!activity) return null;
  const { streak_days: streak, xp } = activity;
  // 等級盾牌與本級進度在連續天數左邊；點按進入成就頁。
  return <>
    <button className={`header-progress${streak ? "" : " is-idle"}`} type="button"
      aria-label={`第 ${xp.level} 級，本級 ${xp.level_xp} / ${xp.level_target} XP，連續學習 ${streak} 天，查看成就`}
      onClick={() => writeRoute({ name: "achievements" })}>
      <span className="header-level" aria-hidden="true">
        <span className="header-level-shield">
          <svg viewBox="0 0 40 44"><path d="M20 2 36 8v12c0 11-7 18-16 22C11 38 4 31 4 20V8Z" /></svg>
          <b>{xp.level}</b>
        </span>
        <span className="header-level-meter">
          <Progress size="small" value={xp.level_xp} max={xp.level_target} aria-label="本級經驗值" />
          <small>{xp.level_xp}/{xp.level_target}</small>
        </span>
      </span>
      <span className="header-streak" aria-hidden="true"><Icon name="flame" size={17} /><b>{streak}</b><span className="header-streak-unit">天</span></span>
    </button>
    {celebrate !== null && <LevelUp level={celebrate} onClose={closeLevelUp} />}
    {/* 同時升級與取得徽章時，先看升級，關閉後再顯示徽章。 */}
    {celebrate === null && newBadges.length > 0 && <BadgeEarned badges={newBadges} onClose={clearBadges} />}
  </>;
}
