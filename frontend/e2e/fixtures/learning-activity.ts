// 學習活動 v2 的 mock 回應；預設為沒有任何紀錄的帳號（頁首顯示 0 天、第 1 級）。
type Day = { date: string; answers: number; correct: number; card_reviews: number; listened_seconds: number; active: boolean };
type Quest = {
  kind: "review_due_cards" | "review_new_cards" | "continue_study" | "finish_podcast_episode";
  progress: number; target: number; done: boolean;
  material_id?: string | null; study_session_id?: string | null; podcast_id?: string | null; episode_index?: number | null;
};

type Badge = { badge_id: string; earned: boolean; earned_on: string | null; progress: number; target: number };
// badge-rules/v4 的代號與門檻（各系列由低到高，最後是特殊成就）；預設全部未取得，等級徽章進度為目前等級。
const badgeTargets: [string, number][] = [
  ["first_answer", 1], ["answers_100", 100], ["answers_300", 300], ["answers_1000", 1000], ["answers_5000", 5000],
  ["first_concept", 1], ["concepts_25", 25], ["concepts_100", 100], ["concepts_300", 300],
  ["first_material", 1], ["materials_3", 3], ["materials_10", 10],
  ["cards_100", 100], ["cards_500", 500], ["cards_2000", 2000],
  ["listen_30_minutes", 30], ["listen_180_minutes", 180], ["listen_600_minutes", 600],
  ["streak_3", 3], ["streak_7", 7], ["streak_30", 30], ["streak_100", 100], ["streak_365", 365],
  ["level_5", 5], ["level_10", 10], ["level_20", 20], ["level_30", 30],
  ["perfect_set_1", 1], ["perfect_set_10", 10], ["perfect_set_30", 30],
  ["combo_10", 10], ["comeback_20", 20], ["daily_50", 50], ["explorer_5", 5], ["night_owl", 10], ["early_bird", 10],
  ["weekend", 1], ["return", 1], ["curious_10", 10], ["researcher", 1], ["creator", 2], ["corrected_5", 5],
  ["first_try_material", 1], ["perfect_day", 1], ["all_round_day", 1], ["thousand_pages", 1], ["revisit", 1],
];

export function learningActivity(options: {
  today?: string; streak?: number; days?: Partial<Day>[]; total?: number; todayXp?: number; level?: [number, number, number];
  sources?: { answers: number; concepts: number; cards: number; podcast: number }; quests?: Quest[];
  // 指定徽章的進度；有 earned_on 即視為已取得（進度等於門檻）。
  badges?: Record<string, { progress?: number; earned_on?: string | null; earned?: boolean }>;
} = {}) {
  const today = options.today ?? "2026-10-21";
  const monday = new Date(`${today}T00:00:00Z`);
  monday.setUTCDate(monday.getUTCDate() - ((monday.getUTCDay() + 6) % 7));
  const week = Array.from({ length: 7 }, (_, index) => {
    const date = new Date(monday);
    date.setUTCDate(monday.getUTCDate() + index);
    return {
      date: date.toISOString().slice(0, 10), answers: 0, correct: 0, card_reviews: 0, listened_seconds: 0, active: false,
      ...options.days?.[index],
    };
  });
  const [level, levelXp, levelTarget] = options.level ?? [1, options.total ?? 0, 100];
  return {
    schema: "learning-activity/v2", timezone: "Asia/Taipei", rule_version: "xp-rules/v1", today,
    streak_days: options.streak ?? 0, active_today: week.some((day) => day.date === today && day.active),
    week,
    total_answers: week.reduce((sum, day) => sum + day.answers, 0),
    total_correct: week.reduce((sum, day) => sum + day.correct, 0),
    xp: {
      total: options.total ?? 0, today: options.todayXp ?? 0, level, level_xp: levelXp, level_target: levelTarget,
      sources: options.sources ?? { answers: options.total ?? 0, concepts: 0, cards: 0, podcast: 0 },
    },
    badges: {
      rule_version: "badge-rules/v4",
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
