-- 先讀後選的論文原檔；與研究狀態同交易保存，避免選取時重新下載另一份 bytes。
CREATE TABLE material_research_documents (
    research_id uuid NOT NULL REFERENCES material_research(research_id) ON DELETE CASCADE,
    sha256 bytea NOT NULL CHECK (octet_length(sha256) = 32),
    size_bytes bigint NOT NULL CHECK (size_bytes > 0 AND size_bytes <= 94371840),
    content bytea NOT NULL,
    PRIMARY KEY (research_id, sha256),
    CHECK (octet_length(content) = size_bytes)
);
