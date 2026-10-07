-- 頁數只描述來源整理；語意區塊與檢核批次使用各階段的真實分母。
ALTER TABLE material_processing_runs
    DROP CONSTRAINT material_processing_runs_progress_stage_check,
    ADD CONSTRAINT material_processing_runs_progress_stage_check CHECK (
        progress_stage IN ('queued', 'evidence', 'semantics', 'review', 'publishing', 'completed')
    ),
    ADD COLUMN completed_units integer,
    ADD COLUMN total_units integer,
    ADD CONSTRAINT material_stage_units CHECK (
        (completed_units IS NULL AND total_units IS NULL) OR
        (completed_units IS NOT NULL AND total_units IS NOT NULL
         AND completed_units >= 0 AND total_units >= completed_units)
    );
