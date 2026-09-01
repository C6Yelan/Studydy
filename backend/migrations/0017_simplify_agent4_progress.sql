ALTER TABLE study_sessions
    DROP CONSTRAINT study_sessions_deferred_concept_id_format,
    DROP CONSTRAINT study_sessions_distinct_current_and_deferred,
    DROP CONSTRAINT study_sessions_last_plan_binding;

UPDATE study_sessions
SET last_applied_adaptive_plan_revision = NULL,
    last_applied_session_state_sha256 = NULL;

ALTER TABLE study_sessions
    DROP COLUMN deferred_formal_concept_id;

ALTER TABLE study_sessions
    RENAME COLUMN last_applied_adaptive_plan_revision
        TO last_applied_guidance_revision;

ALTER TABLE study_sessions
    RENAME COLUMN last_applied_session_state_sha256
        TO last_applied_progress_state_sha256;

ALTER TABLE study_sessions
    ADD CONSTRAINT study_sessions_last_guidance_binding CHECK (
        (last_applied_guidance_revision IS NULL
            AND last_applied_progress_state_sha256 IS NULL)
        OR (
            last_applied_guidance_revision IS NOT NULL
            AND last_applied_progress_state_sha256 IS NOT NULL
            AND last_applied_guidance_revision ~
                '^learner-guidance:sha256:[0-9a-f]{64}$'
            AND last_applied_progress_state_sha256 ~ '^[0-9a-f]{64}$'
        )
    );
