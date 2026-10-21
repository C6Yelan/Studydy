-- 個人顯示名稱：可為空，舊帳號不需回填；前後空白與控制字元由約束拒絕。

ALTER TABLE public.learners
    ADD COLUMN display_name text,
    ADD CONSTRAINT learners_display_name_check CHECK (
        display_name IS NULL OR (
            char_length(display_name) BETWEEN 1 AND 20
            AND display_name = btrim(display_name)
            AND display_name !~ '[[:cntrl:]]'
        )
    );
