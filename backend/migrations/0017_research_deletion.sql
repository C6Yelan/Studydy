-- 隱藏查詢紀錄並保留候選中的來源關係，不移除已加入的教材與原檔。
ALTER TABLE material_research ADD COLUMN deleted_at timestamptz;
