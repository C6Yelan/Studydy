-- 舊對話保留原文；摘要只用來理解指涉，不作為教材 Evidence。
ALTER TABLE voice_conversations ADD COLUMN memory jsonb;
