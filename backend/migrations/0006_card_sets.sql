-- 卡組保存固定版本的概念選取，不另存知識或學習掌握度。
CREATE TABLE card_sets (
    card_set_id uuid PRIMARY KEY,
    learner_id uuid NOT NULL,
    material_id uuid NOT NULL,
    knowledge_structure_revision text,
    name text NOT NULL,
    concept_ids text[] NOT NULL,
    idempotency_key_sha256 bytea NOT NULL CHECK (octet_length(idempotency_key_sha256) = 32),
    request_fingerprint bytea NOT NULL CHECK (octet_length(request_fingerprint) = 32),
    created_at timestamptz NOT NULL,
    deleted_at timestamptz,
    UNIQUE (learner_id, material_id, idempotency_key_sha256),
    FOREIGN KEY (learner_id, material_id) REFERENCES materials (learner_id, material_id),
    FOREIGN KEY (learner_id, material_id, knowledge_structure_revision)
        REFERENCES knowledge_structures (learner_id, material_id, structure_revision),
    CHECK (
        (deleted_at IS NULL AND knowledge_structure_revision IS NOT NULL
         AND char_length(btrim(name)) BETWEEN 1 AND 200 AND cardinality(concept_ids) > 0)
        OR (deleted_at IS NOT NULL AND knowledge_structure_revision IS NULL
            AND name = '' AND cardinality(concept_ids) = 0)
    )
);

CREATE INDEX card_sets_revision ON card_sets (learner_id, material_id, knowledge_structure_revision)
    WHERE deleted_at IS NULL;
