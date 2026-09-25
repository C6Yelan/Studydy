-- 學習 session、題目與作答事件。
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
    assessment_revision text PRIMARY KEY CHECK (
        assessment_revision ~ '^assessment:sha256:[0-9a-f]{64}$'
    ),
    study_session_id uuid NOT NULL,
    knowledge_structure_revision text NOT NULL,
    question_id text NOT NULL CHECK (question_id ~ '^question:sha256:[0-9a-f]{64}$'),
    semantic_identity text NOT NULL CHECK (
        semantic_identity ~ '^assessment-semantic:sha256:[0-9a-f]{64}$'
    ),
    learning_angle text NOT NULL,
    target_concept_id text NOT NULL CHECK (target_concept_id ~ '^concept:sha256:[0-9a-f]{64}$'),
    target_claim_id text NOT NULL CHECK (target_claim_id ~ '^claim:sha256:[0-9a-f]{64}$'),
    public_document jsonb NOT NULL,
    private_answer_document jsonb NOT NULL,
    generation_provenance jsonb NOT NULL,
    mastery_qualified boolean NOT NULL,
    request_idempotency_key_sha256 bytea NOT NULL
        CHECK (octet_length(request_idempotency_key_sha256) = 32),
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

ALTER TABLE study_sessions ADD CONSTRAINT study_session_owned_scope
    UNIQUE (learner_id, material_id, study_session_id, knowledge_structure_revision);
ALTER TABLE assessments ADD CONSTRAINT assessment_target_scope
    UNIQUE (
        assessment_revision, study_session_id, knowledge_structure_revision,
        target_concept_id, target_claim_id
    );
