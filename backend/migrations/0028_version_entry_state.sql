-- S5 最後一個 migration；合併前若 dev 有新 migration 必須重編號。只做不部署。
CREATE TABLE public.material_version_state (
    learner_id uuid NOT NULL,
    material_id uuid NOT NULL,
    target_revision text,
    prior_revision text,
    previous jsonb,
    notice_dismissed boolean NOT NULL DEFAULT false,
    automatic_status text,
    automatic_move_id uuid,
    automatic_error text,
    PRIMARY KEY (learner_id, material_id),
    FOREIGN KEY (learner_id, material_id) REFERENCES public.materials(learner_id, material_id) ON DELETE CASCADE
);
INSERT INTO public.material_version_state (learner_id, material_id, target_revision, prior_revision)
SELECT m.learner_id, m.material_id, m.head_revision, r.base_revision
FROM public.materials m
LEFT JOIN public.knowledge_structures k ON k.learner_id=m.learner_id AND k.material_id=m.material_id AND k.structure_revision=m.head_revision
LEFT JOIN public.material_processing_runs r ON r.run_id=k.run_id;
