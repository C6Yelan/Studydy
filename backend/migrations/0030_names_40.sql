UPDATE materials
SET display_name = left(display_name, 40)
WHERE char_length(display_name) > 40;

ALTER TABLE materials DROP CONSTRAINT materials_display_name_length;
ALTER TABLE materials ADD CONSTRAINT materials_display_name_length
    CHECK (char_length(display_name) BETWEEN 1 AND 40);

UPDATE card_sets
SET name = left(btrim(name), 40)
WHERE char_length(name) > 40;

UPDATE podcasts
SET name = left(btrim(name), 40)
WHERE char_length(name) > 40;

ALTER TABLE card_sets DROP CONSTRAINT card_sets_check;
ALTER TABLE card_sets ADD CONSTRAINT card_sets_check CHECK (
    (deleted_at IS NULL AND knowledge_structure_revision IS NOT NULL
        AND char_length(btrim(name)) BETWEEN 1 AND 40
        AND cardinality(concept_ids) > 0)
    OR (deleted_at IS NOT NULL AND knowledge_structure_revision IS NULL
        AND name = '' AND cardinality(concept_ids) = 0)
);
