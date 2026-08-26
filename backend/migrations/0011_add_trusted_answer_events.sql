ALTER TABLE assessments
    ADD CONSTRAINT assessments_session_revision_question_unique
    UNIQUE (study_session_id, assessment_revision, question_id);

CREATE TABLE answer_events (
    answer_event_id uuid PRIMARY KEY,
    study_session_id uuid NOT NULL,
    material_id uuid NOT NULL,
    knowledge_map_revision text NOT NULL,
    assessment_revision text NOT NULL
        CHECK (assessment_revision ~ '^assessment:sha256:[0-9a-f]{64}$'),
    question_id text NOT NULL
        CHECK (question_id ~ '^question:sha256:[0-9a-f]{64}$'),
    target_formal_concept_id text NOT NULL
        CHECK (target_formal_concept_id ~ '^formal-concept:sha256:[0-9a-f]{64}$'),
    target_claim_id text NOT NULL
        CHECK (target_claim_id ~ '^claim:sha256:[0-9a-f]{64}$'),
    selected_option_id text NOT NULL
        CHECK (selected_option_id ~ '^option:sha256:[0-9a-f]{64}$'),
    is_correct boolean NOT NULL,
    event_number bigint NOT NULL CHECK (event_number >= 1),
    idempotency_key_sha256 bytea NOT NULL
        CHECK (octet_length(idempotency_key_sha256) = 32),
    request_fingerprint bytea NOT NULL
        CHECK (octet_length(request_fingerprint) = 32),
    created_at timestamptz NOT NULL,
    UNIQUE (study_session_id, assessment_revision),
    UNIQUE (study_session_id, event_number),
    UNIQUE (study_session_id, idempotency_key_sha256),
    FOREIGN KEY (study_session_id, knowledge_map_revision)
        REFERENCES study_sessions (study_session_id, knowledge_map_revision),
    FOREIGN KEY (study_session_id, assessment_revision, question_id)
        REFERENCES assessments (
            study_session_id, assessment_revision, question_id
        )
);
