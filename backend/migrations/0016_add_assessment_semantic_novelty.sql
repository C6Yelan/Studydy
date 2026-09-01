ALTER TABLE assessments
    ADD COLUMN semantic_identity text NOT NULL
        CHECK (semantic_identity ~ '^assessment-semantic:sha256:[0-9a-f]{64}$'),
    ADD COLUMN semantic_novelty jsonb NOT NULL,
    ADD CONSTRAINT assessments_session_semantic_identity_unique
        UNIQUE (study_session_id, semantic_identity),
    ADD CONSTRAINT assessments_semantic_novelty_binding
        CHECK (
            jsonb_typeof(semantic_novelty) = 'object'
            AND semantic_novelty ->> 'schema' =
                'assessment-semantic-novelty/v1'
            AND semantic_novelty ->> 'assessment_revision' =
                assessment_revision
            AND semantic_novelty ->> 'question_id' = question_id
            AND semantic_novelty ->> 'semantic_identity' =
                semantic_identity
        );

ALTER TABLE answer_events
    ADD COLUMN semantic_identity text NOT NULL
        CHECK (semantic_identity ~ '^assessment-semantic:sha256:[0-9a-f]{64}$');

ALTER TABLE assessments
    ADD CONSTRAINT assessments_session_revision_question_semantic_unique
        UNIQUE (
            study_session_id, assessment_revision, question_id,
            semantic_identity
        );

DO $$
DECLARE
    previous_assessment_binding text;
BEGIN
    SELECT conname INTO previous_assessment_binding
    FROM pg_constraint
    WHERE conrelid = 'answer_events'::regclass
      AND confrelid = 'assessments'::regclass
      AND contype = 'f';
    IF previous_assessment_binding IS NULL THEN
        RAISE EXCEPTION 'assessment binding missing';
    END IF;
    EXECUTE format(
        'ALTER TABLE answer_events DROP CONSTRAINT %I',
        previous_assessment_binding
    );
END
$$;

ALTER TABLE answer_events
    ADD CONSTRAINT answer_events_assessment_semantic_binding
        FOREIGN KEY (
            study_session_id, assessment_revision, question_id,
            semantic_identity
        ) REFERENCES assessments (
            study_session_id, assessment_revision, question_id,
            semantic_identity
        );
