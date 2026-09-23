-- 來源快照、處理工作與知識結構。
CREATE TABLE material_sources (
    source_id uuid PRIMARY KEY,
    learner_id uuid NOT NULL,
    material_id uuid NOT NULL,
    original_artifact_id uuid NOT NULL,
    original_name text NOT NULL,
    media_type text NOT NULL,
    idempotency_key_sha256 bytea NOT NULL CHECK (octet_length(idempotency_key_sha256) = 32),
    request_fingerprint bytea NOT NULL CHECK (octet_length(request_fingerprint) = 32),
    created_at timestamptz NOT NULL,
    UNIQUE (learner_id, material_id, source_id),
    UNIQUE (learner_id, material_id, idempotency_key_sha256),
    FOREIGN KEY (learner_id, material_id, original_artifact_id)
        REFERENCES artifacts (learner_id, material_id, artifact_id)
);

CREATE TABLE source_normalizations (
    normalization_id uuid PRIMARY KEY,
    learner_id uuid NOT NULL,
    material_id uuid NOT NULL,
    source_id uuid NOT NULL UNIQUE,
    policy jsonb NOT NULL,
    status text NOT NULL CHECK (status IN ('pending', 'running', 'ready', 'failed')),
    attempt integer NOT NULL DEFAULT 0 CHECK (attempt >= 0),
    lease_token uuid,
    lease_expires_at timestamptz,
    normalized_artifact_id uuid,
    mapping_artifact_id uuid,
    page_count integer CHECK (page_count > 0),
    error_code text,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    UNIQUE (learner_id, material_id, source_id, normalization_id),
    FOREIGN KEY (learner_id, material_id, source_id)
        REFERENCES material_sources (learner_id, material_id, source_id),
    FOREIGN KEY (learner_id, material_id, normalized_artifact_id)
        REFERENCES artifacts (learner_id, material_id, artifact_id),
    FOREIGN KEY (learner_id, material_id, mapping_artifact_id)
        REFERENCES artifacts (learner_id, material_id, artifact_id),
    CHECK ((status = 'running') = (lease_token IS NOT NULL AND lease_expires_at IS NOT NULL)),
    CONSTRAINT source_normalizations_ready_payload CHECK (
        (status = 'ready' AND normalized_artifact_id IS NOT NULL
            AND mapping_artifact_id IS NOT NULL AND page_count IS NOT NULL)
        OR (status <> 'ready' AND normalized_artifact_id IS NULL
            AND mapping_artifact_id IS NULL AND page_count IS NULL)
    ),
    CHECK ((status = 'failed') = (error_code IS NOT NULL))
);

CREATE INDEX normalization_pending ON source_normalizations (created_at)
    WHERE status IN ('pending', 'running');

CREATE TABLE material_source_sets (
    source_set_id uuid PRIMARY KEY,
    learner_id uuid NOT NULL,
    material_id uuid NOT NULL,
    manifest jsonb NOT NULL,
    digest character(64) NOT NULL CHECK (digest ~ '^[0-9a-f]{64}$'),
    created_at timestamptz NOT NULL,
    UNIQUE (learner_id, material_id, source_set_id),
    UNIQUE (learner_id, material_id, digest),
    FOREIGN KEY (learner_id, material_id) REFERENCES materials (learner_id, material_id)
);

CREATE TABLE material_source_set_items (
    learner_id uuid NOT NULL,
    material_id uuid NOT NULL,
    source_set_id uuid NOT NULL,
    ordinal integer NOT NULL CHECK (ordinal >= 1),
    source_id uuid NOT NULL,
    normalization_id uuid NOT NULL,
    PRIMARY KEY (source_set_id, ordinal),
    UNIQUE (source_set_id, source_id),
    UNIQUE (source_set_id, normalization_id),
    FOREIGN KEY (learner_id, material_id, source_set_id)
        REFERENCES material_source_sets (learner_id, material_id, source_set_id),
    FOREIGN KEY (learner_id, material_id, source_id, normalization_id)
        REFERENCES source_normalizations (learner_id, material_id, source_id, normalization_id)
);

CREATE TABLE material_processing_runs (
    run_id uuid PRIMARY KEY,
    learner_id uuid NOT NULL,
    material_id uuid NOT NULL,
    source_artifact_id uuid NOT NULL,
    idempotency_key_sha256 bytea NOT NULL CHECK (octet_length(idempotency_key_sha256) = 32),
    request_fingerprint bytea NOT NULL CHECK (octet_length(request_fingerprint) = 32),
    runtime_binding jsonb NOT NULL,
    status text NOT NULL CHECK (
        status IN ('pending', 'running', 'succeeded', 'partial', 'failed', 'cancelled')
    ),
    progress_stage text NOT NULL CHECK (
        progress_stage IN ('queued', 'evidence', 'semantics', 'publishing', 'completed')
    ),
    completed_pages integer NOT NULL DEFAULT 0 CHECK (completed_pages >= 0),
    total_pages integer CHECK (total_pages >= 1),
    error_code text CHECK (error_code ~ '^[A-Z][A-Z0-9_]{0,99}$'),
    output_binding jsonb,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    completed_at timestamptz,
    cancel_requested_at timestamptz,
    input_source_set_id uuid NOT NULL,
    bundle_manifest jsonb NOT NULL,
    bundle_manifest_sha256 character(64) NOT NULL
        CHECK (bundle_manifest_sha256 ~ '^[0-9a-f]{64}$'),
    base_revision text CHECK (
        base_revision IS NULL OR base_revision ~ '^knowledge-structure:sha256:[0-9a-f]{64}$'
    ),
    worker_token uuid,
    lease_expires_at timestamptz,
    runtime_lock_document jsonb NOT NULL CHECK (jsonb_typeof(runtime_lock_document) = 'object'),
    CONSTRAINT run_worker_lease CHECK ((worker_token IS NULL) = (lease_expires_at IS NULL)),
    CONSTRAINT run_source_set_fk FOREIGN KEY (learner_id, material_id, input_source_set_id)
        REFERENCES material_source_sets (learner_id, material_id, source_set_id),
    UNIQUE (learner_id, material_id, run_id),
    UNIQUE (learner_id, idempotency_key_sha256),
    FOREIGN KEY (learner_id, material_id, source_artifact_id)
        REFERENCES artifacts (learner_id, material_id, artifact_id),
    CONSTRAINT material_processing_runs_check CHECK (
        (status = 'pending' AND output_binding IS NULL AND error_code IS NULL
            AND completed_at IS NULL AND cancel_requested_at IS NULL
            AND progress_stage = 'queued')
        OR (status = 'running' AND output_binding IS NULL AND error_code IS NULL
            AND completed_at IS NULL AND progress_stage != 'completed')
        OR (status IN ('succeeded', 'partial') AND output_binding IS NOT NULL
            AND error_code IS NULL AND completed_at IS NOT NULL
            AND cancel_requested_at IS NULL AND progress_stage = 'completed')
        OR (status = 'failed' AND output_binding IS NULL AND error_code IS NOT NULL
            AND completed_at IS NOT NULL AND cancel_requested_at IS NULL
            AND progress_stage != 'completed')
        OR (status = 'cancelled' AND output_binding IS NULL AND error_code IS NULL
            AND completed_at IS NOT NULL AND cancel_requested_at IS NOT NULL
            AND progress_stage != 'completed')
    )
);

CREATE INDEX material_runs_pending ON material_processing_runs (created_at, run_id)
    WHERE status = 'pending';

CREATE TABLE knowledge_structures (
    learner_id uuid NOT NULL,
    material_id uuid NOT NULL,
    structure_revision text NOT NULL CHECK (
        structure_revision ~ '^knowledge-structure:sha256:[0-9a-f]{64}$'
    ),
    run_id uuid NOT NULL,
    document jsonb NOT NULL,
    created_at timestamptz NOT NULL,
    PRIMARY KEY (learner_id, material_id, structure_revision),
    UNIQUE (learner_id, material_id, run_id),
    FOREIGN KEY (learner_id, material_id, run_id)
        REFERENCES material_processing_runs (learner_id, material_id, run_id),
    CONSTRAINT knowledge_structure_version CHECK (
        (document->>'schema' = 'knowledge-structure/v1') IS TRUE
    ),
    CHECK (document ->> 'revision' = structure_revision)
);

-- 教材 head 與知識結構互相參照，待兩表建立後補上外鍵。
ALTER TABLE materials ADD CONSTRAINT material_head_fk
    FOREIGN KEY (learner_id, material_id, head_revision)
    REFERENCES knowledge_structures (learner_id, material_id, structure_revision)
    DEFERRABLE INITIALLY DEFERRED;

CREATE FUNCTION protect_source_snapshot() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'IMMUTABLE_SOURCE_BINDING';
END $$;

CREATE TRIGGER source_identity_immutable BEFORE UPDATE ON material_sources
    FOR EACH ROW EXECUTE FUNCTION protect_source_snapshot();
CREATE TRIGGER source_set_immutable BEFORE UPDATE ON material_source_sets
    FOR EACH ROW EXECUTE FUNCTION protect_source_snapshot();
CREATE TRIGGER source_set_item_immutable BEFORE UPDATE ON material_source_set_items
    FOR EACH ROW EXECUTE FUNCTION protect_source_snapshot();
CREATE TRIGGER normalization_ready_immutable BEFORE UPDATE ON source_normalizations
    FOR EACH ROW WHEN (OLD.status = 'ready') EXECUTE FUNCTION protect_source_snapshot();
