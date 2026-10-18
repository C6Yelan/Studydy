-- 只接軌已核對的歷史 Schema 差異：等價 CHECK 更名與三個函式排版。

ALTER TABLE source_normalizations RENAME CONSTRAINT source_normalizations_check2 TO source_normalizations_check1;

CREATE OR REPLACE FUNCTION public.protect_assessment_set_origin() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF TG_OP = 'UPDATE' AND ROW(NEW.kind, NEW.diagnostic_set_id)
        IS DISTINCT FROM ROW(OLD.kind, OLD.diagnostic_set_id) THEN
        RAISE EXCEPTION 'assessment set origin is immutable';
    END IF;
    IF NEW.diagnostic_set_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM assessment_sets
        WHERE set_id = NEW.diagnostic_set_id AND kind = 'diagnostic'
    ) THEN
        RAISE EXCEPTION 'remediation requires a diagnostic origin';
    END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION public.protect_published_set_item() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF OLD.assessment_revision IS NOT NULL AND NEW IS DISTINCT FROM OLD THEN
        RAISE EXCEPTION 'published assessment set item is immutable';
    END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION public.protect_source_snapshot() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    RAISE EXCEPTION 'IMMUTABLE_SOURCE_BINDING';
END $$;


SET default_tablespace = '';

SET default_table_access_method = heap;
