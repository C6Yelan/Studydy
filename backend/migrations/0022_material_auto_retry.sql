-- 自動接續次數屬工作生命週期，不改寫分析輸入或結構 hash。
ALTER TABLE public.material_processing_runs
    ADD COLUMN auto_retry_count integer NOT NULL DEFAULT 0 CHECK (auto_retry_count BETWEEN 0 AND 1),
    ADD COLUMN auto_retry_reason text;
