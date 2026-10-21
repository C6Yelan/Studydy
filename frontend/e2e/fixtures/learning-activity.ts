// 學習活動 v2 的 mock 回應；預設為沒有任何紀錄的帳號（頁首顯示 0 天、第 1 級）。
type Day = { date: string; answers: number; correct: number; card_reviews: number; listened_seconds: number; active: boolean };
type Quest = {
  kind: "review_due_cards" | "review_new_cards" | "continue_study" | "finish_podcast_episode";
  progress: number; target: number; done: boolean;
  material_id?: string | null; study_session_id?: string | null; podcast_id?: string | null; episode_index?: number | null;
};

export function learningActivity(options: {
  today?: string; streak?: number; days?: Partial<Day>[]; total?: number; todayXp?: number; level?: [number, number, number];
  sources?: { answers: number; concepts: number; cards: number; podcast: number }; quests?: Quest[];
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
    quests: (options.quests ?? []).map((quest) => ({
      material_id: null, study_session_id: null, podcast_id: null, episode_index: null, ...quest,
    })),
  };
}

export const emptyLearningActivity = learningActivity();
