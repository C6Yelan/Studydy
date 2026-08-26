ALTER TABLE assessments
    DROP CONSTRAINT assessments_generation_provenance_object;

ALTER TABLE assessments
    ADD CONSTRAINT assessments_generation_provenance_object
    CHECK (
        generation_provenance IS NULL
        OR (
            jsonb_typeof(generation_provenance) = 'object'
            AND generation_provenance ->> 'schema' =
                'assessment-generation-provenance/v2'
            AND generation_provenance ->> 'assessment_revision' =
                assessment_revision
            AND generation_provenance ->> 'question_id' = question_id
        )
    );
