CREATE TABLE schema_migrations (
    version integer PRIMARY KEY,
    sql_sha256 character(64) NOT NULL CHECK (sql_sha256 ~ '^[0-9a-f]{64}$'),
    applied_at timestamptz NOT NULL
);

CREATE TABLE learners (
    learner_id uuid PRIMARY KEY,
    created_at timestamptz NOT NULL
);

CREATE TABLE learner_sessions (
    session_id uuid PRIMARY KEY,
    learner_id uuid NOT NULL REFERENCES learners,
    token_sha256 bytea NOT NULL UNIQUE CHECK (octet_length(token_sha256) = 32),
    created_at timestamptz NOT NULL,
    idle_expires_at timestamptz NOT NULL,
    absolute_expires_at timestamptz NOT NULL,
    revoked_at timestamptz,
    updated_at timestamptz NOT NULL,
    CHECK (created_at <= idle_expires_at AND idle_expires_at <= absolute_expires_at)
);

CREATE TABLE materials (
    material_id uuid PRIMARY KEY,
    learner_id uuid NOT NULL REFERENCES learners,
    source_artifact_id uuid NOT NULL UNIQUE,
    upload_idempotency_key_sha256 bytea NOT NULL CHECK (octet_length(upload_idempotency_key_sha256) = 32),
    upload_request_fingerprint bytea NOT NULL CHECK (octet_length(upload_request_fingerprint) = 32),
    created_at timestamptz NOT NULL,
    UNIQUE (learner_id, material_id),
    UNIQUE (learner_id, upload_idempotency_key_sha256)
);

CREATE TABLE artifacts (
    artifact_id uuid PRIMARY KEY,
    learner_id uuid NOT NULL,
    material_id uuid NOT NULL,
    kind text NOT NULL CHECK (kind IN ('source_pdf', 'resource_pdf')),
    media_type text NOT NULL CHECK (media_type = 'application/pdf'),
    sha256 bytea NOT NULL CHECK (octet_length(sha256) = 32),
    size_bytes bigint NOT NULL CHECK (size_bytes BETWEEN 1 AND 104857600),
    created_at timestamptz NOT NULL,
    UNIQUE (learner_id, material_id, artifact_id),
    FOREIGN KEY (learner_id, material_id) REFERENCES materials (learner_id, material_id)
);

CREATE UNIQUE INDEX artifacts_one_source_pdf
    ON artifacts (learner_id, material_id) WHERE kind = 'source_pdf';

ALTER TABLE materials ADD CONSTRAINT materials_source_artifact_fk
    FOREIGN KEY (learner_id, material_id, source_artifact_id)
    REFERENCES artifacts (learner_id, material_id, artifact_id)
    DEFERRABLE INITIALLY DEFERRED;

CREATE TABLE material_processing_runs (
    run_id uuid PRIMARY KEY,
    learner_id uuid NOT NULL,
    material_id uuid NOT NULL,
    source_artifact_id uuid NOT NULL,
    idempotency_key_sha256 bytea NOT NULL CHECK (octet_length(idempotency_key_sha256) = 32),
    request_fingerprint bytea NOT NULL CHECK (octet_length(request_fingerprint) = 32),
    runtime_binding jsonb NOT NULL,
    status text NOT NULL CHECK (status IN ('pending', 'running', 'succeeded', 'partial', 'failed')),
    progress_stage text NOT NULL CHECK (progress_stage IN ('queued', 'evidence', 'semantics', 'publishing', 'completed')),
    completed_pages integer NOT NULL DEFAULT 0 CHECK (completed_pages >= 0),
    total_pages integer CHECK (total_pages >= 1),
    error_code text CHECK (error_code ~ '^[A-Z][A-Z0-9_]{0,99}$'),
    output_binding jsonb,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    completed_at timestamptz,
    UNIQUE (learner_id, material_id, run_id),
    UNIQUE (learner_id, idempotency_key_sha256),
    FOREIGN KEY (learner_id, material_id, source_artifact_id)
        REFERENCES artifacts (learner_id, material_id, artifact_id),
    CHECK (
        (status IN ('pending', 'running') AND error_code IS NULL AND output_binding IS NULL AND completed_at IS NULL)
        OR (status IN ('succeeded', 'partial') AND error_code IS NULL AND output_binding IS NOT NULL AND completed_at IS NOT NULL)
        OR (status = 'failed' AND error_code IS NOT NULL AND output_binding IS NULL AND completed_at IS NOT NULL)
    )
);

CREATE INDEX material_runs_pending ON material_processing_runs (created_at, run_id)
    WHERE status = 'pending';

CREATE TABLE knowledge_structures (
    learner_id uuid NOT NULL,
    material_id uuid NOT NULL,
    structure_revision text NOT NULL CHECK (structure_revision ~ '^knowledge-structure:sha256:[0-9a-f]{64}$'),
    run_id uuid NOT NULL,
    document jsonb NOT NULL,
    created_at timestamptz NOT NULL,
    PRIMARY KEY (learner_id, material_id, structure_revision),
    UNIQUE (learner_id, material_id, run_id),
    FOREIGN KEY (learner_id, material_id, run_id)
        REFERENCES material_processing_runs (learner_id, material_id, run_id),
    CHECK (document ->> 'schema' = 'knowledge-structure/v1'),
    CHECK (document ->> 'revision' = structure_revision)
);

CREATE TABLE study_sessions (
    study_session_id uuid PRIMARY KEY,
    learner_id uuid NOT NULL,
    material_id uuid NOT NULL,
    knowledge_structure_revision text NOT NULL,
    current_concept_id text CHECK (current_concept_id ~ '^concept:sha256:[0-9a-f]{64}$'),
    no_safe_claim_ids text[] NOT NULL DEFAULT ARRAY[]::text[],
    deferred_concept_ids text[] NOT NULL DEFAULT ARRAY[]::text[],
    last_applied_guidance_revision text,
    last_applied_progress_sha256 character(64),
    status text NOT NULL CHECK (status IN ('active', 'no_safe', 'completed')),
    idempotency_key_sha256 bytea NOT NULL CHECK (octet_length(idempotency_key_sha256) = 32),
    request_fingerprint bytea NOT NULL CHECK (octet_length(request_fingerprint) = 32),
    started_at timestamptz NOT NULL,
    completed_at timestamptz,
    last_event_number bigint NOT NULL DEFAULT 0 CHECK (last_event_number >= 0),
    UNIQUE (learner_id, idempotency_key_sha256),
    UNIQUE (study_session_id, knowledge_structure_revision),
    FOREIGN KEY (learner_id, material_id, knowledge_structure_revision)
        REFERENCES knowledge_structures (learner_id, material_id, structure_revision),
    CHECK ((status = 'completed') = (completed_at IS NOT NULL))
);

CREATE TABLE assessments (
    assessment_revision text PRIMARY KEY CHECK (assessment_revision ~ '^assessment:sha256:[0-9a-f]{64}$'),
    study_session_id uuid NOT NULL,
    knowledge_structure_revision text NOT NULL,
    question_id text NOT NULL CHECK (question_id ~ '^question:sha256:[0-9a-f]{64}$'),
    semantic_identity text NOT NULL CHECK (semantic_identity ~ '^assessment-semantic:sha256:[0-9a-f]{64}$'),
    learning_angle text NOT NULL,
    target_concept_id text NOT NULL CHECK (target_concept_id ~ '^concept:sha256:[0-9a-f]{64}$'),
    target_claim_id text NOT NULL CHECK (target_claim_id ~ '^claim:sha256:[0-9a-f]{64}$'),
    public_document jsonb NOT NULL,
    private_answer_document jsonb NOT NULL,
    generation_provenance jsonb NOT NULL,
    mastery_qualified boolean NOT NULL,
    request_idempotency_key_sha256 bytea NOT NULL CHECK (octet_length(request_idempotency_key_sha256) = 32),
    request_fingerprint bytea NOT NULL CHECK (octet_length(request_fingerprint) = 32),
    created_at timestamptz NOT NULL,
    UNIQUE (study_session_id, question_id),
    UNIQUE (study_session_id, semantic_identity),
    UNIQUE (study_session_id, request_idempotency_key_sha256),
    FOREIGN KEY (study_session_id, knowledge_structure_revision)
        REFERENCES study_sessions (study_session_id, knowledge_structure_revision)
);

CREATE TABLE answer_events (
    answer_event_id uuid PRIMARY KEY,
    study_session_id uuid NOT NULL,
    material_id uuid NOT NULL,
    knowledge_structure_revision text NOT NULL,
    assessment_revision text NOT NULL REFERENCES assessments,
    question_id text NOT NULL,
    semantic_identity text NOT NULL,
    target_concept_id text NOT NULL,
    target_claim_id text NOT NULL,
    selected_option_id text NOT NULL CHECK (selected_option_id ~ '^option:sha256:[0-9a-f]{64}$'),
    is_correct boolean NOT NULL,
    mastery_qualified boolean NOT NULL,
    event_number bigint NOT NULL CHECK (event_number >= 1),
    idempotency_key_sha256 bytea NOT NULL CHECK (octet_length(idempotency_key_sha256) = 32),
    request_fingerprint bytea NOT NULL CHECK (octet_length(request_fingerprint) = 32),
    created_at timestamptz NOT NULL,
    UNIQUE (study_session_id, assessment_revision),
    UNIQUE (study_session_id, event_number),
    UNIQUE (study_session_id, idempotency_key_sha256),
    FOREIGN KEY (study_session_id, knowledge_structure_revision)
        REFERENCES study_sessions (study_session_id, knowledge_structure_revision)
);
