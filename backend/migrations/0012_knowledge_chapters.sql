-- 知識地圖章節分區：既有知識結構版本的衍生資料，不產生新版本，也不改卡片、進度、XP 或徽章。
-- 每個版本一列，同時是背景佇列：pending → running → ready／fallback／failed。
-- ready：全部窗都由模型分章並通過驗證；fallback：部分窗不合格，該部分沿用來源檔分區；
-- failed：沒有可用章節（全部不合格、provider 不可用或工作中斷），地圖退回來源檔分區。
-- chapters 依學習順序保存 [{title, concept_ids, fallback}]；failed_windows 記錄不合格窗與原因。
-- 版本被實際刪除（教材清除或舊版清理）時一併刪除。

CREATE TABLE public.knowledge_chapters (
    learner_id uuid NOT NULL,
    material_id uuid NOT NULL,
    structure_revision text NOT NULL,
    status text NOT NULL,
    policy text NOT NULL,
    chapters jsonb,
    prompt_sha256 character(64) NOT NULL,
    model_id text,
    failed_windows jsonb NOT NULL DEFAULT '[]'::jsonb,
    calls integer NOT NULL DEFAULT 0,
    attempts integer NOT NULL DEFAULT 0,
    error_code text,
    lease_token uuid,
    lease_expires_at timestamp with time zone,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL,
    CONSTRAINT knowledge_chapters_pkey PRIMARY KEY (learner_id, material_id, structure_revision),
    CONSTRAINT knowledge_chapters_structure_fkey FOREIGN KEY (learner_id, material_id, structure_revision)
        REFERENCES public.knowledge_structures(learner_id, material_id, structure_revision) ON DELETE CASCADE,
    CONSTRAINT knowledge_chapters_status_check CHECK (status = ANY (ARRAY['pending'::text, 'running'::text, 'ready'::text, 'fallback'::text, 'failed'::text])),
    CONSTRAINT knowledge_chapters_policy_check CHECK (policy = 'knowledge-chapters/v1'),
    CONSTRAINT knowledge_chapters_prompt_check CHECK (prompt_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT knowledge_chapters_chapters_check CHECK (
        (status = ANY (ARRAY['ready'::text, 'fallback'::text])) = (chapters IS NOT NULL)
        AND (chapters IS NULL OR (jsonb_typeof(chapters) = 'array' AND jsonb_array_length(chapters) >= 1))
    ),
    CONSTRAINT knowledge_chapters_failed_windows_check CHECK (jsonb_typeof(failed_windows) = 'array'),
    CONSTRAINT knowledge_chapters_lease_check CHECK ((status = 'running'::text) = (lease_token IS NOT NULL AND lease_expires_at IS NOT NULL)),
    CONSTRAINT knowledge_chapters_counts_check CHECK (calls >= 0 AND attempts >= 0)
);

CREATE INDEX knowledge_chapters_queue ON public.knowledge_chapters USING btree (created_at)
    WHERE status = ANY (ARRAY['pending'::text, 'running'::text]);
