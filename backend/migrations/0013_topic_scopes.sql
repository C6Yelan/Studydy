-- 主題草稿與批准獨立保存；使用者選好來源前不建立空教材。
CREATE TABLE topic_scopes (
 topic_id uuid PRIMARY KEY,
 learner_id uuid NOT NULL REFERENCES learners(learner_id),
 request text NOT NULL,
 proposal jsonb,
 generation_provider text,
 version bigint NOT NULL DEFAULT 1,
 status text NOT NULL CHECK(status IN ('pending','ready','approved','failed','cancelled')),
 research_id uuid UNIQUE REFERENCES material_research(research_id) ON DELETE SET NULL,
 approved_sha256 text,
 approved_at timestamptz,
 approval_key_sha256 bytea,
 idempotency_key_sha256 bytea NOT NULL,
 lease_token uuid,
 lease_expires_at timestamptz,
 error_code text,
 created_at timestamptz NOT NULL,
 UNIQUE(learner_id,idempotency_key_sha256)
);
ALTER TABLE material_research ALTER COLUMN material_id DROP NOT NULL;
ALTER TABLE material_research ALTER COLUMN base_revision DROP NOT NULL;
ALTER TABLE material_research ADD COLUMN topic_id uuid REFERENCES topic_scopes(topic_id);
ALTER TABLE material_research ADD CONSTRAINT research_startpoint CHECK(material_id IS NOT NULL OR topic_id IS NOT NULL);
ALTER TABLE material_research ADD FOREIGN KEY(learner_id) REFERENCES learners(learner_id);
