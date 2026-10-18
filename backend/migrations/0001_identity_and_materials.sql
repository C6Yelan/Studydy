-- 帳號、登入、教材與檔案；直接建立現行欄位及約束。

CREATE TABLE public.schema_baseline (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    baseline_id text NOT NULL,
    prefix_sha256 character(64) NOT NULL CHECK (prefix_sha256 ~ '^[0-9a-f]{64}$'),
    method text NOT NULL CHECK (method IN ('fresh', 'adopted')),
    recorded_at timestamptz NOT NULL,
    legacy_ledger jsonb,
    verification jsonb NOT NULL
);

CREATE TABLE public.artifacts (
    artifact_id uuid NOT NULL,
    learner_id uuid NOT NULL,
    material_id uuid NOT NULL,
    kind text NOT NULL,
    media_type text NOT NULL,
    sha256 bytea NOT NULL,
    size_bytes bigint NOT NULL,
    created_at timestamp with time zone NOT NULL,
    CONSTRAINT artifact_role_media CHECK ((((kind = 'podcast_video'::text) AND (media_type = 'video/mp4'::text)) OR ((kind = 'podcast_audio'::text) AND (media_type = 'audio/wav'::text)) OR ((kind = 'normalized_pdf'::text) AND (media_type = 'application/pdf'::text)) OR ((kind = 'source_mapping'::text) AND (media_type = 'application/json'::text)) OR ((kind = 'original'::text) AND (media_type = ANY (ARRAY['application/pdf'::text, 'application/msword'::text, 'application/vnd.ms-powerpoint'::text, 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'::text, 'application/vnd.openxmlformats-officedocument.presentationml.presentation'::text, 'text/plain'::text, 'text/markdown'::text]))))),
    CONSTRAINT artifacts_sha256_check CHECK ((octet_length(sha256) = 32)),
    CONSTRAINT artifacts_size_bytes_check CHECK (((size_bytes >= 1) AND (size_bytes <= 104857600)))
);

CREATE TABLE public.learner_sessions (
    session_id uuid NOT NULL,
    learner_id uuid NOT NULL,
    token_sha256 bytea NOT NULL,
    created_at timestamp with time zone NOT NULL,
    idle_expires_at timestamp with time zone NOT NULL,
    absolute_expires_at timestamp with time zone NOT NULL,
    revoked_at timestamp with time zone,
    updated_at timestamp with time zone NOT NULL,
    CONSTRAINT learner_sessions_check CHECK (((created_at <= idle_expires_at) AND (idle_expires_at <= absolute_expires_at))),
    CONSTRAINT learner_sessions_token_sha256_check CHECK ((octet_length(token_sha256) = 32))
);

CREATE TABLE public.learners (
    learner_id uuid NOT NULL,
    created_at timestamp with time zone NOT NULL,
    email text,
    password_hash text,
    CONSTRAINT learners_credentials_pair CHECK ((((email IS NULL) AND (password_hash IS NULL)) OR ((email IS NOT NULL) AND (password_hash IS NOT NULL) AND ((char_length(email) >= 3) AND (char_length(email) <= 254)) AND (email = lower(email)))))
);

CREATE TABLE public.materials (
    material_id uuid NOT NULL,
    learner_id uuid NOT NULL,
    source_artifact_id uuid,
    upload_idempotency_key_sha256 bytea NOT NULL,
    upload_request_fingerprint bytea NOT NULL,
    created_at timestamp with time zone NOT NULL,
    display_name text NOT NULL,
    discard_requested_at timestamp with time zone,
    head_revision text,
    CONSTRAINT materials_display_name_length CHECK (((char_length(display_name) >= 1) AND (char_length(display_name) <= 200))),
    CONSTRAINT materials_upload_idempotency_key_sha256_check CHECK ((octet_length(upload_idempotency_key_sha256) = 32)),
    CONSTRAINT materials_upload_request_fingerprint_check CHECK ((octet_length(upload_request_fingerprint) = 32))
);

CREATE TABLE public.schema_migrations (
    version integer NOT NULL,
    sql_sha256 character(64) NOT NULL,
    applied_at timestamp with time zone NOT NULL,
    CONSTRAINT schema_migrations_sql_sha256_check CHECK ((sql_sha256 ~ '^[0-9a-f]{64}$'::text))
);

ALTER TABLE ONLY public.artifacts
    ADD CONSTRAINT artifacts_learner_id_material_id_artifact_id_key UNIQUE (learner_id, material_id, artifact_id);

ALTER TABLE ONLY public.artifacts
    ADD CONSTRAINT artifacts_pkey PRIMARY KEY (artifact_id);

ALTER TABLE ONLY public.learner_sessions
    ADD CONSTRAINT learner_sessions_pkey PRIMARY KEY (session_id);

ALTER TABLE ONLY public.learner_sessions
    ADD CONSTRAINT learner_sessions_token_sha256_key UNIQUE (token_sha256);

ALTER TABLE ONLY public.learners
    ADD CONSTRAINT learners_email_key UNIQUE (email);

ALTER TABLE ONLY public.learners
    ADD CONSTRAINT learners_pkey PRIMARY KEY (learner_id);

ALTER TABLE ONLY public.materials
    ADD CONSTRAINT materials_learner_id_material_id_key UNIQUE (learner_id, material_id);

ALTER TABLE ONLY public.materials
    ADD CONSTRAINT materials_learner_id_upload_idempotency_key_sha256_key UNIQUE (learner_id, upload_idempotency_key_sha256);

ALTER TABLE ONLY public.materials
    ADD CONSTRAINT materials_pkey PRIMARY KEY (material_id);

ALTER TABLE ONLY public.materials
    ADD CONSTRAINT materials_source_artifact_id_key UNIQUE (source_artifact_id);

ALTER TABLE ONLY public.schema_migrations
    ADD CONSTRAINT schema_migrations_pkey PRIMARY KEY (version);
