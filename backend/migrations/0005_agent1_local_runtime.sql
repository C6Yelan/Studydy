ALTER TABLE material_processing_runs
    DROP CONSTRAINT material_processing_runs_error_code_check,
    DROP CONSTRAINT material_processing_runs_check,
    DROP COLUMN subject,
    DROP COLUMN page_limit,
    DROP COLUMN catalog_revision;

ALTER TABLE material_processing_runs
    ADD CONSTRAINT material_processing_runs_error_code_v2_check CHECK (
        error_code IS NULL OR error_code ~ '^[A-Z][A-Z0-9_]{0,99}$'
    ),
    ADD CONSTRAINT material_processing_runs_terminal_v2_check CHECK (
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
