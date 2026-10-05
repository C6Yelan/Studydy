-- 僅保存已驗證的 locator／引用；不複製或延長 Podcast script 的保存期限。
ALTER TABLE voice_turns ADD COLUMN context jsonb;
