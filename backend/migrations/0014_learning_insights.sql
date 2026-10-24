-- 難度與學習診斷是版本綁定的衍生資料，不改寫教材或作答事件。
CREATE TABLE public.learning_insights (
    insight_id uuid PRIMARY KEY,
    learner_id uuid NOT NULL,
    material_id uuid NOT NULL,
    structure_revision text NOT NULL,
    task text NOT NULL CHECK (task IN ('difficulty', 'diagnosis')),
    scope_key text NOT NULL,
    study_session_id uuid REFERENCES public.study_sessions(study_session_id) ON DELETE CASCADE,
    set_id uuid REFERENCES public.assessment_sets(set_id) ON DELETE CASCADE,
    event_watermark bigint NOT NULL DEFAULT 0 CHECK (event_watermark >= 0),
    status text NOT NULL CHECK (status IN ('pending', 'running', 'ready', 'failed')),
    input_document jsonb NOT NULL,
    runtime_lock_document jsonb,
    completed_batches jsonb NOT NULL DEFAULT '[]'::jsonb,
    model_calls integer NOT NULL DEFAULT 0 CHECK (model_calls >= 0),
    result_document jsonb,
    failure_reason text,
    lease_token uuid,
    lease_expires_at timestamptz,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    UNIQUE (learner_id, material_id, structure_revision, scope_key),
    FOREIGN KEY (learner_id, material_id, structure_revision)
        REFERENCES public.knowledge_structures(learner_id, material_id, structure_revision) ON DELETE CASCADE
);
CREATE INDEX learning_insights_queue ON public.learning_insights(task, status, created_at);
