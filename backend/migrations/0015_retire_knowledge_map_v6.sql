DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM knowledge_maps AS knowledge_map
        JOIN study_sessions AS study_session
          ON study_session.learner_id = knowledge_map.learner_id
         AND study_session.material_id = knowledge_map.material_id
         AND study_session.knowledge_map_revision = knowledge_map.map_revision
        WHERE knowledge_map.document ->> 'schema' = 'knowledge-map/v6'
    ) THEN
        RAISE EXCEPTION 'KNOWLEDGE_MAP_V6_RETIREMENT_BLOCKED_BY_STUDY_SESSION';
    END IF;
END
$$;

UPDATE material_processing_runs AS material_run
SET status = 'failed',
    progress_stage = 'knowledge_map_generation',
    error_code = 'KNOWLEDGE_MAP_SCHEMA_RETIRED',
    output_binding = NULL,
    updated_at = clock_timestamp()
FROM knowledge_maps AS knowledge_map
WHERE material_run.learner_id = knowledge_map.learner_id
  AND material_run.material_id = knowledge_map.material_id
  AND material_run.status IN ('succeeded', 'partial')
  AND material_run.output_binding ->> 'knowledge_map_revision'
      = knowledge_map.map_revision
  AND knowledge_map.document ->> 'schema' = 'knowledge-map/v6';

DELETE FROM knowledge_maps
WHERE document ->> 'schema' = 'knowledge-map/v6';
