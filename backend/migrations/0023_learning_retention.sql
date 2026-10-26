-- 刪除後只保留每日計量與去重所需身分，不引用已刪教材內容。
CREATE TABLE learning_retained_days (
    learner_id uuid NOT NULL REFERENCES learners(learner_id) ON DELETE CASCADE,
    source_kind text NOT NULL CHECK (source_kind IN ('material','cards','podcast')),
    source_id uuid NOT NULL,
    day date NOT NULL,
    metrics jsonb NOT NULL,
    PRIMARY KEY (learner_id, source_kind, source_id, day)
);
CREATE TABLE learning_awards (
    learner_id uuid NOT NULL REFERENCES learners(learner_id) ON DELETE CASCADE,
    badge_id text NOT NULL,
    earned_on date,
    seen boolean NOT NULL DEFAULT false,
    PRIMARY KEY (learner_id, badge_id)
);
CREATE TABLE learning_celebrations (
    learner_id uuid PRIMARY KEY REFERENCES learners(learner_id) ON DELETE CASCADE,
    seen_level integer NOT NULL DEFAULT 1 CHECK (seen_level >= 1),
    baseline_pending boolean NOT NULL DEFAULT false
);
-- 既有帳號首次計算時把目前等級與徽章標為看過；新帳號正常領取新成就。
INSERT INTO learning_celebrations (learner_id, baseline_pending)
SELECT learner_id, true FROM learners;
