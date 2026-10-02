-- 編輯使用版本檢查，避免兩個頁面互相覆蓋已保存的卡組。
ALTER TABLE card_sets ADD COLUMN version bigint NOT NULL DEFAULT 1 CHECK (version >= 1);
