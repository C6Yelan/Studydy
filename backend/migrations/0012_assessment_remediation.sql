ALTER TABLE assessment_sets DROP CONSTRAINT assessment_sets_kind_check;
ALTER TABLE assessment_sets ADD CONSTRAINT assessment_sets_kind_check CHECK (kind IN ('diagnostic','remediation'));
ALTER TABLE assessment_sets ADD COLUMN diagnostic_set_id uuid;
ALTER TABLE assessment_sets ADD COLUMN review_actions jsonb NOT NULL DEFAULT '{}';
ALTER TABLE assessment_sets ADD COLUMN cycle_closed_at timestamptz;
ALTER TABLE assessment_sets ADD CONSTRAINT remediation_origin_scope
 FOREIGN KEY (diagnostic_set_id,study_session_id,knowledge_structure_revision,target_concept_id)
 REFERENCES assessment_sets(set_id,study_session_id,knowledge_structure_revision,target_concept_id);
ALTER TABLE assessment_sets ADD CONSTRAINT remediation_origin_required
 CHECK ((kind='remediation')=(diagnostic_set_id IS NOT NULL) AND diagnostic_set_id IS DISTINCT FROM set_id);
CREATE INDEX assessment_remediation_origin ON assessment_sets(diagnostic_set_id) WHERE diagnostic_set_id IS NOT NULL;
CREATE FUNCTION protect_assessment_set_origin() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='UPDATE' AND ROW(NEW.kind,NEW.diagnostic_set_id) IS DISTINCT FROM ROW(OLD.kind,OLD.diagnostic_set_id) THEN
  RAISE EXCEPTION 'assessment set origin is immutable';
 END IF;
 IF NEW.diagnostic_set_id IS NOT NULL AND NOT EXISTS (
  SELECT 1 FROM assessment_sets WHERE set_id=NEW.diagnostic_set_id AND kind='diagnostic'
 ) THEN RAISE EXCEPTION 'remediation requires a diagnostic origin'; END IF;
 RETURN NEW;
END;
$$;
CREATE TRIGGER assessment_set_origin_immutable BEFORE INSERT OR UPDATE OF kind,diagnostic_set_id ON assessment_sets
 FOR EACH ROW EXECUTE FUNCTION protect_assessment_set_origin();

-- 已明確完成且沒有錯答待補強的既有初篩，沿用原完成時間；只補入新欄位。
UPDATE assessment_sets s SET cycle_closed_at=s.completed_at
 WHERE s.kind='diagnostic' AND s.status='completed' AND NOT EXISTS (
  SELECT 1 FROM assessment_set_items i JOIN answer_events a ON a.assessment_revision=i.assessment_revision
  WHERE i.set_id=s.set_id AND NOT a.is_correct
 );
