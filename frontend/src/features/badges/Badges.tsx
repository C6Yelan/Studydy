import { useCallback, useEffect, useState } from "react";
import type { LearningActivityView, LearningBadge, LearningBadgeId } from "../../api/contracts";
import { Icon, type IconName } from "../../ui/Icon";
import "./styles.css";

// 徽章（規則 badge-rules/v2）：取得與進度全部來自學習活動 API，這裡只負責名稱、說明與圖示。
// 同一計量的徽章是一個系列的不同層級（I→IV），獎章依層級上色：銅、銀、金、鑽石。
type Series = "answers" | "concepts" | "materials" | "cards" | "listen" | "streak" | "level";
const series: Record<Series, { title: string; unit: string; icon: IconName; condition: (target: number) => string }> = {
  answers: { title: "作答", unit: "題", icon: "quiz", condition: (target) => target === 1 ? "完成第一題作答" : `累計作答 ${target} 題` },
  concepts: { title: "概念完成", unit: "個", icon: "learning", condition: (target) => target === 1 ? "第一次完成一個概念" : `累計完成 ${target} 個概念` },
  materials: { title: "教材完成", unit: "份", icon: "map", condition: (target) => target === 1 ? "完成一份教材的全部學習" : `完成 ${target} 份教材的全部學習` },
  cards: { title: "概念卡複習", unit: "次", icon: "cards", condition: (target) => `累計複習概念卡 ${target} 次` },
  listen: { title: "Podcast 收聽", unit: "分鐘", icon: "headphones", condition: (target) => `Podcast 實際收聽累計 ${target} 分鐘` },
  streak: { title: "連續學習", unit: "天", icon: "flame", condition: (target) => `連續 ${target} 天有學習活動` },
  level: { title: "等級", unit: "級", icon: "sparkle", condition: (target) => `達到第 ${target} 級` },
};
// 各系列依層級由低到高列出；層級即在系列中的位置。
const tiers: Record<Series, [LearningBadgeId, string][]> = {
  answers: [["first_answer", "初次作答"], ["answers_100", "百題挑戰"], ["answers_300", "題海航行者"], ["answers_1000", "千題大師"]],
  concepts: [["first_concept", "點亮概念"], ["concepts_25", "概念收藏家"], ["concepts_100", "知識建築師"], ["concepts_300", "知識大典"]],
  materials: [["first_material", "征服第一座島"], ["materials_3", "群島探險家"], ["materials_10", "群島領主"]],
  cards: [["cards_100", "卡片百回"], ["cards_500", "記憶練習生"], ["cards_2000", "過目不忘"]],
  listen: [["listen_30_minutes", "用耳朵學習"], ["listen_180_minutes", "通勤聆聽者"], ["listen_600_minutes", "聆聽大師"]],
  streak: [["streak_3", "三日不間斷"], ["streak_7", "一週全勤"], ["streak_30", "月度堅持"], ["streak_100", "百日修行"]],
  level: [["level_5", "進階探險家"], ["level_10", "資深航海士"], ["level_20", "傳奇學者"]],
};
const badgeText = Object.fromEntries(Object.entries(tiers).flatMap(([key, list]) =>
  list.map(([id, name], index) => [id, { name, series: key as Series, tier: index + 1 }]))) as Record<LearningBadgeId, { name: string; series: Series; tier: number }>;
const tierNames = ["銅", "銀", "金", "鑽石"];
const roman = ["I", "II", "III", "IV"];

type KnownBadge = LearningBadge & { badge_id: LearningBadgeId };
// 前端不認得的徽章代號（後端規則較新時）不顯示，不猜測名稱。
export function knownBadges(activity: LearningActivityView): KnownBadge[] {
  return activity.badges.items.filter((item): item is KnownBadge => item.badge_id in badgeText);
}

function formatDate(value: string) {
  const [year, month, day] = value.split("-").map(Number);
  return `${year}/${month}/${day}`;
}

function conditionText(badge: KnownBadge) {
  return series[badgeText[badge.badge_id].series].condition(badge.target);
}

function progressText(badge: KnownBadge) {
  const info = series[badgeText[badge.badge_id].series];
  return info === series.level ? `目前第 ${badge.progress} 級` : `${badge.progress} / ${badge.target} ${info.unit}`;
}

export function BadgeMedal({ badge, size = 56 }: { badge: KnownBadge; size?: number }) {
  const text = badgeText[badge.badge_id];
  return <span className={`badge-medal is-tier-${text.tier}${badge.earned ? " is-earned" : " is-locked"}`} style={{ width: size, height: size }} aria-hidden="true">
    <svg viewBox="0 0 64 64">
      {text.tier >= 2 && <>
        <path className="badge-ribbon" d="M20 44 L14 62 L22 57 L27 63 L31 48Z" />
        <path className="badge-ribbon" d="M44 44 L50 62 L42 57 L37 63 L33 48Z" />
      </>}
      <circle className="badge-rim" cx="32" cy="29" r="26" />
      <circle className="badge-face" cx="32" cy="29" r="20" />
      {text.tier >= 3 && <path className="badge-star" d="M32 1.5 l2.4 4.8 5.3.8-3.8 3.7.9 5.3-4.8-2.5-4.8 2.5.9-5.3-3.8-3.7 5.3-.8Z" />}
    </svg>
    <span className="badge-glyph"><Icon name={badge.earned ? series[text.series].icon : "lock"} size={Math.round(size * 0.36)} /></span>
  </span>;
}

function BadgeProgress({ badge, text }: { badge: KnownBadge; text: string }) {
  const name = badgeText[badge.badge_id].name;
  return <span className="badge-progress">
    <span className="badge-progress-bar" role="progressbar" aria-label={`${name}進度`}
      aria-valuemin={0} aria-valuemax={badge.target} aria-valuenow={badge.progress} aria-valuetext={progressText(badge)}>
      <i style={{ width: `${(badge.progress / badge.target) * 100}%` }} />
    </span>
    <span>{text}</span>
  </span>;
}

// 一個系列一格：顯示目前層級與下一層的進度，展開可看各層取得日期。
function BadgeSeriesCard({ kind, badges }: { kind: Series; badges: KnownBadge[] }) {
  const info = series[kind];
  const earned = badges.filter((badge) => badge.earned);
  const current = earned.at(-1);
  const next = badges.find((badge) => !badge.earned);
  const shown = current ?? badges[0];
  const tier = current ? badgeText[current.badge_id].tier : 0;
  return <li className={`badge-series${current ? ` is-earned is-tier-${tier}` : " is-locked"}`}>
    <div className="badge-series-main">
      <BadgeMedal badge={shown} />
      <span className="badge-series-text">
        <small>{info.title} · {current ? `${tierNames[tier - 1]}・${roman[tier - 1]}` : "尚未取得"} <span className="badge-series-count">{earned.length} / {badges.length}</span></small>
        <b>{badgeText[shown.badge_id].name}</b>
        {next ? <>
          <span className="badge-series-next">{current ? "下一層" : "取得條件"}：{current && `${badgeText[next.badge_id].name} · `}{conditionText(next)}</span>
          <BadgeProgress badge={next} text={progressText(next)} />
        </> : <span className="badge-earned-on">已達最高層級{current?.earned_on && ` · ${formatDate(current.earned_on)}`}</span>}
      </span>
    </div>
    <details className="badge-series-tiers">
      <summary>全部 {badges.length} 層</summary>
      <ol>
        {badges.map((badge) => <li key={badge.badge_id} className={badge.earned ? "is-earned" : undefined}>
          <span className="badge-tier-mark">{roman[badgeText[badge.badge_id].tier - 1]}</span>
          <span><b>{badgeText[badge.badge_id].name}</b><small>{conditionText(badge)}</small></span>
          <span className="badge-tier-state">{badge.earned ? (badge.earned_on ? `${formatDate(badge.earned_on)} 取得` : "已取得") : progressText(badge)}</span>
        </li>)}
      </ol>
    </details>
  </li>;
}

// 下一個最接近的徽章：未取得中進度比例最高者，同比例依規則順序。
function nextBadge(badges: KnownBadge[]) {
  return badges.filter((badge) => !badge.earned)
    .reduce<KnownBadge | null>((best, badge) => !best || badge.progress / badge.target > best.progress / best.target ? badge : best, null);
}

// 成就頁：下一個最接近的徽章與各系列的徽章卡。
export function BadgeCollection({ activity }: { activity: LearningActivityView }) {
  const badges = knownBadges(activity);
  if (!badges.length) return null;
  const earned = badges.filter((badge) => badge.earned).length;
  const next = nextBadge(badges);
  const grouped = (Object.keys(tiers) as Series[])
    .map((kind) => [kind, tiers[kind].map(([id]) => badges.find((badge) => badge.badge_id === id)).filter((badge): badge is KnownBadge => !!badge)] as const)
    .filter(([, list]) => list.length > 0);
  return <section className="badge-collection surface" aria-labelledby="badge-collection-title">
    <header className="badge-collection-head">
      <h2 id="badge-collection-title">我的徽章</h2>
      <span className="badge-count">{earned} / {badges.length}</span>
    </header>
    {next && <div className="badge-next">
      <BadgeMedal badge={next} size={40} />
      <span className="badge-next-text">
        <small>最接近的下一個徽章</small>
        <b>{badgeText[next.badge_id].name}</b>
        <BadgeProgress badge={next} text={`${conditionText(next)} · ${progressText(next)}`} />
      </span>
    </div>}
    <ul className="badge-wall">
      {grouped.map(([kind, list]) => <BadgeSeriesCard key={kind} kind={kind} badges={list} />)}
    </ul>
    <p className="badge-note">依作答、概念完成、教材完成、概念卡複習、Podcast 收聽、連續學習與等級的真實紀錄判定，以台灣日期為準；刪除教材時相關紀錄移除，徽章會重新判定。</p>
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
          <small>{conditionText(badge)}</small>
        </li>)}
      </ul>
      {badges.length > shown.length && <p>另外還有 {badges.length - shown.length} 個新徽章，到「成就」頁查看。</p>}
      <button className="primary-button" type="button" onClick={onClose}>太好了</button>
    </div>
  </div>;
}
