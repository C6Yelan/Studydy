-- 僅擴充原檔 MIME；不改寫既有來源、轉檔、KS 或學習紀錄。
ALTER TABLE artifacts DROP CONSTRAINT artifact_role_media;
ALTER TABLE artifacts ADD CONSTRAINT artifact_role_media CHECK (
 (kind IN ('source_pdf','normalized_pdf') AND media_type='application/pdf') OR
 (kind='source_mapping' AND media_type='application/json') OR
 (kind='original' AND media_type IN (
  'application/pdf','application/msword','application/vnd.ms-powerpoint',
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  'application/vnd.openxmlformats-officedocument.presentationml.presentation','text/plain','text/markdown'
 ))
);
