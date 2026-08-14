CREATE TABLE material_processing_runs (
    run_id uuid PRIMARY KEY,
    learner_id uuid NOT NULL,
    material_id uuid NOT NULL,
    source_artifact_id uuid NOT NULL,
    subject text NOT NULL CHECK (char_length(subject) BETWEEN 1 AND 128),
    page_limit integer NOT NULL CHECK (page_limit BETWEEN 1 AND 1000),
    idempotency_key_sha256 bytea NOT NULL CHECK (octet_length(idempotency_key_sha256) = 32),
    request_fingerprint bytea NOT NULL CHECK (octet_length(request_fingerprint) = 32),
    runtime_binding jsonb NOT NULL,
    catalog_revision text,
    status text NOT NULL CHECK (status IN ('running', 'pending', 'succeeded', 'partial', 'failed')),
    error_code text CHECK (error_code IN (
        'RESTART_INTERRUPTED',
        'MATERIAL_CONFIGURATION_INVALID',
        'MATERIAL_S1_FAILED',
        'LOCAL_PROVIDER_TIMEOUT',
        'LOCAL_PROVIDER_RATE_LIMITED',
        'LOCAL_PROVIDER_TRANSIENT_ERROR',
        'CONTROLLED_RESOURCE_INVALID',
        'MATERIAL_OUTPUT_FAILED'
    )),
    output_binding jsonb,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    completed_at timestamptz,
    UNIQUE (learner_id, idempotency_key_sha256),
    UNIQUE (learner_id, material_id, run_id),
    FOREIGN KEY (learner_id, material_id, source_artifact_id)
        REFERENCES artifacts (learner_id, material_id, artifact_id),
    FOREIGN KEY (learner_id, material_id, catalog_revision)
        REFERENCES resource_catalogs (learner_id, material_id, catalog_revision),
    CHECK (
        (status IN ('running', 'pending') AND error_code IS NULL AND output_binding IS NULL AND completed_at IS NULL)
        OR (status IN ('succeeded', 'partial') AND error_code IS NULL AND output_binding IS NOT NULL AND completed_at IS NOT NULL)
        OR (status = 'failed' AND error_code IS NOT NULL AND output_binding IS NULL AND completed_at IS NOT NULL)
    )
);

CREATE INDEX idx_material_runs_pending
    ON material_processing_runs (created_at, run_id)
    WHERE status = 'pending';

CREATE TABLE answer_events (
    answer_event_id text PRIMARY KEY,
    submission_id uuid NOT NULL,
    learner_id uuid NOT NULL,
    material_id uuid NOT NULL,
    assessment_revision text NOT NULL,
    question_id text NOT NULL,
    document jsonb NOT NULL,
    created_at timestamptz NOT NULL,
    UNIQUE (learner_id, submission_id, question_id),
    FOREIGN KEY (learner_id, material_id, assessment_revision)
        REFERENCES assessments (learner_id, material_id, assessment_revision),
    CHECK (document ? 'answer_event_id' AND document ->> 'answer_event_id' = answer_event_id)
);

CREATE TABLE learning_states (
    state_revision text PRIMARY KEY,
    submission_id uuid NOT NULL,
    learner_id uuid NOT NULL,
    material_id uuid NOT NULL,
    map_revision text NOT NULL,
    path_revision text NOT NULL,
    assessment_revision text NOT NULL,
    idempotency_key_sha256 bytea NOT NULL CHECK (octet_length(idempotency_key_sha256) = 32),
    request_fingerprint bytea NOT NULL CHECK (octet_length(request_fingerprint) = 32),
    document jsonb NOT NULL,
    created_at timestamptz NOT NULL,
    UNIQUE (learner_id, idempotency_key_sha256),
    UNIQUE (learner_id, submission_id),
    FOREIGN KEY (learner_id, material_id, map_revision)
        REFERENCES knowledge_maps (learner_id, material_id, map_revision),
    FOREIGN KEY (learner_id, material_id, path_revision)
        REFERENCES learning_paths (learner_id, material_id, path_revision),
    FOREIGN KEY (learner_id, material_id, assessment_revision)
        REFERENCES assessments (learner_id, material_id, assessment_revision),
    CHECK (document ? 'revision' AND document ->> 'revision' = state_revision)
);

CREATE INDEX idx_answer_events_stream
    ON answer_events (learner_id, material_id, created_at, answer_event_id);
