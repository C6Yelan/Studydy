-- Podcast 固定引用教材版本；manifest 持久保存每集腳本與已確認音訊。
CREATE TABLE podcasts (
    podcast_id uuid PRIMARY KEY,
    learner_id uuid NOT NULL,
    material_id uuid NOT NULL,
    knowledge_structure_revision text,
    name text NOT NULL,
    mode text NOT NULL CHECK (mode IN ('quick', 'full')),
    concept_ids text[] NOT NULL,
    episodes jsonb NOT NULL,
    status text NOT NULL CHECK (status IN ('pending', 'running', 'ready', 'failed', 'cancelled', 'deleted')),
    error_code text,
    version bigint NOT NULL DEFAULT 1,
    lease_token uuid,
    lease_expires_at timestamptz,
    idempotency_key_sha256 bytea NOT NULL,
    request_fingerprint bytea NOT NULL,
    created_at timestamptz NOT NULL,
    UNIQUE (learner_id, material_id, idempotency_key_sha256),
    FOREIGN KEY (learner_id, material_id) REFERENCES materials (learner_id, material_id),
    FOREIGN KEY (learner_id, material_id, knowledge_structure_revision)
        REFERENCES knowledge_structures (learner_id, material_id, structure_revision),
    CHECK ((status = 'running') = (lease_token IS NOT NULL AND lease_expires_at IS NOT NULL)),
    CHECK ((status = 'deleted') = (knowledge_structure_revision IS NULL))
);
CREATE INDEX podcasts_revision ON podcasts (learner_id, material_id, knowledge_structure_revision);
CREATE INDEX podcasts_work ON podcasts (created_at) WHERE status IN ('pending', 'running');

ALTER TABLE artifacts DROP CONSTRAINT artifact_role_media;
ALTER TABLE artifacts ADD CONSTRAINT artifact_role_media CHECK (
    (kind = 'podcast_audio' AND media_type = 'audio/wav')
    OR (kind = 'normalized_pdf' AND media_type = 'application/pdf')
    OR (kind = 'source_mapping' AND media_type = 'application/json')
    OR (kind = 'original' AND media_type IN (
        'application/pdf', 'application/msword', 'application/vnd.ms-powerpoint',
        'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        'application/vnd.openxmlformats-officedocument.presentationml.presentation',
        'text/plain', 'text/markdown'
    ))
);
