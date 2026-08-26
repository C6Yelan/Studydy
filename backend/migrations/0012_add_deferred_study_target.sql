ALTER TABLE study_sessions
    ADD COLUMN deferred_formal_concept_id text,
    ADD CONSTRAINT study_sessions_deferred_concept_id_format
    CHECK (
        deferred_formal_concept_id IS NULL
        OR deferred_formal_concept_id ~
            '^formal-concept:sha256:[0-9a-f]{64}$'
    ),
    ADD CONSTRAINT study_sessions_distinct_current_and_deferred
    CHECK (
        deferred_formal_concept_id IS NULL
        OR current_formal_concept_id IS NULL
        OR deferred_formal_concept_id <> current_formal_concept_id
    );
