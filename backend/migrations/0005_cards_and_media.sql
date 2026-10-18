-- 固定教材版本的卡組、問答、Podcast、場景與影片。

CREATE TABLE public.card_sets (
    card_set_id uuid NOT NULL,
    learner_id uuid NOT NULL,
    material_id uuid NOT NULL,
    knowledge_structure_revision text,
    name text NOT NULL,
    concept_ids text[] NOT NULL,
    idempotency_key_sha256 bytea NOT NULL,
    request_fingerprint bytea NOT NULL,
    created_at timestamp with time zone NOT NULL,
    deleted_at timestamp with time zone,
    version bigint DEFAULT 1 NOT NULL,
    status text DEFAULT 'ready'::text NOT NULL,
    generation jsonb DEFAULT '{}'::jsonb NOT NULL,
    lease_token uuid,
    lease_expires_at timestamp with time zone,
    card_count integer,
    page_counts jsonb DEFAULT '{}'::jsonb NOT NULL,
    CONSTRAINT card_sets_card_count_check CHECK ((card_count > 0)),
    CONSTRAINT card_sets_check CHECK ((((deleted_at IS NULL) AND (knowledge_structure_revision IS NOT NULL) AND ((char_length(btrim(name)) >= 1) AND (char_length(btrim(name)) <= 200)) AND (cardinality(concept_ids) > 0)) OR ((deleted_at IS NOT NULL) AND (knowledge_structure_revision IS NULL) AND (name = ''::text) AND (cardinality(concept_ids) = 0)))),
    CONSTRAINT card_sets_generation_status CHECK ((status = ANY (ARRAY['pending'::text, 'running'::text, 'ready'::text, 'failed'::text]))),
    CONSTRAINT card_sets_idempotency_key_sha256_check CHECK ((octet_length(idempotency_key_sha256) = 32)),
    CONSTRAINT card_sets_request_fingerprint_check CHECK ((octet_length(request_fingerprint) = 32)),
    CONSTRAINT card_sets_version_check CHECK ((version >= 1))
);

CREATE TABLE public.podcast_scenes (
    scene_id uuid NOT NULL,
    podcast_id uuid NOT NULL,
    episode_index integer NOT NULL,
    input_sha256 text NOT NULL,
    status text NOT NULL,
    alignment jsonb,
    checks jsonb,
    manifest jsonb,
    error_code text,
    version bigint DEFAULT 1 NOT NULL,
    lease_token uuid,
    lease_expires_at timestamp with time zone,
    created_at timestamp with time zone NOT NULL,
    CONSTRAINT podcast_scenes_episode_index_check CHECK ((episode_index >= 0)),
    CONSTRAINT podcast_scenes_status_check CHECK ((status = ANY (ARRAY['pending'::text, 'running'::text, 'ready'::text, 'failed'::text, 'cancelled'::text])))
);

CREATE TABLE public.podcast_videos (
    video_id uuid NOT NULL,
    podcast_id uuid NOT NULL,
    episode_index integer NOT NULL,
    source_sha256 text NOT NULL,
    status text NOT NULL,
    artifact_id uuid,
    manifest jsonb,
    error_code text,
    version bigint DEFAULT 1 NOT NULL,
    lease_token uuid,
    lease_expires_at timestamp with time zone,
    created_at timestamp with time zone NOT NULL,
    CONSTRAINT podcast_videos_check CHECK (((status = 'running'::text) = ((lease_token IS NOT NULL) AND (lease_expires_at IS NOT NULL)))),
    CONSTRAINT podcast_videos_check1 CHECK (((status = 'ready'::text) = ((artifact_id IS NOT NULL) AND (manifest IS NOT NULL)))),
    CONSTRAINT podcast_videos_episode_index_check CHECK ((episode_index >= 0)),
    CONSTRAINT podcast_videos_status_check CHECK ((status = ANY (ARRAY['pending'::text, 'running'::text, 'ready'::text, 'failed'::text, 'cancelled'::text])))
);

CREATE TABLE public.podcasts (
    podcast_id uuid NOT NULL,
    learner_id uuid NOT NULL,
    material_id uuid NOT NULL,
    knowledge_structure_revision text,
    name text NOT NULL,
    mode text NOT NULL,
    concept_ids text[] NOT NULL,
    episodes jsonb NOT NULL,
    status text NOT NULL,
    error_code text,
    version bigint DEFAULT 1 NOT NULL,
    lease_token uuid,
    lease_expires_at timestamp with time zone,
    idempotency_key_sha256 bytea NOT NULL,
    request_fingerprint bytea NOT NULL,
    created_at timestamp with time zone NOT NULL,
    CONSTRAINT podcasts_check CHECK (((status = 'running'::text) = ((lease_token IS NOT NULL) AND (lease_expires_at IS NOT NULL)))),
    CONSTRAINT podcasts_check1 CHECK (((status = 'deleted'::text) = (knowledge_structure_revision IS NULL))),
    CONSTRAINT podcasts_mode_check CHECK ((mode = ANY (ARRAY['quick'::text, 'full'::text]))),
    CONSTRAINT podcasts_status_check CHECK ((status = ANY (ARRAY['pending'::text, 'running'::text, 'ready'::text, 'failed'::text, 'cancelled'::text, 'deleted'::text])))
);

CREATE TABLE public.voice_conversations (
    conversation_id uuid NOT NULL,
    learner_id uuid NOT NULL,
    material_id uuid NOT NULL,
    knowledge_structure_revision text,
    title text NOT NULL,
    created_at timestamp with time zone NOT NULL,
    deleted_at timestamp with time zone,
    idempotency_key_sha256 bytea NOT NULL,
    memory jsonb
);

CREATE TABLE public.voice_turns (
    turn_id uuid NOT NULL,
    conversation_id uuid NOT NULL,
    request_key text NOT NULL,
    fingerprint text NOT NULL,
    question text NOT NULL,
    answer jsonb,
    status text NOT NULL,
    recording bytea,
    audio bytea,
    error_code text,
    lease_token uuid,
    lease_expires_at timestamp with time zone,
    created_at timestamp with time zone NOT NULL,
    context jsonb,
    mode text,
    transcription jsonb,
    CONSTRAINT voice_turns_mode_check CHECK ((mode = ANY (ARRAY['text'::text, 'voice'::text]))),
    CONSTRAINT voice_turns_status_check CHECK ((status = ANY (ARRAY['transcribing'::text, 'draft'::text, 'pending'::text, 'answering'::text, 'speaking'::text, 'ready'::text, 'failed'::text, 'cancelled'::text])))
);

ALTER TABLE ONLY public.card_sets
    ADD CONSTRAINT card_sets_learner_id_material_id_idempotency_key_sha256_key UNIQUE (learner_id, material_id, idempotency_key_sha256);

ALTER TABLE ONLY public.card_sets
    ADD CONSTRAINT card_sets_pkey PRIMARY KEY (card_set_id);

ALTER TABLE ONLY public.podcast_scenes
    ADD CONSTRAINT podcast_scenes_pkey PRIMARY KEY (scene_id);

ALTER TABLE ONLY public.podcast_scenes
    ADD CONSTRAINT podcast_scenes_podcast_id_episode_index_key UNIQUE (podcast_id, episode_index);

ALTER TABLE ONLY public.podcast_videos
    ADD CONSTRAINT podcast_videos_pkey PRIMARY KEY (video_id);

ALTER TABLE ONLY public.podcast_videos
    ADD CONSTRAINT podcast_videos_podcast_id_episode_index_key UNIQUE (podcast_id, episode_index);

ALTER TABLE ONLY public.podcasts
    ADD CONSTRAINT podcasts_learner_id_material_id_idempotency_key_sha256_key UNIQUE (learner_id, material_id, idempotency_key_sha256);

ALTER TABLE ONLY public.podcasts
    ADD CONSTRAINT podcasts_pkey PRIMARY KEY (podcast_id);

ALTER TABLE ONLY public.voice_conversations
    ADD CONSTRAINT voice_conversations_learner_id_material_id_idempotency_key__key UNIQUE (learner_id, material_id, idempotency_key_sha256);

ALTER TABLE ONLY public.voice_conversations
    ADD CONSTRAINT voice_conversations_pkey PRIMARY KEY (conversation_id);

ALTER TABLE ONLY public.voice_turns
    ADD CONSTRAINT voice_turns_conversation_id_request_key_key UNIQUE (conversation_id, request_key);

ALTER TABLE ONLY public.voice_turns
    ADD CONSTRAINT voice_turns_pkey PRIMARY KEY (turn_id);

CREATE INDEX card_sets_pending_generation ON public.card_sets USING btree (created_at) WHERE ((deleted_at IS NULL) AND (status = ANY (ARRAY['pending'::text, 'running'::text])));

CREATE INDEX card_sets_revision ON public.card_sets USING btree (learner_id, material_id, knowledge_structure_revision) WHERE (deleted_at IS NULL);

CREATE INDEX podcast_videos_work ON public.podcast_videos USING btree (created_at) WHERE (status = ANY (ARRAY['pending'::text, 'running'::text]));

CREATE INDEX podcasts_revision ON public.podcasts USING btree (learner_id, material_id, knowledge_structure_revision);

CREATE INDEX podcasts_work ON public.podcasts USING btree (created_at) WHERE (status = ANY (ARRAY['pending'::text, 'running'::text]));

CREATE INDEX voice_conversations_revision ON public.voice_conversations USING btree (material_id, knowledge_structure_revision);

CREATE INDEX voice_turns_work ON public.voice_turns USING btree (created_at) WHERE (status = ANY (ARRAY['transcribing'::text, 'pending'::text, 'answering'::text, 'speaking'::text]));
