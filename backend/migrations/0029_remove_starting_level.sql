-- 刪除跨概念檢測；暫存投影只服務本次資料轉換，不留下相容路徑。
CREATE TEMP TABLE removed_sets ON COMMIT DROP AS
SELECT set_id, learner_id, material_id, study_session_id
FROM public.assessment_sets WHERE kind = 'material_diagnostic';
CREATE TEMP TABLE affected_materials ON COMMIT DROP AS
SELECT DISTINCT learner_id, material_id FROM removed_sets;
CREATE TEMP TABLE removed_assessments ON COMMIT DROP AS
SELECT assessment_revision FROM public.assessment_set_items
WHERE set_id IN (SELECT set_id FROM removed_sets) AND assessment_revision IS NOT NULL;

UPDATE public.study_sessions s SET start_receipts = coalesce((
    SELECT jsonb_object_agg(receipt.key, receipt.value)
    FROM jsonb_each(s.start_receipts) receipt
    WHERE NOT EXISTS (SELECT 1 FROM removed_sets r WHERE r.set_id::text = receipt.value->>'set_id')
), '{}'::jsonb)
WHERE EXISTS (SELECT 1 FROM removed_sets r WHERE r.study_session_id = s.study_session_id);
DELETE FROM public.answer_events WHERE assessment_revision IN (SELECT assessment_revision FROM removed_assessments);
DELETE FROM public.assessment_set_items WHERE set_id IN (SELECT set_id FROM removed_sets);
DELETE FROM public.assessments WHERE assessment_revision IN (SELECT assessment_revision FROM removed_assessments);
DELETE FROM public.assessment_sets WHERE set_id IN (SELECT set_id FROM removed_sets);

-- 先移到原最大值以上，避免非 deferred 的事件唯一鍵在重編途中碰撞。
CREATE TEMP TABLE remaining_events ON COMMIT DROP AS
SELECT e.answer_event_id, row_number() OVER (PARTITION BY e.study_session_id ORDER BY e.event_number) AS new_number,
       max(e.event_number) OVER (PARTITION BY e.study_session_id) AS offset_number
FROM public.answer_events e WHERE e.study_session_id IN (SELECT study_session_id FROM removed_sets);
UPDATE public.answer_events e SET event_number = r.new_number + r.offset_number
FROM remaining_events r WHERE e.answer_event_id = r.answer_event_id;
UPDATE public.answer_events e SET event_number = r.new_number
FROM remaining_events r WHERE e.answer_event_id = r.answer_event_id;
UPDATE public.study_sessions s SET last_event_number = (
    SELECT count(*) FROM public.answer_events e WHERE e.study_session_id = s.study_session_id
), last_applied_guidance_revision = NULL, last_applied_progress_sha256 = NULL
WHERE EXISTS (SELECT 1 FROM affected_materials a WHERE (a.learner_id,a.material_id) = (s.learner_id,s.material_id));

-- 舊快照沒有逐題身分，含檢測獎勵的教材日快照無法分拆，整列清除。
CREATE TEMP TABLE affected_owners ON COMMIT DROP AS
SELECT learner_id FROM affected_materials UNION
SELECT learner_id FROM public.learning_retained_days WHERE source_kind = 'material' AND metrics ? 'diagnostic_xp';
DELETE FROM public.learning_retained_days WHERE source_kind = 'material' AND metrics ? 'diagnostic_xp';
DELETE FROM public.learning_awards WHERE badge_id IN ('first_diagnostic','diagnostics_5','diagnosed_mastery');
-- 這些已存徽章可能只由被刪作答取得；下次讀取依保留紀錄重算，無關徽章不動。
DELETE FROM public.learning_awards WHERE learner_id IN (SELECT learner_id FROM affected_owners)
AND (badge_id ~ '^(answers_|concepts_|materials_|level_|streak_|days_|sets_|perfect_set_|combo_|comeback_|daily_|explorer_|stages_|difficulties_)'
     OR badge_id IN ('first_answer','first_concept','first_material','first_set','first_stage','first_difficulty',
        'night_owl','early_bird','weekend','return','first_try_material','perfect_day','all_round_day',
        'thousand_pages','revisit','xp_day_300','never_give_up','three_islands','new_year','all_tools_material',
        'media_concepts_5','stage_streak_7','goal_on_time','last_minute','nemesis','same_day_learner','corrected_5'));
DELETE FROM public.goal_milestones g USING affected_materials a
WHERE (g.learner_id,g.material_id) = (a.learner_id,a.material_id);

CREATE TEMP TABLE retained_scopes ON COMMIT DROP AS
SELECT k.learner_id, k.material_id, k.structure_revision, k.learning, k.outline,
       d.document, d.created_at, r.base_revision
FROM public.knowledge_read_indexes k
JOIN affected_materials a USING (learner_id,material_id)
JOIN public.knowledge_structures d USING (learner_id,material_id,structure_revision)
JOIN public.material_processing_runs r ON r.run_id = d.run_id;
CREATE TEMP TABLE retained_evidence ON COMMIT DROP AS
SELECT s.learner_id,s.material_id,s.structure_revision,e.answer_event_id,e.target_concept_id,e.target_claim_id,
       e.semantic_identity,e.is_correct,e.created_at
FROM retained_scopes s JOIN public.answer_events e
ON (e.material_id,e.knowledge_structure_revision)=(s.material_id,s.structure_revision)
UNION ALL
SELECT current.learner_id,current.material_id,current.structure_revision,e.answer_event_id,
       mapped.value->0->>0,mapped.value->0->>1,e.semantic_identity,e.is_correct,e.created_at
FROM retained_scopes current
JOIN retained_scopes prior ON (prior.learner_id,prior.material_id)=(current.learner_id,current.material_id)
                         AND prior.created_at < current.created_at
CROSS JOIN LATERAL jsonb_each(prior.learning->'claim_signatures') original
JOIN LATERAL jsonb_each(current.learning->'claim_signatures') mapped ON original.key = mapped.key
JOIN public.answer_events e ON (e.material_id,e.knowledge_structure_revision,e.target_concept_id,e.target_claim_id)
   = (prior.material_id,prior.structure_revision,original.value->0->>0,original.value->0->>1)
WHERE current.base_revision IS NOT NULL AND jsonb_array_length(original.value)=1 AND jsonb_array_length(mapped.value)=1;
CREATE INDEX ON retained_evidence (learner_id,material_id,structure_revision,target_concept_id);

CREATE FUNCTION pg_temp.point_done(owner uuid, material uuid, revision text, concept text, covered jsonb,
                                   through_at timestamptz DEFAULT 'infinity') RETURNS boolean LANGUAGE plpgsql AS $$
DECLARE event record; correct_ids text[] := '{}'; first_answer boolean := true;
BEGIN
    FOR event IN SELECT e.is_correct,e.semantic_identity FROM retained_evidence e
        WHERE (e.learner_id,e.material_id,e.structure_revision,e.target_concept_id)=(owner,material,revision,concept)
          AND covered ? e.target_claim_id AND e.created_at <= through_at
        ORDER BY e.created_at,e.answer_event_id
    LOOP
        IF NOT event.is_correct THEN correct_ids := '{}';
        ELSE
            IF first_answer THEN RETURN true; END IF;
            IF NOT event.semantic_identity = ANY(correct_ids) THEN
                correct_ids := array_append(correct_ids,event.semantic_identity);
            END IF;
            IF cardinality(correct_ids)>=2 THEN RETURN true; END IF;
        END IF;
        first_answer := false;
    END LOOP;
    RETURN false;
END;
$$;

-- 既有單概念題組的可出題範圍，沿用最後初篩及其補強的安全題結果。
CREATE TEMP TABLE unavailable_points ON COMMIT DROP AS
WITH roots AS (
    SELECT DISTINCT ON (s.study_session_id,s.target_concept_id) s.*
    FROM public.assessment_sets s JOIN affected_materials a USING (learner_id,material_id)
    WHERE s.kind='diagnostic' ORDER BY s.study_session_id,s.target_concept_id,s.created_at DESC,s.set_id DESC
), latest AS (
    SELECT DISTINCT ON (r.study_session_id,r.target_concept_id,i.target_claim_id)
        r.study_session_id,r.target_concept_id,r.target_plan,i.target_claim_id,i.assessment_revision,
        i.failure_reason,g.sealed_at
    FROM roots r JOIN public.assessment_sets g ON g.set_id=r.set_id OR g.diagnostic_set_id=r.set_id
    JOIN public.assessment_set_items i ON i.set_id=g.set_id
    ORDER BY r.study_session_id,r.target_concept_id,i.target_claim_id,g.created_at DESC,g.set_id DESC
)
SELECT l.study_session_id, covered.value #>> '{}' AS claim_id
FROM latest l CROSS JOIN LATERAL jsonb_array_elements(l.target_plan->'targets') target
CROSS JOIN LATERAL jsonb_array_elements(target->'covered_claim_ids') covered
WHERE target->>'claim_id'=l.target_claim_id AND l.assessment_revision IS NULL
AND (l.failure_reason='NO_SAFE_ASSESSMENT' OR l.sealed_at IS NOT NULL)
UNION SELECT s.study_session_id,unnest(s.no_safe_claim_ids) FROM public.study_sessions s
JOIN affected_materials a USING (learner_id,material_id);

-- 通過單概念題組的完成事實不因後續練習改成無安全題而撤銷。
CREATE TEMP TABLE passed_concepts ON COMMIT DROP AS
SELECT r.study_session_id,r.target_concept_id
FROM public.assessment_sets r JOIN affected_materials a USING (learner_id,material_id)
WHERE r.kind='diagnostic' AND r.status='completed' AND EXISTS (
    SELECT 1 FROM jsonb_array_elements(r.target_plan->'targets') target
    WHERE pg_temp.point_done(r.learner_id,r.material_id,r.knowledge_structure_revision,
                            r.target_concept_id,target->'covered_claim_ids')
) AND NOT EXISTS (
    SELECT 1 FROM jsonb_array_elements(r.target_plan->'targets') target
    WHERE NOT pg_temp.point_done(r.learner_id,r.material_id,r.knowledge_structure_revision,
                                 r.target_concept_id,target->'covered_claim_ids')
    AND NOT coalesce((SELECT i.assessment_revision IS NULL AND
        (i.failure_reason='NO_SAFE_ASSESSMENT' OR g.sealed_at IS NOT NULL)
        FROM public.assessment_sets g JOIN public.assessment_set_items i ON i.set_id=g.set_id
        WHERE (g.set_id=r.set_id OR g.diagnostic_set_id=r.set_id)
          AND i.target_claim_id=target->>'claim_id'
        ORDER BY g.created_at DESC,g.set_id DESC LIMIT 1),false)
);

CREATE FUNCTION pg_temp.concept_done(study uuid, concept text) RETURNS boolean LANGUAGE sql AS $$
    SELECT coalesce(
        EXISTS (SELECT 1 FROM passed_concepts p WHERE p.study_session_id=study AND p.target_concept_id=concept)
        OR EXISTS (SELECT 1 FROM public.study_session_moves m WHERE m.target_study_session_id=s.study_session_id
                AND concept=ANY(m.completed_concept_ids))
        OR (SELECT count(*) FILTER (WHERE NOT EXISTS (
                  SELECT 1 FROM unavailable_points u WHERE u.study_session_id=study AND target->'covered_claim_ids' ? u.claim_id)) > 0
            AND bool_and(pg_temp.point_done(s.learner_id,s.material_id,s.knowledge_structure_revision,concept,target->'covered_claim_ids'))
                FILTER (WHERE NOT EXISTS (SELECT 1 FROM unavailable_points u
                    WHERE u.study_session_id=study AND target->'covered_claim_ids' ? u.claim_id))
            FROM jsonb_array_elements(c->'targets') target),false)
    FROM public.study_sessions s JOIN retained_scopes k
      ON (k.learner_id,k.material_id,k.structure_revision)=(s.learner_id,s.material_id,s.knowledge_structure_revision)
    CROSS JOIN LATERAL jsonb_array_elements(k.learning->'concepts') c
    WHERE s.study_session_id=study AND c->>'concept_id'=concept;
$$;

-- 使用與移版相同的原文區塊／名稱規則對應概念，不以被刪答案猜測新完成。
CREATE TEMP TABLE concept_blocks ON COMMIT DROP AS
SELECT DISTINCT k.learner_id,k.material_id,k.structure_revision,c->>'concept_id' AS concept_id,
       jsonb_build_array(source->'original_sha256',source->'normalized_sha256',location->'normalized_page',
           evidence->'kind',evidence->'exact_text',evidence->'block_order',evidence->'source_locator'->'region') AS block
FROM retained_scopes k
CROSS JOIN LATERAL jsonb_array_elements(k.document->'concepts') c
CROSS JOIN LATERAL jsonb_array_elements(c->'claims') claim
CROSS JOIN LATERAL jsonb_array_elements(claim->'source_spans') span
CROSS JOIN LATERAL jsonb_array_elements(k.document->'evidence') evidence
CROSS JOIN LATERAL (SELECT k.document->'input_binding'->'bundle'->'pages'->((evidence->>'page')::integer-1) AS location) loc
CROSS JOIN LATERAL jsonb_array_elements(k.document->'input_binding'->'manifest'->'items') source
WHERE span->>'evidence_id'=evidence->>'evidence_id' AND source->>'source_id'=location->>'source_id';
CREATE TEMP TABLE concept_names ON COMMIT DROP AS
SELECT DISTINCT k.learner_id,k.material_id,k.structure_revision,c->>'concept_id' AS concept_id,
       regexp_replace(casefold(normalize(btrim(name.value),NFKC)), '\s+', ' ', 'g') AS name
FROM retained_scopes k CROSS JOIN LATERAL jsonb_array_elements(k.document->'concepts') c
CROSS JOIN LATERAL jsonb_array_elements_text(jsonb_build_array(c->>'label') || coalesce(c->'aliases','[]')) name;
CREATE TEMP TABLE prior_moves ON COMMIT DROP AS
SELECT m.*,s.material_id,s.knowledge_structure_revision AS from_revision,t.knowledge_structure_revision AS to_revision,v.created_at
FROM public.study_session_moves m
JOIN public.study_sessions s ON s.study_session_id=m.source_study_session_id
JOIN public.study_sessions t ON t.study_session_id=m.target_study_session_id
JOIN affected_materials a ON (a.learner_id,a.material_id)=(s.learner_id,s.material_id)
JOIN public.material_version_moves v ON v.move_id=m.move_id;
UPDATE public.study_session_moves SET completed_concept_ids='{}'
WHERE target_study_session_id IN (SELECT target_study_session_id FROM prior_moves);
DO $$
DECLARE m record; old_concept text; candidate text; mapped_ids text[]; valid_ids text[];
BEGIN
    FOR m IN SELECT * FROM prior_moves ORDER BY created_at,target_study_session_id LOOP
        valid_ids := '{}';
        FOREACH candidate IN ARRAY m.completed_concept_ids LOOP
            mapped_ids := '{}';
            FOR old_concept IN SELECT c->>'concept_id' FROM retained_scopes k,
                LATERAL jsonb_array_elements(k.document->'concepts') c
                WHERE (k.learner_id,k.material_id,k.structure_revision)=(m.learner_id,m.material_id,m.from_revision)
            LOOP
                DECLARE leaders text[]; best bigint; total bigint;
                BEGIN
                    SELECT count(*) INTO total FROM concept_blocks b WHERE
                        (b.learner_id,b.material_id,b.structure_revision,b.concept_id)=(m.learner_id,m.material_id,m.from_revision,old_concept);
                    WITH counts AS (SELECT n.concept_id,count(*) AS shared
                        FROM concept_blocks o JOIN concept_blocks n ON n.block=o.block
                        AND (n.learner_id,n.material_id,n.structure_revision)=(m.learner_id,m.material_id,m.to_revision)
                        WHERE (o.learner_id,o.material_id,o.structure_revision,o.concept_id)=(m.learner_id,m.material_id,m.from_revision,old_concept)
                        GROUP BY n.concept_id)
                    SELECT array_agg(concept_id),max(shared) INTO leaders,best FROM counts WHERE shared=(SELECT max(shared) FROM counts);
                    IF coalesce(best,0)=0 OR cardinality(leaders)<>1 OR best*2<total THEN
                        SELECT array_agg(DISTINCT n.concept_id) INTO leaders FROM concept_names o JOIN concept_names n USING (name)
                        WHERE (o.learner_id,o.material_id,o.structure_revision,o.concept_id)=(m.learner_id,m.material_id,m.from_revision,old_concept)
                          AND (n.learner_id,n.material_id,n.structure_revision)=(m.learner_id,m.material_id,m.to_revision);
                    END IF;
                    IF cardinality(leaders)=1 AND leaders[1]=candidate THEN mapped_ids:=array_append(mapped_ids,old_concept); END IF;
                END;
            END LOOP;
            IF cardinality(mapped_ids)>0 AND NOT EXISTS (
                SELECT 1 FROM unnest(mapped_ids) old WHERE NOT coalesce(pg_temp.concept_done(m.source_study_session_id,old),false)
            ) THEN valid_ids:=array_append(valid_ids,candidate); END IF;
        END LOOP;
        UPDATE public.study_session_moves SET completed_concept_ids=valid_ids
        WHERE target_study_session_id=m.target_study_session_id;
    END LOOP;
END;
$$;

UPDATE public.study_sessions s SET status='active',completed_at=NULL
WHERE s.status='completed' AND EXISTS (SELECT 1 FROM affected_materials a
    WHERE (a.learner_id,a.material_id)=(s.learner_id,s.material_id))
AND EXISTS (SELECT 1 FROM retained_scopes k,LATERAL jsonb_array_elements(k.learning->'concepts') c
    WHERE (k.learner_id,k.material_id,k.structure_revision)=(s.learner_id,s.material_id,s.knowledge_structure_revision)
    AND NOT coalesce(pg_temp.concept_done(s.study_session_id,c->>'concept_id'),false));

CREATE FUNCTION pg_temp.goal_points(owner uuid, material uuid, revision text, mask text[], through_at timestamptz)
RETURNS TABLE(assessable integer,completed integer) LANGUAGE sql AS $$
    SELECT count(*)::integer,count(*) FILTER (WHERE pg_temp.point_done(owner,material,revision,
        c->>'concept_id',target->'covered_claim_ids',through_at))::integer
    FROM retained_scopes k CROSS JOIN LATERAL jsonb_array_elements(k.learning->'concepts') c
    CROSS JOIN LATERAL jsonb_array_elements(c->'targets') target
    WHERE (k.learner_id,k.material_id,k.structure_revision)=(owner,material,revision)
    AND NOT EXISTS (SELECT 1 FROM unnest(mask) blocked WHERE target->'covered_claim_ids' ? blocked);
$$;
-- 基準日期與使用者設定保留，只用剩餘作答重算其原時間點的計量。
UPDATE public.learning_goals g SET
    baseline_assessable_points=b.assessable,baseline_completed_points=b.completed,
    daily_completed_points=all_day.completed,
    daily_quota_points=greatest(0,(ceil(day_start.assessable*g.target_percent/100.0)::integer-day_start.completed
        + greatest(1,g.target_date-g.daily_date+1)-1)/greatest(1,g.target_date-g.daily_date+1))
FROM affected_materials a,
LATERAL (SELECT goal.* FROM public.learning_goals goal WHERE (goal.learner_id,goal.material_id)=(a.learner_id,a.material_id)) original,
LATERAL pg_temp.goal_points(original.learner_id,original.material_id,original.baseline_revision,original.baseline_unavailable_claim_ids,
    (original.baseline_at AT TIME ZONE 'Asia/Taipei')::date::timestamp AT TIME ZONE 'Asia/Taipei' - interval '1 microsecond') b,
LATERAL pg_temp.goal_points(original.learner_id,original.material_id,original.baseline_revision,original.daily_unavailable_claim_ids,
    original.daily_date::timestamp AT TIME ZONE 'Asia/Taipei' - interval '1 microsecond') day_start,
LATERAL pg_temp.goal_points(original.learner_id,original.material_id,original.baseline_revision,'{}',
    original.daily_date::timestamp AT TIME ZONE 'Asia/Taipei' - interval '1 microsecond') all_day
WHERE (g.learner_id,g.material_id)=(a.learner_id,a.material_id);

DROP TRIGGER assessment_member_concept_scope ON public.assessment_set_items;
DROP TRIGGER assessment_parent_concept_scope ON public.assessment_sets;
DROP FUNCTION public.protect_assessment_member_concept();
DROP INDEX public.one_material_diagnostic_per_study;
ALTER TABLE public.assessment_set_items
    DROP CONSTRAINT assessment_set_items_set_scope_fkey,
    DROP CONSTRAINT assessment_set_items_concept_claim_unique,
    ADD CONSTRAINT assessment_set_items_set_id_study_session_id_knowledge_str_fkey
        FOREIGN KEY (set_id,study_session_id,knowledge_structure_revision,target_concept_id)
        REFERENCES public.assessment_sets(set_id,study_session_id,knowledge_structure_revision,target_concept_id),
    ADD CONSTRAINT assessment_set_items_set_id_target_claim_id_key UNIQUE (set_id,target_claim_id);
ALTER TABLE public.assessment_sets
    DROP CONSTRAINT assessment_sets_member_scope_unique,
    DROP CONSTRAINT assessment_sets_material_scope_check,
    DROP CONSTRAINT assessment_sets_material_diagnostic_limit,
    DROP CONSTRAINT assessment_sets_kind_check,
    ADD CONSTRAINT assessment_sets_kind_check CHECK (kind IN ('diagnostic','remediation')),
    ALTER COLUMN target_concept_id SET NOT NULL;
ALTER TABLE public.materials DROP COLUMN starting_level;
ALTER TABLE public.topic_scopes DROP COLUMN starting_level;
