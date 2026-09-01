ALTER TABLE study_sessions
    ADD COLUMN no_safe_claim_ids text[] NOT NULL DEFAULT ARRAY[]::text[],
    ADD COLUMN no_safe_deferred_formal_concept_ids text[] NOT NULL
        DEFAULT ARRAY[]::text[],
    ADD COLUMN last_applied_adaptive_plan_revision text,
    ADD COLUMN last_applied_session_state_sha256 text;

ALTER TABLE study_sessions
    DROP CONSTRAINT study_sessions_status_check,
    DROP CONSTRAINT study_sessions_check,
    ADD CONSTRAINT study_sessions_status_check
        CHECK (status IN ('active', 'completed', 'no_safe')),
    ADD CONSTRAINT study_sessions_lifecycle_check CHECK (
        (status = 'active' AND completed_at IS NULL)
        OR (
            status = 'completed'
            AND completed_at IS NOT NULL
            AND completed_at >= started_at
        )
        OR (status = 'no_safe' AND completed_at IS NULL)
    ),
    ADD CONSTRAINT study_sessions_no_safe_claim_ids_format CHECK (
        array_position(no_safe_claim_ids, NULL) IS NULL
        AND (
            array_to_string(no_safe_claim_ids, ',') = ''
            OR array_to_string(no_safe_claim_ids, ',') ~
                '^claim:sha256:[0-9a-f]{64}(,claim:sha256:[0-9a-f]{64})*$'
        )
    ),
    ADD CONSTRAINT study_sessions_no_safe_concept_ids_format CHECK (
        array_position(no_safe_deferred_formal_concept_ids, NULL) IS NULL
        AND (
            array_to_string(no_safe_deferred_formal_concept_ids, ',') = ''
            OR array_to_string(no_safe_deferred_formal_concept_ids, ',') ~
                '^formal-concept:sha256:[0-9a-f]{64}(,formal-concept:sha256:[0-9a-f]{64})*$'
        )
    ),
    ADD CONSTRAINT study_sessions_last_plan_binding CHECK (
        (last_applied_adaptive_plan_revision IS NULL
            AND last_applied_session_state_sha256 IS NULL)
        OR (
            last_applied_adaptive_plan_revision IS NOT NULL
            AND last_applied_session_state_sha256 IS NOT NULL
            AND last_applied_adaptive_plan_revision ~
                '^adaptive-plan:sha256:[0-9a-f]{64}$'
            AND last_applied_session_state_sha256 ~ '^[0-9a-f]{64}$'
        )
    );
