-- 講稿統一為發言輪次；只轉換表示方式，保留文字、引用、provider 與音訊身分。
UPDATE podcasts AS p
SET episodes = (
    SELECT COALESCE(jsonb_agg(
        (episode.value || jsonb_build_object('delivery', COALESCE(episode.value->'delivery', '"solo"'::jsonb)))
        || CASE WHEN jsonb_typeof(episode.value->'script') = 'object' THEN
            jsonb_build_object('script', (episode.value->'script') || jsonb_build_object('segments', (
                SELECT jsonb_agg(
                    CASE WHEN segment.value ? 'text' THEN
                        (segment.value - 'text') || jsonb_build_object('turns', jsonb_build_array(
                            jsonb_build_object('speaker', 'host', 'text', segment.value->'text')
                        ))
                    ELSE segment.value END ORDER BY segment.ordinality
                )
                FROM jsonb_array_elements(episode.value->'script'->'segments') WITH ORDINALITY AS segment(value, ordinality)
            )))
        ELSE '{}'::jsonb END
        ORDER BY episode.ordinality
    ), '[]'::jsonb)
    FROM jsonb_array_elements(p.episodes) WITH ORDINALITY AS episode(value, ordinality)
);
