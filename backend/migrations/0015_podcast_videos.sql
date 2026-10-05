-- 影片依附每集原音訊，獨立排程；不更動 Podcast 音訊工作的結果。
CREATE TABLE podcast_videos (
 video_id uuid PRIMARY KEY,
 podcast_id uuid NOT NULL REFERENCES podcasts(podcast_id) ON DELETE CASCADE,
 episode_index integer NOT NULL CHECK(episode_index>=0),
 source_sha256 text NOT NULL,
 status text NOT NULL CHECK(status IN ('pending','running','ready','failed','cancelled')),
 artifact_id uuid REFERENCES artifacts(artifact_id),
 manifest jsonb,
 error_code text,
 version bigint NOT NULL DEFAULT 1,
 lease_token uuid,
 lease_expires_at timestamptz,
 created_at timestamptz NOT NULL,
 UNIQUE(podcast_id,episode_index),
 CHECK ((status='running')=(lease_token IS NOT NULL AND lease_expires_at IS NOT NULL)),
 CHECK ((status='ready')=(artifact_id IS NOT NULL AND manifest IS NOT NULL))
);
CREATE INDEX podcast_videos_work ON podcast_videos(created_at) WHERE status IN ('pending','running');

ALTER TABLE artifacts DROP CONSTRAINT artifact_role_media;
ALTER TABLE artifacts ADD CONSTRAINT artifact_role_media CHECK (
 (kind='podcast_video' AND media_type='video/mp4')
 OR (kind='podcast_audio' AND media_type='audio/wav')
 OR (kind='normalized_pdf' AND media_type='application/pdf')
 OR (kind='source_mapping' AND media_type='application/json')
 OR (kind='original' AND media_type IN (
   'application/pdf','application/msword','application/vnd.ms-powerpoint',
   'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
   'application/vnd.openxmlformats-officedocument.presentationml.presentation','text/plain','text/markdown'
 ))
);
