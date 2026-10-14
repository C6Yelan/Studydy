-- 既有卡組保留原文；新卡組明確排入精簡工作，刷新頁面仍可接續。
ALTER TABLE card_sets ADD COLUMN status text NOT NULL DEFAULT 'ready';
ALTER TABLE card_sets ADD COLUMN generation jsonb NOT NULL DEFAULT '{}';
ALTER TABLE card_sets ADD COLUMN lease_token uuid;
ALTER TABLE card_sets ADD COLUMN lease_expires_at timestamptz;
ALTER TABLE card_sets ADD CONSTRAINT card_sets_generation_status
    CHECK (status IN ('pending', 'running', 'ready', 'failed'));
CREATE INDEX card_sets_pending_generation ON card_sets (created_at)
    WHERE deleted_at IS NULL AND status IN ('pending', 'running');
