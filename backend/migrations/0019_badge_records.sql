-- 成就 badge-rules/v7 與 xp-rules/v4 需要、但無法由既有資料推導的最小紀錄。

-- 目標第一次設定的時間（之後修改目標不變）。既有目標以目前的 baseline_at 回填：
-- 沒改過目標時就是設定時間；改過時晚於實際設定時間。
ALTER TABLE public.learning_goals ADD COLUMN created_at timestamptz;
UPDATE public.learning_goals SET created_at = baseline_at;
ALTER TABLE public.learning_goals ALTER COLUMN created_at SET NOT NULL;

-- 分天關卡完成（每份教材每個台灣日期一筆）與在目標日以前達成目標（每份教材一筆）。
-- 當日配額只保存「今天」，過去的關卡無法重算，因此在交卷時記錄；只記日期與目標日，不含作答內容。
CREATE TABLE public.goal_milestones (
    learner_id uuid NOT NULL,
    material_id uuid NOT NULL,
    kind text NOT NULL CHECK (kind IN ('stage', 'achieved')),
    day date NOT NULL,
    target_date date,
    created_at timestamptz NOT NULL,
    PRIMARY KEY (material_id, kind, day),
    CHECK ((kind = 'achieved') = (target_date IS NOT NULL)),
    CHECK (kind <> 'achieved' OR day <= target_date),
    FOREIGN KEY (learner_id, material_id) REFERENCES public.materials(learner_id, material_id) ON DELETE CASCADE
);
CREATE UNIQUE INDEX goal_milestones_achieved_once ON public.goal_milestones(material_id) WHERE kind = 'achieved';
CREATE INDEX goal_milestones_owner ON public.goal_milestones(learner_id);

-- 每種匯出第一次成功的時間；只記種類與時間，不記檔案內容或教材。
CREATE TABLE public.learner_exports (
    learner_id uuid NOT NULL REFERENCES public.learners(learner_id),
    kind text NOT NULL CHECK (kind IN ('notes', 'cards', 'podcast', 'map', 'diagnosis')),
    first_exported_at timestamptz NOT NULL,
    PRIMARY KEY (learner_id, kind)
);
