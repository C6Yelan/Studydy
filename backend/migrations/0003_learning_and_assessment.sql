-- 學習工作階段、題組、作答事件與原子發布保護。

CREATE FUNCTION public.protect_assessment_set_origin() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF TG_OP = 'UPDATE' AND ROW(NEW.kind, NEW.diagnostic_set_id)
        IS DISTINCT FROM ROW(OLD.kind, OLD.diagnostic_set_id) THEN
        RAISE EXCEPTION 'assessment set origin is immutable';
    END IF;
    IF NEW.diagnostic_set_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM assessment_sets
        WHERE set_id = NEW.diagnostic_set_id AND kind = 'diagnostic'
    ) THEN
        RAISE EXCEPTION 'remediation requires a diagnostic origin';
    END IF;
    RETURN NEW;
END;
$$;

CREATE FUNCTION public.protect_published_set_item() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF OLD.assessment_revision IS NOT NULL AND NEW IS DISTINCT FROM OLD THEN
        RAISE EXCEPTION 'published assessment set item is immutable';
    END IF;
    RETURN NEW;
END;
$$;

CREATE FUNCTION public.protect_sealed_assessment_set() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF OLD.sealed_at IS NOT NULL AND
        ROW(
            NEW.learner_id, NEW.material_id, NEW.study_session_id,
            NEW.knowledge_structure_revision, NEW.target_concept_id,
            NEW.target_plan, NEW.requested_count, NEW.runtime_lock_document,
            NEW.sealed_at
        ) IS DISTINCT FROM ROW(
            OLD.learner_id, OLD.material_id, OLD.study_session_id,
            OLD.knowledge_structure_revision, OLD.target_concept_id,
            OLD.target_plan, OLD.requested_count, OLD.runtime_lock_document,
            OLD.sealed_at
        ) THEN
        RAISE EXCEPTION 'sealed assessment set scope is immutable';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TABLE public.answer_events (
    answer_event_id uuid NOT NULL,
    study_session_id uuid NOT NULL,
    material_id uuid NOT NULL,
    knowledge_structure_revision text NOT NULL,
    assessment_revision text NOT NULL,
    question_id text NOT NULL,
    semantic_identity text NOT NULL,
    target_concept_id text NOT NULL,
    target_claim_id text NOT NULL,
    selected_option_id text NOT NULL,
    is_correct boolean NOT NULL,
    mastery_qualified boolean NOT NULL,
    event_number bigint NOT NULL,
    idempotency_key_sha256 bytea NOT NULL,
    request_fingerprint bytea NOT NULL,
    created_at timestamp with time zone NOT NULL,
    CONSTRAINT answer_events_event_number_check CHECK ((event_number >= 1)),
    CONSTRAINT answer_events_idempotency_key_sha256_check CHECK ((octet_length(idempotency_key_sha256) = 32)),
    CONSTRAINT answer_events_request_fingerprint_check CHECK ((octet_length(request_fingerprint) = 32)),
    CONSTRAINT answer_events_selected_option_id_check CHECK ((selected_option_id ~ '^option:sha256:[0-9a-f]{64}$'::text))
);

CREATE TABLE public.assessment_set_items (
    set_id uuid NOT NULL,
    ordinal integer NOT NULL,
    study_session_id uuid NOT NULL,
    knowledge_structure_revision text NOT NULL,
    target_concept_id text NOT NULL,
    target_claim_id text NOT NULL,
    state text NOT NULL,
    attempts integer DEFAULT 0 NOT NULL,
    failure_reason text,
    prepared_document jsonb,
    assessment_revision text,
    CONSTRAINT assessment_set_items_attempts_check CHECK (((attempts >= 0) AND (attempts <= 2))),
    CONSTRAINT assessment_set_items_check CHECK (((state = 'published'::text) = (assessment_revision IS NOT NULL))),
    CONSTRAINT assessment_set_items_check1 CHECK (((state = 'verified'::text) = (prepared_document IS NOT NULL))),
    CONSTRAINT assessment_set_items_ordinal_check CHECK ((ordinal > 0)),
    CONSTRAINT assessment_set_items_state_check CHECK ((state = ANY (ARRAY['pending'::text, 'generating'::text, 'verified'::text, 'published'::text, 'failed'::text, 'omitted'::text])))
);

CREATE TABLE public.assessment_sets (
    set_id uuid NOT NULL,
    learner_id uuid NOT NULL,
    material_id uuid NOT NULL,
    study_session_id uuid NOT NULL,
    knowledge_structure_revision text NOT NULL,
    target_concept_id text NOT NULL,
    kind text NOT NULL,
    target_plan jsonb NOT NULL,
    requested_count integer NOT NULL,
    runtime_lock_document jsonb NOT NULL,
    status text NOT NULL,
    set_version bigint DEFAULT 1 NOT NULL,
    idempotency_key_sha256 bytea NOT NULL,
    request_fingerprint bytea NOT NULL,
    action_receipts jsonb DEFAULT '{}'::jsonb NOT NULL,
    lease_token uuid,
    lease_expires_at timestamp with time zone,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL,
    sealed_at timestamp with time zone,
    completed_at timestamp with time zone,
    diagnostic_set_id uuid,
    CONSTRAINT assessment_sets_check CHECK (((lease_token IS NULL) = (lease_expires_at IS NULL))),
    CONSTRAINT assessment_sets_check1 CHECK (((status <> ALL (ARRAY['ready'::text, 'in_progress'::text, 'completed'::text])) OR (sealed_at IS NOT NULL))),
    CONSTRAINT assessment_sets_check2 CHECK (((status <> ALL (ARRAY['preparing'::text, 'partial_ready'::text, 'failed'::text])) OR (sealed_at IS NULL))),
    CONSTRAINT assessment_sets_idempotency_key_sha256_check CHECK ((octet_length(idempotency_key_sha256) = 32)),
    CONSTRAINT assessment_sets_kind_check CHECK ((kind = ANY (ARRAY['diagnostic'::text, 'remediation'::text]))),
    CONSTRAINT assessment_sets_request_fingerprint_check CHECK ((octet_length(request_fingerprint) = 32)),
    CONSTRAINT assessment_sets_requested_count_check CHECK ((requested_count >= 0)),
    CONSTRAINT assessment_sets_set_version_check CHECK ((set_version > 0)),
    CONSTRAINT assessment_sets_status_check CHECK ((status = ANY (ARRAY['preparing'::text, 'partial_ready'::text, 'failed'::text, 'ready'::text, 'in_progress'::text, 'completed'::text, 'cancelled'::text]))),
    CONSTRAINT remediation_origin_required CHECK ((((kind = 'remediation'::text) = (diagnostic_set_id IS NOT NULL)) AND (diagnostic_set_id IS DISTINCT FROM set_id)))
);

CREATE TABLE public.assessments (
    assessment_revision text NOT NULL,
    study_session_id uuid NOT NULL,
    knowledge_structure_revision text NOT NULL,
    question_id text NOT NULL,
    semantic_identity text NOT NULL,
    learning_angle text NOT NULL,
    target_concept_id text NOT NULL,
    target_claim_id text NOT NULL,
    public_document jsonb NOT NULL,
    private_answer_document jsonb NOT NULL,
    generation_provenance jsonb NOT NULL,
    mastery_qualified boolean NOT NULL,
    request_idempotency_key_sha256 bytea NOT NULL,
    request_fingerprint bytea NOT NULL,
    created_at timestamp with time zone NOT NULL,
    CONSTRAINT assessments_assessment_revision_check CHECK ((assessment_revision ~ '^assessment:sha256:[0-9a-f]{64}$'::text)),
    CONSTRAINT assessments_question_id_check CHECK ((question_id ~ '^question:sha256:[0-9a-f]{64}$'::text)),
    CONSTRAINT assessments_request_fingerprint_check CHECK ((octet_length(request_fingerprint) = 32)),
    CONSTRAINT assessments_request_idempotency_key_sha256_check CHECK ((octet_length(request_idempotency_key_sha256) = 32)),
    CONSTRAINT assessments_semantic_identity_check CHECK ((semantic_identity ~ '^assessment-semantic:sha256:[0-9a-f]{64}$'::text)),
    CONSTRAINT assessments_target_claim_id_check CHECK ((target_claim_id ~ '^claim:sha256:[0-9a-f]{64}$'::text)),
    CONSTRAINT assessments_target_concept_id_check CHECK ((target_concept_id ~ '^concept:sha256:[0-9a-f]{64}$'::text))
);

CREATE TABLE public.study_sessions (
    study_session_id uuid NOT NULL,
    learner_id uuid NOT NULL,
    material_id uuid NOT NULL,
    knowledge_structure_revision text NOT NULL,
    current_concept_id text,
    no_safe_claim_ids text[] DEFAULT ARRAY[]::text[] NOT NULL,
    deferred_concept_ids text[] DEFAULT ARRAY[]::text[] NOT NULL,
    last_applied_guidance_revision text,
    last_applied_progress_sha256 character(64),
    status text NOT NULL,
    idempotency_key_sha256 bytea NOT NULL,
    request_fingerprint bytea NOT NULL,
    started_at timestamp with time zone NOT NULL,
    completed_at timestamp with time zone,
    last_event_number bigint DEFAULT 0 NOT NULL,
    CONSTRAINT study_sessions_check CHECK (((status = 'completed'::text) = (completed_at IS NOT NULL))),
    CONSTRAINT study_sessions_current_concept_id_check CHECK ((current_concept_id ~ '^concept:sha256:[0-9a-f]{64}$'::text)),
    CONSTRAINT study_sessions_idempotency_key_sha256_check CHECK ((octet_length(idempotency_key_sha256) = 32)),
    CONSTRAINT study_sessions_last_event_number_check CHECK ((last_event_number >= 0)),
    CONSTRAINT study_sessions_request_fingerprint_check CHECK ((octet_length(request_fingerprint) = 32)),
    CONSTRAINT study_sessions_status_check CHECK ((status = ANY (ARRAY['active'::text, 'no_safe'::text, 'completed'::text])))
);

ALTER TABLE ONLY public.answer_events
    ADD CONSTRAINT answer_events_pkey PRIMARY KEY (answer_event_id);

ALTER TABLE ONLY public.answer_events
    ADD CONSTRAINT answer_events_study_session_id_assessment_revision_key UNIQUE (study_session_id, assessment_revision);

ALTER TABLE ONLY public.answer_events
    ADD CONSTRAINT answer_events_study_session_id_event_number_key UNIQUE (study_session_id, event_number);

ALTER TABLE ONLY public.answer_events
    ADD CONSTRAINT answer_events_study_session_id_idempotency_key_sha256_key UNIQUE (study_session_id, idempotency_key_sha256);

ALTER TABLE ONLY public.assessment_set_items
    ADD CONSTRAINT assessment_set_items_assessment_revision_key UNIQUE (assessment_revision);

ALTER TABLE ONLY public.assessment_set_items
    ADD CONSTRAINT assessment_set_items_pkey PRIMARY KEY (set_id, ordinal);

ALTER TABLE ONLY public.assessment_set_items
    ADD CONSTRAINT assessment_set_items_set_id_target_claim_id_key UNIQUE (set_id, target_claim_id);

ALTER TABLE ONLY public.assessment_sets
    ADD CONSTRAINT assessment_sets_pkey PRIMARY KEY (set_id);

ALTER TABLE ONLY public.assessment_sets
    ADD CONSTRAINT assessment_sets_set_id_study_session_id_knowledge_structure_key UNIQUE (set_id, study_session_id, knowledge_structure_revision, target_concept_id);

ALTER TABLE ONLY public.assessment_sets
    ADD CONSTRAINT assessment_sets_study_session_id_idempotency_key_sha256_key UNIQUE (study_session_id, idempotency_key_sha256);

ALTER TABLE ONLY public.assessments
    ADD CONSTRAINT assessment_target_scope UNIQUE (assessment_revision, study_session_id, knowledge_structure_revision, target_concept_id, target_claim_id);

ALTER TABLE ONLY public.assessments
    ADD CONSTRAINT assessments_pkey PRIMARY KEY (assessment_revision);

ALTER TABLE ONLY public.assessments
    ADD CONSTRAINT assessments_study_session_id_question_id_key UNIQUE (study_session_id, question_id);

ALTER TABLE ONLY public.assessments
    ADD CONSTRAINT assessments_study_session_id_request_idempotency_key_sha256_key UNIQUE (study_session_id, request_idempotency_key_sha256);

ALTER TABLE ONLY public.assessments
    ADD CONSTRAINT assessments_study_session_id_semantic_identity_key UNIQUE (study_session_id, semantic_identity);

ALTER TABLE ONLY public.study_sessions
    ADD CONSTRAINT study_session_owned_scope UNIQUE (learner_id, material_id, study_session_id, knowledge_structure_revision);

ALTER TABLE ONLY public.study_sessions
    ADD CONSTRAINT study_sessions_learner_id_idempotency_key_sha256_key UNIQUE (learner_id, idempotency_key_sha256);

ALTER TABLE ONLY public.study_sessions
    ADD CONSTRAINT study_sessions_pkey PRIMARY KEY (study_session_id);

ALTER TABLE ONLY public.study_sessions
    ADD CONSTRAINT study_sessions_study_session_id_knowledge_structure_revisio_key UNIQUE (study_session_id, knowledge_structure_revision);

CREATE INDEX assessment_remediation_origin ON public.assessment_sets USING btree (diagnostic_set_id) WHERE (diagnostic_set_id IS NOT NULL);

CREATE UNIQUE INDEX one_active_assessment_set_per_concept ON public.assessment_sets USING btree (study_session_id, target_concept_id) WHERE (status = ANY (ARRAY['preparing'::text, 'partial_ready'::text, 'ready'::text, 'in_progress'::text]));

CREATE TRIGGER assessment_set_item_immutable BEFORE UPDATE ON public.assessment_set_items FOR EACH ROW EXECUTE FUNCTION public.protect_published_set_item();

CREATE TRIGGER assessment_set_origin_immutable BEFORE INSERT OR UPDATE OF kind, diagnostic_set_id ON public.assessment_sets FOR EACH ROW EXECUTE FUNCTION public.protect_assessment_set_origin();

CREATE TRIGGER assessment_set_scope_immutable BEFORE UPDATE ON public.assessment_sets FOR EACH ROW EXECUTE FUNCTION public.protect_sealed_assessment_set();
