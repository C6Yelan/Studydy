CREATE TABLE public.assessment_presence (
    learner_id uuid NOT NULL,
    material_id uuid NOT NULL,
    browser_id uuid NOT NULL,
    set_id uuid NOT NULL REFERENCES public.assessment_sets(set_id) ON DELETE CASCADE,
    expires_at timestamptz NOT NULL,
    PRIMARY KEY (learner_id, material_id, browser_id)
);
