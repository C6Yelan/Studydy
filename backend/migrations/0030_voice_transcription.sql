-- 保留辨識片段與實際使用的詞彙提示，供語音問題理解與誤辨回查；不保存原始錄音。
ALTER TABLE voice_turns ADD COLUMN transcription jsonb;
