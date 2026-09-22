-- 補強直接依 AnswerEvent 投影；只移除閱讀／手動結束的 workflow metadata。
ALTER TABLE assessment_sets DROP COLUMN review_actions;
ALTER TABLE assessment_sets DROP COLUMN cycle_closed_at;

-- 題目、作答、來源、補強歸屬與既有選取目標保持不變。
-- 同一 migration transaction 持有表鎖；只為 policy 名稱升級暫停 scope trigger。
ALTER TABLE assessment_sets DISABLE TRIGGER assessment_set_scope_immutable;
UPDATE assessment_sets
 SET target_plan = jsonb_set(target_plan, '{policy}', '"needs-review-points/v1"'::jsonb)
 WHERE kind = 'remediation';
ALTER TABLE assessment_sets ENABLE TRIGGER assessment_set_scope_immutable;
