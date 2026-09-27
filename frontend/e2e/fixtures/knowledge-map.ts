// 地圖 browser 測試共用的合成資料與 API 攔截；不呼叫真實後端或模型。
import { expect, type Page, type Route } from "@playwright/test";
import type { EvidenceView } from "../../src/api/contracts";

export const browserOrigin = process.env.STUDYDY_E2E_BASE_URL ?? "http://127.0.0.1:4173";

export const materialId = "11111111-1111-4111-8111-111111111111";

export const runId = "22222222-2222-4222-8222-222222222222";

export const sessionId = "33333333-3333-4333-8333-333333333333";

export const artifactId = "44444444-4444-4444-8444-444444444444";

export const structureRevision = `knowledge-structure:sha256:${"a".repeat(64)}`;

export const firstConcept = `concept:sha256:${"b".repeat(64)}`;

export const secondConcept = `concept:sha256:${"c".repeat(64)}`;

export const firstClaim = `claim:sha256:${"d".repeat(64)}`;

const secondClaim = `claim:sha256:${"e".repeat(64)}`;

const evidenceId = `evidence:sha256:${"1".repeat(64)}`;

export function structureView(revision = structureRevision) {
  const concept = (conceptId: string, claimId: string, label: string, page: number) => ({
    concept_id: conceptId,
    label,
    aliases: [],
    section_ids: [`section:sha256:${"2".repeat(64)}`],
    source_pages: [page],
    claims: [
      {
        claim_id: claimId,
        text:
          label === "Stack" ? "A stack follows LIFO order." : "An array stores contiguous values.",
        evidence: [
          {
            evidence_id: label === "Stack" ? evidenceId : `evidence:sha256:${"6".repeat(64)}`,
            page_ref: `page:sha256:${String(page).repeat(64)}`,
            page,
            block_order: 0,
            kind: "paragraph",
            source: "native_text",
            source_locator: {
              page,
              block_id: `block:sha256:${String(page).repeat(64)}`,
              region: [1, 2, 30, 40],
            },
            quote:
              label === "Stack"
                ? "A stack follows LIFO order."
                : "An array stores contiguous values.",
          },
        ],
      },
    ],
  });
  return {
    schema: "knowledge-structure-view/v1",
    source_resolver: `/v1/materials/${materialId}/knowledge-structures/${encodeURIComponent(revision)}/evidence`,
    material_id: `material:sha256:${"7".repeat(64)}`,
    knowledge_structure_revision: revision,
    status: { processing: "succeeded", quality: "accepted", decision: "retain", reason_codes: [] },
    document_tree: {
      material_id: `material:sha256:${"7".repeat(64)}`,
      sections: [
        {
          section_id: `section:sha256:${"2".repeat(64)}`,
          title: "Data structures",
          order: 0,
          heading_evidence_id: null,
          concept_ids: [firstConcept, secondConcept],
        },
      ],
    },
    concepts: [
      concept(firstConcept, firstClaim, "Stack", 1),
      concept(secondConcept, secondClaim, "Array", 2),
    ],
    relations: [
      {
        relation_id: `relation:sha256:${"8".repeat(64)}`,
        source_concept_id: firstConcept,
        target_concept_id: secondConcept,
        type: "prerequisite",
        learner_reason: "Stack must be learned before Array traversal.",
        evidence_refs: [evidenceId],
        context_refs: [`section:sha256:${"2".repeat(64)}`],
        inference_basis: "dependency",
        confidence: 0.9,
      },
    ],
    initial_learning_path: [
      { position: 1, concept_id: firstConcept, reason: "document_order" },
      { position: 2, concept_id: secondConcept, reason: "prerequisite" },
    ],
    excluded_pages: [],
  };
}

export const run = {
  schema: "material-processing-run/v1",
  cancel_requested_at: null,
  run_id: runId,
  material_id: materialId,
  source_artifact_id: artifactId,
  status: "succeeded",
  progress_stage: "completed",
  completed_pages: 2,
  total_pages: 2,
  error_code: null,
  created_at: "2026-09-05T00:00:00Z",
  updated_at: "2026-09-05T00:01:00Z",
  completed_at: "2026-09-05T00:01:00Z",
  output_binding: {
    schema: "material-run-output-binding/v1",
    knowledge_structure_revision: structureRevision,
    page_count: 2,
  },
};

export function session(status = "active") {
  return {
    schema: "study-session/v1",
    study_session_id: sessionId,
    material_id: materialId,
    knowledge_structure_revision: structureRevision,
    current_concept_id: firstConcept,
    deferred_concept_ids: [],
    no_safe_claim_ids: [],
    status,
    started_at: "2026-09-05T00:01:00Z",
    completed_at: status === "completed" ? "2026-09-05T00:02:00Z" : null,
    event_watermark: 0,
  };
}

export const progress = {
  schema: "learner-progress/v1",
  assessment_cycles: [],
  study_session_id: sessionId,
  knowledge_structure_revision: structureRevision,
  event_watermark: 0,
  current_concept_id: firstConcept,
  deferred_concept_ids: [],
  concept_states: [
    {
      concept_id: firstConcept,
      label: "Stack",
      status: "not_started",
      attempts: 0,
      correct_answers: 0,
      qualified_correct_items: 0,
      covered_claim_ids: [],
      mastered_claim_ids: [],
      weak_claim_ids: [],
      latest_is_correct: null,
    },
    {
      concept_id: secondConcept,
      label: "Array",
      status: "not_started",
      attempts: 0,
      correct_answers: 0,
      qualified_correct_items: 0,
      covered_claim_ids: [],
      mastered_claim_ids: [],
      weak_claim_ids: [],
      latest_is_correct: null,
    },
  ],
  weaknesses: [],
  next_action: {
    action: "assess",
    target_concept_id: firstConcept,
    target_claim_id: firstClaim,
    prerequisite_concept_ids: [],
    reason: "current_concept",
  },
  guidance_revision: `learner-guidance:sha256:${"f".repeat(64)}`,
};

export async function json(route: Route, body: unknown, status = 200) {
  await route.fulfill({ status, json: body });
}

function pathMatches(path: string) {
  return (url: URL) => decodeURIComponent(url.pathname) === decodeURIComponent(path);
}

function apiError(route: Route, reasonCode: string, status: number) {
  return json(
    route,
    {
      schema: "api-error/v1",
      request_id: materialId,
      reason_code: reasonCode,
      retryable: false,
      message: "Synthetic API request rejected.",
    },
    status,
  );
}

async function mockGet(page: Page, path: string, response: (route: Route) => unknown) {
  await page.route(pathMatches(path), (route) =>
    route.request().method() === "GET"
      ? json(route, response(route))
      : apiError(route, "METHOD_NOT_ALLOWED", 405),
  );
}

export async function mockKnowledgeMapApi(
  page: Page,
  view = structureView(),
  readProgress?: () => typeof progress,
) {
  const revision = view.knowledge_structure_revision;
  const mapPath = `/v1/materials/${materialId}/knowledge-structures/${revision}`;
  const resolverPath = `${mapPath}/evidence`;
  const boundRun = {
    ...run,
    output_binding: { ...run.output_binding, knowledge_structure_revision: revision },
  };
  let currentConceptId = view.concepts[0]?.concept_id ?? null;
  const getProgress =
    readProgress ??
    (() => ({
      ...progress,
      knowledge_structure_revision: revision,
      current_concept_id: currentConceptId,
      concept_states: view.concepts.map((concept) => ({
        ...progress.concept_states[0],
        concept_id: concept.concept_id,
        label: concept.label,
      })),
      next_action: {
        ...progress.next_action,
        target_concept_id: currentConceptId,
        target_claim_id:
          view.concepts.find((concept) => concept.concept_id === currentConceptId)?.claims[0]
            ?.claim_id ?? null,
      },
    }));

  // 未設定的 API、教材／版本／題組 ID 回傳 404，不能由 wildcard 假裝成功。
  await page.route(
    (url) => /^\/v[12]\//.test(url.pathname),
    (route) => apiError(route, "RESOURCE_NOT_FOUND", 404),
  );
  await mockGet(page, "/v1/source-capabilities", () => ({
    schema: "source-capabilities/v1",
    quality_notice: "PDF 優先",
    formats: [{ extension: ".pdf", media_type: "application/pdf", max_bytes: 104857600 }],
  }));
  await page.route(pathMatches("/v1/session"), (route) => {
    if (route.request().method() === "GET")
      return json(route, { schema: "learner-identity/v1", learner_id: sessionId });
    if (route.request().method() === "DELETE") return route.fulfill({ status: 204 });
    return apiError(route, "METHOD_NOT_ALLOWED", 405);
  });
  await page.route(pathMatches("/v1/session/refresh"), (route) =>
    route.request().method() === "POST"
      ? json(route, { schema: "learner-identity/v1", learner_id: sessionId })
      : apiError(route, "METHOD_NOT_ALLOWED", 405),
  );
  await mockGet(page, `/v1/materials/${materialId}`, () => ({
    schema: "material-library-item/v1",
    material_id: materialId,
    source_artifact_id: artifactId,
    display_name: "Data structures.pdf",
    size_bytes: 100,
    created_at: run.created_at,
    latest_attempt: boundRun,
    available_structures: [
      {
        run_id: runId,
        knowledge_structure_revision: revision,
        created_at: run.created_at,
        status: "succeeded",
      },
    ],
    study_sessions: [],
  }));
  await mockGet(page, `/v1/material-processing-runs/${runId}`, () => boundRun);
  await mockGet(page, mapPath, () => view);
  await page.route(
    (url) => {
      const path = decodeURIComponent(url.pathname);
      return path.startsWith(`${resolverPath}/`) && path.endsWith("/source");
    },
    (route) => {
      if (route.request().method() !== "GET") return apiError(route, "METHOD_NOT_ALLOWED", 405);
      const identity = decodeURIComponent(new URL(route.request().url()).pathname)
        .split("/")
        .at(-2);
      const evidence = view.concepts
        .flatMap((concept) => concept.claims.flatMap((claim) => claim.evidence))
        .find((item) => item.evidence_id === identity) as EvidenceView | undefined;
      if (!evidence) return apiError(route, "RESOURCE_NOT_FOUND", 404);
      const pageNumber = evidence.normalized_page ?? evidence.page;
      return json(route, {
        schema: "evidence-source/v1",
        format: "pdf",
        original_name: evidence.source_name ?? "Synthetic.pdf",
        original_url: `/v1/artifacts/${artifactId}/download`,
        preview_url: `/v1/artifacts/${artifactId}#page=${pageNumber}`,
        normalized_page: pageNumber,
        accuracy: "exact",
        origin_locators: [],
        label: `PDF 第 ${pageNumber} 頁`,
      });
    },
  );
  await mockGet(page, `/v1/study-sessions/${sessionId}/progress`, getProgress);
  await page.route(pathMatches("/v1/study-sessions"), (route) => {
    if (route.request().method() !== "POST") return apiError(route, "METHOD_NOT_ALLOWED", 405);
    const body = route.request().postDataJSON();
    if (
      body.material_id !== materialId ||
      body.knowledge_structure_revision !== revision ||
      !view.concepts.some((concept) => concept.concept_id === body.current_concept_id)
    ) {
      return apiError(route, "RESOURCE_NOT_FOUND", 404);
    }
    currentConceptId = body.current_concept_id;
    return json(
      route,
      {
        ...session(),
        knowledge_structure_revision: revision,
        current_concept_id: currentConceptId,
      },
      201,
    );
  });
  await page.route(pathMatches(`/v1/study-sessions/${sessionId}/assessment-plan`), (route) => {
    if (route.request().method() !== "GET") return apiError(route, "METHOD_NOT_ALLOWED", 405);
    const concept = view.concepts.find(
      (item) => item.concept_id === new URL(route.request().url()).searchParams.get("concept_id"),
    );
    if (!concept) return apiError(route, "RESOURCE_NOT_FOUND", 404);
    return json(route, {
      schema: "assessment-plan/v1",
      study_session_id: sessionId,
      knowledge_structure_revision: revision,
      policy: "single-concept-grounded-points/v1",
      concept_id: concept.concept_id,
      point_count: concept.claims.length,
      requested_count: concept.claims.length,
      targets: concept.claims.map((claim) => ({
        claim_id: claim.claim_id,
        covered_claim_ids: [claim.claim_id],
        reason: "distinct_grounded_point",
      })),
      excluded: [],
    });
  });
  await mockGet(page, `${mapPath}/study-sessions/${sessionId}/resume`, () => {
    const currentProgress = getProgress();
    return {
      schema: "study-resume/v1",
      assessment_sets: [],
      selected_set_id: null,
      session: {
        ...session(),
        knowledge_structure_revision: revision,
        current_concept_id: currentProgress.current_concept_id,
        event_watermark: currentProgress.event_watermark,
      },
      run_id: runId,
      source_artifact_id: artifactId,
      knowledge_structure: view,
      progress: currentProgress,
    };
  });
}

export function navigationConcept(page: Page, label: string) {
  return page
    .getByRole("navigation", { name: "學習導覽", includeHidden: true })
    .getByRole("button", { includeHidden: true })
    .filter({ has: page.getByText(label, { exact: true }) });
}

export async function openMapConcept(page: Page, label?: string) {
  if (await page.getByRole("dialog", { name: /^(概念詳情|關係詳情)$/ }).count())
    await page.keyboard.press("Escape");
  if (label) {
    const toggle = page.getByRole("button", { name: "學習導覽", exact: true });
    if ((await toggle.getAttribute("aria-expanded")) !== "true") await toggle.click();
    await navigationConcept(page, label).click();
  } else {
    await page.locator(".concept-flow-node.is-focus").click();
  }
  await expect(page.getByRole("dialog", { name: "概念詳情", exact: true })).toBeVisible();
}

export function workspaceView(count: number, longNames = false) {
  const view = structureView();
  const seed = view.concepts[0];
  view.concepts = Array.from({ length: count }, (_, index) => ({
    ...seed,
    concept_id: `concept:sha256:${(index + 10).toString(16).padStart(64, "0")}`,
    label: `Concept ${index + 1}${longNames && index % 3 === 0 ? " — 時間與資料結構的跨章節概念_" + "LongTechnicalConceptName".repeat(3) : ""}`,
    aliases: index === 29 ? ["target-alias"] : [],
    section_ids: [`section:sha256:${(Math.floor(index / 6) + 10).toString(16).padStart(64, "0")}`],
    claims: [
      {
        ...seed.claims[0],
        claim_id: `claim:sha256:${(index + 100).toString(16).padStart(64, "0")}`,
        text:
          index === 29
            ? "A searchable unique learning point."
            : `Learning point ${index + 1}: read the source and explore how these ideas connect.`,
      },
    ],
  }));
  view.document_tree.sections = Array.from({ length: Math.ceil(count / 6) }, (_, index) => ({
    section_id: view.concepts[index * 6].section_ids[0],
    title: `Section ${index + 1}`,
    order: index,
    heading_evidence_id: null,
    concept_ids: view.concepts.slice(index * 6, index * 6 + 6).map((concept) => concept.concept_id),
  }));
  view.initial_learning_path = view.concepts.map((concept, index) => ({
    position: index + 1,
    concept_id: concept.concept_id,
    reason: "document_order",
  }));
  view.relations = Array.from({ length: count - 1 }, (_, index) => ({
    ...view.relations[0],
    relation_id: `relation:sha256:${(index + 100).toString(16).padStart(64, "0")}`,
    source_concept_id: view.concepts[index < 8 ? 0 : index].concept_id,
    target_concept_id: view.concepts[index + 1].concept_id,
    learner_reason: `Connection ${index + 1}: the source explains why these concepts belong together in this section.`,
    context_refs: view.concepts[index + 1].section_ids,
  }));
  return view;
}

export function learningMap(count: number, reordered = false) {
  const view = workspaceView(count);
  view.concepts.slice(0, 4).forEach((concept, index) => {
    concept.label = ["A", "B", "C", "D"][index];
  });
  view.concepts[3].aliases = ["navigation-target"];
  const sections = [0, 1].map((index) => ({
    section_id: `section:sha256:${String(index + 7).repeat(64)}`,
    title: `Section ${index ? "B" : "A"}`,
    order: index,
    heading_evidence_id: null,
    concept_ids: [] as string[],
  }));
  view.concepts.forEach((concept, index) => {
    const group = index < 4 ? index % 2 : 2 + Math.floor((index - 4) / 6);
    sections[group] ??= {
      section_id: `section:sha256:${(group + 50).toString(16).padStart(64, "0")}`,
      title: `Section ${group + 1}`,
      order: group,
      heading_evidence_id: null,
      concept_ids: [],
    };
    concept.section_ids = [sections[group].section_id];
    sections[group].concept_ids.push(concept.concept_id);
  });
  view.document_tree.sections = sections;
  const order = reordered
    ? [2, 0, 1, ...Array.from({ length: count - 3 }, (_, index) => index + 3)]
    : Array.from({ length: count }, (_, index) => index);
  view.initial_learning_path = order.map((index, position) => ({
    position: position + 1,
    concept_id: view.concepts[index].concept_id,
    reason: position === 1 || position === 2 ? "prerequisite" : "document_order",
  }));
  const seed = view.relations[0];
  view.relations = order.slice(1).map((target, index) => ({
    ...seed,
    relation_id: `relation:sha256:${(index + 300).toString(16).padStart(64, "0")}`,
    source_concept_id: view.concepts[order[index]].concept_id,
    target_concept_id: view.concepts[target].concept_id,
    type: index < 2 ? "prerequisite" : "example",
    inference_basis: index < 2 ? "dependency" : "instantiation",
    context_refs: view.concepts[target].section_ids,
    learner_reason: "教材中的直接關係。",
  }));
  return view;
}

export async function mockLearningMapApi(
  page: Page,
  view: ReturnType<typeof structureView>,
  hasProgress: boolean,
  action = "advance",
  currentIndex = 2,
  nextIndex = 3,
) {
  const currentId = view.concepts[currentIndex].concept_id;
  const revision = view.knowledge_structure_revision;
  const saved = {
    ...session(),
    knowledge_structure_revision: revision,
    current_concept_id: currentId,
  };
  const snapshot = {
    ...progress,
    knowledge_structure_revision: revision,
    current_concept_id: currentId,
    concept_states: view.concepts.map((concept, index) => ({
      ...progress.concept_states[0],
      concept_id: concept.concept_id,
      label: concept.label,
      status:
        index < 2
          ? "mastered"
          : index === 2 || index === 5
            ? "learning"
            : index === 4
              ? "needs_review"
              : "not_started",
      mastered_claim_ids: index < 2 ? [concept.claims[0].claim_id] : [],
      weak_claim_ids: index === 4 ? [concept.claims[0].claim_id] : [],
    })),
    next_action: {
      ...progress.next_action,
      action,
      target_concept_id: view.concepts[nextIndex].concept_id,
      target_claim_id: null,
    },
  };
  await mockKnowledgeMapApi(page, view, () => snapshot);
  await mockGet(page, `/v1/materials/${materialId}`, () => ({
    schema: "material-library-item/v1",
    material_id: materialId,
    source_artifact_id: artifactId,
    display_name: "Navigation.pdf",
    size_bytes: 100,
    created_at: run.created_at,
    latest_attempt: {
      ...run,
      output_binding: { ...run.output_binding, knowledge_structure_revision: revision },
    },
    available_structures: [
      {
        run_id: runId,
        knowledge_structure_revision: revision,
        status: "succeeded",
        created_at: run.created_at,
      },
    ],
    study_sessions: hasProgress ? [{ ...saved, run_id: runId }] : [],
  }));
  await mockGet(
    page,
    `/v1/materials/${materialId}/knowledge-structures/${revision}/study-sessions/${sessionId}/resume`,
    () => ({
      schema: "study-resume/v1",
      assessment_sets: [],
      selected_set_id: null,
      session: saved,
      run_id: runId,
      source_artifact_id: artifactId,
      knowledge_structure: view,
      progress: snapshot,
    }),
  );
}
