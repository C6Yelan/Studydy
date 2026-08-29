ALTER TABLE material_processing_runs
    ADD COLUMN progress_stage text,
    ADD COLUMN completed_pages integer,
    ADD COLUMN total_pages integer;

UPDATE material_processing_runs
SET progress_stage = CASE
        WHEN status IN ('succeeded', 'partial') THEN 'completed'
        ELSE 'queued'
    END,
    completed_pages = CASE
        WHEN status IN ('succeeded', 'partial')
            THEN CASE
                WHEN output_binding ? 'page_count'
                    AND jsonb_typeof(output_binding -> 'page_count') = 'number'
                    AND (output_binding ->> 'page_count') ~ '^[1-9][0-9]*$'
                    THEN CASE
                        WHEN (output_binding ->> 'page_count')::numeric <= 2147483647
                            THEN (output_binding ->> 'page_count')::integer
                        ELSE NULL
                    END
                ELSE NULL
            END
        ELSE 0
    END,
    total_pages = CASE
        WHEN status IN ('succeeded', 'partial')
            THEN CASE
                WHEN output_binding ? 'page_count'
                    AND jsonb_typeof(output_binding -> 'page_count') = 'number'
                    AND (output_binding ->> 'page_count') ~ '^[1-9][0-9]*$'
                    THEN CASE
                        WHEN (output_binding ->> 'page_count')::numeric <= 2147483647
                            THEN (output_binding ->> 'page_count')::integer
                        ELSE NULL
                    END
                ELSE NULL
            END
        ELSE NULL
    END;

ALTER TABLE material_processing_runs
    ALTER COLUMN progress_stage SET NOT NULL,
    ALTER COLUMN completed_pages SET NOT NULL,
    ADD CONSTRAINT material_processing_runs_progress_stage_check CHECK (
        progress_stage IN (
            'queued',
            'page_evidence',
            'concept_generation',
            'knowledge_map_generation',
            'publishing',
            'completed'
        )
    ),
    ADD CONSTRAINT material_processing_runs_progress_count_check CHECK (
        completed_pages >= 0
        AND (
            (progress_stage = 'queued'
                AND completed_pages = 0
                AND total_pages IS NULL)
            OR (progress_stage <> 'queued'
                AND total_pages IS NOT NULL
                AND total_pages >= 1
                AND completed_pages <= total_pages)
        )
        AND (
            progress_stage NOT IN ('knowledge_map_generation', 'publishing')
            OR completed_pages = total_pages
        )
    ),
    ADD CONSTRAINT material_processing_runs_progress_status_check CHECK (
        (status = 'pending'
            AND progress_stage = 'queued'
            AND completed_pages = 0
            AND total_pages IS NULL)
        OR (status = 'running' AND progress_stage <> 'completed')
        OR (status = 'failed' AND progress_stage <> 'completed')
        OR (status IN ('succeeded', 'partial')
            AND progress_stage = 'completed'
            AND total_pages IS NOT NULL
            AND total_pages = completed_pages
            AND CASE
                WHEN output_binding ? 'page_count'
                    AND jsonb_typeof(output_binding -> 'page_count') = 'number'
                    AND (output_binding ->> 'page_count') ~ '^[1-9][0-9]*$'
                    THEN CASE
                        WHEN (output_binding ->> 'page_count')::numeric <= 2147483647
                            THEN total_pages = (output_binding ->> 'page_count')::integer
                        ELSE FALSE
                    END
                ELSE FALSE
            END)
    );
