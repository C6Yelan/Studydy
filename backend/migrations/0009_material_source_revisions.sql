-- Run 保存明確的更新意圖；base revision 是稽核身分，不以 FK 阻止無引用舊圖清理。
ALTER TABLE material_processing_runs ADD COLUMN base_revision text
 CHECK (base_revision IS NULL OR base_revision ~ '^knowledge-structure:sha256:[0-9a-f]{64}$');
ALTER TABLE material_processing_runs ADD COLUMN worker_token uuid;
ALTER TABLE material_processing_runs ADD COLUMN lease_expires_at timestamptz;
ALTER TABLE material_processing_runs ADD CONSTRAINT run_worker_lease
 CHECK ((worker_token IS NULL) = (lease_expires_at IS NULL));

ALTER TABLE knowledge_structures DROP CONSTRAINT knowledge_structure_version;
ALTER TABLE knowledge_structures ADD CONSTRAINT knowledge_structure_version
 CHECK(document->>'schema' IN ('knowledge-structure/v2','knowledge-structure/v3','knowledge-structure/v4'));

-- publishing 尚未提交前仍可保存取消意圖；publication 以相同 Material→run 鎖序決定先後。
ALTER TABLE material_processing_runs DROP CONSTRAINT material_processing_runs_check;
ALTER TABLE material_processing_runs ADD CONSTRAINT material_processing_runs_check CHECK (
 (status='pending' AND output_binding IS NULL AND error_code IS NULL AND completed_at IS NULL AND cancel_requested_at IS NULL AND progress_stage='queued') OR
 (status='running' AND output_binding IS NULL AND error_code IS NULL AND completed_at IS NULL AND progress_stage!='completed') OR
 (status IN ('succeeded','partial') AND output_binding IS NOT NULL AND error_code IS NULL AND completed_at IS NOT NULL AND cancel_requested_at IS NULL AND progress_stage='completed') OR
 (status='failed' AND output_binding IS NULL AND error_code IS NOT NULL AND completed_at IS NOT NULL AND cancel_requested_at IS NULL AND progress_stage!='completed') OR
 (status='cancelled' AND output_binding IS NULL AND error_code IS NULL AND completed_at IS NOT NULL AND cancel_requested_at IS NOT NULL AND progress_stage!='completed')
);
