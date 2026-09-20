-- 未完成的題組只限制同一觀念重複建立，不封鎖其他觀念的學習。
DROP INDEX one_active_assessment_set;
CREATE UNIQUE INDEX one_active_assessment_set_per_concept
 ON assessment_sets(study_session_id, target_concept_id)
 WHERE status IN ('preparing','partial_ready','ready','in_progress');
