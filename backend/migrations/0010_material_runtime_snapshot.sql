-- 保存工作開始時的非機密設定；出題設定變更不改寫原 runtime binding／產物 hash。
ALTER TABLE material_processing_runs ADD COLUMN runtime_lock_document jsonb
 CHECK (runtime_lock_document IS NULL OR jsonb_typeof(runtime_lock_document) = 'object');
