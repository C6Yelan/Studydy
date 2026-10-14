-- 各頁共用已發布教材的輕量導覽與學習規則；完整 Evidence 仍按概念讀取。
CREATE TABLE knowledge_read_indexes (
    learner_id uuid NOT NULL,
    material_id uuid NOT NULL,
    structure_revision text NOT NULL,
    outline jsonb NOT NULL,
    outline_sha256 text NOT NULL,
    learning jsonb NOT NULL,
    learning_sha256 text NOT NULL,
    PRIMARY KEY (learner_id, material_id, structure_revision),
    FOREIGN KEY (learner_id, material_id, structure_revision)
        REFERENCES knowledge_structures (learner_id, material_id, structure_revision) ON DELETE CASCADE
);

-- 回查單一 Evidence 時只讀定位，不展開整份 canonical document。
CREATE TABLE knowledge_evidence_refs (
    learner_id uuid NOT NULL,
    material_id uuid NOT NULL,
    structure_revision text NOT NULL,
    evidence_id text NOT NULL,
    payload jsonb NOT NULL,
    content_sha256 text NOT NULL,
    PRIMARY KEY (learner_id, material_id, structure_revision, evidence_id),
    FOREIGN KEY (learner_id, material_id, structure_revision)
        REFERENCES knowledge_read_indexes (learner_id, material_id, structure_revision) ON DELETE CASCADE
);
