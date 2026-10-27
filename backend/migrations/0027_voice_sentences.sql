ALTER TABLE public.voice_turns ADD COLUMN stage text, ADD COLUMN timings jsonb;
CREATE TABLE public.voice_sentences (
    turn_id uuid NOT NULL REFERENCES public.voice_turns(turn_id) ON DELETE CASCADE,
    index integer NOT NULL,
    text text NOT NULL,
    status text NOT NULL,
    audio bytea,
    duration_seconds double precision,
    queued_at timestamptz,
    PRIMARY KEY (turn_id, index)
);
