-- 張數由圖卡的實際分頁計算後保存；NULL 表示尚未完成排版，不以概念數代替。
ALTER TABLE card_sets ADD COLUMN card_count integer CHECK (card_count > 0);
