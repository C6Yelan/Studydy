-- 排除條件併入可編輯的學習目標，現行提案不再維護另一份 exclude 清單。
-- 只轉換提案；已發布教材、來源集合與封存 bundle 不改寫。
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
    SELECT topic_id, (proposal - 'exclude') || jsonb_build_object('goals',
        (proposal->'goals') || COALESCE((
            SELECT jsonb_agg('範圍限制：不涵蓋「' || item || '」。' ORDER BY position)
            FROM jsonb_array_elements_text(proposal->'exclude') WITH ORDINALITY AS exclusions(item, position)
            WHERE btrim(item) <> ''
        ), '[]'::jsonb)) AS proposal
    FROM topic_scopes
    WHERE proposal ? 'exclude'
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
