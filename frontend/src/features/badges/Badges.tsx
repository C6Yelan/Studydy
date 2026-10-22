import { useCallback, useEffect, useState } from "react";
import type { LearningActivityView, LearningBadge, LearningBadgeId } from "../../api/contracts";
import { Icon, type IconName } from "../../ui/Icon";
import "./styles.css";

// 徽章（規則 badge-rules/v5）：取得與進度全部來自學習活動 API，這裡只負責名稱、說明、圖示與稀有度。
// 同一計量的徽章是一個系列的不同層級（I→V），獎章依層級上色：銅、銀、金、鑽石、星耀。
// 稀有度（普通／稀有／史詩／傳說）只是呈現難度，不影響經驗值。
type Series = "answers" | "concepts" | "materials" | "cards" | "listen" | "streak" | "level" | "perfect" | "firstTry";
type Rarity = 0 | 1 | 2 | 3;
const rarityNames = ["普通", "稀有", "史詩", "傳說"] as const;
const series: Record<Series, { title: string; unit: string; icon: IconName; condition: (target: number) => string }> = {
  answers: { title: "作答", unit: "題", icon: "quiz", condition: (target) => target === 1 ? "完成第一題作答" : `累計作答 ${target} 題` },
  concepts: { title: "概念完成", unit: "個", icon: "learning", condition: (target) => target === 1 ? "第一次完成一個概念" : `累計完成 ${target} 個概念` },
  materials: { title: "教材完成", unit: "份", icon: "map", condition: (target) => target === 1 ? "完成一份教材的全部學習" : `完成 ${target} 份教材的全部學習` },
  cards: { title: "概念卡標記", unit: "張", icon: "cards", condition: (target) => `累計標記概念卡 ${target} 張（同一張卡每天算一次）` },
  listen: { title: "Podcast 收聽", unit: "分鐘", icon: "headphones", condition: (target) => `Podcast 實際收聽累計 ${target} 分鐘` },
  streak: { title: "連續學習", unit: "天", icon: "flame", condition: (target) => `連續 ${target} 天有學習活動` },
  level: { title: "等級", unit: "級", icon: "sparkle", condition: (target) => `達到第 ${target} 級` },
  perfect: { title: "零失誤題組", unit: "次", icon: "check", condition: (target) => target === 1 ? "完成一個至少 3 題、每題第一次就答對的題組" : `完成 ${target} 次零失誤題組（每組至少 3 題）` },
  firstTry: { title: "一次就懂", unit: "份", icon: "learning", condition: () => "完成一份教材，每個作答過的概念第一次作答都答對" },
};
// 各系列依層級由低到高列出：[代號, 名稱, 稀有度]；層級即在系列中的位置。
const tiers: Record<Series, [LearningBadgeId, string, Rarity][]> = {
  answers: [["first_answer", "初次作答", 0], ["answers_100", "百題挑戰", 1], ["answers_300", "題海航行者", 2], ["answers_1000", "千題大師", 3], ["answers_5000", "五千題宗師", 3]],
  concepts: [["first_concept", "點亮概念", 0], ["concepts_25", "概念收藏家", 1], ["concepts_100", "知識建築師", 2], ["concepts_300", "知識大典", 3]],
  materials: [["first_material", "征服第一座島", 1], ["materials_3", "群島探險家", 2], ["materials_10", "群島領主", 3]],
  cards: [["cards_100", "卡片百回", 0], ["cards_500", "記憶練習生", 1], ["cards_2000", "過目不忘", 2]],
  listen: [["listen_30_minutes", "用耳朵學習", 0], ["listen_180_minutes", "三小時聆聽者", 1], ["listen_600_minutes", "聆聽大師", 2]],
  streak: [["streak_3", "三日不間斷", 0], ["streak_7", "一週全勤", 1], ["streak_30", "月度堅持", 2], ["streak_100", "百日修行", 3], ["streak_365", "全年無休", 3]],
  level: [["level_5", "進階探險家", 1], ["level_10", "資深航海士", 2], ["level_20", "傳奇學者", 3], ["level_30", "三十級宗師", 3]],
  perfect: [["perfect_set_1", "零失誤", 1], ["perfect_set_10", "十次零失誤", 2], ["perfect_set_30", "三十次零失誤", 3]],
  firstTry: [["first_try_material", "一次就懂", 3]],
};
const badgeText: Partial<Record<LearningBadgeId, { name: string; series: Series; tier: number; rarity: Rarity }>> = Object.fromEntries(Object.entries(tiers).flatMap(([key, list]) =>
  list.map(([id, name, rarity], index) => [id, { name, series: key as Series, tier: index + 1, rarity }])));
// 特殊成就：未取得時只顯示「？？？」、稀有度與一句提示，取得後才揭曉名稱與條件（判定在後端 SPECIAL_BADGES）。
type SpecialId = "combo_10" | "comeback_20" | "daily_50" | "explorer_5" | "night_owl" | "early_bird"
  | "weekend" | "return" | "curious_10" | "researcher" | "creator" | "corrected_5"
  | "perfect_day" | "all_round_day" | "thousand_pages" | "revisit";
const specials: Record<SpecialId, { name: string; condition: string; hint: string; icon: IconName; rarity: Rarity }> = {
  combo_10: { name: "連對王", condition: "連續答對 10 題", hint: "一口氣，一題都不能錯。", icon: "check", rarity: 1 },
  comeback_20: { name: "越挫越勇", condition: "答錯後下一題就答對，累計 20 次", hint: "跌倒之後，馬上站起來。", icon: "refresh", rarity: 1 },
  daily_50: { name: "一日五十題", condition: "同一天作答 50 題", hint: "今天，好像停不下來。", icon: "quiz", rarity: 1 },
  explorer_5: { name: "五島探險家", condition: "在 5 份不同教材作答", hint: "一座島嶼還不夠。", icon: "map", rarity: 1 },
  night_owl: { name: "夜貓子", condition: "凌晨 0:00～4:59 作答累計 10 題", hint: "月亮出來的時候……", icon: "eye", rarity: 0 },
  early_bird: { name: "早起的鳥兒", condition: "早上 5:00～7:59 作答累計 10 題", hint: "太陽還沒完全醒來。", icon: "sparkle", rarity: 0 },
  weekend: { name: "週末學者", condition: "同一個週末的週六與週日都有學習", hint: "別人放假的時候……", icon: "book", rarity: 0 },
  return: { name: "浪子回頭", condition: "中斷 7 天以上後重新開始學習", hint: "離開，然後回來。", icon: "route", rarity: 0 },
  curious_10: { name: "好奇寶寶", condition: "在教材問答提出 10 個問題", hint: "有問題，就問吧。", icon: "chat", rarity: 0 },
  researcher: { name: "追根究柢", condition: "補充研究並把新來源加入教材", hint: "教材之外，還有更多。", icon: "search", rarity: 0 },
  creator: { name: "自己動手做", condition: "建立一組概念卡和一集 Podcast", hint: "把學到的，變成自己的。", icon: "edit", rarity: 0 },
  corrected_5: { name: "知錯能改", condition: "答錯過的概念之後又答對，累計 5 個", hint: "錯過的，不會再錯。", icon: "learning", rarity: 1 },
  perfect_day: { name: "完美的一天", condition: "同一天作答 20 題以上且全部答對", hint: "一整天，一題都沒錯。", icon: "sparkle", rarity: 2 },
  all_round_day: { name: "全能學習日", condition: "同一天作答、標記概念卡、收聽 Podcast 滿 1 分鐘並在教材問答提問", hint: "答題、卡片、耳朵、提問，一天之內……", icon: "compass", rarity: 2 },
  thousand_pages: { name: "千頁征服者", condition: "完成一份 500 頁以上教材的全部學習", hint: "很厚、很厚的一本。", icon: "book", rarity: 3 },
  revisit: { name: "溫故知新", condition: "教材完成 7 天以上後，又標記它的概念卡", hint: "學完了，過一陣子再回來看看。", icon: "cards", rarity: 1 },
};
const isSpecial = (id: LearningBadgeId): id is SpecialId => id in specials;
const nameOf = (badge: KnownBadge) => isSpecial(badge.badge_id) ? specials[badge.badge_id].name : badgeText[badge.badge_id]!.name;
const rarityOf = (badge: KnownBadge): Rarity => isSpecial(badge.badge_id) ? specials[badge.badge_id].rarity : badgeText[badge.badge_id]!.rarity;
const tierNames = ["銅", "銀", "金", "鑽石", "星耀"];
const roman = ["I", "II", "III", "IV", "V"];

type KnownBadge = LearningBadge & { badge_id: LearningBadgeId };
// 前端不認得的徽章代號（後端規則較新時）不顯示，不猜測名稱。
export function knownBadges(activity: LearningActivityView): KnownBadge[] {
  return activity.badges.items.filter((item): item is KnownBadge => item.badge_id in badgeText || item.badge_id in specials);
}

function formatDate(value: string) {
  const [year, month, day] = value.split("-").map(Number);
  return `${year}/${month}/${day}`;
}

function conditionText(badge: KnownBadge) {
  return isSpecial(badge.badge_id) ? specials[badge.badge_id].condition : series[badgeText[badge.badge_id]!.series].condition(badge.target);
}

function progressText(badge: KnownBadge) {
  if (isSpecial(badge.badge_id)) return `${badge.progress} / ${badge.target}`;
  const info = series[badgeText[badge.badge_id]!.series];
  return info === series.level ? `目前第 ${badge.progress} 級` : `${badge.progress} / ${badge.target} ${info.unit}`;
}

function RarityTag({ rarity }: { rarity: Rarity }) {
  return <span className={`badge-rarity is-rarity-${rarity}`}>{rarityNames[rarity]}</span>;
}

export function BadgeMedal({ badge, size = 56 }: { badge: KnownBadge; size?: number }) {
  const special = isSpecial(badge.badge_id) ? specials[badge.badge_id] : null;
  const text = special ? { tier: 4, icon: special.icon } : { tier: badgeText[badge.badge_id]!.tier, icon: series[badgeText[badge.badge_id]!.series].icon };
  return <span className={`badge-medal ${special ? "is-special" : `is-tier-${text.tier}`}${badge.earned ? " is-earned" : " is-locked"}`} style={{ width: size, height: size }} aria-hidden="true">
    <svg viewBox="0 0 64 64">
      {text.tier >= 2 && <>
        <path className="badge-ribbon" d="M20 44 L14 62 L22 57 L27 63 L31 48Z" />
        <path className="badge-ribbon" d="M44 44 L50 62 L42 57 L37 63 L33 48Z" />
      </>}
      <circle className="badge-rim" cx="32" cy="29" r="26" />
      <circle className="badge-face" cx="32" cy="29" r="20" />
      {text.tier >= 3 && <path className="badge-star" d="M32 1.5 l2.4 4.8 5.3.8-3.8 3.7.9 5.3-4.8-2.5-4.8 2.5.9-5.3-3.8-3.7 5.3-.8Z" />}
    </svg>
    <span className="badge-glyph">{special && !badge.earned ? <b>?</b> : <Icon name={badge.earned ? text.icon : "lock"} size={Math.round(size * 0.36)} />}</span>
  </span>;
}

function BadgeProgress({ badge, text }: { badge: KnownBadge; text: string }) {
  const name = nameOf(badge);
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
  const tier = current ? badgeText[current.badge_id]!.tier : 0;
  return <li className={`badge-series is-rarity-${rarityOf(next ?? shown)}${current ? ` is-earned is-tier-${tier}` : " is-locked"}`}>
    <div className="badge-series-main">
      <BadgeMedal badge={shown} size={48} />
      <span className="badge-series-text">
        <small>{info.title} · {current ? `${tierNames[tier - 1]}・${roman[tier - 1]}` : "尚未取得"} <span className="badge-series-count">{earned.length} / {badges.length}</span></small>
        <b>{nameOf(shown)}</b>
        {next ? <>
          <span className="badge-series-next"><RarityTag rarity={rarityOf(next)} />{current ? "下一層" : "取得條件"}：{current && `${nameOf(next)} · `}{conditionText(next)}</span>
          <BadgeProgress badge={next} text={progressText(next)} />
        </> : <span className="badge-earned-on">已達最高層級{current?.earned_on && ` · ${formatDate(current.earned_on)}`}</span>}
      </span>
    </div>
    {badges.length > 1 && <details className="badge-series-tiers">
      <summary>全部 {badges.length} 層</summary>
      <ol>
        {badges.map((badge) => <li key={badge.badge_id} className={badge.earned ? "is-earned" : undefined}>
          <span className="badge-tier-mark">{roman[badgeText[badge.badge_id]!.tier - 1]}</span>
          <span><b>{nameOf(badge)} <RarityTag rarity={rarityOf(badge)} /></b><small>{conditionText(badge)}</small></span>
          <span className="badge-tier-state">{badge.earned ? (badge.earned_on ? `${formatDate(badge.earned_on)} 取得` : "已取得") : progressText(badge)}</span>
        </li>)}
      </ol>
    </details>}
  </li>;
}

// 下一個最接近的徽章：未取得中進度比例最高者，同比例依規則順序。
function nextBadge(badges: KnownBadge[]) {
  // 特殊成就的條件保密，不列為「最接近的下一個」。
  return badges.filter((badge) => !badge.earned && !isSpecial(badge.badge_id))
    .reduce<KnownBadge | null>((best, badge) => !best || badge.progress / badge.target > best.progress / best.target ? badge : best, null);
}

// 成就頁右欄：依稀有度的取得總覽、最接近的下一個徽章、各系列與特殊成就。
export function BadgeCollection({ activity }: { activity: LearningActivityView }) {
  const badges = knownBadges(activity);
  if (!badges.length) return null;
  const tiered = badges.filter((badge) => !isSpecial(badge.badge_id));
  const special = badges.filter((badge) => isSpecial(badge.badge_id));
  const earned = tiered.filter((badge) => badge.earned).length;
  const next = nextBadge(badges);
  const grouped = (Object.keys(tiers) as Series[])
    .map((kind) => [kind, tiers[kind].map(([id]) => badges.find((badge) => badge.badge_id === id)).filter((badge): badge is KnownBadge => !!badge)] as const)
    .filter(([, list]) => list.length > 0);
  return <section className="badge-collection surface" aria-labelledby="badge-collection-title">
    <header className="badge-collection-head">
      <h2 id="badge-collection-title">成就總覽</h2>
      <span className="badge-count">{earned} / {tiered.length}</span>
    </header>
    <ul className="badge-rarity-summary" aria-label="依稀有度的取得數">
      {([0, 1, 2, 3] as Rarity[]).map((rarity) => {
        const all = badges.filter((badge) => rarityOf(badge) === rarity);
        return <li key={rarity} className={`is-rarity-${rarity}`}><b>{all.filter((badge) => badge.earned).length}<small> / {all.length}</small></b><span>{rarityNames[rarity]}</span></li>;
      })}
    </ul>
    {next && <div className="badge-next">
      <BadgeMedal badge={next} size={40} />
      <span className="badge-next-text">
        <small>最接近的下一個徽章 <RarityTag rarity={rarityOf(next)} /></small>
        <b>{nameOf(next)}</b>
        <BadgeProgress badge={next} text={`${conditionText(next)} · ${progressText(next)}`} />
      </span>
    </div>}
    <ul className="badge-wall">
      {grouped.map(([kind, list]) => <BadgeSeriesCard key={kind} kind={kind} badges={list} />)}
    </ul>
    {special.length > 0 && <section className="badge-specials" aria-labelledby="badge-specials-title">
      <header className="badge-collection-head">
        <h3 id="badge-specials-title">特殊成就</h3>
        <span className="badge-count is-special">{special.filter((badge) => badge.earned).length} / {special.length}</span>
        <small>條件保密，只有提示；達成後才揭曉。</small>
      </header>
      <ul className="badge-special-wall">
        {special.map((badge) => {
          const text = specials[badge.badge_id as SpecialId];
          return <li key={badge.badge_id} className={`${badge.earned ? "is-earned" : "is-locked"} is-rarity-${text.rarity}`}>
            <BadgeMedal badge={badge} size={44} />
            {badge.earned ? <span className="badge-wall-text">
              <b>{text.name} <RarityTag rarity={text.rarity} /></b>
              <small>{text.condition}</small>
              <span className="badge-earned-on">{badge.earned_on ? `${formatDate(badge.earned_on)} 取得` : "已取得"}</span>
            </span> : <span className="badge-wall-text">
              <b><span aria-label="尚未揭曉的成就">？？？</span> <RarityTag rarity={text.rarity} /></b>
              <small>{text.hint}</small>
            </span>}
          </li>;
        })}
      </ul>
    </section>}
    <p className="badge-note">依作答、題組、概念與教材完成、概念卡標記、Podcast 收聽、教材問答、連續學習與等級的真實紀錄判定，以台灣日期為準；刪除教材時相關紀錄移除，徽章會重新判定。稀有度只表示難度，不影響經驗值。</p>
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
          <b>{nameOf(badge)}</b>
          <small>{conditionText(badge)}</small>
        </li>)}
      </ul>
      {badges.length > shown.length && <p>另外還有 {badges.length - shown.length} 個新徽章，到「成就」頁查看。</p>}
      <button className="primary-button" type="button" onClick={onClose}>太好了</button>
    </div>
  </div>;
}
