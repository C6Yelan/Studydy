-- 登入裝置清單：每個 session 記錄登入時由 User-Agent 解析的「瀏覽器 · 系統」標籤（不存原始
-- User-Agent 與 IP，無法辨識為 NULL）與最後使用時間。兩欄皆可為空，既有 session 不回填。

ALTER TABLE public.learner_sessions
    ADD COLUMN device_label text,
    ADD COLUMN last_used_at timestamp with time zone,
    ADD CONSTRAINT learner_sessions_device_label_check CHECK (
        device_label IS NULL OR char_length(device_label) BETWEEN 1 AND 64
    );
