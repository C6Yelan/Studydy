ALTER TABLE study_sessions
    ADD CONSTRAINT study_sessions_map_binding_unique
    UNIQUE (study_session_id, knowledge_map_revision);

CREATE TABLE assessments (
    assessment_revision text PRIMARY KEY
        CHECK (assessment_revision ~ '^assessment:sha256:[0-9a-f]{64}$'),
    study_session_id uuid NOT NULL,
    knowledge_map_revision text NOT NULL,
    question_id text NOT NULL
        CHECK (question_id ~ '^question:sha256:[0-9a-f]{64}$'),
    target_formal_concept_id text NOT NULL
        CHECK (target_formal_concept_id ~ '^formal-concept:sha256:[0-9a-f]{64}$'),
    target_claim_id text NOT NULL
        CHECK (target_claim_id ~ '^claim:sha256:[0-9a-f]{64}$'),
    public_document jsonb NOT NULL,
    private_answer_document jsonb NOT NULL,
    policy_revision text NOT NULL
        CHECK (policy_revision = 'single-choice-assessment-policy/v1'),
    created_at timestamptz NOT NULL,
    UNIQUE (study_session_id, question_id),
    FOREIGN KEY (study_session_id, knowledge_map_revision)
        REFERENCES study_sessions (study_session_id, knowledge_map_revision),
    CHECK (
        jsonb_typeof(public_document) = 'object'
        AND public_document ?& ARRAY[
            'schema', 'study_session_id', 'knowledge_map_revision',
            'assessment_revision', 'question_id', 'target_formal_concept_id',
            'target_claim_id', 'source_evidence_ids', 'question_type', 'prompt',
            'options', 'policy_revision'
        ]
        AND public_document ->> 'schema' = 'single-choice-assessment-public/v1'
        AND public_document ->> 'study_session_id' = study_session_id::text
        AND public_document ->> 'knowledge_map_revision' = knowledge_map_revision
        AND public_document ->> 'assessment_revision' = assessment_revision
        AND public_document ->> 'question_id' = question_id
        AND public_document ->> 'target_formal_concept_id' = target_formal_concept_id
        AND public_document ->> 'target_claim_id' = target_claim_id
        AND public_document ->> 'question_type' = 'single_choice'
        AND public_document ->> 'policy_revision' = policy_revision
    ),
    CHECK (
        jsonb_typeof(private_answer_document) = 'object'
        AND private_answer_document ?& ARRAY[
            'schema', 'assessment_revision', 'question_id',
            'correct_option_id', 'rationale', 'source_evidence_ids',
            'private_answer_sha256'
        ]
        AND private_answer_document ->> 'schema' = 'single-choice-assessment-answer/v1'
        AND private_answer_document ->> 'assessment_revision' = assessment_revision
        AND private_answer_document ->> 'question_id' = question_id
    )
);
