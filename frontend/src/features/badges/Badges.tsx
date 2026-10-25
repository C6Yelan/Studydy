import { useCallback, useEffect, useState } from "react";
import type { LearningActivityView, LearningBadge, LearningBadgeId } from "../../api/contracts";
import { CelebrationDialog } from "../../ui/CelebrationDialog";
import { Icon, type IconName } from "../../ui/Icon";
import "./styles.css";

// 徽章（規則 badge-rules/v7）：取得與進度全部來自學習活動 API，這裡只負責名稱、說明、圖示與稀有度。
// 同一計量的徽章是一個系列的不同層級（I→V），獎章依層級上色：銅、銀、金、鑽石、星耀。
// 稀有度（普通／稀有／史詩／傳說）只是呈現難度，不影響經驗值。
type Series = "answers" | "concepts" | "materials" | "cards" | "listen" | "streak" | "days" | "level" | "perfect" | "sets" | "episodes" | "firstTry"
  | "goal" | "stages" | "difficulties" | "diagnostics" | "notes" | "annotations"
  | "uploadImage" | "uploadAudio" | "uploadVideo" | "uploadYoutube" | "exports" | "podcastSeries" | "podcastHosts"
  | "allTools" | "diagnosedMastery" | "mediaConcepts" | "stageStreak";
type Rarity = 0 | 1 | 2 | 3;
const rarityNames = ["普通", "稀有", "史詩", "傳說"] as const;
const series: Record<Series, { title: string; unit: string; icon: IconName; condition: (target: number) => string }> = {
  answers: { title: "作答", unit: "題", icon: "quiz", condition: (target) => target === 1 ? "完成第一題作答" : `累計作答 ${target} 題` },
  concepts: { title: "概念完成", unit: "個", icon: "learning", condition: (target) => target === 1 ? "第一次完成一個概念" : `累計完成 ${target} 個概念` },
  materials: { title: "教材完成", unit: "份", icon: "map", condition: (target) => target === 1 ? "完成一份教材的全部學習" : `完成 ${target} 份教材的全部學習` },
  cards: { title: "概念卡標記", unit: "張", icon: "cards", condition: (target) => `累計標記概念卡 ${target} 張（同一張卡每天算一次）` },
  listen: { title: "Podcast 收聽", unit: "分鐘", icon: "headphones", condition: (target) => `Podcast 實際收聽累計 ${target} 分鐘` },
  streak: { title: "連續學習", unit: "天", icon: "flame", condition: (target) => `連續 ${target} 天有學習活動` },
  days: { title: "學習天數", unit: "天", icon: "clock", condition: (target) => `累計 ${target} 天有學習活動（不必連續）` },
  level: { title: "等級", unit: "級", icon: "sparkle", condition: (target) => `達到第 ${target} 級` },
  perfect: { title: "零失誤題組", unit: "次", icon: "check", condition: (target) => target === 1 ? "完成一個至少 3 題、每題第一次就答對的題組" : `完成 ${target} 次零失誤題組（每組至少 3 題）` },
  sets: { title: "題組完成", unit: "組", icon: "quiz", condition: (target) => target === 1 ? "完成第一個題組" : `累計完成 ${target} 個題組` },
  episodes: { title: "Podcast 聽完", unit: "集", icon: "headphones", condition: (target) => target === 1 ? "第一次聽完一集 Podcast" : `累計聽完 ${target} 集 Podcast` },
  firstTry: { title: "一次就懂", unit: "份", icon: "learning", condition: () => "完成一份教材，每個作答過的概念第一次作答都答對" },
  goal: { title: "目標與關卡", unit: "次", icon: "compass", condition: () => "第一次為教材設定目標日期" },
  stages: { title: "目標與關卡", unit: "關", icon: "route", condition: (target) => target === 1 ? "第一次完成分天關卡（達到今天的目標重點量）" : `累計完成 ${target} 個分天關卡（每份教材每天一關）` },
  difficulties: { title: "克服難點", unit: "個", icon: "check", condition: (target) => target === 1 ? "克服一個「你的難點」：作答 3 題以上、錯一半以上的概念，之後錯誤率降到一半以下" : `克服 ${target} 個「你的難點」（錯誤率降到一半以下）` },
  diagnostics: { title: "起始程度診斷", unit: "份", icon: "search", condition: (target) => target === 1 ? "完成第一次起始程度診斷題組" : `在 ${target} 份教材完成起始程度診斷` },
  notes: { title: "筆記", unit: "份", icon: "book", condition: () => "生成第一份完整筆記" },
  annotations: { title: "個人註記", unit: "段", icon: "edit", condition: (target) => target === 1 ? "第一次在筆記寫個人註記" : `在 ${target} 個筆記段落寫個人註記（同一段算一次）` },
  uploadImage: { title: "多元教材", unit: "份", icon: "upload", condition: () => "第一次上傳圖片教材" },
  uploadAudio: { title: "多元教材", unit: "份", icon: "microphone", condition: () => "第一次上傳音訊教材" },
  uploadVideo: { title: "多元教材", unit: "份", icon: "play", condition: () => "第一次上傳影片教材" },
  uploadYoutube: { title: "多元教材", unit: "份", icon: "monitor", condition: () => "第一次用 YouTube 連結建立教材" },
  exports: { title: "匯出與整理", unit: "種", icon: "file", condition: (target) => target === 1 ? "第一次匯出（筆記、概念卡組、Podcast、知識地圖或學習診斷）" : "筆記、概念卡組、Podcast、知識地圖四種都匯出過" },
  podcastSeries: { title: "Podcast 節目", unit: "部", icon: "headphones", condition: () => "聽完一部多集 Podcast 的每一集" },
  podcastHosts: { title: "Podcast 節目", unit: "組", icon: "user", condition: (target) => `聽完 ${target} 組不同主持人的 Podcast（各至少一集）` },
  allTools: { title: "組合成就", unit: "份", icon: "compass", condition: () => "同一份教材：完成題組、標記概念卡、聽完一集 Podcast，並在筆記寫個人註記" },
  diagnosedMastery: { title: "組合成就", unit: "份", icon: "map", condition: () => "做過起始程度診斷的教材，之後完成全部學習" },
  mediaConcepts: { title: "組合成就", unit: "個", icon: "play", condition: (target) => `在含圖片、音訊、影片或 YouTube 來源的教材完成 ${target} 個概念` },
  stageStreak: { title: "組合成就", unit: "天", icon: "flame", condition: (target) => `連續 ${target} 天都完成分天關卡` },
};
// 各系列依層級由低到高列出：[代號, 名稱, 稀有度]；層級即在系列中的位置。
const tiers: Record<Series, [LearningBadgeId, string, Rarity][]> = {
  answers: [["first_answer", "初次作答", 0], ["answers_100", "百題挑戰", 1], ["answers_300", "題海航行者", 2], ["answers_1000", "千題大師", 3], ["answers_5000", "五千題宗師", 3]],
  concepts: [["first_concept", "點亮概念", 0], ["concepts_25", "概念收藏家", 1], ["concepts_100", "知識建築師", 2], ["concepts_300", "知識大典", 3], ["concepts_1000", "知識宇宙", 3]],
  materials: [["first_material", "征服第一座島", 1], ["materials_3", "群島探險家", 2], ["materials_10", "群島領主", 3], ["materials_25", "群島帝國", 3]],
  cards: [["cards_100", "卡片百回", 0], ["cards_500", "記憶練習生", 1], ["cards_2000", "過目不忘", 2], ["cards_5000", "卡片傳奇", 3]],
  listen: [["listen_30_minutes", "用耳朵學習", 0], ["listen_180_minutes", "三小時聆聽者", 1], ["listen_600_minutes", "聆聽大師", 2], ["listen_1800_minutes", "三十小時聆聽", 3]],
  streak: [["streak_3", "三日不間斷", 0], ["streak_7", "一週全勤", 1], ["streak_30", "月度堅持", 2], ["streak_100", "百日修行", 3], ["streak_365", "全年無休", 3]],
  days: [["days_7", "七日學徒", 0], ["days_30", "月積月累", 1], ["days_100", "百日學者", 2], ["days_200", "兩百日航程", 3], ["days_365", "一年之功", 3]],
  level: [["level_5", "進階探險家", 1], ["level_10", "資深航海士", 2], ["level_15", "十五級旅者", 2], ["level_20", "傳奇學者", 3], ["level_30", "三十級宗師", 3]],
  perfect: [["perfect_set_1", "零失誤", 1], ["perfect_set_10", "十次零失誤", 2], ["perfect_set_30", "三十次零失誤", 3], ["perfect_set_100", "百次零失誤", 3]],
  sets: [["first_set", "第一份題組", 0], ["sets_10", "十組練習", 1], ["sets_50", "五十組磨練", 2], ["sets_200", "題組大師", 3]],
  episodes: [["first_episode", "第一集聽完", 0], ["episodes_10", "十集聽眾", 1], ["episodes_50", "忠實聽眾", 2]],
  firstTry: [["first_try_material", "一次就懂", 3]],
  goal: [["first_goal", "立下目標", 0]],
  stages: [["first_stage", "第一關", 0], ["stages_10", "十關闖將", 1], ["stages_50", "五十關攻略", 2]],
  difficulties: [["first_difficulty", "突破難點", 1], ["difficulties_5", "難點剋星", 2], ["difficulties_20", "難不倒我", 3]],
  diagnostics: [["first_diagnostic", "摸清底細", 0], ["diagnostics_5", "五島健檢", 1]],
  notes: [["first_note", "第一份筆記", 0]],
  annotations: [["first_annotation", "寫下心得", 0], ["annotations_10", "筆記達人", 1], ["annotations_50", "眉批大師", 2]],
  uploadImage: [["upload_image", "圖片教材", 0]],
  uploadAudio: [["upload_audio", "聲音教材", 0]],
  uploadVideo: [["upload_video", "影片教材", 0]],
  uploadYoutube: [["upload_youtube", "YouTube 教材", 0]],
  exports: [["first_export", "帶著走", 0], ["export_kinds_4", "整理收藏家", 1]],
  podcastSeries: [["podcast_series", "追完一季", 1]],
  podcastHosts: [["podcast_hosts_6", "主持人全收集", 2]],
  allTools: [["all_tools_material", "全能島主", 2]],
  diagnosedMastery: [["diagnosed_mastery", "對症下藥", 2]],
  mediaConcepts: [["media_concepts_5", "影音學習者", 1]],
  stageStreak: [["stage_streak_7", "一週闖關", 2]],
};
const badgeText: Partial<Record<LearningBadgeId, { name: string; series: Series; tier: number; rarity: Rarity }>> = Object.fromEntries(Object.entries(tiers).flatMap(([key, list]) =>
  list.map(([id, name, rarity], index) => [id, { name, series: key as Series, tier: index + 1, rarity }])));
// 特殊成就：未取得時只顯示「？？？」、稀有度與一句提示，取得後才揭曉名稱與條件（判定在後端 SPECIAL_BADGES）。
type SpecialId = "combo_10" | "combo_25" | "combo_50" | "comeback_20" | "daily_50" | "daily_100" | "explorer_5" | "explorer_10"
  | "night_owl" | "early_bird" | "weekend" | "return" | "curious_10" | "curious_50" | "researcher" | "creator" | "corrected_5"
  | "perfect_day" | "all_round_day" | "thousand_pages" | "revisit"
  | "xp_day_300" | "never_give_up" | "binge_3" | "three_islands" | "new_year"
  | "goal_on_time" | "all_media" | "last_minute" | "night_listener" | "nemesis" | "same_day_learner";
const specials: Record<SpecialId, { name: string; condition: string; hint: string; icon: IconName; rarity: Rarity }> = {
  combo_10: { name: "連對王", condition: "連續答對 10 題", hint: "一口氣，一題都不能錯。", icon: "check", rarity: 1 },
  combo_25: { name: "連對達人", condition: "連續答對 25 題", hint: "十題只是開始。", icon: "check", rarity: 2 },
  combo_50: { name: "連對傳說", condition: "連續答對 50 題", hint: "再遠一點，還是一題都不能錯。", icon: "check", rarity: 3 },
  comeback_20: { name: "越挫越勇", condition: "答錯後下一題就答對，累計 20 次", hint: "跌倒之後，馬上站起來。", icon: "refresh", rarity: 1 },
  daily_50: { name: "一日五十題", condition: "同一天作答 50 題", hint: "今天，好像停不下來。", icon: "quiz", rarity: 1 },
  daily_100: { name: "百題一日", condition: "同一天作答 100 題", hint: "比停不下來，再多一倍。", icon: "quiz", rarity: 2 },
  explorer_5: { name: "五島探險家", condition: "在 5 份不同教材作答", hint: "一座島嶼還不夠。", icon: "map", rarity: 1 },
  explorer_10: { name: "十島巡航者", condition: "在 10 份不同教材作答", hint: "群島比想像中更大。", icon: "map", rarity: 2 },
  night_owl: { name: "夜貓子", condition: "凌晨 0:00～4:59 作答累計 10 題", hint: "月亮出來的時候……", icon: "eye", rarity: 0 },
  early_bird: { name: "早起的鳥兒", condition: "早上 5:00～7:59 作答累計 10 題", hint: "太陽還沒完全醒來。", icon: "sparkle", rarity: 0 },
  weekend: { name: "週末學者", condition: "同一個週末的週六與週日都有學習", hint: "別人放假的時候……", icon: "book", rarity: 0 },
  return: { name: "浪子回頭", condition: "中斷 7 天以上後重新開始學習", hint: "離開，然後回來。", icon: "route", rarity: 0 },
  curious_10: { name: "好奇寶寶", condition: "在教材問答提出 10 個問題", hint: "有問題，就問吧。", icon: "chat", rarity: 0 },
  curious_50: { name: "提問大師", condition: "在教材問答提出 50 個問題", hint: "問題越問越多。", icon: "chat", rarity: 1 },
  researcher: { name: "追根究柢", condition: "補充研究並把新來源加入教材", hint: "教材之外，還有更多。", icon: "search", rarity: 0 },
  creator: { name: "自己動手做", condition: "建立一組概念卡和一集 Podcast", hint: "把學到的，變成自己的。", icon: "edit", rarity: 0 },
  corrected_5: { name: "知錯能改", condition: "答錯過的概念之後又答對，累計 5 個", hint: "錯過的，不會再錯。", icon: "learning", rarity: 1 },
  perfect_day: { name: "完美的一天", condition: "同一天作答 20 題以上且全部答對", hint: "一整天，一題都沒錯。", icon: "sparkle", rarity: 2 },
  all_round_day: { name: "全能學習日", condition: "同一天作答、標記概念卡、收聽 Podcast 滿 1 分鐘並在教材問答提問", hint: "答題、卡片、耳朵、提問，一天之內……", icon: "compass", rarity: 2 },
  thousand_pages: { name: "千頁征服者", condition: "完成一份 500 頁以上教材的全部學習", hint: "很厚、很厚的一本。", icon: "book", rarity: 3 },
  revisit: { name: "溫故知新", condition: "教材完成 7 天以上後，又標記它的概念卡", hint: "學完了，過一陣子再回來看看。", icon: "cards", rarity: 1 },
  xp_day_300: { name: "火力全開", condition: "同一天獲得 300 XP", hint: "今天的經驗值條，好像特別長。", icon: "sparkle", rarity: 2 },
  never_give_up: { name: "永不放棄", condition: "同一個概念答錯 3 次以上後終於答對", hint: "錯了三次，還要再試一次。", icon: "refresh", rarity: 1 },
  binge_3: { name: "追劇模式", condition: "同一天聽完 3 集 Podcast", hint: "下一集，再下一集……", icon: "headphones", rarity: 1 },
  three_islands: { name: "一日三島", condition: "同一天在 3 份不同教材作答", hint: "一天之內，到處走走。", icon: "map", rarity: 1 },
  new_year: { name: "新年新希望", condition: "1 月 1 日有學習活動", hint: "一年的第一天……", icon: "sparkle", rarity: 1 },
  goal_on_time: { name: "準時達陣", condition: "在目標日以前達成目標（設定目標時尚未達標）", hint: "說到做到。", icon: "route", rarity: 1 },
  all_media: { name: "多媒體大師", condition: "圖片、音訊、影片、YouTube 四種教材都上傳過", hint: "教材不只有文字。", icon: "upload", rarity: 1 },
  last_minute: { name: "壓線達陣", condition: "在目標日當天才達成目標", hint: "最後一天，剛剛好。", icon: "clock", rarity: 2 },
  night_listener: { name: "深夜電台", condition: "凌晨 0:00～4:59 聽完一集 Podcast", hint: "夜深了，耳朵還醒著。", icon: "headphones", rarity: 1 },
  nemesis: { name: "宿敵", condition: "克服一個錯誤率曾達 70%（至少 5 題）的難點", hint: "最難的那一個，也有被打敗的一天。", icon: "flame", rarity: 3 },
  same_day_learner: { name: "現學現用", condition: "建立教材的同一天就完成它的一個概念", hint: "剛放進來的，馬上就學會。", icon: "sparkle", rarity: 1 },
};
// 成就鏈：前一步取得後才出現下一步（仍是「？？？」與提示），更後面的步驟完全不顯示。
const chains: SpecialId[][] = [["combo_10", "combo_25", "combo_50"], ["daily_50", "daily_100"], ["explorer_5", "explorer_10"], ["curious_10", "curious_50"]];
const chainOf = (id: SpecialId) => {
  const chain = chains.find((list) => list.includes(id));
  return chain ? { chain, step: chain.indexOf(id) } : null;
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

function BadgeProgress({ badge }: { badge: KnownBadge }) {
  return <span className="badge-progress">
    <span className="badge-progress-bar" role="progressbar" aria-label={`${nameOf(badge)}進度`}
      aria-valuemin={0} aria-valuemax={badge.target} aria-valuenow={badge.progress} aria-valuetext={progressText(badge)}>
      <i style={{ width: `${(badge.progress / badge.target) * 100}%` }} />
    </span>
    <span>{progressText(badge)}</span>
  </span>;
}

// 格內小字：系列與層級、成就鏈的第幾步，或特殊成就。
function kindText(badge: KnownBadge) {
  if (!isSpecial(badge.badge_id)) {
    const text = badgeText[badge.badge_id]!;
    return tiers[text.series].length > 1 ? `${series[text.series].title} · ${tierNames[text.tier - 1]}・${roman[text.tier - 1]}` : series[text.series].title;
  }
  const link = chainOf(badge.badge_id);
  return link ? `成就鏈 · 第 ${link.step + 1} 步` : "特殊成就";
}

// 一個成就一格：已取得顯示名稱、條件與日期；未取得的系列層級顯示條件與真實進度；
// 未取得的特殊成就只有「？？？」、稀有度與提示，不顯示名稱、條件或進度。
function BadgeCell({ badge }: { badge: KnownBadge }) {
  const special = isSpecial(badge.badge_id);
  const hidden = special && !badge.earned;
  return <li className={`badge-cell is-rarity-${rarityOf(badge)}${badge.earned ? " is-earned" : " is-locked"}${special ? " is-special" : ""}`}>
    <BadgeMedal badge={badge} size={44} />
    <span className="badge-cell-text">
      <small className="badge-cell-kind">{kindText(badge)}</small>
      <b>{hidden ? <span aria-label="尚未揭曉的成就">？？？</span> : nameOf(badge)} <RarityTag rarity={rarityOf(badge)} /></b>
      <small>{hidden ? specials[badge.badge_id as SpecialId].hint : conditionText(badge)}</small>
      {badge.earned ? <span className="badge-earned-on">{badge.earned_on ? `${formatDate(badge.earned_on)} 取得` : "已取得"}</span>
        : !special && <BadgeProgress badge={badge} />}
    </span>
  </li>;
}

// 成就鏈中前一步還沒取得的步驟不顯示。
function revealed(badge: KnownBadge, earned: Set<string>) {
  if (badge.earned || !isSpecial(badge.badge_id)) return true;
  const link = chainOf(badge.badge_id);
  return !link || link.step === 0 || earned.has(link.chain[link.step - 1]);
}

// 成就頁右欄：依稀有度的取得數，上方已取得、下方未取得，每個成就獨立一格。
export function BadgeCollection({ activity }: { activity: LearningActivityView }) {
  const all = knownBadges(activity);
  if (!all.length) return null;
  const earnedIds = new Set(all.filter((badge) => badge.earned).map((badge) => badge.badge_id));
  const shown = all.filter((badge) => revealed(badge, earnedIds));
  // 系列依規則表順序（同系列由低到高），特殊成就排在後面。
  const order = [...Object.values(tiers).flat().map(([id]) => id), ...Object.keys(specials)];
  const sorted = [...shown].sort((a, b) => order.indexOf(a.badge_id) - order.indexOf(b.badge_id));
  const earned = sorted.filter((badge) => badge.earned);
  const locked = sorted.filter((badge) => !badge.earned);
  return <section className="badge-collection surface" aria-labelledby="badge-collection-title">
    <header className="badge-collection-head">
      <h2 id="badge-collection-title">成就總覽</h2>
      <span className="badge-count">{earned.length} / {shown.length}</span>
    </header>
    <ul className="badge-rarity-summary" aria-label="依稀有度的取得數">
      {([0, 1, 2, 3] as Rarity[]).map((rarity) => {
        const list = shown.filter((badge) => rarityOf(badge) === rarity);
        return <li key={rarity} className={`is-rarity-${rarity}`}><b>{list.filter((badge) => badge.earned).length}<small> / {list.length}</small></b><span>{rarityNames[rarity]}</span></li>;
      })}
    </ul>
    <section className="badge-group" aria-labelledby="badge-earned-title">
      <header className="badge-collection-head">
        <h3 id="badge-earned-title">已取得</h3>
        <span className="badge-count">{earned.length}</span>
      </header>
      {earned.length ? <ul className="badge-grid">{earned.map((badge) => <BadgeCell key={badge.badge_id} badge={badge} />)}</ul>
        : <p className="badge-empty">還沒有取得任何成就；作答、標記概念卡或收聽 Podcast 就能拿到第一個。</p>}
    </section>
    <section className="badge-group" aria-labelledby="badge-locked-title">
      <header className="badge-collection-head">
        <h3 id="badge-locked-title">未取得</h3>
        <span className="badge-count is-locked">{locked.length}</span>
        <small>「？？？」是隱藏成就，只有提示，達成後才揭曉。</small>
      </header>
      {locked.length > 0 && <ul className="badge-grid">{locked.map((badge) => <BadgeCell key={badge.badge_id} badge={badge} />)}</ul>}
    </section>
    <p className="badge-note">依作答、題組、概念與教材完成、概念卡標記、Podcast 收聽、教材問答、目標與分天關卡、難點、起始程度診斷、筆記註記、上傳的教材、匯出、學習天數與等級的真實紀錄判定，以台灣日期為準；刪除教材時相關紀錄移除，徽章會重新判定。稀有度只表示難度，不影響經驗值。</p>
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
  const shown = badges.slice(0, 3);
  return <CelebrationDialog className="level-up badge-earned" label="獲得新徽章" onClose={onClose}>
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
  </CelebrationDialog>;
}
