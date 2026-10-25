-- 最近一次生成結果的刪減／失敗摘要碼（錯誤碼、事件、段落位置、重點編號、理由 SHA-256 前 12 碼、頁高比例）；
-- 不含教材文字或核對理由全文。既有列為 NULL。
ALTER TABLE material_notes ADD COLUMN generation_diagnostics jsonb;
