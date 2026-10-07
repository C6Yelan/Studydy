-- 舊 turn 無法可靠還原輸入種類，保留 NULL 與既有流程；新 turn 必須由輸入入口指定模式。
ALTER TABLE voice_turns ADD COLUMN mode text CHECK (mode IN ('text', 'voice'));
