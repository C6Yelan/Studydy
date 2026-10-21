import { useCallback, useEffect, useState } from "react";
import type { LearningActivityView, LearningBadge, LearningBadgeId } from "../../api/contracts";
import { Modal } from "../../app/HeaderTools";
import { Icon, type IconName } from "../../ui/Icon";
import "./styles.css";

// 徽章（規則 badge-rules/v1）：取得與進度全部來自學習活動 API，這裡只負責名稱、說明與圖示。
type BadgeText = { name: string; condition: string; unit: string; icon: IconName; tone: string; tier: 1 | 2 };
const badgeText: Record<LearningBadgeId, BadgeText> = {
  first_answer: { name: "初次作答", condition: "完成第一題作答", unit: "題", icon: "quiz", tone: "is-blue", tier: 1 },
  answers_100: { name: "百題挑戰", condition: "累計作答 100 題", unit: "題", icon: "quiz", tone: "is-blue", tier: 2 },
  first_concept: { name: "點亮概念", condition: "第一次完成一個概念", unit: "個", icon: "learning", tone: "is-green", tier: 1 },
  concepts_25: { name: "概念收藏家", condition: "累計完成 25 個概念", unit: "個", icon: "learning", tone: "is-green", tier: 2 },
  first_material: { name: "征服第一座島", condition: "完成一份教材的全部學習", unit: "份", icon: "map", tone: "is-amber", tier: 2 },
  cards_100: { name: "卡片百回", condition: "累計複習概念卡 100 次", unit: "次", icon: "cards", tone: "is-violet", tier: 2 },
  listen_30_minutes: { name: "用耳朵學習", condition: "Podcast 實際收聽累計 30 分鐘", unit: "分鐘", icon: "headphones", tone: "is-sea", tier: 1 },
  streak_3: { name: "三日不間斷", condition: "連續 3 天有學習活動", unit: "天", icon: "flame", tone: "is-coral", tier: 1 },
  streak_7: { name: "一週全勤", condition: "連續 7 天有學習活動", unit: "天", icon: "flame", tone: "is-coral", tier: 2 },
  level_5: { name: "進階探險家", condition: "達到第 5 級（累計 1000 XP）", unit: "級", icon: "sparkle", tone: "is-gold", tier: 2 },
};

type KnownBadge = LearningBadge & { badge_id: LearningBadgeId };
// 前端不認得的徽章代號（後端規則較新時）不顯示，不猜測名稱。
export function knownBadges(activity: LearningActivityView): KnownBadge[] {
  return activity.badges.items.filter((item): item is KnownBadge => item.badge_id in badgeText);
}

function formatDate(value: string) {
  const [year, month, day] = value.split("-").map(Number);
  return `${year}/${month}/${day}`;
}

function progressText(badge: KnownBadge) {
  const text = badgeText[badge.badge_id];
  return badge.badge_id === "level_5" ? `目前第 ${badge.progress} 級` : `${badge.progress} / ${badge.target} ${text.unit}`;
}

export function BadgeMedal({ badge, size = 56 }: { badge: KnownBadge; size?: number }) {
  const text = badgeText[badge.badge_id];
  return <span className={`badge-medal ${text.tone}${badge.earned ? " is-earned" : " is-locked"}`} style={{ width: size, height: size }} aria-hidden="true">
    <svg viewBox="0 0 64 64">
      {text.tier === 2 && <>
        <path className="badge-ribbon" d="M20 44 L14 62 L22 57 L27 63 L31 48Z" />
        <path className="badge-ribbon" d="M44 44 L50 62 L42 57 L37 63 L33 48Z" />
      </>}
      <circle className="badge-rim" cx="32" cy="29" r="26" />
      <circle className="badge-face" cx="32" cy="29" r="20" />
      {text.tier === 2 && <path className="badge-star" d="M32 1.5 l2.4 4.8 5.3.8-3.8 3.7.9 5.3-4.8-2.5-4.8 2.5.9-5.3-3.8-3.7 5.3-.8Z" />}
    </svg>
    <span className="badge-glyph"><Icon name={badge.earned ? text.icon : "lock"} size={Math.round(size * 0.36)} /></span>
  </span>;
}

// 下一個最接近的徽章：未取得中進度比例最高者，同比例依規則順序。
function nextBadge(badges: KnownBadge[]) {
  return badges.filter((badge) => !badge.earned)
    .reduce<KnownBadge | null>((best, badge) => !best || badge.progress / badge.target > best.progress / best.target ? badge : best, null);
}

export function BadgeWall({ badges, open, onClose }: { badges: KnownBadge[]; open: boolean; onClose: () => void }) {
  const earned = badges.filter((badge) => badge.earned).length;
  return <Modal label="我的徽章" open={open} onClose={onClose} className="badge-dialog">
    <header className="badge-dialog-head">
      <div>
        <h2>我的徽章</h2>
        <small>已取得 {earned} / {badges.length} 個</small>
      </div>
      <button className="header-dialog-close" type="button" aria-label="關閉我的徽章" onClick={onClose}>×</button>
    </header>
    <ul className="badge-wall">
      {badges.map((badge) => {
        const text = badgeText[badge.badge_id];
        return <li key={badge.badge_id} className={badge.earned ? "is-earned" : "is-locked"}>
          <BadgeMedal badge={badge} />
          <span className="badge-wall-text">
            <b>{text.name}</b>
            <small>{text.condition}</small>
            {badge.earned
              ? <span className="badge-earned-on">{badge.earned_on ? `${formatDate(badge.earned_on)} 取得` : "已取得"}</span>
              : <span className="badge-progress">
                <span className="badge-progress-bar" role="progressbar" aria-label={`${text.name}進度`}
                  aria-valuemin={0} aria-valuemax={badge.target} aria-valuenow={badge.progress} aria-valuetext={progressText(badge)}>
                  <i style={{ width: `${(badge.progress / badge.target) * 100}%` }} />
                </span>
                <span>{progressText(badge)}</span>
              </span>}
          </span>
        </li>;
      })}
    </ul>
    <p className="badge-note">依作答、概念完成、教材完成、概念卡複習、Podcast 收聽與連續學習的真實紀錄判定，以台灣日期為準；刪除教材時相關紀錄移除，徽章會重新判定。</p>
  </Modal>;
}

// 首頁：已取得的徽章、下一個最接近的徽章，以及開啟完整徽章牆的入口。
export function BadgeShelf({ activity }: { activity: LearningActivityView }) {
  const badges = knownBadges(activity);
  const [open, setOpen] = useState(false);
  if (!badges.length) return null;
  const earned = badges.filter((badge) => badge.earned);
  const next = nextBadge(badges);
  return <section className="dashboard-badges surface" aria-labelledby="dashboard-badges-title">
    <header>
      <h2 id="dashboard-badges-title">我的徽章</h2>
      <span className="badge-count">{earned.length} / {badges.length}</span>
      <button className="text-button" type="button" aria-haspopup="dialog" onClick={() => setOpen(true)}>全部徽章<Icon name="chevron-right" size={16} /></button>
    </header>
    {earned.length > 0 ? <ul className="badge-shelf" aria-label="已取得的徽章">
      {earned.map((badge) => <li key={badge.badge_id} title={badgeText[badge.badge_id].name}>
        <BadgeMedal badge={badge} size={44} />
        <span className="header-sr-only">{badgeText[badge.badge_id].name}</span>
      </li>)}
    </ul> : <p className="badge-empty">還沒有徽章。作答、複習卡片或收聽 Podcast，就能拿到第一個。</p>}
    {next && <div className="badge-next">
      <BadgeMedal badge={next} size={40} />
      <span className="badge-next-text">
        <small>下一個徽章</small>
        <b>{badgeText[next.badge_id].name}</b>
        <span className="badge-progress">
          <span className="badge-progress-bar" role="progressbar" aria-label={`${badgeText[next.badge_id].name}進度`}
            aria-valuemin={0} aria-valuemax={next.target} aria-valuenow={next.progress} aria-valuetext={progressText(next)}>
            <i style={{ width: `${(next.progress / next.target) * 100}%` }} />
          </span>
          <span>{badgeText[next.badge_id].condition} · {progressText(next)}</span>
        </span>
      </span>
    </div>}
    <BadgeWall badges={badges} open={open} onClose={() => setOpen(false)} />
  </section>;
}

// 「上次看過的徽章」只用來決定是否顯示一次性提示，不是學習資料。
function seenBadges(key: string): string[] | null {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(key) ?? "null");
    return Array.isArray(value) && value.every((item) => typeof item === "string") ? value : null;
  } catch { return null; }
}
function rememberBadges(key: string, ids: string[]) {
  try { localStorage.setItem(key, JSON.stringify(ids)); } catch { /* 無法保存時只是不再提示。 */ }
}

// 回傳這次新取得、此瀏覽器還沒提示過的徽章；第一次在此瀏覽器讀到時只記下，不把既有徽章當成剛取得。
export function useNewBadges(activity: LearningActivityView | null, learnerId: string): [KnownBadge[], () => void] {
  const [fresh, setFresh] = useState<KnownBadge[]>([]);
  const key = `studydy.badges-seen:${learnerId}`;
  const earnedIds = activity ? knownBadges(activity).filter((badge) => badge.earned).map((badge) => badge.badge_id).join(",") : null;
  useEffect(() => {
    if (earnedIds === null || !activity) return;
    const earned = knownBadges(activity).filter((badge) => badge.earned);
    const seen = seenBadges(key);
    const added = seen === null ? [] : earned.filter((badge) => !seen.includes(badge.badge_id));
    if (added.length) setFresh(added);
    // 保留已看過但目前因刪除教材而失去的徽章，日後重新取得時不再重複提示。
    if (seen === null || added.length) rememberBadges(key, [...new Set([...(seen ?? []), ...earned.map((badge) => badge.badge_id)])]);
    // earnedIds 代表已取得集合；activity 其他欄位變動不需重新比較。
  }, [key, earnedIds]);
  const clear = useCallback(() => setFresh([]), []);
  return [fresh, clear];
}

export function BadgeEarned({ badges, onClose }: { badges: KnownBadge[]; onClose: () => void }) {
  useEffect(() => {
    const timer = window.setTimeout(onClose, 8000);
    const onKey = (event: KeyboardEvent) => { if (event.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => { window.clearTimeout(timer); window.removeEventListener("keydown", onKey); };
  }, [onClose]);
  const shown = badges.slice(0, 3);
  return <div className="level-up badge-earned" role="status" aria-live="polite">
    <div className="level-up-card">
      <span className="level-up-burst" aria-hidden="true" />
      <img src="/assets/studydy/success-jump.png" alt="" />
      <p className="level-up-eyebrow">獲得新徽章！</p>
      <ul className="badge-earned-list">
        {shown.map((badge) => <li key={badge.badge_id}>
          <BadgeMedal badge={badge} size={64} />
          <b>{badgeText[badge.badge_id].name}</b>
          <small>{badgeText[badge.badge_id].condition}</small>
        </li>)}
      </ul>
      {badges.length > shown.length && <p>另外還有 {badges.length - shown.length} 個新徽章，到首頁「我的徽章」查看。</p>}
      <button className="primary-button" type="button" onClick={onClose}>太好了</button>
    </div>
  </div>;
}
