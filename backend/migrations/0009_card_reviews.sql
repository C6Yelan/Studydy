-- 概念卡間隔重複：每次評分保留一筆事件；排程表記錄每張卡目前的間隔與到期時間。
-- 卡片以「卡組＋概念」識別；卡組被實際刪除（教材清除）時一併刪除，軟刪除與移出卡組只是不再列出。

CREATE TABLE public.card_review_events (
    review_event_id uuid NOT NULL,
    learner_id uuid NOT NULL,
    card_set_id uuid NOT NULL,
    concept_id text NOT NULL,
    rating text NOT NULL,
    rule_version text NOT NULL,
    review_count integer NOT NULL,
    interval_days integer NOT NULL,
    due_at timestamp with time zone NOT NULL,
    idempotency_key_sha256 bytea NOT NULL,
    request_fingerprint bytea NOT NULL,
    created_at timestamp with time zone NOT NULL,
    CONSTRAINT card_review_events_pkey PRIMARY KEY (review_event_id),
    CONSTRAINT card_review_events_idempotency_key UNIQUE (learner_id, idempotency_key_sha256),
    CONSTRAINT card_review_events_card_set_fkey FOREIGN KEY (card_set_id)
        REFERENCES public.card_sets(card_set_id) ON DELETE CASCADE,
    CONSTRAINT card_review_events_concept_check CHECK (concept_id ~ '^concept:sha256:[0-9a-f]{64}$'),
    CONSTRAINT card_review_events_rating_check CHECK (rating = ANY (ARRAY['forgot'::text, 'fuzzy'::text, 'remembered'::text])),
    CONSTRAINT card_review_events_rule_check CHECK (rule_version = 'card-review/v1'),
    CONSTRAINT card_review_events_count_check CHECK (review_count >= 1),
    CONSTRAINT card_review_events_interval_check CHECK (interval_days BETWEEN 0 AND 365),
    CONSTRAINT card_review_events_idempotency_key_sha256_check CHECK (octet_length(idempotency_key_sha256) = 32),
    CONSTRAINT card_review_events_request_fingerprint_check CHECK (octet_length(request_fingerprint) = 32)
);

CREATE INDEX card_review_events_learner_time ON public.card_review_events USING btree (learner_id, created_at);

CREATE TABLE public.card_review_states (
    card_set_id uuid NOT NULL,
    concept_id text NOT NULL,
    learner_id uuid NOT NULL,
    streak integer NOT NULL,
    review_count integer NOT NULL,
    interval_days integer NOT NULL,
    last_rating text NOT NULL,
    last_reviewed_at timestamp with time zone NOT NULL,
    due_at timestamp with time zone NOT NULL,
    CONSTRAINT card_review_states_pkey PRIMARY KEY (card_set_id, concept_id),
    CONSTRAINT card_review_states_card_set_fkey FOREIGN KEY (card_set_id)
        REFERENCES public.card_sets(card_set_id) ON DELETE CASCADE,
    CONSTRAINT card_review_states_concept_check CHECK (concept_id ~ '^concept:sha256:[0-9a-f]{64}$'),
    CONSTRAINT card_review_states_rating_check CHECK (last_rating = ANY (ARRAY['forgot'::text, 'fuzzy'::text, 'remembered'::text])),
    CONSTRAINT card_review_states_streak_check CHECK (streak >= 0),
    CONSTRAINT card_review_states_count_check CHECK (review_count >= 1),
    CONSTRAINT card_review_states_interval_check CHECK (interval_days BETWEEN 0 AND 365)
);

CREATE INDEX card_review_states_due ON public.card_review_states USING btree (learner_id, due_at);
