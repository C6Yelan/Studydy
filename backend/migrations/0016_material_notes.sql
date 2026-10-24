-- 筆記的發布內容與生成工作分開保存；重新生成失敗不移除可讀筆記。
CREATE TABLE material_notes (
    learner_id uuid NOT NULL,
    material_id uuid NOT NULL,
    structure_revision text NOT NULL,
    kind text NOT NULL CHECK (kind IN ('summary', 'full')),
    status text NOT NULL CHECK (status IN ('pending', 'running', 'ready', 'failed')),
    generation integer NOT NULL DEFAULT 0,
    paragraphs jsonb,
    runtime_lock jsonb NOT NULL,
    calls integer NOT NULL DEFAULT 0 CHECK (calls >= 0),
    attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    error_code text,
    section_plan jsonb,
    section_results jsonb,
    progress_stage text CHECK (progress_stage IN ('generating', 'validating', 'completed')),
    lease_token uuid,
    lease_expires_at timestamptz,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    PRIMARY KEY (learner_id, material_id, structure_revision, kind),
    FOREIGN KEY (learner_id, material_id, structure_revision)
        REFERENCES knowledge_structures(learner_id, material_id, structure_revision) ON DELETE CASCADE,
    CHECK ((status = 'running') = (lease_token IS NOT NULL AND lease_expires_at IS NOT NULL)),
    CHECK (status <> 'ready' OR paragraphs IS NOT NULL)
);
CREATE INDEX material_notes_queue ON material_notes(created_at) WHERE status IN ('pending', 'running');

-- 不依賴會被替換／清理的段落或版本；原段落快照與註記隨教材保存。
CREATE TABLE note_annotations (
    annotation_id uuid PRIMARY KEY,
    learner_id uuid NOT NULL,
    material_id uuid NOT NULL,
    kind text NOT NULL CHECK (kind IN ('summary', 'full')),
    structure_revision text NOT NULL,
    anchor text NOT NULL,
    paragraph_snapshot jsonb NOT NULL,
    body text NOT NULL CHECK (length(body) BETWEEN 1 AND 10000),
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    FOREIGN KEY (learner_id, material_id) REFERENCES materials(learner_id, material_id) ON DELETE CASCADE
);
CREATE INDEX note_annotations_material ON note_annotations(learner_id, material_id, kind, created_at);
