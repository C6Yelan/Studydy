-- 帳號、登入與教材原檔。
CREATE TABLE schema_migrations (
    version integer PRIMARY KEY,
    sql_sha256 character(64) NOT NULL CHECK (sql_sha256 ~ '^[0-9a-f]{64}$'),
    applied_at timestamptz NOT NULL
);

CREATE TABLE learners (
    learner_id uuid PRIMARY KEY,
    created_at timestamptz NOT NULL,
    email text UNIQUE,
    password_hash text,
    CONSTRAINT learners_credentials_pair CHECK (
        (email IS NULL AND password_hash IS NULL)
        OR (email IS NOT NULL AND password_hash IS NOT NULL
            AND char_length(email) BETWEEN 3 AND 254 AND email = lower(email))
    )
);

CREATE TABLE learner_sessions (
    session_id uuid PRIMARY KEY,
    learner_id uuid NOT NULL REFERENCES learners,
    token_sha256 bytea NOT NULL UNIQUE CHECK (octet_length(token_sha256) = 32),
    created_at timestamptz NOT NULL,
    idle_expires_at timestamptz NOT NULL,
    absolute_expires_at timestamptz NOT NULL,
    revoked_at timestamptz,
    updated_at timestamptz NOT NULL,
    CHECK (created_at <= idle_expires_at AND idle_expires_at <= absolute_expires_at)
);

CREATE TABLE materials (
    material_id uuid PRIMARY KEY,
    learner_id uuid NOT NULL REFERENCES learners,
    source_artifact_id uuid UNIQUE,
    upload_idempotency_key_sha256 bytea NOT NULL
        CHECK (octet_length(upload_idempotency_key_sha256) = 32),
    upload_request_fingerprint bytea NOT NULL CHECK (octet_length(upload_request_fingerprint) = 32),
    created_at timestamptz NOT NULL,
    display_name text NOT NULL,
    discard_requested_at timestamptz,
    head_revision text,
    CONSTRAINT materials_display_name_length CHECK (char_length(display_name) BETWEEN 1 AND 200),
    UNIQUE (learner_id, material_id),
    UNIQUE (learner_id, upload_idempotency_key_sha256)
);

CREATE TABLE artifacts (
    artifact_id uuid PRIMARY KEY,
    learner_id uuid NOT NULL,
    material_id uuid NOT NULL,
    kind text NOT NULL,
    media_type text NOT NULL,
    sha256 bytea NOT NULL CHECK (octet_length(sha256) = 32),
    size_bytes bigint NOT NULL CHECK (size_bytes BETWEEN 1 AND 104857600),
    created_at timestamptz NOT NULL,
    CONSTRAINT artifact_role_media CHECK (
        (kind = 'normalized_pdf' AND media_type = 'application/pdf')
        OR (kind = 'source_mapping' AND media_type = 'application/json')
        OR (kind = 'original' AND media_type IN (
            'application/pdf',
            'application/msword',
            'application/vnd.ms-powerpoint',
            'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            'application/vnd.openxmlformats-officedocument.presentationml.presentation',
            'text/plain',
            'text/markdown'
        ))
    ),
    UNIQUE (learner_id, material_id, artifact_id),
    FOREIGN KEY (learner_id, material_id) REFERENCES materials (learner_id, material_id)
);

ALTER TABLE materials ADD CONSTRAINT materials_source_artifact_fk
    FOREIGN KEY (learner_id, material_id, source_artifact_id)
    REFERENCES artifacts (learner_id, material_id, artifact_id)
    DEFERRABLE INITIALLY DEFERRED;
