-- 媒體來源：圖片組（依序打包的 ZIP）、音訊（含影片抽出的音軌）與 YouTube 影片參照可作為原檔。
-- 音訊原檔上限 300 MiB，其餘 artifact 維持 100 MiB。原影片與 YouTube 影音不保存。
ALTER TABLE public.artifacts DROP CONSTRAINT artifact_role_media;
ALTER TABLE public.artifacts ADD CONSTRAINT artifact_role_media CHECK (
    ((kind = 'podcast_video') AND (media_type = 'video/mp4'))
    OR ((kind = 'podcast_audio') AND (media_type = 'audio/wav'))
    OR ((kind = 'normalized_pdf') AND (media_type = 'application/pdf'))
    OR ((kind = 'source_mapping') AND (media_type = 'application/json'))
    OR ((kind = 'original') AND (media_type = ANY (ARRAY[
        'application/pdf',
        'application/msword',
        'application/vnd.ms-powerpoint',
        'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        'application/vnd.openxmlformats-officedocument.presentationml.presentation',
        'text/plain',
        'text/markdown',
        'application/zip',
        'audio/mpeg',
        'audio/mp4',
        'audio/wav',
        'application/vnd.studydy.youtube+json'
    ])))
);
ALTER TABLE public.artifacts DROP CONSTRAINT artifacts_size_bytes_check;
ALTER TABLE public.artifacts ADD CONSTRAINT artifacts_size_bytes_check CHECK (
    (size_bytes >= 1)
    AND (
        (size_bytes <= 104857600)
        OR (
            (kind = 'original')
            AND (media_type = ANY (ARRAY['audio/mpeg', 'audio/mp4', 'audio/wav']))
            AND (size_bytes <= 314572800)
        )
    )
);
