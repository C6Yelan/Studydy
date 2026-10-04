-- 研究候選不是知識地圖；只有既有分析／發布流程可以更新教材。
CREATE TABLE material_research (
 research_id uuid PRIMARY KEY,
 learner_id uuid NOT NULL,
 material_id uuid NOT NULL,
 base_revision text NOT NULL,
 query text NOT NULL,
 mode text NOT NULL CHECK(mode IN ('review','self-study')),
 status text NOT NULL CHECK(status IN ('searching','selecting','acquiring','normalizing','ready','submitted','failed','cancelled')),
 candidates jsonb NOT NULL DEFAULT '[]',
 selection jsonb NOT NULL DEFAULT '[]',
 search_query text,
 cursor text,
 error_code text,
 run_id uuid,
 lease_token uuid,
 lease_expires_at timestamptz,
 idempotency_key_sha256 bytea NOT NULL,
 created_at timestamptz NOT NULL,
 UNIQUE(learner_id,material_id,idempotency_key_sha256),
 FOREIGN KEY(learner_id,material_id) REFERENCES materials(learner_id,material_id)
);
CREATE INDEX material_research_work ON material_research(created_at) WHERE status IN ('searching','acquiring','normalizing');
ALTER TABLE material_research ADD COLUMN staged_content bytea;
ALTER TABLE material_research ADD COLUMN staged_metadata jsonb;
