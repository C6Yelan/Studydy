-- Podcast 建立時選擇是否生成影片；既有節目建立時都會自動生成影片，因此標為 true，新建節目由程式明確寫入。
ALTER TABLE public.podcasts ADD COLUMN video_enabled boolean NOT NULL DEFAULT true;
ALTER TABLE public.podcasts ALTER COLUMN video_enabled DROP DEFAULT;
-- Podcast 音訊改存無損 FLAC；既有 WAV 保留，可由維護腳本逐檔轉換。
ALTER TABLE public.artifacts DROP CONSTRAINT artifact_role_media;
ALTER TABLE public.artifacts ADD CONSTRAINT artifact_role_media CHECK (
    ((kind = 'podcast_video') AND (media_type = 'video/mp4'))
    OR ((kind = 'podcast_audio') AND (media_type = ANY (ARRAY['audio/wav', 'audio/flac'])))
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
-- 使用者刪除影片：只刪影片 artifact，保留 manifest（舊版講稿的逐字稿時間軸只存在影片 manifest）。
ALTER TABLE public.podcast_videos DROP CONSTRAINT podcast_videos_status_check;
ALTER TABLE public.podcast_videos ADD CONSTRAINT podcast_videos_status_check CHECK (
    status = ANY (ARRAY['pending', 'running', 'ready', 'failed', 'cancelled', 'removed'])
);
ALTER TABLE public.podcast_videos DROP CONSTRAINT podcast_videos_check1;
ALTER TABLE public.podcast_videos ADD CONSTRAINT podcast_videos_check1 CHECK (
    ((status = 'ready') = (artifact_id IS NOT NULL))
    AND ((status = ANY (ARRAY['ready', 'removed'])) = (manifest IS NOT NULL))
);
-- 帳號空間保留額度：接受新增工作時固定的大小；生命週期見 docs/materials.md「帳號空間」。
-- upload：一個上傳意圖（入口、Idempotency-Key）一筆，綁定意圖雜湊（入口、檔名、媒體、大小，單次上傳另含內容雜湊）
--   與寫入者租約 expires_at；podcast_audio／podcast_video：每集一筆，建立或排隊當時的估值。
CREATE TABLE public.storage_reservations (
    reservation_id uuid PRIMARY KEY,
    learner_id uuid NOT NULL,
    material_id uuid NOT NULL,
    kind text NOT NULL CHECK (kind = ANY (ARRAY['upload', 'podcast_audio', 'podcast_video'])),
    podcast_id uuid REFERENCES public.podcasts(podcast_id) ON DELETE CASCADE,
    episode_index integer CHECK (episode_index >= 0),
    intent_sha256 bytea,
    expires_at timestamp with time zone,
    bytes bigint NOT NULL CHECK (bytes >= 1),
    created_at timestamp with time zone NOT NULL,
    FOREIGN KEY (learner_id, material_id) REFERENCES public.materials(learner_id, material_id) ON DELETE CASCADE,
    CHECK ((kind = 'upload') = (podcast_id IS NULL)),
    CHECK ((kind = 'upload') = (episode_index IS NULL)),
    CHECK ((kind = 'upload') = (intent_sha256 IS NOT NULL AND expires_at IS NOT NULL)),
    UNIQUE (podcast_id, kind, episode_index)
);
CREATE INDEX storage_reservations_learner ON public.storage_reservations (learner_id);
