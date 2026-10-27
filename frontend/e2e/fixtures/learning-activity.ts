// 學習活動 v3 與學習節奏的 mock 回應；預設為沒有任何紀錄的帳號（頁首顯示第 1 級、0 天）。
type Day = { date: string; answers: number; correct: number; card_reviews: number; listened_seconds: number; active: boolean };
type Quest = {
  kind: "mark_cards" | "continue_study" | "finish_podcast_episode";
  progress: number; target: number; done: boolean;
  material_id?: string | null; study_session_id?: string | null; podcast_id?: string | null; episode_index?: number | null;
};

type Badge = { badge_id: string; earned: boolean; earned_on: string | null; progress: number; target: number };
// badge-rules/v8 的代號與門檻（各系列由低到高，最後是特殊成就）；預設全部未取得，等級徽章進度為目前等級。
const badgeTargets: [string, number][] = [
  ["first_answer", 1], ["answers_100", 100], ["answers_300", 300], ["answers_1000", 1000], ["answers_5000", 5000],
  ["first_concept", 1], ["concepts_25", 25], ["concepts_100", 100], ["concepts_300", 300], ["concepts_1000", 1000],
  ["first_material", 1], ["materials_3", 3], ["materials_10", 10], ["materials_25", 25],
  ["cards_100", 100], ["cards_500", 500], ["cards_2000", 2000], ["cards_5000", 5000],
  ["listen_30_minutes", 30], ["listen_180_minutes", 180], ["listen_600_minutes", 600], ["listen_1800_minutes", 1800],
  ["streak_3", 3], ["streak_7", 7], ["streak_30", 30], ["streak_100", 100], ["streak_365", 365],
  ["level_5", 5], ["level_10", 10], ["level_15", 15], ["level_20", 20], ["level_30", 30],
  ["perfect_set_1", 1], ["perfect_set_10", 10], ["perfect_set_30", 30], ["perfect_set_100", 100],
  ["days_7", 7], ["days_30", 30], ["days_100", 100], ["days_200", 200], ["days_365", 365],
  ["first_set", 1], ["sets_10", 10], ["sets_50", 50], ["sets_200", 200],
  ["first_episode", 1], ["episodes_10", 10], ["episodes_50", 50],
  ["combo_10", 10], ["combo_25", 25], ["combo_50", 50], ["comeback_20", 20], ["daily_50", 50], ["daily_100", 100],
  ["explorer_5", 5], ["explorer_10", 10], ["night_owl", 10], ["early_bird", 10],
  ["weekend", 1], ["return", 1], ["curious_10", 10], ["curious_50", 50], ["researcher", 1], ["creator", 2], ["corrected_5", 5],
  ["first_try_material", 1], ["perfect_day", 1], ["all_round_day", 1], ["thousand_pages", 1], ["revisit", 1],
  ["xp_day_300", 300], ["never_give_up", 1], ["binge_3", 3], ["three_islands", 3], ["new_year", 1],
];
// v7 新增：系列（後端 BADGES 尾端）與特殊成就（SPECIAL_BADGES 尾端），依後端順序插入。
const v7Series: [string, number][] = [
  ["first_goal", 1], ["first_stage", 1], ["stages_10", 10], ["stages_50", 50],
  ["first_difficulty", 1], ["difficulties_5", 5], ["difficulties_20", 20],
  ["first_note", 1], ["first_annotation", 1], ["annotations_10", 10], ["annotations_50", 50],
  ["upload_image", 1], ["upload_audio", 1], ["upload_video", 1], ["upload_youtube", 1], ["first_export", 1], ["export_kinds_4", 4],
  ["podcast_series", 1], ["podcast_hosts_all", 6],
  ["all_tools_material", 1], ["media_concepts_5", 5], ["stage_streak_7", 7],
];
const v7Specials: [string, number][] = [
  ["goal_on_time", 1], ["all_media", 4], ["last_minute", 1], ["night_listener", 1], ["nemesis", 1], ["same_day_learner", 1],
];
badgeTargets.splice(badgeTargets.findIndex(([id]) => id === "combo_10"), 0, ...v7Series);
badgeTargets.push(...v7Specials);

export function learningActivity(options: {
  today?: string; streak?: number; activeToday?: boolean; answers?: [number, number]; total?: number; todayXp?: number; level?: [number, number, number];
  sources?: { answers: number; concepts: number; cards: number; podcast: number; goals?: number; difficulties?: number; diagnostics?: number }; quests?: Quest[];
  // 指定徽章的進度；有 earned_on 即視為已取得（進度等於門檻）。
  badges?: Record<string, { progress?: number; earned_on?: string | null; earned?: boolean }>;
} = {}) {
  const today = options.today ?? "2026-10-21";
  const [level, levelXp, levelTarget] = options.level ?? [1, options.total ?? 0, 100];
  return {
    schema: "learning-activity/v4", timezone: "Asia/Taipei", rule_version: "xp-rules/v5", today,
    streak_days: options.streak ?? 0, active_today: options.activeToday ?? false,
    total_answers: options.answers?.[0] ?? 0,
    total_correct: options.answers?.[1] ?? 0,
    xp: {
      total: options.total ?? 0, today: options.todayXp ?? 0, level, level_xp: levelXp, level_target: levelTarget,
      sources: { goals: 0, difficulties: 0, ...(options.sources ?? { answers: options.total ?? 0, concepts: 0, cards: 0, podcast: 0 }) },
    },
    badges: {
      rule_version: "badge-rules/v8",
      // 指定了不在規則表的代號時照樣回傳（門檻 1），用來驗證畫面忽略不認得的徽章。
      items: [...badgeTargets, ...Object.keys(options.badges ?? {}).filter((id) => !badgeTargets.some(([known]) => known === id)).map((id): [string, number] => [id, 1])]
        .map(([badge_id, target]): Badge => {
          const custom = options.badges?.[badge_id];
          // 等級徽章預設跟隨目前等級（達到門檻等級即已取得）。
          const isLevel = badge_id.startsWith("level_");
          const earned = custom?.earned ?? (!!custom?.earned_on || (isLevel && level >= target));
          const progress = earned ? target : custom?.progress ?? (isLevel ? Math.min(level, target) : 0);
          return { badge_id, earned, earned_on: earned ? custom?.earned_on ?? null : null, progress, target };
        }),
    },
    quests: (options.quests ?? []).map((quest) => ({
      material_id: null, study_session_id: null, podcast_id: null, episode_index: null, ...quest,
    })),
  };
}

export const emptyLearningActivity = learningActivity();

const iso = (date: Date) => date.toISOString().slice(0, 10);
const shift = (date: Date, days: number) => { const next = new Date(date); next.setUTCDate(next.getUTCDate() + days); return next; };

// 與後端 rhythm_window 相同的區間：週一到週日、自然月、13 週與 52 週（以本週日為最後一天）。
export function learningRhythm(options: {
  period?: "week" | "month" | "quarter" | "year"; offset?: number; today?: string; streak?: number; firstDay?: string | null;
  // 依日期指定當天紀錄；沒有指定的日期為 0。
  days?: Record<string, Partial<Day>>;
} = {}) {
  const today = options.today ?? "2026-10-21";
  const period = options.period ?? "week", offset = options.offset ?? 0;
  const now = new Date(`${today}T00:00:00Z`);
  const monday = shift(now, -((now.getUTCDay() + 6) % 7));
  let start: Date, end: Date;
  if (period === "week") { start = shift(monday, -7 * offset); end = shift(start, 6); }
  else if (period === "month") {
    start = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth() - offset, 1));
    end = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth() - offset + 1, 0));
  } else {
    const weeks = period === "quarter" ? 13 : 52;
    end = shift(monday, 6 - 7 * weeks * offset);
    start = shift(end, 1 - 7 * weeks);
  }
  const days: Day[] = [];
  for (let day = start; day <= end; day = shift(day, 1)) {
    const date = iso(day);
    days.push({ date, answers: 0, correct: 0, card_reviews: 0, listened_seconds: 0, active: false, ...options.days?.[date] });
  }
  const recorded = Object.keys(options.days ?? {}).sort();
  return {
    schema: "learning-rhythm/v1", timezone: "Asia/Taipei", today, period, offset, start: iso(start), end: iso(end),
    first_day: options.firstDay === undefined ? recorded[0] ?? null : options.firstDay,
    streak_days: options.streak ?? 0, days,
  };
}

export const emptyCelebrationClaim = { schema: "celebration-claim/v1", level: null, badge_ids: [] };
