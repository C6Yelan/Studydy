UPDATE materials
SET display_name = left(display_name, 40)
WHERE char_length(display_name) > 40;

ALTER TABLE materials DROP CONSTRAINT materials_display_name_length;
ALTER TABLE materials ADD CONSTRAINT materials_display_name_length
    CHECK (char_length(display_name) BETWEEN 1 AND 40);
