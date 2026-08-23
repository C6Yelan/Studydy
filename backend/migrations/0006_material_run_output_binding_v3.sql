ALTER TABLE material_processing_runs
    DROP CONSTRAINT material_processing_runs_terminal_v2_check;

ALTER TABLE material_processing_runs
    ADD CONSTRAINT material_processing_runs_terminal_v3_check CHECK (
        (status IN ('running', 'pending')
            AND error_code IS NULL
            AND output_binding IS NULL
            AND completed_at IS NULL)
        OR (status IN ('succeeded', 'partial')
            AND error_code IS NULL
            AND output_binding IS NOT NULL
            AND output_binding ->> 'schema' = 'material-run-output-binding/v3'
            AND completed_at IS NOT NULL)
        OR (status = 'failed'
            AND error_code IS NOT NULL
            AND output_binding IS NULL
            AND completed_at IS NOT NULL)
    );
