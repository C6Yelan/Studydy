-- 個人標記：位置與原文 quote 隨教材保存，重新生成後由程式依 quote 重新對應；原段落快照供無法對應時顯示。
CREATE TABLE note_highlights (
    highlight_id uuid PRIMARY KEY,
    learner_id uuid NOT NULL,
    material_id uuid NOT NULL,
    kind text NOT NULL CHECK (kind IN ('summary', 'full')),
    structure_revision text NOT NULL,
    generation integer NOT NULL CHECK (generation >= 0),
    paragraph_id text NOT NULL,
    block_path jsonb NOT NULL,
    start_offset integer NOT NULL CHECK (start_offset >= 0),
    end_offset integer NOT NULL,
    quote text NOT NULL CHECK (length(quote) BETWEEN 1 AND 4000),
    style text NOT NULL CHECK (style IN ('yellow', 'green', 'blue', 'pink', 'bold', 'underline', 'strikethrough')),
    paragraph_snapshot jsonb NOT NULL,
    created_at timestamptz NOT NULL,
    CHECK (end_offset > start_offset),
    FOREIGN KEY (learner_id, material_id) REFERENCES materials(learner_id, material_id) ON DELETE CASCADE
);
CREATE INDEX note_highlights_material ON note_highlights(learner_id, material_id, kind, created_at);

-- 註記加選取範圍錨點；舊註記四欄皆為 NULL，仍以段落 anchor 對應，不搬資料。
ALTER TABLE note_annotations
    ADD COLUMN block_path jsonb,
    ADD COLUMN start_offset integer,
    ADD COLUMN end_offset integer,
    ADD COLUMN quote text,
    ADD COLUMN generation integer,
    ADD CONSTRAINT note_annotations_anchor_complete CHECK (
        (block_path IS NULL AND generation IS NULL AND start_offset IS NULL AND end_offset IS NULL AND quote IS NULL)
        OR (block_path IS NOT NULL AND generation IS NOT NULL AND start_offset >= 0 AND end_offset > start_offset AND quote IS NOT NULL));
