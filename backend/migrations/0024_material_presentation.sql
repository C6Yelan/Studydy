-- 顯示摘要獨立保存；不改分析輸入、runtime lock 或已發布結構的 hash。
ALTER TABLE material_processing_runs ADD COLUMN display_summary jsonb;
ALTER TABLE materials ADD COLUMN pending_goal jsonb;
ALTER TABLE topic_scopes ADD COLUMN starting_level text CHECK (starting_level IN ('some_knowledge','familiar','gaps'));
ALTER TABLE topic_scopes ADD COLUMN learning_goal jsonb;
ALTER TABLE material_notes ADD COLUMN started_at timestamptz;
