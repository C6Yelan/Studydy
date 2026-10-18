-- 來源、固定輸入與分析結果；來源不可變保護維持原契約。

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

ALTER TABLE ONLY public.knowledge_structures
    ADD CONSTRAINT knowledge_structures_learner_id_material_id_run_id_key UNIQUE (learner_id, material_id, run_id);

ALTER TABLE ONLY public.knowledge_structures
    ADD CONSTRAINT knowledge_structures_pkey PRIMARY KEY (learner_id, material_id, structure_revision);

ALTER TABLE ONLY public.material_processing_runs
    ADD CONSTRAINT material_processing_runs_learner_id_idempotency_key_sha256_key UNIQUE (learner_id, idempotency_key_sha256);

ALTER TABLE ONLY public.material_processing_runs
    ADD CONSTRAINT material_processing_runs_learner_id_material_id_run_id_key UNIQUE (learner_id, material_id, run_id);

ALTER TABLE ONLY public.material_processing_runs
    ADD CONSTRAINT material_processing_runs_pkey PRIMARY KEY (run_id);

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

ALTER TABLE ONLY public.source_normalizations
    ADD CONSTRAINT source_normalizations_learner_id_material_id_source_id_norm_key UNIQUE (learner_id, material_id, source_id, normalization_id);

ALTER TABLE ONLY public.source_normalizations
    ADD CONSTRAINT source_normalizations_pkey PRIMARY KEY (normalization_id);

ALTER TABLE ONLY public.source_normalizations
    ADD CONSTRAINT source_normalizations_source_id_key UNIQUE (source_id);

CREATE INDEX material_runs_pending ON public.material_processing_runs USING btree (created_at, run_id) WHERE (status = 'pending'::text);

CREATE INDEX normalization_pending ON public.source_normalizations USING btree (created_at) WHERE (status = ANY (ARRAY['pending'::text, 'running'::text]));

CREATE TRIGGER normalization_ready_immutable BEFORE UPDATE ON public.source_normalizations FOR EACH ROW WHEN ((old.status = 'ready'::text)) EXECUTE FUNCTION public.protect_source_snapshot();

CREATE TRIGGER source_identity_immutable BEFORE UPDATE ON public.material_sources FOR EACH ROW EXECUTE FUNCTION public.protect_source_identity();

CREATE TRIGGER source_set_immutable BEFORE UPDATE ON public.material_source_sets FOR EACH ROW EXECUTE FUNCTION public.protect_source_snapshot();

CREATE TRIGGER source_set_item_immutable BEFORE UPDATE ON public.material_source_set_items FOR EACH ROW EXECUTE FUNCTION public.protect_source_snapshot();
