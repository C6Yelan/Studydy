-- 原始來源保留完整；研究選讀範圍與教材檢索為新的正式處理契約。
ALTER TABLE material_sources ADD COLUMN analysis_scope jsonb;
ALTER TABLE source_normalizations ADD COLUMN analysis_scope jsonb;

ALTER TABLE material_processing_runs
    DROP CONSTRAINT material_processing_runs_progress_stage_check,
    ADD CONSTRAINT material_processing_runs_progress_stage_check CHECK (
        progress_stage IN ('queued', 'evidence', 'semantics', 'review', 'indexing', 'publishing', 'completed')
    );

CREATE TABLE material_retrieval_indexes (
    learner_id uuid NOT NULL,
    material_id uuid NOT NULL,
    structure_revision text NOT NULL,
    policy text NOT NULL CHECK (policy = 'evidence-passages/v1'),
    passage_count integer NOT NULL CHECK (passage_count > 0),
    content_sha256 text NOT NULL CHECK (content_sha256 ~ '^[a-f0-9]{64}$'),
    outline jsonb NOT NULL,
    created_at timestamptz NOT NULL,
    PRIMARY KEY (learner_id, material_id, structure_revision),
    FOREIGN KEY (learner_id, material_id, structure_revision)
        REFERENCES knowledge_structures(learner_id, material_id, structure_revision) ON DELETE CASCADE
);

CREATE TABLE material_passages (
    learner_id uuid NOT NULL,
    material_id uuid NOT NULL,
    structure_revision text NOT NULL,
    position integer NOT NULL CHECK (position >= 0),
    evidence_id text NOT NULL,
    source_id uuid NOT NULL,
    page integer NOT NULL,
    section_id text NOT NULL,
    concept_ids text[] NOT NULL,
    payload jsonb NOT NULL,
    keywords tsvector NOT NULL,
    PRIMARY KEY (learner_id, material_id, structure_revision, position),
    FOREIGN KEY (learner_id, material_id, structure_revision)
        REFERENCES material_retrieval_indexes(learner_id, material_id, structure_revision) ON DELETE CASCADE
);
CREATE INDEX material_passages_terms ON material_passages USING gin(keywords);
CREATE INDEX material_passages_evidence ON material_passages(learner_id, material_id, structure_revision, evidence_id);
CREATE INDEX material_passages_concepts ON material_passages USING gin(concept_ids);
