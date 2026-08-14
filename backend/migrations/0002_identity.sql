CREATE TABLE learners (
    learner_id uuid PRIMARY KEY,
    created_at timestamptz NOT NULL
);

CREATE TABLE learner_sessions (
    session_id uuid PRIMARY KEY,
    learner_id uuid NOT NULL REFERENCES learners (learner_id),
    token_sha256 bytea NOT NULL
        CHECK (octet_length(token_sha256) = 32),
    created_at timestamptz NOT NULL,
    idle_expires_at timestamptz NOT NULL,
    absolute_expires_at timestamptz NOT NULL,
    revoked_at timestamptz NULL,
    updated_at timestamptz NOT NULL,
    CONSTRAINT idx_sessions_resolve UNIQUE (token_sha256),
    CHECK (
        created_at <= idle_expires_at
        AND idle_expires_at <= absolute_expires_at
    )
);
