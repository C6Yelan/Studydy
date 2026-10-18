-- 教材檢索及按需讀取投影；canonical 內容仍為資料權威。

CREATE TABLE public.card_source_concepts (
    learner_id uuid NOT NULL,
    material_id uuid NOT NULL,
    structure_revision text NOT NULL,
    concept_id text NOT NULL,
    "position" integer NOT NULL,
    payload jsonb NOT NULL,
    content_sha256 text NOT NULL
);

CREATE TABLE public.card_source_indexes (
    learner_id uuid NOT NULL,
    material_id uuid NOT NULL,
    structure_revision text NOT NULL,
    metadata jsonb NOT NULL,
    content_sha256 text NOT NULL
);

CREATE TABLE public.knowledge_evidence_refs (
    learner_id uuid NOT NULL,
    material_id uuid NOT NULL,
    structure_revision text NOT NULL,
    evidence_id text NOT NULL,
    payload jsonb NOT NULL,
    content_sha256 text NOT NULL
);

CREATE TABLE public.knowledge_read_indexes (
    learner_id uuid NOT NULL,
    material_id uuid NOT NULL,
    structure_revision text NOT NULL,
    outline jsonb NOT NULL,
    outline_sha256 text NOT NULL,
    learning jsonb NOT NULL,
    learning_sha256 text NOT NULL
);

CREATE TABLE public.material_passages (
    learner_id uuid NOT NULL,
    material_id uuid NOT NULL,
    structure_revision text NOT NULL,
    "position" integer NOT NULL,
    evidence_id text NOT NULL,
    source_id uuid NOT NULL,
    page integer NOT NULL,
    section_id text NOT NULL,
    concept_ids text[] NOT NULL,
    payload jsonb NOT NULL,
    keywords tsvector NOT NULL,
    CONSTRAINT material_passages_position_check CHECK (("position" >= 0))
);

CREATE TABLE public.material_retrieval_indexes (
    learner_id uuid NOT NULL,
    material_id uuid NOT NULL,
    structure_revision text NOT NULL,
    policy text NOT NULL,
    passage_count integer NOT NULL,
    content_sha256 text NOT NULL,
    outline jsonb NOT NULL,
    created_at timestamp with time zone NOT NULL,
    CONSTRAINT material_retrieval_indexes_content_sha256_check CHECK ((content_sha256 ~ '^[a-f0-9]{64}$'::text)),
    CONSTRAINT material_retrieval_indexes_passage_count_check CHECK ((passage_count > 0)),
    CONSTRAINT material_retrieval_indexes_policy_check CHECK ((policy = 'evidence-passages/v1'::text))
);

ALTER TABLE ONLY public.card_source_concepts
    ADD CONSTRAINT card_source_concepts_pkey PRIMARY KEY (learner_id, material_id, structure_revision, concept_id);

ALTER TABLE ONLY public.card_source_indexes
    ADD CONSTRAINT card_source_indexes_pkey PRIMARY KEY (learner_id, material_id, structure_revision);

ALTER TABLE ONLY public.knowledge_evidence_refs
    ADD CONSTRAINT knowledge_evidence_refs_pkey PRIMARY KEY (learner_id, material_id, structure_revision, evidence_id);

ALTER TABLE ONLY public.knowledge_read_indexes
    ADD CONSTRAINT knowledge_read_indexes_pkey PRIMARY KEY (learner_id, material_id, structure_revision);

ALTER TABLE ONLY public.material_passages
    ADD CONSTRAINT material_passages_pkey PRIMARY KEY (learner_id, material_id, structure_revision, "position");

ALTER TABLE ONLY public.material_retrieval_indexes
    ADD CONSTRAINT material_retrieval_indexes_pkey PRIMARY KEY (learner_id, material_id, structure_revision);

CREATE INDEX material_passages_concepts ON public.material_passages USING gin (concept_ids);

CREATE INDEX material_passages_evidence ON public.material_passages USING btree (learner_id, material_id, structure_revision, evidence_id);

CREATE INDEX material_passages_terms ON public.material_passages USING gin (keywords);
