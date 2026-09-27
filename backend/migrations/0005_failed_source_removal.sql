-- 退出目前教材清單，但保留失敗分析封存的來源與 artifact。
ALTER TABLE material_sources ADD COLUMN removed_at timestamptz;

CREATE FUNCTION protect_source_identity() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF (to_jsonb(NEW) - 'removed_at') IS DISTINCT FROM (to_jsonb(OLD) - 'removed_at') THEN
        RAISE EXCEPTION 'IMMUTABLE_SOURCE_BINDING';
    END IF;
    RETURN NEW;
END $$;

DROP TRIGGER source_identity_immutable ON material_sources;
CREATE TRIGGER source_identity_immutable BEFORE UPDATE ON material_sources
    FOR EACH ROW EXECUTE FUNCTION protect_source_identity();
