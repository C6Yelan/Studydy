-- 對話只引用單一教材版本。短語音與暫存錄音跟隨 turn，刪除時一併清除。
CREATE TABLE voice_conversations (
 conversation_id uuid PRIMARY KEY,
 learner_id uuid NOT NULL,
 material_id uuid NOT NULL,
 knowledge_structure_revision text,
 title text NOT NULL,
 created_at timestamptz NOT NULL,
 deleted_at timestamptz,
 idempotency_key_sha256 bytea NOT NULL,
 UNIQUE(learner_id, material_id, idempotency_key_sha256),
 FOREIGN KEY(learner_id,material_id) REFERENCES materials(learner_id,material_id),
 FOREIGN KEY(learner_id,material_id,knowledge_structure_revision)
 REFERENCES knowledge_structures(learner_id,material_id,structure_revision)
);
CREATE TABLE voice_turns (
 turn_id uuid PRIMARY KEY,
 conversation_id uuid NOT NULL REFERENCES voice_conversations(conversation_id) ON DELETE CASCADE,
 request_key text NOT NULL,
 fingerprint text NOT NULL,
 question text NOT NULL,
 answer jsonb,
 status text NOT NULL CHECK(status IN ('transcribing','draft','pending','answering','speaking','ready','failed','cancelled')),
 recording bytea,
 audio bytea,
 error_code text,
 lease_token uuid,
 lease_expires_at timestamptz,
 created_at timestamptz NOT NULL,
 UNIQUE(conversation_id,request_key)
);
CREATE INDEX voice_conversations_revision ON voice_conversations(material_id,knowledge_structure_revision);
CREATE INDEX voice_turns_work ON voice_turns(created_at) WHERE status IN ('transcribing','pending','answering','speaking');
