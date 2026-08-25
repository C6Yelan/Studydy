DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM learning_paths)
        OR EXISTS (SELECT 1 FROM assessments)
        OR EXISTS (SELECT 1 FROM answer_events)
        OR EXISTS (SELECT 1 FROM learning_states)
    THEN
        RAISE EXCEPTION 'DORMANT_LEARNING_TABLES_NOT_EMPTY';
    END IF;
END
$$;

DROP TABLE learning_states;
DROP TABLE answer_events;
DROP TABLE assessments;
DROP TABLE learning_paths;

CREATE TABLE study_sessions (
    study_session_id uuid PRIMARY KEY,
    learner_id uuid NOT NULL,
    material_id uuid NOT NULL,
    knowledge_map_revision text NOT NULL,
    current_formal_concept_id text NULL,
    status text NOT NULL CHECK (status IN ('active', 'completed')),
    idempotency_key_sha256 bytea NOT NULL
        CHECK (octet_length(idempotency_key_sha256) = 32),
    request_fingerprint bytea NOT NULL
        CHECK (octet_length(request_fingerprint) = 32),
    started_at timestamptz NOT NULL,
    completed_at timestamptz NULL,
    last_event_number bigint NOT NULL DEFAULT 0
        CHECK (last_event_number >= 0),
    UNIQUE (learner_id, idempotency_key_sha256),
    FOREIGN KEY (learner_id, material_id, knowledge_map_revision)
        REFERENCES knowledge_maps (learner_id, material_id, map_revision),
    CHECK (
        current_formal_concept_id IS NULL
        OR current_formal_concept_id ~ '^formal-concept:sha256:[0-9a-f]{64}$'
    ),
    CHECK (
        (status = 'active' AND completed_at IS NULL)
        OR (
            status = 'completed'
            AND completed_at IS NOT NULL
            AND completed_at >= started_at
        )
    )
);
