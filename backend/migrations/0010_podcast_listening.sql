-- Podcast 收聽進度上雲：每集的播放位置與首次聽完時間，以及每天實際收聽秒數。
-- 播放位置供跨裝置接續；每日收聽秒數供學習活動（XP、連續天數）彙總，不另存累計點數。
-- Podcast 被實際刪除（教材清除）時一併刪除；軟刪除時由程式刪除播放位置，收聽紀錄保留。

CREATE TABLE public.podcast_episode_progress (
    podcast_id uuid NOT NULL,
    episode_index integer NOT NULL,
    learner_id uuid NOT NULL,
    position_seconds double precision NOT NULL,
    completed_at timestamp with time zone,
    updated_at timestamp with time zone NOT NULL,
    CONSTRAINT podcast_episode_progress_pkey PRIMARY KEY (podcast_id, episode_index),
    CONSTRAINT podcast_episode_progress_podcast_fkey FOREIGN KEY (podcast_id)
        REFERENCES public.podcasts(podcast_id) ON DELETE CASCADE,
    CONSTRAINT podcast_episode_progress_episode_check CHECK (episode_index >= 0),
    CONSTRAINT podcast_episode_progress_position_check CHECK (position_seconds >= 0 AND position_seconds <= 86400)
);

CREATE INDEX podcast_episode_progress_learner ON public.podcast_episode_progress USING btree (learner_id, updated_at);

CREATE TABLE public.podcast_listening_days (
    podcast_id uuid NOT NULL,
    episode_index integer NOT NULL,
    listened_on date NOT NULL,
    learner_id uuid NOT NULL,
    listened_seconds integer NOT NULL,
    updated_at timestamp with time zone NOT NULL,
    CONSTRAINT podcast_listening_days_pkey PRIMARY KEY (podcast_id, episode_index, listened_on),
    CONSTRAINT podcast_listening_days_podcast_fkey FOREIGN KEY (podcast_id)
        REFERENCES public.podcasts(podcast_id) ON DELETE CASCADE,
    CONSTRAINT podcast_listening_days_episode_check CHECK (episode_index >= 0),
    CONSTRAINT podcast_listening_days_seconds_check CHECK (listened_seconds BETWEEN 0 AND 86400)
);

CREATE INDEX podcast_listening_days_learner ON public.podcast_listening_days USING btree (learner_id, listened_on);
