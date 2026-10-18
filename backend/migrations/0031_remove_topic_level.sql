-- 學習範圍只保留名稱、目標、主題及排除項目；自訂背景移到目標，不保留程度欄位。
-- 此函式僅供本次遷移重新計算提案確認指紋，完成後移除。
CREATE FUNCTION pg_temp.topic_scope_json(value jsonb) RETURNS text
LANGUAGE plpgsql IMMUTABLE AS $$
BEGIN
    CASE jsonb_typeof(value)
        WHEN 'object' THEN
            RETURN '{' || COALESCE((SELECT string_agg(to_jsonb(key)::text || ':' || pg_temp.topic_scope_json(item), ',' ORDER BY key COLLATE "C")
                FROM jsonb_each(value) AS fields(key, item)), '') || '}';
        WHEN 'array' THEN
            RETURN '[' || COALESCE((SELECT string_agg(pg_temp.topic_scope_json(item), ',' ORDER BY position)
                FROM jsonb_array_elements(value) WITH ORDINALITY AS items(item, position)), '') || ']';
        ELSE RETURN value::text;
    END CASE;
END;
$$;

WITH converted AS (
    SELECT topic_id, (proposal - 'level') || jsonb_build_object('goals',
        proposal->'goals' || CASE
            WHEN btrim(proposal->>'level') NOT IN ('', '入門', '有基礎', '深入')
            THEN jsonb_build_array('學習背景與需求：' || (proposal->>'level'))
            ELSE '[]'::jsonb
        END) AS proposal
    FROM topic_scopes
    WHERE proposal ? 'level'
)
UPDATE topic_scopes AS scopes
SET proposal = converted.proposal,
    approved_sha256 = CASE WHEN scopes.approved_sha256 IS NOT NULL
        THEN encode(sha256(convert_to(pg_temp.topic_scope_json(converted.proposal), 'UTF8')), 'hex')
        ELSE NULL END,
    version = scopes.version + 1
FROM converted
WHERE scopes.topic_id = converted.topic_id;

DROP FUNCTION pg_temp.topic_scope_json(jsonb);
