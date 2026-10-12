-- 搜尋規劃、閱讀位置與缺口補查都隨原工作保存，取消／接續沿用同一個租約。
ALTER TABLE material_research ADD COLUMN search_state jsonb NOT NULL DEFAULT '{}';
