// 學習活動 v2 的 mock 回應；預設為沒有任何紀錄的帳號（頁首顯示 0 天、第 1 級）。
type Day = { date: string; answers: number; correct: number; card_reviews: number; listened_seconds: number; active: boolean };
type Quest = {
  kind: "review_due_cards" | "review_new_cards" | "continue_study" | "finish_podcast_episode";
  progress: number; target: number; done: boolean;
  material_id?: string | null; study_session_id?: string | null; podcast_id?: string | null; episode_index?: number | null;
};

type Badge = { badge_id: string; earned: boolean; earned_on: string | null; progress: number; target: number };
// badge-rules/v1 的代號與門檻；預設全部未取得，等級徽章進度為目前等級。
const badgeTargets: [string, number][] = [
  ["first_answer", 1], ["answers_100", 100], ["first_concept", 1], ["concepts_25", 25], ["first_material", 1],
  ["cards_100", 100], ["listen_30_minutes", 30], ["streak_3", 3], ["streak_7", 7], ["level_5", 5],
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
      rule_version: "badge-rules/v1",
      items: badgeTargets.map(([badge_id, target]): Badge => {
        const custom = options.badges?.[badge_id];
        // 等級徽章預設跟隨目前等級（達到第 5 級即已取得）。
        const earned = custom?.earned ?? (!!custom?.earned_on || (badge_id === "level_5" && level >= target));
        const progress = earned ? target : custom?.progress ?? (badge_id === "level_5" ? level : 0);
        return { badge_id, earned, earned_on: earned ? custom?.earned_on ?? null : null, progress, target };
      }),
    },
    quests: (options.quests ?? []).map((quest) => ({
      material_id: null, study_session_id: null, podcast_id: null, episode_index: null, ...quest,
    })),
  };
}

export const emptyLearningActivity = learningActivity();
