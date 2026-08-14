CREATE TABLE schema_migrations (
    version integer PRIMARY KEY,
    sql_sha256 character(64) NOT NULL
        CHECK (sql_sha256 ~ '^[0-9a-f]{64}$'),
    applied_at timestamptz NOT NULL
);
