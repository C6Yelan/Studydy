-- 新教材與補充學習共用範圍確認；原研究與教材資料保留。
ALTER TABLE topic_scopes ADD COLUMN material_id uuid;
ALTER TABLE topic_scopes ADD COLUMN base_revision text;
ALTER TABLE topic_scopes ADD CONSTRAINT topic_material_owner
    FOREIGN KEY (learner_id, material_id) REFERENCES materials(learner_id, material_id);
ALTER TABLE topic_scopes ADD CONSTRAINT topic_material_revision
    CHECK ((material_id IS NULL) = (base_revision IS NULL));
CREATE INDEX topic_scopes_material ON topic_scopes(learner_id, material_id, created_at);
