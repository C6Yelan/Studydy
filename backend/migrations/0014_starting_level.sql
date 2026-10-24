-- 起始程度屬於教材；題組與答案仍綁定建立時的知識結構版本。
ALTER TABLE public.materials ADD COLUMN starting_level text
    CHECK (starting_level IN ('beginner', 'some_knowledge', 'familiar', 'gaps'));
ALTER TABLE public.study_sessions ADD COLUMN start_receipts jsonb NOT NULL DEFAULT '{}'::jsonb;

ALTER TABLE public.assessment_sets
    ALTER COLUMN target_concept_id DROP NOT NULL,
    DROP CONSTRAINT assessment_sets_kind_check,
    ADD CONSTRAINT assessment_sets_kind_check
        CHECK (kind IN ('diagnostic', 'remediation', 'material_diagnostic')),
    ADD CONSTRAINT assessment_sets_material_scope_check
        CHECK ((kind = 'material_diagnostic') = (target_concept_id IS NULL)),
    ADD CONSTRAINT assessment_sets_material_diagnostic_limit
        CHECK (kind <> 'material_diagnostic' OR requested_count <= 15),
    ADD CONSTRAINT assessment_sets_member_scope_unique
        UNIQUE (set_id, study_session_id, knowledge_structure_revision);

ALTER TABLE public.assessment_set_items
    DROP CONSTRAINT assessment_set_items_set_id_study_session_id_knowledge_str_fkey,
    DROP CONSTRAINT assessment_set_items_set_id_target_claim_id_key,
    ADD CONSTRAINT assessment_set_items_set_scope_fkey
        FOREIGN KEY (set_id, study_session_id, knowledge_structure_revision)
        REFERENCES public.assessment_sets(set_id, study_session_id, knowledge_structure_revision),
    ADD CONSTRAINT assessment_set_items_concept_claim_unique
        UNIQUE (set_id, target_concept_id, target_claim_id);

-- 每個固定版本只做一組跨概念診斷；重選程度與重送請求沿用原組。
CREATE UNIQUE INDEX one_material_diagnostic_per_study
    ON public.assessment_sets(study_session_id) WHERE kind = 'material_diagnostic';

-- 原本由四欄外鍵保護的單概念成員約束仍保留。
CREATE FUNCTION public.protect_assessment_member_concept() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE parent_kind text; parent_concept text;
BEGIN
    IF TG_TABLE_NAME = 'assessment_set_items' THEN
        SELECT kind, target_concept_id INTO parent_kind, parent_concept
        FROM public.assessment_sets WHERE set_id = NEW.set_id FOR SHARE;
        IF parent_kind <> 'material_diagnostic'
            AND NEW.target_concept_id IS DISTINCT FROM parent_concept THEN
            RAISE EXCEPTION 'single concept set member scope mismatch';
        END IF;
    ELSIF NEW.kind <> 'material_diagnostic' AND EXISTS (
        SELECT 1 FROM public.assessment_set_items
        WHERE set_id = NEW.set_id AND target_concept_id IS DISTINCT FROM NEW.target_concept_id
    ) THEN
        RAISE EXCEPTION 'single concept set member scope mismatch';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER assessment_member_concept_scope
    BEFORE INSERT OR UPDATE OF set_id, target_concept_id ON public.assessment_set_items
    FOR EACH ROW EXECUTE FUNCTION public.protect_assessment_member_concept();
CREATE TRIGGER assessment_parent_concept_scope
    BEFORE UPDATE OF target_concept_id ON public.assessment_sets
    FOR EACH ROW EXECUTE FUNCTION public.protect_assessment_member_concept();
