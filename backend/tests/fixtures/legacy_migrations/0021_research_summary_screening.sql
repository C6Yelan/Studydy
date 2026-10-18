-- 既有研究一次轉成摘要分流格式；不在執行時保留舊流程分支。
-- 原正文引用、閱讀位置、工作狀態與取消狀態均保留。
WITH documents AS (
    SELECT research_id, candidates,
           CASE WHEN jsonb_typeof(candidates) = 'array' THEN candidates ELSE candidates->'items' END AS items
    FROM material_research
    WHERE search_state <> '{}'::jsonb
), converted AS (
    SELECT research_id, candidates,
        COALESCE((SELECT jsonb_agg(
            CASE WHEN item ? 'screening' THEN item
                 WHEN item->'review'->>'phase' = 'body'
                   OR COALESCE((item->>'_read_offset')::integer, 0) > 0
                   OR item->'access'->>'status' = 'unavailable'
                 THEN item || jsonb_build_object('screening', jsonb_build_object(
                     'decision', 'read', 'reason', '已保存的正文核對與存取紀錄。', 'basis', 'saved_review'))
                 ELSE item END ORDER BY ordinal)
            FROM jsonb_array_elements(items) WITH ORDINALITY AS entries(item, ordinal)), '[]'::jsonb) AS items
    FROM documents
)
UPDATE material_research AS work
SET candidates = CASE WHEN jsonb_typeof(converted.candidates) = 'array' THEN converted.items
                      ELSE jsonb_set(converted.candidates, '{items}', converted.items) END,
    search_state = CASE WHEN work.search_state->>'stage' IN ('screening', 'reading', 'assessing', 'following_up', 'organizing')
                        THEN jsonb_set(work.search_state - 'workflow_version', '{stage}', '"collecting"'::jsonb)
                        ELSE work.search_state - 'workflow_version' END
FROM converted WHERE work.research_id = converted.research_id;
