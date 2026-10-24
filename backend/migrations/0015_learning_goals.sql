-- 只存目標設定、重排基準與當前日計量基準；未來日程及概念名單都不保存。
CREATE TABLE public.learning_goals (
    material_id uuid PRIMARY KEY,
    learner_id uuid NOT NULL,
    target_date date NOT NULL,
    target_percent integer NOT NULL CHECK (target_percent IN (60,80,100)),
    baseline_at timestamptz NOT NULL,
    baseline_revision text NOT NULL,
    baseline_assessable_points integer NOT NULL CHECK (baseline_assessable_points >= 0),
    baseline_completed_points integer NOT NULL CHECK (baseline_completed_points >= 0),
    baseline_unavailable_claim_ids text[] NOT NULL,
    notice_revision text NOT NULL,
    notice_points integer NOT NULL CHECK (notice_points >= 0),
    daily_date date NOT NULL,
    daily_unavailable_claim_ids text[] NOT NULL,
    daily_completed_points integer NOT NULL CHECK (daily_completed_points >= 0),
    daily_quota_points integer NOT NULL CHECK (daily_quota_points >= 0),
    FOREIGN KEY (learner_id,material_id) REFERENCES public.materials(learner_id,material_id) ON DELETE CASCADE
);
CREATE INDEX learning_goals_owner ON public.learning_goals(learner_id);
