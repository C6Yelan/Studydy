-- 主題範圍、研究工作與已核對原檔；空庫不重播歷史提案轉換。

CREATE TABLE public.material_research (
    research_id uuid NOT NULL,
    learner_id uuid NOT NULL,
    material_id uuid,
    base_revision text,
    query text NOT NULL,
    mode text NOT NULL,
    status text NOT NULL,
    candidates jsonb DEFAULT '[]'::jsonb NOT NULL,
    selection jsonb DEFAULT '[]'::jsonb NOT NULL,
    search_query text,
    cursor text,
    error_code text,
    run_id uuid,
    lease_token uuid,
    lease_expires_at timestamp with time zone,
    idempotency_key_sha256 bytea NOT NULL,
    created_at timestamp with time zone NOT NULL,
    staged_content bytea,
    staged_metadata jsonb,
    topic_id uuid,
    deleted_at timestamp with time zone,
    search_state jsonb DEFAULT '{}'::jsonb NOT NULL,
    CONSTRAINT material_research_mode_check CHECK ((mode = ANY (ARRAY['review'::text, 'self-study'::text]))),
    CONSTRAINT material_research_status_check CHECK ((status = ANY (ARRAY['searching'::text, 'selecting'::text, 'acquiring'::text, 'normalizing'::text, 'ready'::text, 'submitted'::text, 'failed'::text, 'cancelled'::text]))),
    CONSTRAINT research_startpoint CHECK (((material_id IS NOT NULL) OR (topic_id IS NOT NULL)))
);

CREATE TABLE public.material_research_documents (
    research_id uuid NOT NULL,
    sha256 bytea NOT NULL,
    size_bytes bigint NOT NULL,
    content bytea NOT NULL,
    CONSTRAINT material_research_documents_check CHECK ((octet_length(content) = size_bytes)),
    CONSTRAINT material_research_documents_sha256_check CHECK ((octet_length(sha256) = 32)),
    CONSTRAINT material_research_documents_size_bytes_check CHECK (((size_bytes > 0) AND (size_bytes <= 94371840)))
);

CREATE TABLE public.topic_scopes (
    topic_id uuid NOT NULL,
    learner_id uuid NOT NULL,
    request text NOT NULL,
    proposal jsonb,
    generation_provider text,
    version bigint DEFAULT 1 NOT NULL,
    status text NOT NULL,
    research_id uuid,
    approved_sha256 text,
    approved_at timestamp with time zone,
    approval_key_sha256 bytea,
    idempotency_key_sha256 bytea NOT NULL,
    lease_token uuid,
    lease_expires_at timestamp with time zone,
    error_code text,
    created_at timestamp with time zone NOT NULL,
    material_id uuid,
    base_revision text,
    CONSTRAINT topic_material_revision CHECK (((material_id IS NULL) = (base_revision IS NULL))),
    CONSTRAINT topic_scopes_status_check CHECK ((status = ANY (ARRAY['pending'::text, 'ready'::text, 'approved'::text, 'failed'::text, 'cancelled'::text])))
);

ALTER TABLE ONLY public.material_research_documents
    ADD CONSTRAINT material_research_documents_pkey PRIMARY KEY (research_id, sha256);

ALTER TABLE ONLY public.material_research
    ADD CONSTRAINT material_research_learner_id_material_id_idempotency_key_sh_key UNIQUE (learner_id, material_id, idempotency_key_sha256);

ALTER TABLE ONLY public.material_research
    ADD CONSTRAINT material_research_pkey PRIMARY KEY (research_id);

ALTER TABLE ONLY public.topic_scopes
    ADD CONSTRAINT topic_scopes_learner_id_idempotency_key_sha256_key UNIQUE (learner_id, idempotency_key_sha256);

ALTER TABLE ONLY public.topic_scopes
    ADD CONSTRAINT topic_scopes_pkey PRIMARY KEY (topic_id);

ALTER TABLE ONLY public.topic_scopes
    ADD CONSTRAINT topic_scopes_research_id_key UNIQUE (research_id);

CREATE INDEX material_research_work ON public.material_research USING btree (created_at) WHERE (status = ANY (ARRAY['searching'::text, 'acquiring'::text, 'normalizing'::text]));

CREATE INDEX topic_scopes_material ON public.topic_scopes USING btree (learner_id, material_id, created_at);
