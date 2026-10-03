-- 此版本也作為讀取端的能力界線：舊後端不可誤讀已編碼的 Evidence。
-- 既有文件不重寫；canonical 字串／revision／來源 hash 均維持原值。
ALTER TABLE knowledge_structures ADD CONSTRAINT knowledge_structure_storage_encoding CHECK (
    NOT (document ? '_studydy_json_storage')
    OR document->>'_studydy_json_storage' = 'nul-object/v1'
);
COMMENT ON COLUMN knowledge_structures.document IS
    'Canonical knowledge document; optional lossless NUL storage encoding decoded by EvidenceJSONB';
