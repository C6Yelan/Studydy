CREATE TABLE materials (
    material_id uuid PRIMARY KEY,
    learner_id uuid NOT NULL REFERENCES learners (learner_id),
    source_artifact_id uuid NOT NULL UNIQUE,
    upload_idempotency_key_sha256 bytea NOT NULL
        CHECK (octet_length(upload_idempotency_key_sha256) = 32),
    upload_request_fingerprint bytea NOT NULL
        CHECK (octet_length(upload_request_fingerprint) = 32),
    created_at timestamptz NOT NULL,
    UNIQUE (learner_id, material_id),
    UNIQUE (learner_id, upload_idempotency_key_sha256)
);

CREATE TABLE artifacts (
    artifact_id uuid PRIMARY KEY,
    learner_id uuid NOT NULL,
    material_id uuid NOT NULL,
    kind text NOT NULL CHECK (kind IN ('source_pdf', 'resource_pdf')),
    media_type text NOT NULL CHECK (media_type = 'application/pdf'),
    sha256 bytea NOT NULL CHECK (octet_length(sha256) = 32),
    size_bytes bigint NOT NULL CHECK (size_bytes > 0 AND size_bytes <= 104857600),
    created_at timestamptz NOT NULL,
    UNIQUE (learner_id, material_id, artifact_id),
    FOREIGN KEY (learner_id, material_id)
        REFERENCES materials (learner_id, material_id)
);

CREATE UNIQUE INDEX idx_artifacts_one_source_pdf
    ON artifacts (learner_id, material_id)
    WHERE kind = 'source_pdf';

ALTER TABLE materials ADD CONSTRAINT materials_source_artifact_fk
    FOREIGN KEY (learner_id, material_id, source_artifact_id)
    REFERENCES artifacts (learner_id, material_id, artifact_id)
    DEFERRABLE INITIALLY DEFERRED;

CREATE TABLE study_material_outputs (
    learner_id uuid NOT NULL,
    material_id uuid NOT NULL,
    output_revision text NOT NULL,
    document jsonb NOT NULL,
    created_at timestamptz NOT NULL,
    PRIMARY KEY (learner_id, material_id, output_revision),
    FOREIGN KEY (learner_id, material_id)
        REFERENCES materials (learner_id, material_id),
    CHECK (document ? 'output_id' AND document ->> 'output_id' = output_revision)
);

CREATE TABLE knowledge_maps (
    learner_id uuid NOT NULL,
    material_id uuid NOT NULL,
    map_revision text NOT NULL,
    source_output_revision text NOT NULL,
    document jsonb NOT NULL,
    created_at timestamptz NOT NULL,
    PRIMARY KEY (learner_id, material_id, map_revision),
    FOREIGN KEY (learner_id, material_id, source_output_revision)
        REFERENCES study_material_outputs (learner_id, material_id, output_revision),
    CHECK (document ? 'revision' AND document ->> 'revision' = map_revision),
    CHECK (
        document ? 'source_output_id'
        AND document ->> 'source_output_id' = source_output_revision
    )
);

CREATE TABLE learning_paths (
    learner_id uuid NOT NULL,
    material_id uuid NOT NULL,
    path_revision text NOT NULL,
    map_revision text NOT NULL,
    document jsonb NOT NULL,
    created_at timestamptz NOT NULL,
    PRIMARY KEY (learner_id, material_id, path_revision),
    FOREIGN KEY (learner_id, material_id, map_revision)
        REFERENCES knowledge_maps (learner_id, material_id, map_revision),
    CHECK (document ? 'revision' AND document ->> 'revision' = path_revision),
    CHECK (
        document ? 'knowledge_map_revision'
        AND document ->> 'knowledge_map_revision' = map_revision
    )
);

CREATE TABLE resource_catalogs (
    learner_id uuid NOT NULL,
    material_id uuid NOT NULL,
    catalog_revision text NOT NULL,
    subject text NOT NULL CHECK (char_length(subject) BETWEEN 1 AND 128),
    document jsonb NOT NULL,
    created_at timestamptz NOT NULL,
    PRIMARY KEY (learner_id, material_id, catalog_revision),
    FOREIGN KEY (learner_id, material_id)
        REFERENCES materials (learner_id, material_id),
    CHECK (
        document ? 'catalog_revision'
        AND document ->> 'catalog_revision' = catalog_revision
    )
);

CREATE TABLE learning_resource_results (
    learner_id uuid NOT NULL,
    material_id uuid NOT NULL,
    result_revision text NOT NULL,
    source_output_revision text NOT NULL,
    catalog_revision text NOT NULL,
    document jsonb NOT NULL,
    created_at timestamptz NOT NULL,
    PRIMARY KEY (learner_id, material_id, result_revision),
    FOREIGN KEY (learner_id, material_id, source_output_revision)
        REFERENCES study_material_outputs (learner_id, material_id, output_revision),
    FOREIGN KEY (learner_id, material_id, catalog_revision)
        REFERENCES resource_catalogs (learner_id, material_id, catalog_revision),
    CHECK (document ? 'result_revision' AND document ->> 'result_revision' = result_revision),
    CHECK (
        document ? 'source_s2_revision'
        AND document ->> 'source_s2_revision' = source_output_revision
    ),
    CHECK (document ? 'catalog_revision' AND document ->> 'catalog_revision' = catalog_revision)
);

CREATE TABLE assessments (
    learner_id uuid NOT NULL,
    material_id uuid NOT NULL,
    assessment_revision text NOT NULL,
    output_revision text NOT NULL,
    map_revision text NOT NULL,
    path_revision text NOT NULL,
    public_document jsonb NOT NULL,
    answer_key_document jsonb NOT NULL,
    created_at timestamptz NOT NULL,
    PRIMARY KEY (learner_id, material_id, assessment_revision),
    FOREIGN KEY (learner_id, material_id, output_revision)
        REFERENCES study_material_outputs (learner_id, material_id, output_revision),
    FOREIGN KEY (learner_id, material_id, map_revision)
        REFERENCES knowledge_maps (learner_id, material_id, map_revision),
    FOREIGN KEY (learner_id, material_id, path_revision)
        REFERENCES learning_paths (learner_id, material_id, path_revision),
    CHECK (public_document ? 'assessment_view_id'),
    CHECK (answer_key_document ? 'assessment_id')
);
