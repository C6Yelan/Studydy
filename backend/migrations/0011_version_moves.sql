-- 教材新版承接：學生確認「移到新版」後，才把舊版卡組與學習進度承接到目前版本。
-- material_version_moves 保存每次確認的請求（Idempotency-Key 重送取得同一結果）；
-- card_set_moves 連結舊卡組與新版卡組，並記錄每張新卡承接自哪個舊概念（排程與 XP 卡片身分）；
-- study_session_moves 連結舊版與新版學習，記錄依概念對應承接的已完成概念。
-- 舊卡組、舊學習、作答與複習事件都不改寫；卡組或學習被實際刪除時連結一併刪除。

CREATE TABLE public.material_version_moves (
    move_id uuid NOT NULL,
    learner_id uuid NOT NULL,
    material_id uuid NOT NULL,
    to_revision text NOT NULL,
    idempotency_key_sha256 bytea NOT NULL,
    request_fingerprint bytea NOT NULL,
    result jsonb NOT NULL,
    created_at timestamp with time zone NOT NULL,
    CONSTRAINT material_version_moves_pkey PRIMARY KEY (move_id),
    CONSTRAINT material_version_moves_idempotency_key UNIQUE (learner_id, idempotency_key_sha256),
    CONSTRAINT material_version_moves_material_fkey FOREIGN KEY (learner_id, material_id)
        REFERENCES public.materials(learner_id, material_id) ON DELETE CASCADE,
    CONSTRAINT material_version_moves_revision_check CHECK (to_revision ~ '^knowledge-structure:sha256:[0-9a-f]{64}$'),
    CONSTRAINT material_version_moves_idempotency_key_sha256_check CHECK (octet_length(idempotency_key_sha256) = 32),
    CONSTRAINT material_version_moves_request_fingerprint_check CHECK (octet_length(request_fingerprint) = 32)
);

CREATE TABLE public.card_set_moves (
    target_card_set_id uuid NOT NULL,
    source_card_set_id uuid NOT NULL,
    move_id uuid NOT NULL,
    learner_id uuid NOT NULL,
    concept_map jsonb NOT NULL,
    CONSTRAINT card_set_moves_pkey PRIMARY KEY (target_card_set_id),
    CONSTRAINT card_set_moves_target_fkey FOREIGN KEY (target_card_set_id)
        REFERENCES public.card_sets(card_set_id) ON DELETE CASCADE,
    CONSTRAINT card_set_moves_source_fkey FOREIGN KEY (source_card_set_id)
        REFERENCES public.card_sets(card_set_id) ON DELETE CASCADE,
    CONSTRAINT card_set_moves_move_fkey FOREIGN KEY (move_id)
        REFERENCES public.material_version_moves(move_id) ON DELETE CASCADE,
    CONSTRAINT card_set_moves_distinct_check CHECK (target_card_set_id <> source_card_set_id),
    CONSTRAINT card_set_moves_map_check CHECK (jsonb_typeof(concept_map) = 'object')
);

CREATE INDEX card_set_moves_source ON public.card_set_moves USING btree (source_card_set_id);
CREATE INDEX card_set_moves_learner ON public.card_set_moves USING btree (learner_id);

CREATE TABLE public.study_session_moves (
    target_study_session_id uuid NOT NULL,
    source_study_session_id uuid NOT NULL,
    move_id uuid NOT NULL,
    learner_id uuid NOT NULL,
    completed_concept_ids text[] NOT NULL,
    CONSTRAINT study_session_moves_pkey PRIMARY KEY (target_study_session_id),
    CONSTRAINT study_session_moves_target_fkey FOREIGN KEY (target_study_session_id)
        REFERENCES public.study_sessions(study_session_id) ON DELETE CASCADE,
    CONSTRAINT study_session_moves_source_fkey FOREIGN KEY (source_study_session_id)
        REFERENCES public.study_sessions(study_session_id) ON DELETE CASCADE,
    CONSTRAINT study_session_moves_move_fkey FOREIGN KEY (move_id)
        REFERENCES public.material_version_moves(move_id) ON DELETE CASCADE,
    CONSTRAINT study_session_moves_distinct_check CHECK (target_study_session_id <> source_study_session_id)
);

CREATE INDEX study_session_moves_source ON public.study_session_moves USING btree (source_study_session_id);
