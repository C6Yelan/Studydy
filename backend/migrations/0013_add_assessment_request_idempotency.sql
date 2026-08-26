ALTER TABLE assessments
    ADD COLUMN request_idempotency_key_sha256 bytea,
    ADD COLUMN request_fingerprint bytea,
    ADD CONSTRAINT assessments_request_identity_pair
    CHECK (
        (request_idempotency_key_sha256 IS NULL AND request_fingerprint IS NULL)
        OR (
            octet_length(request_idempotency_key_sha256) = 32
            AND octet_length(request_fingerprint) = 32
        )
    ),
    ADD CONSTRAINT assessments_request_idempotency_unique
    UNIQUE (study_session_id, request_idempotency_key_sha256);
