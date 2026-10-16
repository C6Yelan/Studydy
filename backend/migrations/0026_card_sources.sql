-- 已驗證教材的卡片讀取投影：文字與來源按概念索引，不保存圖片。
CREATE TABLE card_source_indexes (
    learner_id uuid NOT NULL,
    material_id uuid NOT NULL,
    structure_revision text NOT NULL,
    metadata jsonb NOT NULL,
    content_sha256 text NOT NULL,
    PRIMARY KEY (learner_id, material_id, structure_revision),
    FOREIGN KEY (learner_id, material_id, structure_revision)
        REFERENCES knowledge_structures (learner_id, material_id, structure_revision) ON DELETE CASCADE
);
CREATE TABLE card_source_concepts (
    learner_id uuid NOT NULL,
    material_id uuid NOT NULL,
    structure_revision text NOT NULL,
    concept_id text NOT NULL,
    position integer NOT NULL,
    payload jsonb NOT NULL,
    content_sha256 text NOT NULL,
    PRIMARY KEY (learner_id, material_id, structure_revision, concept_id),
    FOREIGN KEY (learner_id, material_id, structure_revision)
        REFERENCES card_source_indexes (learner_id, material_id, structure_revision) ON DELETE CASCADE
);
ALTER TABLE card_sets ADD COLUMN page_counts jsonb NOT NULL DEFAULT '{}';
