ALTER TABLE public.podcast_scenes ADD COLUMN started_at timestamptz, ADD COLUMN finished_at timestamptz;
ALTER TABLE public.podcast_videos ADD COLUMN started_at timestamptz, ADD COLUMN finished_at timestamptz;
