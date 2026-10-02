-- 圖卡組只保存選材及瀏覽位置；內容仍由固定版本 KS 提供。
CREATE TABLE card_sets (
    card_set_id uuid PRIMARY KEY,
    learner_id uuid NOT NULL,
    material_id uuid NOT NULL,
    knowledge_structure_revision text NOT NULL,
    name text NOT NULL CHECK (char_length(name) BETWEEN 1 AND 200),
    ordering_policy text NOT NULL CHECK (ordering_policy = 'published_order'),
    current_position integer NOT NULL DEFAULT 0 CHECK (current_position >= 0),
    version bigint NOT NULL DEFAULT 1 CHECK (version >= 1),
    idempotency_key_sha256 bytea NOT NULL CHECK (octet_length(idempotency_key_sha256) = 32),
    request_fingerprint bytea NOT NULL CHECK (octet_length(request_fingerprint) = 32),
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    UNIQUE (learner_id, material_id, idempotency_key_sha256),
    FOREIGN KEY (learner_id, material_id) REFERENCES materials (learner_id, material_id),
    FOREIGN KEY (learner_id, material_id, knowledge_structure_revision)
        REFERENCES knowledge_structures (learner_id, material_id, structure_revision)
);

CREATE INDEX card_sets_revision ON card_sets (learner_id, material_id, knowledge_structure_revision);

CREATE TABLE card_set_items (
    card_set_id uuid NOT NULL REFERENCES card_sets ON DELETE CASCADE,
    position integer NOT NULL CHECK (position >= 0),
    concept_id text NOT NULL CHECK (concept_id ~ '^concept:sha256:[0-9a-f]{64}$'),
    PRIMARY KEY (card_set_id, position),
    UNIQUE (card_set_id, concept_id)
);
