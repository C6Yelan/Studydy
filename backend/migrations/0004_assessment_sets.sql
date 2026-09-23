-- 初篩與補強題組。
CREATE TABLE assessment_sets (
    set_id uuid PRIMARY KEY,
    learner_id uuid NOT NULL,
    material_id uuid NOT NULL,
    study_session_id uuid NOT NULL,
    knowledge_structure_revision text NOT NULL,
    target_concept_id text NOT NULL,
    kind text NOT NULL CHECK (kind IN ('diagnostic', 'remediation')),
    target_plan jsonb NOT NULL,
    requested_count integer NOT NULL CHECK (requested_count >= 0),
    runtime_lock_document jsonb NOT NULL,
    status text NOT NULL CHECK (
        status IN ('preparing', 'partial_ready', 'failed', 'ready',
                   'in_progress', 'completed', 'cancelled')
    ),
    set_version bigint NOT NULL DEFAULT 1 CHECK (set_version > 0),
    idempotency_key_sha256 bytea NOT NULL CHECK (octet_length(idempotency_key_sha256) = 32),
    request_fingerprint bytea NOT NULL CHECK (octet_length(request_fingerprint) = 32),
    action_receipts jsonb NOT NULL DEFAULT '{}',
    lease_token uuid,
    lease_expires_at timestamptz,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    sealed_at timestamptz,
    completed_at timestamptz,
    diagnostic_set_id uuid,
    UNIQUE (study_session_id, idempotency_key_sha256),
    UNIQUE (set_id, study_session_id, knowledge_structure_revision, target_concept_id),
    FOREIGN KEY (learner_id, material_id, study_session_id, knowledge_structure_revision)
        REFERENCES study_sessions (
            learner_id, material_id, study_session_id, knowledge_structure_revision
        ),
    CHECK ((lease_token IS NULL) = (lease_expires_at IS NULL)),
    CHECK (status NOT IN ('ready', 'in_progress', 'completed') OR sealed_at IS NOT NULL),
    CHECK (status NOT IN ('preparing', 'partial_ready', 'failed') OR sealed_at IS NULL)
);

CREATE UNIQUE INDEX one_active_assessment_set_per_concept
    ON assessment_sets (study_session_id, target_concept_id)
    WHERE status IN ('preparing', 'partial_ready', 'ready', 'in_progress');

CREATE TABLE assessment_set_items (
    set_id uuid NOT NULL,
    ordinal integer NOT NULL CHECK (ordinal > 0),
    study_session_id uuid NOT NULL,
    knowledge_structure_revision text NOT NULL,
    target_concept_id text NOT NULL,
    target_claim_id text NOT NULL,
    state text NOT NULL CHECK (
        state IN ('pending', 'generating', 'verified', 'published', 'failed', 'omitted')
    ),
    attempts integer NOT NULL DEFAULT 0 CHECK (attempts BETWEEN 0 AND 2),
    failure_reason text,
    prepared_document jsonb,
    assessment_revision text,
    PRIMARY KEY (set_id, ordinal),
    UNIQUE (set_id, target_claim_id),
    UNIQUE (assessment_revision),
    FOREIGN KEY (set_id, study_session_id, knowledge_structure_revision, target_concept_id)
        REFERENCES assessment_sets (
            set_id, study_session_id, knowledge_structure_revision, target_concept_id
        ),
    FOREIGN KEY (
        assessment_revision, study_session_id, knowledge_structure_revision,
        target_concept_id, target_claim_id
    ) REFERENCES assessments (
        assessment_revision, study_session_id, knowledge_structure_revision,
        target_concept_id, target_claim_id
    ),
    CHECK ((state = 'published') = (assessment_revision IS NOT NULL)),
    CHECK ((state = 'verified') = (prepared_document IS NOT NULL))
);

CREATE FUNCTION protect_published_set_item() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.assessment_revision IS NOT NULL AND NEW IS DISTINCT FROM OLD THEN
        RAISE EXCEPTION 'published assessment set item is immutable';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER assessment_set_item_immutable
    BEFORE UPDATE ON assessment_set_items
    FOR EACH ROW EXECUTE FUNCTION protect_published_set_item();

CREATE FUNCTION protect_sealed_assessment_set() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.sealed_at IS NOT NULL AND
        ROW(
            NEW.learner_id, NEW.material_id, NEW.study_session_id,
            NEW.knowledge_structure_revision, NEW.target_concept_id,
            NEW.target_plan, NEW.requested_count, NEW.runtime_lock_document,
            NEW.sealed_at
        ) IS DISTINCT FROM ROW(
            OLD.learner_id, OLD.material_id, OLD.study_session_id,
            OLD.knowledge_structure_revision, OLD.target_concept_id,
            OLD.target_plan, OLD.requested_count, OLD.runtime_lock_document,
            OLD.sealed_at
        ) THEN
        RAISE EXCEPTION 'sealed assessment set scope is immutable';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER assessment_set_scope_immutable
    BEFORE UPDATE ON assessment_sets
    FOR EACH ROW EXECUTE FUNCTION protect_sealed_assessment_set();

ALTER TABLE assessment_sets ADD CONSTRAINT remediation_origin_scope
    FOREIGN KEY (
        diagnostic_set_id, study_session_id, knowledge_structure_revision, target_concept_id
    ) REFERENCES assessment_sets (
        set_id, study_session_id, knowledge_structure_revision, target_concept_id
    );
ALTER TABLE assessment_sets ADD CONSTRAINT remediation_origin_required
    CHECK (
        (kind = 'remediation') = (diagnostic_set_id IS NOT NULL)
        AND diagnostic_set_id IS DISTINCT FROM set_id
    );

CREATE INDEX assessment_remediation_origin ON assessment_sets (diagnostic_set_id)
    WHERE diagnostic_set_id IS NOT NULL;

CREATE FUNCTION protect_assessment_set_origin() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'UPDATE' AND ROW(NEW.kind, NEW.diagnostic_set_id)
        IS DISTINCT FROM ROW(OLD.kind, OLD.diagnostic_set_id) THEN
        RAISE EXCEPTION 'assessment set origin is immutable';
    END IF;
    IF NEW.diagnostic_set_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM assessment_sets
        WHERE set_id = NEW.diagnostic_set_id AND kind = 'diagnostic'
    ) THEN
        RAISE EXCEPTION 'remediation requires a diagnostic origin';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER assessment_set_origin_immutable
    BEFORE INSERT OR UPDATE OF kind, diagnostic_set_id ON assessment_sets
    FOR EACH ROW EXECUTE FUNCTION protect_assessment_set_origin();
