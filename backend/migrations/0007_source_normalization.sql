-- 舊 PDF 欄位與資料不重寫；新草稿在轉檔完成前可以沒有 PDF。
ALTER TABLE materials ADD COLUMN ingestion_kind text NOT NULL DEFAULT 'pdf-v1' CHECK (ingestion_kind IN ('pdf-v1', 'sources-v2'));
ALTER TABLE materials ALTER COLUMN source_artifact_id DROP NOT NULL;
ALTER TABLE materials ADD CONSTRAINT material_legacy_pdf_required CHECK (ingestion_kind <> 'pdf-v1' OR source_artifact_id IS NOT NULL);
ALTER TABLE materials ADD COLUMN head_revision text;
ALTER TABLE materials ADD CONSTRAINT material_head_fk FOREIGN KEY (learner_id, material_id, head_revision)
 REFERENCES knowledge_structures (learner_id, material_id, structure_revision) DEFERRABLE INITIALLY DEFERRED;
UPDATE materials m SET head_revision = (
 SELECT k.structure_revision FROM knowledge_structures k JOIN material_processing_runs r USING (learner_id, material_id, run_id)
 WHERE k.material_id=m.material_id AND k.learner_id=m.learner_id AND r.status IN ('succeeded','partial')
 AND r.source_artifact_id=m.source_artifact_id AND r.output_binding->>'knowledge_structure_revision'=k.structure_revision
 ORDER BY k.created_at DESC, k.run_id DESC LIMIT 1
);
ALTER TABLE artifacts DROP CONSTRAINT artifacts_kind_check;
ALTER TABLE artifacts DROP CONSTRAINT artifacts_media_type_check;
ALTER TABLE artifacts ADD CONSTRAINT artifact_role_media CHECK (
 (kind IN ('source_pdf','normalized_pdf') AND media_type='application/pdf') OR
 (kind='source_mapping' AND media_type='application/json') OR
 (kind='original' AND media_type IN ('application/pdf','application/vnd.openxmlformats-officedocument.wordprocessingml.document',
 'application/vnd.openxmlformats-officedocument.presentationml.presentation','text/plain','text/markdown'))
);
CREATE TABLE material_sources (
 source_id uuid PRIMARY KEY, learner_id uuid NOT NULL, material_id uuid NOT NULL,
 original_artifact_id uuid NOT NULL, original_name text NOT NULL, media_type text NOT NULL,
 idempotency_key_sha256 bytea NOT NULL CHECK(octet_length(idempotency_key_sha256)=32),
 request_fingerprint bytea NOT NULL CHECK(octet_length(request_fingerprint)=32), created_at timestamptz NOT NULL,
 UNIQUE(learner_id, material_id, source_id), UNIQUE(learner_id, material_id, idempotency_key_sha256),
 FOREIGN KEY(learner_id, material_id, original_artifact_id) REFERENCES artifacts(learner_id, material_id, artifact_id)
);
CREATE TABLE source_normalizations (
 normalization_id uuid PRIMARY KEY, learner_id uuid NOT NULL, material_id uuid NOT NULL, source_id uuid NOT NULL,
 policy jsonb NOT NULL, status text NOT NULL CHECK(status IN ('pending','running','ready','failed')),
 attempt integer NOT NULL DEFAULT 0 CHECK(attempt>=0), lease_token uuid, lease_expires_at timestamptz,
 normalized_artifact_id uuid, mapping_artifact_id uuid, page_count integer CHECK(page_count>0), error_code text,
 created_at timestamptz NOT NULL, updated_at timestamptz NOT NULL,
 UNIQUE(learner_id, material_id, source_id, normalization_id),
 FOREIGN KEY(learner_id, material_id, source_id) REFERENCES material_sources(learner_id, material_id, source_id),
 FOREIGN KEY(learner_id, material_id, normalized_artifact_id) REFERENCES artifacts(learner_id, material_id, artifact_id),
 FOREIGN KEY(learner_id, material_id, mapping_artifact_id) REFERENCES artifacts(learner_id, material_id, artifact_id),
 CHECK((status='running')=(lease_token IS NOT NULL AND lease_expires_at IS NOT NULL)),
 CHECK((status='ready')=(normalized_artifact_id IS NOT NULL)),
 CHECK((status='failed')=(error_code IS NOT NULL))
);
CREATE INDEX normalization_pending ON source_normalizations(created_at) WHERE status IN ('pending','running');
CREATE TABLE material_source_sets (
 source_set_id uuid PRIMARY KEY, learner_id uuid NOT NULL, material_id uuid NOT NULL, manifest jsonb NOT NULL,
 digest character(64) NOT NULL CHECK(digest ~ '^[0-9a-f]{64}$'), created_at timestamptz NOT NULL,
 UNIQUE(learner_id,material_id,source_set_id), UNIQUE(learner_id,material_id,digest),
 FOREIGN KEY(learner_id,material_id) REFERENCES materials(learner_id,material_id)
);
CREATE TABLE material_source_set_items (
 learner_id uuid NOT NULL, material_id uuid NOT NULL, source_set_id uuid NOT NULL, ordinal integer NOT NULL CHECK(ordinal>=1),
 source_id uuid NOT NULL, normalization_id uuid NOT NULL,
 PRIMARY KEY(source_set_id,ordinal), UNIQUE(source_set_id,source_id), UNIQUE(source_set_id,normalization_id),
 FOREIGN KEY(learner_id,material_id,source_set_id) REFERENCES material_source_sets(learner_id,material_id,source_set_id),
 FOREIGN KEY(learner_id,material_id,source_id,normalization_id) REFERENCES source_normalizations(learner_id,material_id,source_id,normalization_id)
);
ALTER TABLE material_processing_runs ADD COLUMN input_source_set_id uuid;
ALTER TABLE material_processing_runs ADD COLUMN bundle_manifest jsonb;
ALTER TABLE material_processing_runs ADD COLUMN bundle_manifest_sha256 character(64);
ALTER TABLE material_processing_runs ADD CONSTRAINT run_source_set_fk FOREIGN KEY(learner_id,material_id,input_source_set_id)
 REFERENCES material_source_sets(learner_id,material_id,source_set_id);
ALTER TABLE material_processing_runs ADD CONSTRAINT run_bundle_binding CHECK (
 (input_source_set_id IS NULL AND bundle_manifest IS NULL AND bundle_manifest_sha256 IS NULL) OR
 (input_source_set_id IS NOT NULL AND bundle_manifest IS NOT NULL AND bundle_manifest_sha256 ~ '^[0-9a-f]{64}$')
);
ALTER TABLE knowledge_structures DROP CONSTRAINT knowledge_structures_document_check;
ALTER TABLE knowledge_structures ADD CONSTRAINT knowledge_structure_version CHECK(document->>'schema' IN ('knowledge-structure/v2','knowledge-structure/v3'));
-- 已存在的 PDF 只建立 identity descriptors；不改檔案、KS、run、Assessment 或 AnswerEvent。
INSERT INTO material_sources SELECT a.artifact_id,a.learner_id,a.material_id,a.artifact_id,
 coalesce(m.display_name,'material.pdf'),'application/pdf',m.upload_idempotency_key_sha256,m.upload_request_fingerprint,a.created_at
 FROM artifacts a JOIN materials m USING(learner_id,material_id) WHERE a.artifact_id=m.source_artifact_id AND a.kind='source_pdf';
INSERT INTO source_normalizations(normalization_id,learner_id,material_id,source_id,policy,status,normalized_artifact_id,created_at,updated_at)
 SELECT source_id,learner_id,material_id,source_id,'{"schema":"normalization-policy/v1","renderer":"pdf-identity"}',
 'ready',original_artifact_id,created_at,created_at FROM material_sources;
CREATE FUNCTION protect_source_snapshot() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'IMMUTABLE_SOURCE_BINDING'; END $$;
CREATE TRIGGER source_identity_immutable BEFORE UPDATE ON material_sources FOR EACH ROW EXECUTE FUNCTION protect_source_snapshot();
CREATE TRIGGER source_set_immutable BEFORE UPDATE ON material_source_sets FOR EACH ROW EXECUTE FUNCTION protect_source_snapshot();
CREATE TRIGGER source_set_item_immutable BEFORE UPDATE ON material_source_set_items FOR EACH ROW EXECUTE FUNCTION protect_source_snapshot();
CREATE TRIGGER normalization_ready_immutable BEFORE UPDATE ON source_normalizations FOR EACH ROW WHEN (OLD.status='ready') EXECUTE FUNCTION protect_source_snapshot();
