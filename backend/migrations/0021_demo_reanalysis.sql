-- 指定教材一次性重分析：維護額度獨立於不可改寫的分析輸入 hash。
ALTER TABLE public.material_processing_runs ADD COLUMN maintenance jsonb;
ALTER TABLE public.material_processing_runs ADD CONSTRAINT demo_reanalysis_budget CHECK (
    maintenance IS NULL OR (
        maintenance->>'mode' = 'demo-full-reanalysis/v1'
        AND (maintenance->>'limit')::integer = 300
        AND (maintenance->>'reserved_calls')::integer BETWEEN 0 AND 300
    )
);
