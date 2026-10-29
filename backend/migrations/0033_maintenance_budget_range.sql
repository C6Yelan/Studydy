-- 維護重分析額度改為每個 run 自訂上限（1～2000）；保留不超支約束，既有 300 的 run 不變。
ALTER TABLE public.material_processing_runs DROP CONSTRAINT demo_reanalysis_budget;
ALTER TABLE public.material_processing_runs ADD CONSTRAINT demo_reanalysis_budget CHECK (
    maintenance IS NULL OR (
        maintenance->>'mode' = 'demo-full-reanalysis/v1'
        AND (maintenance->>'limit')::integer BETWEEN 1 AND 2000
        AND (maintenance->>'reserved_calls')::integer BETWEEN 0 AND (maintenance->>'limit')::integer
    )
);
