-- 場景只依附既有 Podcast，不重新生成音訊或建立另一份知識地圖。
CREATE TABLE podcast_scenes (
 scene_id uuid PRIMARY KEY,
 podcast_id uuid NOT NULL REFERENCES podcasts(podcast_id) ON DELETE CASCADE,
 episode_index integer NOT NULL CHECK(episode_index>=0),
 input_sha256 text NOT NULL,
 status text NOT NULL CHECK(status IN ('pending','running','ready','failed','cancelled')),
 alignment jsonb,
 checks jsonb,
 manifest jsonb,
 error_code text,
 version bigint NOT NULL DEFAULT 1,
 lease_token uuid,
 lease_expires_at timestamptz,
 created_at timestamptz NOT NULL,
 UNIQUE(podcast_id,episode_index)
);
