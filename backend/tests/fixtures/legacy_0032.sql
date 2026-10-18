-- 來源：dev@6e317ee2d7486e175a5fd55ca3df514224a446da 的 0001–0032，在 PostgreSQL 18 實際重播所得。

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

CREATE FUNCTION public.protect_source_identity() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF (to_jsonb(NEW) - 'removed_at') IS DISTINCT FROM (to_jsonb(OLD) - 'removed_at') THEN
        RAISE EXCEPTION 'IMMUTABLE_SOURCE_BINDING';
    END IF;
    RETURN NEW;
END $$;

CREATE FUNCTION public.protect_source_snapshot() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    RAISE EXCEPTION 'IMMUTABLE_SOURCE_BINDING';
END $$;


SET default_tablespace = '';

SET default_table_access_method = heap;

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

CREATE TABLE public.card_source_concepts (
    learner_id uuid NOT NULL,
    material_id uuid NOT NULL,
    structure_revision text NOT NULL,
    concept_id text NOT NULL,
    "position" integer NOT NULL,
    payload jsonb NOT NULL,
    content_sha256 text NOT NULL
);

CREATE TABLE public.card_source_indexes (
    learner_id uuid NOT NULL,
    material_id uuid NOT NULL,
    structure_revision text NOT NULL,
    metadata jsonb NOT NULL,
    content_sha256 text NOT NULL
);

CREATE TABLE public.knowledge_evidence_refs (
    learner_id uuid NOT NULL,
    material_id uuid NOT NULL,
    structure_revision text NOT NULL,
    evidence_id text NOT NULL,
    payload jsonb NOT NULL,
    content_sha256 text NOT NULL
);

CREATE TABLE public.knowledge_read_indexes (
    learner_id uuid NOT NULL,
    material_id uuid NOT NULL,
    structure_revision text NOT NULL,
    outline jsonb NOT NULL,
    outline_sha256 text NOT NULL,
    learning jsonb NOT NULL,
    learning_sha256 text NOT NULL
);

CREATE TABLE public.knowledge_structures (
    learner_id uuid NOT NULL,
    material_id uuid NOT NULL,
    structure_revision text NOT NULL,
    run_id uuid NOT NULL,
    document jsonb NOT NULL,
    created_at timestamp with time zone NOT NULL,
    CONSTRAINT knowledge_structure_storage_encoding CHECK (((NOT (document ? '_studydy_json_storage'::text)) OR ((document ->> '_studydy_json_storage'::text) = 'nul-object/v1'::text))),
    CONSTRAINT knowledge_structure_version CHECK ((((document ->> 'schema'::text) = 'knowledge-structure/v1'::text) IS TRUE)),
    CONSTRAINT knowledge_structures_check CHECK (((document ->> 'revision'::text) = structure_revision)),
    CONSTRAINT knowledge_structures_structure_revision_check CHECK ((structure_revision ~ '^knowledge-structure:sha256:[0-9a-f]{64}$'::text))
);

COMMENT ON COLUMN public.knowledge_structures.document IS 'Canonical knowledge document; optional lossless NUL storage encoding decoded by EvidenceJSONB';

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

CREATE TABLE public.material_passages (
    learner_id uuid NOT NULL,
    material_id uuid NOT NULL,
    structure_revision text NOT NULL,
    "position" integer NOT NULL,
    evidence_id text NOT NULL,
    source_id uuid NOT NULL,
    page integer NOT NULL,
    section_id text NOT NULL,
    concept_ids text[] NOT NULL,
    payload jsonb NOT NULL,
    keywords tsvector NOT NULL,
    CONSTRAINT material_passages_position_check CHECK (("position" >= 0))
);

CREATE TABLE public.material_processing_runs (
    run_id uuid NOT NULL,
    learner_id uuid NOT NULL,
    material_id uuid NOT NULL,
    source_artifact_id uuid NOT NULL,
    idempotency_key_sha256 bytea NOT NULL,
    request_fingerprint bytea NOT NULL,
    runtime_binding jsonb NOT NULL,
    status text NOT NULL,
    progress_stage text NOT NULL,
    completed_pages integer DEFAULT 0 NOT NULL,
    total_pages integer,
    error_code text,
    output_binding jsonb,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL,
    completed_at timestamp with time zone,
    cancel_requested_at timestamp with time zone,
    input_source_set_id uuid NOT NULL,
    bundle_manifest jsonb NOT NULL,
    bundle_manifest_sha256 character(64) NOT NULL,
    base_revision text,
    worker_token uuid,
    lease_expires_at timestamp with time zone,
    runtime_lock_document jsonb NOT NULL,
    completed_units integer,
    total_units integer,
    CONSTRAINT material_processing_runs_base_revision_check CHECK (((base_revision IS NULL) OR (base_revision ~ '^knowledge-structure:sha256:[0-9a-f]{64}$'::text))),
    CONSTRAINT material_processing_runs_bundle_manifest_sha256_check CHECK ((bundle_manifest_sha256 ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT material_processing_runs_check CHECK ((((status = 'pending'::text) AND (output_binding IS NULL) AND (error_code IS NULL) AND (completed_at IS NULL) AND (cancel_requested_at IS NULL) AND (progress_stage = 'queued'::text)) OR ((status = 'running'::text) AND (output_binding IS NULL) AND (error_code IS NULL) AND (completed_at IS NULL) AND (progress_stage <> 'completed'::text)) OR ((status = ANY (ARRAY['succeeded'::text, 'partial'::text])) AND (output_binding IS NOT NULL) AND (error_code IS NULL) AND (completed_at IS NOT NULL) AND (cancel_requested_at IS NULL) AND (progress_stage = 'completed'::text)) OR ((status = 'failed'::text) AND (output_binding IS NULL) AND (error_code IS NOT NULL) AND (completed_at IS NOT NULL) AND (cancel_requested_at IS NULL) AND (progress_stage <> 'completed'::text)) OR ((status = 'cancelled'::text) AND (output_binding IS NULL) AND (error_code IS NULL) AND (completed_at IS NOT NULL) AND (cancel_requested_at IS NOT NULL) AND (progress_stage <> 'completed'::text)))),
    CONSTRAINT material_processing_runs_completed_pages_check CHECK ((completed_pages >= 0)),
    CONSTRAINT material_processing_runs_error_code_check CHECK ((error_code ~ '^[A-Z][A-Z0-9_]{0,99}$'::text)),
    CONSTRAINT material_processing_runs_idempotency_key_sha256_check CHECK ((octet_length(idempotency_key_sha256) = 32)),
    CONSTRAINT material_processing_runs_progress_stage_check CHECK ((progress_stage = ANY (ARRAY['queued'::text, 'evidence'::text, 'semantics'::text, 'review'::text, 'indexing'::text, 'publishing'::text, 'completed'::text]))),
    CONSTRAINT material_processing_runs_request_fingerprint_check CHECK ((octet_length(request_fingerprint) = 32)),
    CONSTRAINT material_processing_runs_runtime_lock_document_check CHECK ((jsonb_typeof(runtime_lock_document) = 'object'::text)),
    CONSTRAINT material_processing_runs_status_check CHECK ((status = ANY (ARRAY['pending'::text, 'running'::text, 'succeeded'::text, 'partial'::text, 'failed'::text, 'cancelled'::text]))),
    CONSTRAINT material_processing_runs_total_pages_check CHECK ((total_pages >= 1)),
    CONSTRAINT material_stage_units CHECK ((((completed_units IS NULL) AND (total_units IS NULL)) OR ((completed_units IS NOT NULL) AND (total_units IS NOT NULL) AND (completed_units >= 0) AND (total_units >= completed_units)))),
    CONSTRAINT run_worker_lease CHECK (((worker_token IS NULL) = (lease_expires_at IS NULL)))
);

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

CREATE TABLE public.material_retrieval_indexes (
    learner_id uuid NOT NULL,
    material_id uuid NOT NULL,
    structure_revision text NOT NULL,
    policy text NOT NULL,
    passage_count integer NOT NULL,
    content_sha256 text NOT NULL,
    outline jsonb NOT NULL,
    created_at timestamp with time zone NOT NULL,
    CONSTRAINT material_retrieval_indexes_content_sha256_check CHECK ((content_sha256 ~ '^[a-f0-9]{64}$'::text)),
    CONSTRAINT material_retrieval_indexes_passage_count_check CHECK ((passage_count > 0)),
    CONSTRAINT material_retrieval_indexes_policy_check CHECK ((policy = 'evidence-passages/v1'::text))
);

CREATE TABLE public.material_source_set_items (
    learner_id uuid NOT NULL,
    material_id uuid NOT NULL,
    source_set_id uuid NOT NULL,
    ordinal integer NOT NULL,
    source_id uuid NOT NULL,
    normalization_id uuid NOT NULL,
    CONSTRAINT material_source_set_items_ordinal_check CHECK ((ordinal >= 1))
);

CREATE TABLE public.material_source_sets (
    source_set_id uuid NOT NULL,
    learner_id uuid NOT NULL,
    material_id uuid NOT NULL,
    manifest jsonb NOT NULL,
    digest character(64) NOT NULL,
    created_at timestamp with time zone NOT NULL,
    CONSTRAINT material_source_sets_digest_check CHECK ((digest ~ '^[0-9a-f]{64}$'::text))
);

CREATE TABLE public.material_sources (
    source_id uuid NOT NULL,
    learner_id uuid NOT NULL,
    material_id uuid NOT NULL,
    original_artifact_id uuid NOT NULL,
    original_name text NOT NULL,
    media_type text NOT NULL,
    idempotency_key_sha256 bytea NOT NULL,
    request_fingerprint bytea NOT NULL,
    created_at timestamp with time zone NOT NULL,
    removed_at timestamp with time zone,
    analysis_scope jsonb,
    CONSTRAINT material_sources_idempotency_key_sha256_check CHECK ((octet_length(idempotency_key_sha256) = 32)),
    CONSTRAINT material_sources_request_fingerprint_check CHECK ((octet_length(request_fingerprint) = 32))
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

CREATE TABLE public.schema_migrations (
    version integer NOT NULL,
    sql_sha256 character(64) NOT NULL,
    applied_at timestamp with time zone NOT NULL,
    CONSTRAINT schema_migrations_sql_sha256_check CHECK ((sql_sha256 ~ '^[0-9a-f]{64}$'::text))
);

CREATE TABLE public.source_normalizations (
    normalization_id uuid NOT NULL,
    learner_id uuid NOT NULL,
    material_id uuid NOT NULL,
    source_id uuid NOT NULL,
    policy jsonb NOT NULL,
    status text NOT NULL,
    attempt integer DEFAULT 0 NOT NULL,
    lease_token uuid,
    lease_expires_at timestamp with time zone,
    normalized_artifact_id uuid,
    mapping_artifact_id uuid,
    page_count integer,
    error_code text,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL,
    analysis_scope jsonb,
    CONSTRAINT source_normalizations_attempt_check CHECK ((attempt >= 0)),
    CONSTRAINT source_normalizations_check CHECK (((status = 'running'::text) = ((lease_token IS NOT NULL) AND (lease_expires_at IS NOT NULL)))),
    CONSTRAINT source_normalizations_check1 CHECK (((status = 'failed'::text) = (error_code IS NOT NULL))),
    CONSTRAINT source_normalizations_page_count_check CHECK ((page_count > 0)),
    CONSTRAINT source_normalizations_ready_payload CHECK ((((status = 'ready'::text) AND (normalized_artifact_id IS NOT NULL) AND (mapping_artifact_id IS NOT NULL) AND (page_count IS NOT NULL)) OR ((status <> 'ready'::text) AND (normalized_artifact_id IS NULL) AND (mapping_artifact_id IS NULL) AND (page_count IS NULL)))),
    CONSTRAINT source_normalizations_status_check CHECK ((status = ANY (ARRAY['pending'::text, 'running'::text, 'ready'::text, 'failed'::text])))
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

ALTER TABLE ONLY public.answer_events
    ADD CONSTRAINT answer_events_pkey PRIMARY KEY (answer_event_id);

ALTER TABLE ONLY public.answer_events
    ADD CONSTRAINT answer_events_study_session_id_assessment_revision_key UNIQUE (study_session_id, assessment_revision);

ALTER TABLE ONLY public.answer_events
    ADD CONSTRAINT answer_events_study_session_id_event_number_key UNIQUE (study_session_id, event_number);

ALTER TABLE ONLY public.answer_events
    ADD CONSTRAINT answer_events_study_session_id_idempotency_key_sha256_key UNIQUE (study_session_id, idempotency_key_sha256);

ALTER TABLE ONLY public.artifacts
    ADD CONSTRAINT artifacts_learner_id_material_id_artifact_id_key UNIQUE (learner_id, material_id, artifact_id);

ALTER TABLE ONLY public.artifacts
    ADD CONSTRAINT artifacts_pkey PRIMARY KEY (artifact_id);

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

ALTER TABLE ONLY public.card_sets
    ADD CONSTRAINT card_sets_learner_id_material_id_idempotency_key_sha256_key UNIQUE (learner_id, material_id, idempotency_key_sha256);

ALTER TABLE ONLY public.card_sets
    ADD CONSTRAINT card_sets_pkey PRIMARY KEY (card_set_id);

ALTER TABLE ONLY public.card_source_concepts
    ADD CONSTRAINT card_source_concepts_pkey PRIMARY KEY (learner_id, material_id, structure_revision, concept_id);

ALTER TABLE ONLY public.card_source_indexes
    ADD CONSTRAINT card_source_indexes_pkey PRIMARY KEY (learner_id, material_id, structure_revision);

ALTER TABLE ONLY public.knowledge_evidence_refs
    ADD CONSTRAINT knowledge_evidence_refs_pkey PRIMARY KEY (learner_id, material_id, structure_revision, evidence_id);

ALTER TABLE ONLY public.knowledge_read_indexes
    ADD CONSTRAINT knowledge_read_indexes_pkey PRIMARY KEY (learner_id, material_id, structure_revision);

ALTER TABLE ONLY public.knowledge_structures
    ADD CONSTRAINT knowledge_structures_learner_id_material_id_run_id_key UNIQUE (learner_id, material_id, run_id);

ALTER TABLE ONLY public.knowledge_structures
    ADD CONSTRAINT knowledge_structures_pkey PRIMARY KEY (learner_id, material_id, structure_revision);

ALTER TABLE ONLY public.learner_sessions
    ADD CONSTRAINT learner_sessions_pkey PRIMARY KEY (session_id);

ALTER TABLE ONLY public.learner_sessions
    ADD CONSTRAINT learner_sessions_token_sha256_key UNIQUE (token_sha256);

ALTER TABLE ONLY public.learners
    ADD CONSTRAINT learners_email_key UNIQUE (email);

ALTER TABLE ONLY public.learners
    ADD CONSTRAINT learners_pkey PRIMARY KEY (learner_id);

ALTER TABLE ONLY public.material_passages
    ADD CONSTRAINT material_passages_pkey PRIMARY KEY (learner_id, material_id, structure_revision, "position");

ALTER TABLE ONLY public.material_processing_runs
    ADD CONSTRAINT material_processing_runs_learner_id_idempotency_key_sha256_key UNIQUE (learner_id, idempotency_key_sha256);

ALTER TABLE ONLY public.material_processing_runs
    ADD CONSTRAINT material_processing_runs_learner_id_material_id_run_id_key UNIQUE (learner_id, material_id, run_id);

ALTER TABLE ONLY public.material_processing_runs
    ADD CONSTRAINT material_processing_runs_pkey PRIMARY KEY (run_id);

ALTER TABLE ONLY public.material_research_documents
    ADD CONSTRAINT material_research_documents_pkey PRIMARY KEY (research_id, sha256);

ALTER TABLE ONLY public.material_research
    ADD CONSTRAINT material_research_learner_id_material_id_idempotency_key_sh_key UNIQUE (learner_id, material_id, idempotency_key_sha256);

ALTER TABLE ONLY public.material_research
    ADD CONSTRAINT material_research_pkey PRIMARY KEY (research_id);

ALTER TABLE ONLY public.material_retrieval_indexes
    ADD CONSTRAINT material_retrieval_indexes_pkey PRIMARY KEY (learner_id, material_id, structure_revision);

ALTER TABLE ONLY public.material_source_set_items
    ADD CONSTRAINT material_source_set_items_pkey PRIMARY KEY (source_set_id, ordinal);

ALTER TABLE ONLY public.material_source_set_items
    ADD CONSTRAINT material_source_set_items_source_set_id_normalization_id_key UNIQUE (source_set_id, normalization_id);

ALTER TABLE ONLY public.material_source_set_items
    ADD CONSTRAINT material_source_set_items_source_set_id_source_id_key UNIQUE (source_set_id, source_id);

ALTER TABLE ONLY public.material_source_sets
    ADD CONSTRAINT material_source_sets_learner_id_material_id_digest_key UNIQUE (learner_id, material_id, digest);

ALTER TABLE ONLY public.material_source_sets
    ADD CONSTRAINT material_source_sets_learner_id_material_id_source_set_id_key UNIQUE (learner_id, material_id, source_set_id);

ALTER TABLE ONLY public.material_source_sets
    ADD CONSTRAINT material_source_sets_pkey PRIMARY KEY (source_set_id);

ALTER TABLE ONLY public.material_sources
    ADD CONSTRAINT material_sources_learner_id_material_id_idempotency_key_sha_key UNIQUE (learner_id, material_id, idempotency_key_sha256);

ALTER TABLE ONLY public.material_sources
    ADD CONSTRAINT material_sources_learner_id_material_id_source_id_key UNIQUE (learner_id, material_id, source_id);

ALTER TABLE ONLY public.material_sources
    ADD CONSTRAINT material_sources_pkey PRIMARY KEY (source_id);

ALTER TABLE ONLY public.materials
    ADD CONSTRAINT materials_learner_id_material_id_key UNIQUE (learner_id, material_id);

ALTER TABLE ONLY public.materials
    ADD CONSTRAINT materials_learner_id_upload_idempotency_key_sha256_key UNIQUE (learner_id, upload_idempotency_key_sha256);

ALTER TABLE ONLY public.materials
    ADD CONSTRAINT materials_pkey PRIMARY KEY (material_id);

ALTER TABLE ONLY public.materials
    ADD CONSTRAINT materials_source_artifact_id_key UNIQUE (source_artifact_id);

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

ALTER TABLE ONLY public.schema_migrations
    ADD CONSTRAINT schema_migrations_pkey PRIMARY KEY (version);

ALTER TABLE ONLY public.source_normalizations
    ADD CONSTRAINT source_normalizations_learner_id_material_id_source_id_norm_key UNIQUE (learner_id, material_id, source_id, normalization_id);

ALTER TABLE ONLY public.source_normalizations
    ADD CONSTRAINT source_normalizations_pkey PRIMARY KEY (normalization_id);

ALTER TABLE ONLY public.source_normalizations
    ADD CONSTRAINT source_normalizations_source_id_key UNIQUE (source_id);

ALTER TABLE ONLY public.study_sessions
    ADD CONSTRAINT study_session_owned_scope UNIQUE (learner_id, material_id, study_session_id, knowledge_structure_revision);

ALTER TABLE ONLY public.study_sessions
    ADD CONSTRAINT study_sessions_learner_id_idempotency_key_sha256_key UNIQUE (learner_id, idempotency_key_sha256);

ALTER TABLE ONLY public.study_sessions
    ADD CONSTRAINT study_sessions_pkey PRIMARY KEY (study_session_id);

ALTER TABLE ONLY public.study_sessions
    ADD CONSTRAINT study_sessions_study_session_id_knowledge_structure_revisio_key UNIQUE (study_session_id, knowledge_structure_revision);

ALTER TABLE ONLY public.topic_scopes
    ADD CONSTRAINT topic_scopes_learner_id_idempotency_key_sha256_key UNIQUE (learner_id, idempotency_key_sha256);

ALTER TABLE ONLY public.topic_scopes
    ADD CONSTRAINT topic_scopes_pkey PRIMARY KEY (topic_id);

ALTER TABLE ONLY public.topic_scopes
    ADD CONSTRAINT topic_scopes_research_id_key UNIQUE (research_id);

ALTER TABLE ONLY public.voice_conversations
    ADD CONSTRAINT voice_conversations_learner_id_material_id_idempotency_key__key UNIQUE (learner_id, material_id, idempotency_key_sha256);

ALTER TABLE ONLY public.voice_conversations
    ADD CONSTRAINT voice_conversations_pkey PRIMARY KEY (conversation_id);

ALTER TABLE ONLY public.voice_turns
    ADD CONSTRAINT voice_turns_conversation_id_request_key_key UNIQUE (conversation_id, request_key);

ALTER TABLE ONLY public.voice_turns
    ADD CONSTRAINT voice_turns_pkey PRIMARY KEY (turn_id);

CREATE INDEX assessment_remediation_origin ON public.assessment_sets USING btree (diagnostic_set_id) WHERE (diagnostic_set_id IS NOT NULL);

CREATE INDEX card_sets_pending_generation ON public.card_sets USING btree (created_at) WHERE ((deleted_at IS NULL) AND (status = ANY (ARRAY['pending'::text, 'running'::text])));

CREATE INDEX card_sets_revision ON public.card_sets USING btree (learner_id, material_id, knowledge_structure_revision) WHERE (deleted_at IS NULL);

CREATE INDEX material_passages_concepts ON public.material_passages USING gin (concept_ids);

CREATE INDEX material_passages_evidence ON public.material_passages USING btree (learner_id, material_id, structure_revision, evidence_id);

CREATE INDEX material_passages_terms ON public.material_passages USING gin (keywords);

CREATE INDEX material_research_work ON public.material_research USING btree (created_at) WHERE (status = ANY (ARRAY['searching'::text, 'acquiring'::text, 'normalizing'::text]));

CREATE INDEX material_runs_pending ON public.material_processing_runs USING btree (created_at, run_id) WHERE (status = 'pending'::text);

CREATE INDEX normalization_pending ON public.source_normalizations USING btree (created_at) WHERE (status = ANY (ARRAY['pending'::text, 'running'::text]));

CREATE UNIQUE INDEX one_active_assessment_set_per_concept ON public.assessment_sets USING btree (study_session_id, target_concept_id) WHERE (status = ANY (ARRAY['preparing'::text, 'partial_ready'::text, 'ready'::text, 'in_progress'::text]));

CREATE INDEX podcast_videos_work ON public.podcast_videos USING btree (created_at) WHERE (status = ANY (ARRAY['pending'::text, 'running'::text]));

CREATE INDEX podcasts_revision ON public.podcasts USING btree (learner_id, material_id, knowledge_structure_revision);

CREATE INDEX podcasts_work ON public.podcasts USING btree (created_at) WHERE (status = ANY (ARRAY['pending'::text, 'running'::text]));

CREATE INDEX topic_scopes_material ON public.topic_scopes USING btree (learner_id, material_id, created_at);

CREATE INDEX voice_conversations_revision ON public.voice_conversations USING btree (material_id, knowledge_structure_revision);

CREATE INDEX voice_turns_work ON public.voice_turns USING btree (created_at) WHERE (status = ANY (ARRAY['transcribing'::text, 'pending'::text, 'answering'::text, 'speaking'::text]));

CREATE TRIGGER assessment_set_item_immutable BEFORE UPDATE ON public.assessment_set_items FOR EACH ROW EXECUTE FUNCTION public.protect_published_set_item();

CREATE TRIGGER assessment_set_origin_immutable BEFORE INSERT OR UPDATE OF kind, diagnostic_set_id ON public.assessment_sets FOR EACH ROW EXECUTE FUNCTION public.protect_assessment_set_origin();

CREATE TRIGGER assessment_set_scope_immutable BEFORE UPDATE ON public.assessment_sets FOR EACH ROW EXECUTE FUNCTION public.protect_sealed_assessment_set();

CREATE TRIGGER normalization_ready_immutable BEFORE UPDATE ON public.source_normalizations FOR EACH ROW WHEN ((old.status = 'ready'::text)) EXECUTE FUNCTION public.protect_source_snapshot();

CREATE TRIGGER source_identity_immutable BEFORE UPDATE ON public.material_sources FOR EACH ROW EXECUTE FUNCTION public.protect_source_identity();

CREATE TRIGGER source_set_immutable BEFORE UPDATE ON public.material_source_sets FOR EACH ROW EXECUTE FUNCTION public.protect_source_snapshot();

CREATE TRIGGER source_set_item_immutable BEFORE UPDATE ON public.material_source_set_items FOR EACH ROW EXECUTE FUNCTION public.protect_source_snapshot();

ALTER TABLE ONLY public.answer_events
    ADD CONSTRAINT answer_events_assessment_revision_fkey FOREIGN KEY (assessment_revision) REFERENCES public.assessments(assessment_revision);

ALTER TABLE ONLY public.answer_events
    ADD CONSTRAINT answer_events_study_session_id_knowledge_structure_revisio_fkey FOREIGN KEY (study_session_id, knowledge_structure_revision) REFERENCES public.study_sessions(study_session_id, knowledge_structure_revision);

ALTER TABLE ONLY public.artifacts
    ADD CONSTRAINT artifacts_learner_id_material_id_fkey FOREIGN KEY (learner_id, material_id) REFERENCES public.materials(learner_id, material_id);

ALTER TABLE ONLY public.assessment_set_items
    ADD CONSTRAINT assessment_set_items_assessment_revision_study_session_id__fkey FOREIGN KEY (assessment_revision, study_session_id, knowledge_structure_revision, target_concept_id, target_claim_id) REFERENCES public.assessments(assessment_revision, study_session_id, knowledge_structure_revision, target_concept_id, target_claim_id);

ALTER TABLE ONLY public.assessment_set_items
    ADD CONSTRAINT assessment_set_items_set_id_study_session_id_knowledge_str_fkey FOREIGN KEY (set_id, study_session_id, knowledge_structure_revision, target_concept_id) REFERENCES public.assessment_sets(set_id, study_session_id, knowledge_structure_revision, target_concept_id);

ALTER TABLE ONLY public.assessment_sets
    ADD CONSTRAINT assessment_sets_learner_id_material_id_study_session_id_kn_fkey FOREIGN KEY (learner_id, material_id, study_session_id, knowledge_structure_revision) REFERENCES public.study_sessions(learner_id, material_id, study_session_id, knowledge_structure_revision);

ALTER TABLE ONLY public.assessments
    ADD CONSTRAINT assessments_study_session_id_knowledge_structure_revision_fkey FOREIGN KEY (study_session_id, knowledge_structure_revision) REFERENCES public.study_sessions(study_session_id, knowledge_structure_revision);

ALTER TABLE ONLY public.card_sets
    ADD CONSTRAINT card_sets_learner_id_material_id_fkey FOREIGN KEY (learner_id, material_id) REFERENCES public.materials(learner_id, material_id);

ALTER TABLE ONLY public.card_sets
    ADD CONSTRAINT card_sets_learner_id_material_id_knowledge_structure_revis_fkey FOREIGN KEY (learner_id, material_id, knowledge_structure_revision) REFERENCES public.knowledge_structures(learner_id, material_id, structure_revision);

ALTER TABLE ONLY public.card_source_concepts
    ADD CONSTRAINT card_source_concepts_learner_id_material_id_structure_revi_fkey FOREIGN KEY (learner_id, material_id, structure_revision) REFERENCES public.card_source_indexes(learner_id, material_id, structure_revision) ON DELETE CASCADE;

ALTER TABLE ONLY public.card_source_indexes
    ADD CONSTRAINT card_source_indexes_learner_id_material_id_structure_revis_fkey FOREIGN KEY (learner_id, material_id, structure_revision) REFERENCES public.knowledge_structures(learner_id, material_id, structure_revision) ON DELETE CASCADE;

ALTER TABLE ONLY public.knowledge_evidence_refs
    ADD CONSTRAINT knowledge_evidence_refs_learner_id_material_id_structure_r_fkey FOREIGN KEY (learner_id, material_id, structure_revision) REFERENCES public.knowledge_read_indexes(learner_id, material_id, structure_revision) ON DELETE CASCADE;

ALTER TABLE ONLY public.knowledge_read_indexes
    ADD CONSTRAINT knowledge_read_indexes_learner_id_material_id_structure_re_fkey FOREIGN KEY (learner_id, material_id, structure_revision) REFERENCES public.knowledge_structures(learner_id, material_id, structure_revision) ON DELETE CASCADE;

ALTER TABLE ONLY public.knowledge_structures
    ADD CONSTRAINT knowledge_structures_learner_id_material_id_run_id_fkey FOREIGN KEY (learner_id, material_id, run_id) REFERENCES public.material_processing_runs(learner_id, material_id, run_id);

ALTER TABLE ONLY public.learner_sessions
    ADD CONSTRAINT learner_sessions_learner_id_fkey FOREIGN KEY (learner_id) REFERENCES public.learners(learner_id);

ALTER TABLE ONLY public.materials
    ADD CONSTRAINT material_head_fk FOREIGN KEY (learner_id, material_id, head_revision) REFERENCES public.knowledge_structures(learner_id, material_id, structure_revision) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE ONLY public.material_passages
    ADD CONSTRAINT material_passages_learner_id_material_id_structure_revisio_fkey FOREIGN KEY (learner_id, material_id, structure_revision) REFERENCES public.material_retrieval_indexes(learner_id, material_id, structure_revision) ON DELETE CASCADE;

ALTER TABLE ONLY public.material_processing_runs
    ADD CONSTRAINT material_processing_runs_learner_id_material_id_source_art_fkey FOREIGN KEY (learner_id, material_id, source_artifact_id) REFERENCES public.artifacts(learner_id, material_id, artifact_id);

ALTER TABLE ONLY public.material_research_documents
    ADD CONSTRAINT material_research_documents_research_id_fkey FOREIGN KEY (research_id) REFERENCES public.material_research(research_id) ON DELETE CASCADE;

ALTER TABLE ONLY public.material_research
    ADD CONSTRAINT material_research_learner_id_fkey FOREIGN KEY (learner_id) REFERENCES public.learners(learner_id);

ALTER TABLE ONLY public.material_research
    ADD CONSTRAINT material_research_learner_id_material_id_fkey FOREIGN KEY (learner_id, material_id) REFERENCES public.materials(learner_id, material_id);

ALTER TABLE ONLY public.material_research
    ADD CONSTRAINT material_research_topic_id_fkey FOREIGN KEY (topic_id) REFERENCES public.topic_scopes(topic_id);

ALTER TABLE ONLY public.material_retrieval_indexes
    ADD CONSTRAINT material_retrieval_indexes_learner_id_material_id_structur_fkey FOREIGN KEY (learner_id, material_id, structure_revision) REFERENCES public.knowledge_structures(learner_id, material_id, structure_revision) ON DELETE CASCADE;

ALTER TABLE ONLY public.material_source_set_items
    ADD CONSTRAINT material_source_set_items_learner_id_material_id_source_id_fkey FOREIGN KEY (learner_id, material_id, source_id, normalization_id) REFERENCES public.source_normalizations(learner_id, material_id, source_id, normalization_id);

ALTER TABLE ONLY public.material_source_set_items
    ADD CONSTRAINT material_source_set_items_learner_id_material_id_source_se_fkey FOREIGN KEY (learner_id, material_id, source_set_id) REFERENCES public.material_source_sets(learner_id, material_id, source_set_id);

ALTER TABLE ONLY public.material_source_sets
    ADD CONSTRAINT material_source_sets_learner_id_material_id_fkey FOREIGN KEY (learner_id, material_id) REFERENCES public.materials(learner_id, material_id);

ALTER TABLE ONLY public.material_sources
    ADD CONSTRAINT material_sources_learner_id_material_id_original_artifact__fkey FOREIGN KEY (learner_id, material_id, original_artifact_id) REFERENCES public.artifacts(learner_id, material_id, artifact_id);

ALTER TABLE ONLY public.materials
    ADD CONSTRAINT materials_learner_id_fkey FOREIGN KEY (learner_id) REFERENCES public.learners(learner_id);

ALTER TABLE ONLY public.materials
    ADD CONSTRAINT materials_source_artifact_fk FOREIGN KEY (learner_id, material_id, source_artifact_id) REFERENCES public.artifacts(learner_id, material_id, artifact_id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE ONLY public.podcast_scenes
    ADD CONSTRAINT podcast_scenes_podcast_id_fkey FOREIGN KEY (podcast_id) REFERENCES public.podcasts(podcast_id) ON DELETE CASCADE;

ALTER TABLE ONLY public.podcast_videos
    ADD CONSTRAINT podcast_videos_artifact_id_fkey FOREIGN KEY (artifact_id) REFERENCES public.artifacts(artifact_id);

ALTER TABLE ONLY public.podcast_videos
    ADD CONSTRAINT podcast_videos_podcast_id_fkey FOREIGN KEY (podcast_id) REFERENCES public.podcasts(podcast_id) ON DELETE CASCADE;

ALTER TABLE ONLY public.podcasts
    ADD CONSTRAINT podcasts_learner_id_material_id_fkey FOREIGN KEY (learner_id, material_id) REFERENCES public.materials(learner_id, material_id);

ALTER TABLE ONLY public.podcasts
    ADD CONSTRAINT podcasts_learner_id_material_id_knowledge_structure_revisi_fkey FOREIGN KEY (learner_id, material_id, knowledge_structure_revision) REFERENCES public.knowledge_structures(learner_id, material_id, structure_revision);

ALTER TABLE ONLY public.assessment_sets
    ADD CONSTRAINT remediation_origin_scope FOREIGN KEY (diagnostic_set_id, study_session_id, knowledge_structure_revision, target_concept_id) REFERENCES public.assessment_sets(set_id, study_session_id, knowledge_structure_revision, target_concept_id);

ALTER TABLE ONLY public.material_processing_runs
    ADD CONSTRAINT run_source_set_fk FOREIGN KEY (learner_id, material_id, input_source_set_id) REFERENCES public.material_source_sets(learner_id, material_id, source_set_id);

ALTER TABLE ONLY public.source_normalizations
    ADD CONSTRAINT source_normalizations_learner_id_material_id_mapping_artif_fkey FOREIGN KEY (learner_id, material_id, mapping_artifact_id) REFERENCES public.artifacts(learner_id, material_id, artifact_id);

ALTER TABLE ONLY public.source_normalizations
    ADD CONSTRAINT source_normalizations_learner_id_material_id_normalized_ar_fkey FOREIGN KEY (learner_id, material_id, normalized_artifact_id) REFERENCES public.artifacts(learner_id, material_id, artifact_id);

ALTER TABLE ONLY public.source_normalizations
    ADD CONSTRAINT source_normalizations_learner_id_material_id_source_id_fkey FOREIGN KEY (learner_id, material_id, source_id) REFERENCES public.material_sources(learner_id, material_id, source_id);

ALTER TABLE ONLY public.study_sessions
    ADD CONSTRAINT study_sessions_learner_id_material_id_knowledge_structure__fkey FOREIGN KEY (learner_id, material_id, knowledge_structure_revision) REFERENCES public.knowledge_structures(learner_id, material_id, structure_revision);

ALTER TABLE ONLY public.topic_scopes
    ADD CONSTRAINT topic_material_owner FOREIGN KEY (learner_id, material_id) REFERENCES public.materials(learner_id, material_id);

ALTER TABLE ONLY public.topic_scopes
    ADD CONSTRAINT topic_scopes_learner_id_fkey FOREIGN KEY (learner_id) REFERENCES public.learners(learner_id);

ALTER TABLE ONLY public.topic_scopes
    ADD CONSTRAINT topic_scopes_research_id_fkey FOREIGN KEY (research_id) REFERENCES public.material_research(research_id) ON DELETE SET NULL;

ALTER TABLE ONLY public.voice_conversations
    ADD CONSTRAINT voice_conversations_learner_id_material_id_fkey FOREIGN KEY (learner_id, material_id) REFERENCES public.materials(learner_id, material_id);

ALTER TABLE ONLY public.voice_conversations
    ADD CONSTRAINT voice_conversations_learner_id_material_id_knowledge_struc_fkey FOREIGN KEY (learner_id, material_id, knowledge_structure_revision) REFERENCES public.knowledge_structures(learner_id, material_id, structure_revision);

ALTER TABLE ONLY public.voice_turns
    ADD CONSTRAINT voice_turns_conversation_id_fkey FOREIGN KEY (conversation_id) REFERENCES public.voice_conversations(conversation_id) ON DELETE CASCADE;
