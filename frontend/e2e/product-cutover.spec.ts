const browserOrigin = process.env.STUDYDY_E2E_BASE_URL ?? "http://127.0.0.1:4173";
import { expect, test, type Page, type Route } from "@playwright/test";

const materialId = "11111111-1111-4111-8111-111111111111";
const runId = "22222222-2222-4222-8222-222222222222";
const sessionId = "33333333-3333-4333-8333-333333333333";
const artifactId = "44444444-4444-4444-8444-444444444444";
const structureRevision = `knowledge-structure:sha256:${"a".repeat(64)}`;
const firstConcept = `concept:sha256:${"b".repeat(64)}`;
const secondConcept = `concept:sha256:${"c".repeat(64)}`;
const firstClaim = `claim:sha256:${"d".repeat(64)}`;
const secondClaim = `claim:sha256:${"e".repeat(64)}`;
const evidenceId = `evidence:sha256:${"1".repeat(64)}`;

function structureView() {
  const concept = (conceptId: string, claimId: string, label: string, page: number) => ({
    concept_id: conceptId,
    label,
    aliases: [],
    section_ids: [`section:sha256:${"2".repeat(64)}`],
    source_pages: [page],
    claims: [{
      claim_id: claimId,
      text: label === "Stack" ? "A stack follows LIFO order." : "An array stores contiguous values.",
      evidence: [{
        evidence_id: label === "Stack" ? evidenceId : `evidence:sha256:${"6".repeat(64)}`,
        page_ref: `page:sha256:${String(page).repeat(64)}`,
        page,
        block_order: 0,
        kind: "paragraph",
        source: "native_text",
        source_locator: { page, block_id: `block:sha256:${String(page).repeat(64)}`, region: [1, 2, 30, 40] },
        quote: label === "Stack" ? "A stack follows LIFO order." : "An array stores contiguous values.",
      }],
    }],
  });
  return {
    schema: "knowledge-structure-view/v3", source_resolver: "/v2/materials/11111111-1111-4111-8111-111111111111/knowledge-structures/fixture/evidence",
    material_id: `material:sha256:${"7".repeat(64)}`,
    knowledge_structure_revision: structureRevision,
    status: { processing: "succeeded", quality: "accepted", decision: "retain", reason_codes: [] },
    document_tree: {
      material_id: `material:sha256:${"7".repeat(64)}`,
      sections: [{ section_id: `section:sha256:${"2".repeat(64)}`, title: "Data structures", order: 0, heading_evidence_id: null, concept_ids: [firstConcept, secondConcept] }],
    },
    concepts: [concept(firstConcept, firstClaim, "Stack", 1), concept(secondConcept, secondClaim, "Array", 2)],
    relations: [{
      relation_id: `relation:sha256:${"8".repeat(64)}`,
      source_concept_id: firstConcept,
      target_concept_id: secondConcept,
      type: "prerequisite",
      learner_reason: "Stack must be learned before Array traversal.",
      evidence_refs: [evidenceId],
      context_refs: [`section:sha256:${"2".repeat(64)}`],
      inference_basis: "dependency",
      confidence: 0.9,
    }],
    initial_learning_path: [
      { position: 1, concept_id: firstConcept, reason: "document_order" },
      { position: 2, concept_id: secondConcept, reason: "prerequisite" },
    ],
    excluded_pages: [],
  };
}

const run = {
  schema: "material-processing-run/v6", cancel_requested_at: null, run_id: runId, material_id: materialId,
  source_artifact_id: artifactId, status: "succeeded", progress_stage: "completed",
  completed_pages: 2, total_pages: 2, error_code: null,
  created_at: "2026-09-05T00:00:00Z", updated_at: "2026-09-05T00:01:00Z", completed_at: "2026-09-05T00:01:00Z",
  output_binding: {
    schema: "material-run-output-binding/v4", knowledge_structure_revision: structureRevision,
    runtime_lock_sha256: "9".repeat(64), page_count: 2, processing: "succeeded",
    quality: "accepted", decision: "retain", reason_codes: [], ocr_calls: 0, semantic_calls: 1,
  },
};

function session(status = "active") {
  return {
    schema: "study-session/v2", study_session_id: sessionId, material_id: materialId,
    knowledge_structure_revision: structureRevision, current_concept_id: firstConcept,
    deferred_concept_ids: [], no_safe_claim_ids: [], status, started_at: "2026-09-05T00:01:00Z",
    completed_at: status === "completed" ? "2026-09-05T00:02:00Z" : null, event_watermark: 0,
  };
}

const progress = {
  schema: "learner-progress/v4", assessment_cycles: [], study_session_id: sessionId,
  knowledge_structure_revision: structureRevision, event_watermark: 0,
  current_concept_id: firstConcept, deferred_concept_ids: [],
  concept_states: [
    { concept_id: firstConcept, label: "Stack", status: "not_started", attempts: 0, correct_answers: 0, qualified_correct_items: 0, covered_claim_ids: [], mastered_claim_ids: [], weak_claim_ids: [], latest_is_correct: null },
    { concept_id: secondConcept, label: "Array", status: "not_started", attempts: 0, correct_answers: 0, qualified_correct_items: 0, covered_claim_ids: [], mastered_claim_ids: [], weak_claim_ids: [], latest_is_correct: null },
  ],
  weaknesses: [], next_action: { action: "assess", target_concept_id: firstConcept, target_claim_id: firstClaim, prerequisite_concept_ids: [], reason: "current_concept" },
  guidance_revision: `learner-guidance:sha256:${"f".repeat(64)}`,
};

async function json(route: Route, body: unknown, status = 200) {
  await route.fulfill({ status, json: body });
}

async function routes(page: Page, view = structureView(), readProgress = () => progress) {
  await page.route("**/v2/materials/*/knowledge-structures/*/evidence/*/source", route=>json(route,{schema:"evidence-source/v1",format:"pdf",original_name:"Synthetic.pdf",original_url:`/v2/artifacts/${artifactId}`,preview_url:`/v1/artifacts/${artifactId}#page=1`,normalized_page:1,accuracy:"exact",origin_locators:[],label:"PDF 第 1 頁"}));
  await page.route("**/v2/source-capabilities", route => json(route, { schema: "source-capabilities/v1", quality_notice: "PDF 優先", formats: [
    { extension: ".pdf", media_type: "application/pdf", max_bytes: 104857600 },
  ] }));
  await page.route("**/v1/session", (route) => route.request().method() === "GET" ? json(route, { schema: "learner-identity/v1", learner_id: sessionId }) : route.fulfill({ status: 204 }));
  await page.route("**/v1/session/refresh", (route) => route.fulfill({ json: { schema: "learner-identity/v1", learner_id: "33333333-3333-4333-8333-333333333333" } }));
  await page.route(`**/v1/materials/${materialId}`, route => json(route, {
    schema: "material-library-item/v3", material_id: materialId, source_artifact_id: artifactId,
    display_name: "Data structures.pdf", size_bytes: 100, created_at: run.created_at,
    latest_attempt: run, available_structures: [{ run_id: runId, knowledge_structure_revision: structureRevision,
      created_at: run.created_at, status: "succeeded" }], study_sessions: [],
  }));
  await page.route(`**/v1/material-processing-runs/${runId}`, (route) => json(route, run));
  await page.route("**/v1/materials/*/knowledge-structures/**", (route) => json(route, view));
  await page.route("**/v1/study-sessions/*/progress", route => json(route, readProgress()));
  await page.route("**/v1/study-sessions", (route) => json(route, session(), 201));
  await page.route("**/v1/study-sessions/*/assessment-plan?*", route => {
    const concept = view.concepts.find(item => item.concept_id === new URL(route.request().url()).searchParams.get("concept_id"))!;
    return json(route, { schema: "assessment-plan/v1", study_session_id: sessionId,
      knowledge_structure_revision: structureRevision, policy: "single-concept-grounded-points/v1",
      concept_id: concept.concept_id, point_count: concept.claims.length, requested_count: concept.claims.length,
      targets: concept.claims.map(claim => ({ claim_id: claim.claim_id, covered_claim_ids: [claim.claim_id], reason: "distinct_grounded_point" })), excluded: [] });
  });
  await page.route("**/v1/materials/*/knowledge-structures/*/study-sessions/*/resume?*", route => {
    const currentProgress = readProgress();
    return json(route, { schema: "study-resume/v5", assessment_sets: [], selected_set_id: null, session: { ...session(), event_watermark: currentProgress.event_watermark },
      run_id: runId, source_artifact_id: artifactId, knowledge_structure: view, progress: currentProgress,
 });
  });
}

function navigationConcept(page: Page, label: string) {
  return page.getByRole("navigation", { name: "學習導覽", includeHidden: true }).getByRole("button", { includeHidden: true }).filter({ has: page.getByText(label, { exact: true }) });
}

async function openMapConcept(page: Page, label?: string) {
  if (await page.getByRole("dialog", { name: /^(概念詳情|關係詳情)$/ }).count()) await page.keyboard.press("Escape");
  if (label) {
    const toggle = page.getByRole("button", { name: "學習導覽", exact: true });
    if (await toggle.getAttribute("aria-expanded") !== "true") await toggle.click();
    await navigationConcept(page, label).click();
  } else {
    await page.locator(".concept-flow-node.is-focus").click();
  }
  await expect(page.getByRole("dialog", { name: "概念詳情", exact: true })).toBeVisible();
}

async function openFocusRelation(page: Page, _mobile: boolean) {
  if (await page.getByRole("dialog", { name: "概念詳情" }).count()) await page.keyboard.press("Escape");
  const edge = page.locator(".focus-graph .concept-flow-edge").first();
  await edge.focus(); await page.keyboard.press("Enter");
  return edge;
}

test("map opens concept details and source-backed learning on demand", async ({ page }) => {
  await routes(page);
  await page.route("**/v1/study-sessions", route => {
    expect(route.request().postDataJSON()).toEqual({ schema: "study-session-create/v2", material_id: materialId,
      knowledge_structure_revision: structureRevision, current_concept_id: firstConcept });
    return json(route, session(), 201);
  });
  await page.context().route(`**/v1/artifacts/${artifactId}`, route => route.fulfill({ contentType: "text/plain", body: "Synthetic source document" }));
  await page.goto(`/materials/${materialId}/runs/${runId}`);
  await page.getByRole("button", { name: "開啟知識地圖", exact: true }).click();
  await expect(page).toHaveURL(`${browserOrigin}/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}`);
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(page.getByRole("region", { name: "學習入口" })).toHaveCount(0);
  await expect(page.locator(".react-flow__node")).toHaveCount(2);
  const edge = await openFocusRelation(page, false);
  await expect(page.getByRole("dialog", { name: "關係詳情" })).toContainText("Stack must be learned before Array traversal.");
  await page.keyboard.press("Escape"); await expect(edge).toBeFocused();
  await openMapConcept(page, "Stack");
  await page.getByRole("button", { name: /查看第 1 頁來源/ }).click();
  const popup = page.waitForEvent("popup");
  await page.getByRole("link",{name:"開啟 PDF 來源頁"}).click();
  const source = await popup;
  await expect(source).toHaveURL(`${browserOrigin}/v1/artifacts/${artifactId}#page=1`);
  await source.close();
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "開始學習", exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`/study-sessions/${sessionId}$`));
});

test("mobile map has a modal detail drawer with keyboard focus and source links", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await routes(page);
  await page.goto(`/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}`);
  await page.getByRole("tab", { name: "概念地圖" }).click();
  await openMapConcept(page);
  const dialog = page.getByRole("dialog", { name: "概念詳情" });
  await expect(dialog).toBeInViewport();
  await expect(dialog.getByRole("button", { name: /查看第 1 頁來源/ })).toBeVisible();
  await expect.poll(() => dialog.evaluate(element => element.matches(":modal"))).toBe(true);
  await page.keyboard.press("Tab");
  await expect.poll(() => page.evaluate(() => !!document.activeElement?.closest(".detail-panel"))).toBe(true);
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  await expect(page.locator(".concept-flow-node.is-focus")).toBeFocused();
  await openFocusRelation(page, true);
  await expect(page.getByRole("dialog", { name: "關係詳情" })).toContainText("Stack must be learned before Array traversal.");
});

test("library loading, read failure and empty state retain usable actions", async ({ page }) => {
  await routes(page);
  let release: (() => void) | undefined;
  const ready = new Promise<void>(resolve => { release = resolve; });
  let failRead = true;
  await page.route("**/v1/materials", async route => {
    if (failRead) {
      await ready;
      return json(route, { schema: "api-error/v1", request_id: sessionId, reason_code: "STORAGE_UNAVAILABLE", retryable: true, message: "Request could not be completed." }, 503);
    }
    return json(route, { schema: "material-library/v2", materials: [] });
  });
  await page.goto("/materials");
  await expect(page.getByRole("heading", { name: "正在讀取教材庫", exact: true })).toBeVisible();
  release!();
  await expect(page.getByRole("heading", { name: "無法讀取教材", exact: true })).toBeVisible();
  failRead = false;
  await page.getByRole("button", { name: "重新讀取", exact: true }).click();
  await expect(page.getByRole("heading", { name: "尚未有學習教材", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "上傳第一份教材", exact: true }).click();
  await expect(page).toHaveURL(/\/upload$/);
  await expect(page.locator('input[type="file"]')).toHaveCount(1);
});

test("reopen rejects a run from a different Knowledge Structure revision", async ({ page }) => {
  await routes(page);
  await page.route(`**/v1/materials/${materialId}`, route => json(route, {
    schema: "material-library-item/v3", material_id: materialId, source_artifact_id: artifactId,
    display_name: "Data structures.pdf", size_bytes: 100, created_at: run.created_at,
    latest_attempt: run, available_structures: [{ run_id: runId, knowledge_structure_revision: structureRevision,
      created_at: run.created_at, status: "succeeded" }], study_sessions: [],
  }));
  await page.route(`**/v1/material-processing-runs/${runId}`, route => json(route, {
    ...run, output_binding: { ...run.output_binding, knowledge_structure_revision: `knowledge-structure:sha256:${"7".repeat(64)}` },
  }));
  await page.route("**/v1/materials", route => json(route, { schema: "material-library/v2", materials: [] }));
  await page.goto(`/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}`);
  await expect(page.getByRole("heading", { name: "無法讀取知識地圖", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "查看學習成果", exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "返回教材庫", exact: true }).click();
  await expect(page.getByRole("heading", { name: "尚未有學習教材", exact: true })).toBeVisible();
});

test("dashboard keeps hero and overview through a read failure and retry", async ({ page }) => {
  await routes(page);
  let unavailable = true;
  await page.route('**/v1/materials', route => unavailable
    ? json(route, { schema: 'api-error/v1', request_id: sessionId, reason_code: 'STORAGE_UNAVAILABLE', retryable: true, message: 'Request could not be completed.' }, 503)
    : json(route, { schema: 'material-library/v2', materials: [] }));
  await page.goto('/');
  await expect(page.getByRole('heading', { name: '歡迎回來！', exact: true })).toBeVisible();
  await expect(page.getByRole('alert')).toContainText('資料服務暫時無法使用');
  await expect(page.locator('.dashboard-stat strong')).toHaveText(['—', '—', '—', '—']);
  await expect(page.locator('.dashboard-hero')).toBeVisible();
  unavailable = false;
  await page.getByRole('button', { name: '重新讀取', exact: true }).click();
  await expect(page.locator('.dashboard-stat strong')).toHaveText(['0', '0', '0', '0']);
  await page.getByRole('navigation', { name: '主要導覽' }).getByRole('button', { name: /^(教材庫|我的教材)$/, exact: true }).click();
  await expect(page).toHaveURL(/\/materials$/);
  await expect(page.getByRole('region', { name: '空教材引導' })).toBeVisible();
});

test("map errors can be retried and an empty map has an actionable explanation", async ({ page }) => {
  await routes(page);
  let failed = true;
  const empty = structureView();
  empty.concepts = [];
  empty.relations = [];
  empty.initial_learning_path = [];
  empty.document_tree.sections = [];
  await page.route("**/v1/materials/*/knowledge-structures/**", route => failed
    ? json(route, { schema: "api-error/v1", request_id: materialId, reason_code: "STORAGE_UNAVAILABLE", retryable: true, message: "Request could not be completed." }, 503)
    : json(route, empty));
  await page.goto(`/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}`);
  await expect(page.getByRole("heading", { name: "無法讀取知識地圖", exact: true })).toBeVisible();
  failed = false;
  await page.getByRole("button", { name: "重新讀取", exact: true }).click();
  await expect(page.getByRole("heading", { name: "知識地圖目前是空的", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "查看處理狀態", exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`/runs/${runId}$`));
});

test("parallel relations retain separate labels, paths and details in both directions", async ({ page }) => {
  const view = structureView();
  view.relations = ["prerequisite", "part_of", "application", "example", "contrast"].map((type, index) => ({
    ...view.relations[0], type,
    relation_id: `relation:sha256:${(index + 20).toString(16).padStart(64, "0")}`,
    source_concept_id: index % 2 ? secondConcept : firstConcept,
    target_concept_id: index % 2 ? firstConcept : secondConcept,
    learner_reason: `Distinct grounded explanation for ${type}.`,
  }));
  await routes(page, view);
  await page.goto(`/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}`);
  const assertSeparate = async () => {
    await expect(page.locator(".concept-flow-edge")).toHaveCount(5);
    await expect.poll(() => page.locator(".concept-flow-edge .react-flow__edge-path").evaluateAll((paths) => new Set(paths.map((path) => path.getAttribute("d"))).size)).toBe(5);
    await expect.poll(() => page.locator(".concept-flow-edge .react-flow__edge-textbg").evaluateAll((labels) => {
      const boxes = labels.map((label) => label.getBoundingClientRect());
      return boxes.length === 5 && boxes.every((a, i) => boxes.every((b, j) => i === j || a.right <= b.left || b.right <= a.left || a.bottom <= b.top || b.bottom <= a.top));
    })).toBe(true);
  };
  await assertSeparate();
  await expect(page.locator(".focus-context-content .relation-list")).toHaveCount(0);
  for (const relation of view.relations) {
    await page.locator(`.concept-flow-edge.is-${relation.type} .react-flow__edge-textbg`).click();
    await expect(page.getByRole("dialog", { name: "關係詳情" })).toContainText(relation.learner_reason);
    await expect(page.getByRole("dialog").locator(".relation-direction strong")).toHaveText([
      view.concepts.find(concept => concept.concept_id === relation.source_concept_id)!.label,
      view.concepts.find(concept => concept.concept_id === relation.target_concept_id)!.label,
    ]);
    await assertSeparate();
    if (relation.type === "prerequisite") {
      await page.getByRole("dialog").getByRole("button", { name: /目標概念/ }).click();
      await expect(page.getByRole("dialog", { name: "概念詳情" }).getByRole("heading", { name: "Array", exact: true })).toBeVisible();
    }
    await page.keyboard.press("Escape");
    await expect(page.locator(`.concept-flow-edge.is-${relation.type}`)).toBeFocused();
  }
  await openMapConcept(page, "Array");
  await assertSeparate();
  await page.keyboard.press("Escape");
  await page.setViewportSize({ width: 900, height: 800 });
  await assertSeparate();
  await page.locator(".concept-flow-edge.is-application").focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("dialog", { name: "關係詳情" })).toContainText("Distinct grounded explanation for application.");
});

test("recovered map reads owned progress and continues the same session without creating learning", async ({ page }) => {
  await page.setViewportSize({ width: 1366, height: 768 });
  await routes(page);
  await page.route(`**/v1/materials/${materialId}`, route => json(route, {
    schema: "material-library-item/v3", material_id: materialId, source_artifact_id: artifactId,
    display_name: "Data structures.pdf", size_bytes: 100, created_at: run.created_at,
    latest_attempt: run, available_structures: [{ run_id: runId, knowledge_structure_revision: structureRevision,
      created_at: run.created_at, status: "succeeded" }], study_sessions: [
      { ...session(), run_id: runId, knowledge_structure_revision: `knowledge-structure:sha256:${"9".repeat(64)}`, started_at: "2026-09-06T00:00:00Z" },
      { ...session(), run_id: runId },
    ],
  }));
  let creates = 0;
  await page.route("**/v1/study-sessions", route => { creates += 1; return json(route, session(), 201); });
  const path = `/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}`;
  await page.goto(path);
  await openMapConcept(page);
  await expect(page.getByRole("button", { name: "繼續學習", exact: true })).toBeEnabled();
  await expect(page.getByRole("button", { name: "繼續學習", exact: true })).toBeInViewport();
  await page.screenshot({ path: "/tmp/studydy-map-workspace/1366-resumed.png", fullPage: true });
  await page.reload();
  await openMapConcept(page);
  await expect(page.getByRole("button", { name: "繼續學習", exact: true })).toBeEnabled();
  await openMapConcept(page, "Array");
  await expect(page.getByRole("region", { name: "學習入口" }).getByRole("button", { name: "從這個概念繼續", exact: true })).toBeEnabled();
  await expect(page.locator(".map-study-bar")).toHaveCount(0);
  await page.keyboard.press("Escape");
  await page.getByRole("tab", { name: "複習重點", exact: true }).click();
  await page.getByRole("button", { name: "查看學習導覽", exact: true }).click();
  await openMapConcept(page, "Stack");
  await page.getByRole("button", { name: "繼續學習", exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`/study-sessions/${sessionId}$`));
  await expect(page.getByRole("button", { name: /開始本輪 \d+ 題/ })).toBeVisible();
  expect(creates).toBe(0);
});

test("shared shell density keeps standard pages and map workspace bounded", async ({ page }) => {
  await routes(page);
  await page.route("**/v1/materials", route => json(route, { schema: "material-library/v2", materials: [] }));
  const map = `/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}`;
  for (const viewport of [{ width: 1536, height: 1024 }, { width: 390, height: 844 }]) {
    await page.setViewportSize(viewport);
    for (const [name, path, heading] of [
      ["home", "/", "歡迎回來！"], ["materials", "/materials", "我的教材"],
      ["upload", "/upload", "上傳教材"], ["study", `${map}/study-sessions/${sessionId}`, "Stack"],
      ["processing", `/materials/${materialId}/runs/${runId}`, "教材整理完成"],
      ["map", map, "知識地圖"],
    ]) {
      await page.goto(path);
      await expect(page.locator(".app-header")).toBeVisible();
      if (name === "study") await expect(page.locator(".study-session-page")).toBeVisible();
      else await expect(page.getByRole("heading", { name: heading, exact: true }).first()).toBeVisible();
      expect((await page.locator(".app-header").boundingBox())!.height).toBe(["map", "study"].includes(name) ? 56 : viewport.width > 900 ? 74 : 72);
      if (["map", "study"].includes(name)) await expect(page.locator(".app-sidebar")).toHaveCount(0);
      else {
        await expect(page.locator(".app-sidebar")).toHaveCount(1);
        await expect(page.locator(".brand small")).toHaveText("AI 智慧學習平台");
        await expect(page.locator(".account-avatar")).toHaveCount(0);
      }
      if (!["map", "home", "materials", "maps", "upload", "processing", "study"].includes(name) && viewport.width > 900) await expect(page.locator(".sidebar-helper")).toBeVisible();
      if (["home", "materials", "maps", "upload", "processing", "study"].includes(name)) await expect(page.locator(".sidebar-helper")).toHaveCount(0);
      if (["home", "materials", "processing", "upload"].includes(name)) {
        const usable = await page.locator(".app-main").evaluate(el => {
          const css = getComputedStyle(el);
          return el.getBoundingClientRect().width - parseFloat(css.paddingLeft) - parseFloat(css.paddingRight);
        });
        expect((await page.locator(".app-main > *").first().boundingBox())!.width).toBeCloseTo(Math.min(usable, 1600), 0);
      }
      await expect(page.locator(".task-page")).toHaveCount(["upload", "processing"].includes(name) ? 1 : 0);
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(viewport.width);
      await page.screenshot({ path: `/tmp/studydy-dashboard/shell-${name}-${viewport.width}.png`, fullPage: true });
    }
  }
});

function workspaceView(count = 52, longNames = false) {
  const view = structureView();
  const seed = view.concepts[0];
  view.concepts = Array.from({ length: count }, (_, index) => ({ ...seed,
    concept_id: `concept:sha256:${(index + 10).toString(16).padStart(64, "0")}`,
    label: `Concept ${index + 1}${longNames && index % 3 === 0 ? " — 時間與資料結構的跨章節概念_" + "LongTechnicalConceptName".repeat(3) : ""}`,
    aliases: index === 29 ? ["target-alias"] : [],
    section_ids: [`section:sha256:${(Math.floor(index / 6) + 10).toString(16).padStart(64, "0")}`],
    claims: [{ ...seed.claims[0], claim_id: `claim:sha256:${(index + 100).toString(16).padStart(64, "0")}`,
      text: index === 29 ? "A searchable unique learning point." : `Learning point ${index + 1}: read the source and explore how these ideas connect.` }],
  }));
  view.document_tree.sections = Array.from({ length: Math.ceil(count / 6) }, (_, index) => ({
    section_id: view.concepts[index * 6].section_ids[0], title: `Section ${index + 1}`, order: index,
    heading_evidence_id: null, concept_ids: view.concepts.slice(index * 6, index * 6 + 6).map(concept => concept.concept_id),
  }));
  view.initial_learning_path = view.concepts.map((concept, index) => ({ position: index + 1, concept_id: concept.concept_id, reason: "document_order" }));
  view.relations = Array.from({ length: count === 52 ? 47 : count - 1 }, (_, index) => ({ ...view.relations[0],
    relation_id: `relation:sha256:${(index + 100).toString(16).padStart(64, "0")}`,
    source_concept_id: view.concepts[index < 8 ? 0 : index].concept_id,
    target_concept_id: view.concepts[index + 1].concept_id,
    learner_reason: `Connection ${index + 1}: the source explains why these concepts belong together in this section.`,
  }));
  return view;
}

test("compact learning entry shares loading, starting and new-study authority with reading tabs", async ({ page }) => {
  const view = structureView();
  await routes(page, view);
  let loaded!: () => void, started!: () => void;
  const readingMaterial = new Promise<void>(resolve => { loaded = resolve; });
  const startingStudy = new Promise<void>(resolve => { started = resolve; });
  let hasHistory = false, completed = false, creates = 0;
  await page.route(`**/v1/materials/${materialId}`, async route => {
    await readingMaterial;
    return json(route, { schema: "material-library-item/v3", material_id: materialId, source_artifact_id: artifactId,
      display_name: "Data structures.pdf", size_bytes: 100, created_at: run.created_at, latest_attempt: run,
      available_structures: [{ run_id: runId, knowledge_structure_revision: structureRevision, created_at: run.created_at, status: "succeeded" }],
      study_sessions: hasHistory ? [{ ...session("completed"), run_id: runId }] : [] });
  });
  await page.route("**/v1/materials/*/knowledge-structures/*/study-sessions/*/resume?*", route => json(route, {
    schema: "study-resume/v5", assessment_sets: [], selected_set_id: null, session: session(completed ? "completed" : "active"), run_id: runId,
    source_artifact_id: artifactId, knowledge_structure: view, progress,
  }));
  await page.route("**/v1/study-sessions", async route => {
    creates++;
    expect(route.request().postDataJSON()).toEqual({ schema: "study-session-create/v2", material_id: materialId,
      knowledge_structure_revision: structureRevision, current_concept_id: firstConcept });
    await startingStudy;
    completed = false;
    return json(route, session(), 201);
  });
  const mapPath = `/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}`;
  await page.goto(mapPath);
  await openMapConcept(page);
  const entry = page.getByRole("region", { name: "學習入口" });
  await expect(entry.getByRole("button", { name: "讀取學習進度…", exact: true })).toBeDisabled();
  loaded();
  await expect(entry.getByRole("button", { name: "開始學習", exact: true })).toBeEnabled();
  await entry.getByRole("button", { name: "開始學習", exact: true }).click();
  await expect(entry.getByRole("button", { name: "正在開始…", exact: true })).toBeDisabled();
  await expect.poll(() => creates).toBe(1);
  started();
  await expect(page).toHaveURL(new RegExp(`/study-sessions/${sessionId}$`));
  await expect(page.getByRole("button", { name: /開始本輪 \d+ 題/ })).toBeVisible();
  hasHistory = true; completed = true;
  await page.goto(mapPath);
  await openMapConcept(page);
  await expect(entry.getByRole("button", { name: "查看學習成果", exact: true })).toBeEnabled();
  await expect(page.locator(".map-study-bar")).toHaveCount(0);
  for (const name of ["複習重點"]) {
    await page.keyboard.press("Escape");
    await page.getByRole("tab", { name, exact: true }).click();
    await expect(page.getByRole("complementary", { name: "Studydy 學習引導" })).toHaveCount(0);
    await expect(entry).toHaveCount(0);
    await page.getByRole("button", { name: "查看學習導覽", exact: true }).click();
    await openMapConcept(page);
  }
  await page.getByRole("button", { name: "查看學習成果", exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`/study-sessions/${sessionId}$`));
  expect(creates).toBe(1);
});

for (const viewport of [{ width: 1536, height: 1024 }, { width: 1366, height: 768 }, { width: 390, height: 844 }]) {
  for (const history of ["none", "same", "different"] as const) {
    test(`Focus study targets the selected concept with ${history} session at ${viewport.width}px`, async ({ page }) => {
      await page.setViewportSize(viewport);
      const view = structureView();
      view.concepts[0].label = "指標"; view.concepts[1].label = "陣列";
      view.concepts[0].claims[0].text = view.concepts[0].claims[0].evidence[0].quote = "指標保存記憶體位址。";
      const currentId = history === "same" ? secondConcept : firstConcept;
      const saved = { ...session(), current_concept_id: currentId };
      const savedProgress = { ...progress, current_concept_id: currentId,
        next_action: { ...progress.next_action, target_concept_id: currentId, target_claim_id: history === "same" ? secondClaim : firstClaim } };
      await routes(page, view, () => savedProgress);
      await page.route(`**/v1/materials/${materialId}`, route => json(route, {
        schema: "material-library-item/v3", material_id: materialId, source_artifact_id: artifactId,
        display_name: "Data structures.pdf", size_bytes: 100, created_at: run.created_at, latest_attempt: run,
        available_structures: [{ run_id: runId, knowledge_structure_revision: structureRevision, created_at: run.created_at, status: "succeeded" }],
        study_sessions: history === "none" ? [] : [{ ...saved, run_id: runId }],
      }));
      const newSessionId = "55555555-5555-4555-8555-555555555555";
      const created = { ...session(), study_session_id: history === "none" ? newSessionId : sessionId, current_concept_id: secondConcept };
      let focused = false;
      const requests: unknown[] = [];
      const writes: string[] = [];
      page.on("request", request => { if (request.url().includes("/v1/study-sessions") && request.method() !== "GET") writes.push(request.method() + " " + new URL(request.url()).pathname); });
      await page.route("**/v1/study-sessions", route => {
        requests.push(route.request().postDataJSON());
        return json(route, created, 201);
      });
      await page.route(`**/v1/study-sessions/${sessionId}/focus`, route => {
        requests.push(route.request().postDataJSON()); focused = true; return json(route, created);
      });
      await page.route("**/v1/materials/*/knowledge-structures/*/study-sessions/*/resume?*", route => {
        const isNew = focused || route.request().url().includes(newSessionId);
        return json(route, { schema: "study-resume/v5", assessment_sets: [], selected_set_id: null, session: isNew ? created : saved, run_id: runId,
          source_artifact_id: artifactId, knowledge_structure: view,
          progress: isNew ? { ...savedProgress, study_session_id: created.study_session_id, current_concept_id: secondConcept,
            next_action: { ...savedProgress.next_action, target_concept_id: secondConcept, target_claim_id: secondClaim } } : savedProgress,
          });
      });
      await page.goto(`/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}`);
      const entry = page.getByRole("region", { name: "學習入口" });
      await openMapConcept(page, "陣列");
      const label = history === "same" ? "繼續學習" : history === "different" ? "從這個概念繼續" : "開始學習";
      await expect(entry.getByRole("button", { name: label, exact: true })).toBeEnabled();
      await page.screenshot({ path: `/tmp/studydy-map-study-action/${viewport.width}-${history}.png`, fullPage: true });
      const detail = page.getByRole("dialog", { name: "概念詳情" });
      await expect(detail.getByRole("heading", { name: "陣列", exact: true })).toBeVisible();
      await expect(page.locator(".focus-study-action")).toHaveCount(0);
      expect(writes).toEqual([]);
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(viewport.width);
      await entry.getByRole("button", { name: label, exact: true }).click();
      await expect(page).toHaveURL(new RegExp(`/study-sessions/${history === "none" ? newSessionId : sessionId}$`));
      await expect(page.getByRole("heading", { name: "陣列", level: 1, exact: true })).toBeVisible();
      expect(requests).toEqual(history === "same" ? [] : history === "different" ? [{ schema: "study-session-focus/v1", current_concept_id: secondConcept }] : [{ schema: "study-session-create/v2", material_id: materialId,
        knowledge_structure_revision: structureRevision, current_concept_id: secondConcept }]);
      expect(writes).toEqual(history === "same" ? [] : history === "different" ? [`POST /v1/study-sessions/${sessionId}/focus`] : ["POST /v1/study-sessions"]);
      expect(saved.current_concept_id).toBe(currentId);
    });
  }
}

for (const viewport of [{ width: 1536, height: 1024 }, { width: 1366, height: 768 }, { width: 390, height: 844 }]) {
  for (const identity of ["id", "name", "unknown"] as const) {
    test(`Concept Detail concise sources and alias search (${identity}) at ${viewport.width}px`, async ({ page }) => {
      await page.setViewportSize(viewport);
      const view = workspaceView(6);
      const concept = view.concepts[0];
      concept.label = "主機";
      concept.aliases = ["Host"];
      const resolver = `/v2/materials/${materialId}/knowledge-structures/${structureRevision}/evidence`;
      if (identity !== "single") Object.assign(view, { schema: "knowledge-structure-view/v3", source_resolver: resolver });
      const evidence = [0, 1, 2, 3].map(index => ({ ...concept.claims[0].evidence[0],
        evidence_id: `evidence:sha256:${String(index + 1).repeat(64)}`,
        // 原始頁碼刻意相同，確認多來源入口依 normalized_page 分組。
        page: identity === "single" && index === 3 ? 2 : 1,
        ...(identity === "id" ? { source_id: index === 2 ? materialId : artifactId } : {}),
        ...(identity === "unknown" || identity === "single" ? {} : {
          source_name: identity === "id" || index !== 2 ? "Network.pptx" : "Other.pptx",
          normalized_page: index === 3 ? 2 : 1,
        }),
        quote: "Extraction text should not appear in the concept panel.",
      }));
      concept.claims = [
        { ...concept.claims[0], text: "能參與網路通訊的端點。", evidence: evidence.slice(0, 1) },
        { ...concept.claims[0], claim_id: firstClaim, text: "主機包括電腦與行動裝置。", evidence: evidence.slice(1) },
      ];
      view.concepts.slice(1).forEach(item => { item.claims[0].evidence = [{ ...item.claims[0].evidence[0], evidence_id: `evidence:sha256:${"9".repeat(64)}` }]; });
      view.relations[0].evidence_refs = evidence.map(item => item.evidence_id);
      await routes(page, view);
      const resolved: string[] = [];
      await page.route("**/v2/materials/*/knowledge-structures/*/evidence/*/source", route => {
        const id = decodeURIComponent(new URL(route.request().url()).pathname.split("/").at(-2)!);
        resolved.push(id);
        const item = evidence.find(item => item.evidence_id === id)!;
        return json(route, { schema: "evidence-source/v1", format: "pptx", original_name: "Network.pptx",
          original_url: `/v2/artifacts/${artifactId}`, preview_url: `/v1/artifacts/${artifactId}#page=${item.normalized_page ?? item.page}`,
          normalized_page: item.normalized_page ?? item.page, accuracy: "exact", origin_locators: [], label: "PDF 來源" });
      });
      await page.goto(`/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}`);
      const search = page.getByRole("searchbox", { name: "搜尋概念或關鍵字" });
      await search.fill("Host");
      await expect(page.locator(".map-search-results strong")).toHaveText(["主機"]);
      await search.press("Enter");
      const detail = page.getByRole("dialog", { name: "概念詳情", exact: true });
      await expect(detail.getByRole("heading", { name: "主機", exact: true })).toBeVisible();
      await expect(detail.getByRole("heading", { name: "教材來源", exact: true })).toBeVisible();
      await expect(detail.getByRole("region", { name: /^教材重點 / })).toHaveCount(2);
      await expect(detail.getByRole("button", { name: "開始學習", exact: true })).toBeEnabled();
      await expect(detail).not.toContainText(/對照教材原文|教材中的其他名稱|延伸探索|Extraction text|Host|sha256/);
      const count = identity === "single" ? 2 : identity === "unknown" ? 4 : 3;
      const links = detail.getByRole("region", { name: "教材來源", exact: true }).getByRole("button");
      await expect(links).toHaveCount(count);
      if (identity !== "single") {
        for (let index = 0; index < count; index++) {
          await links.nth(index).click();
          const source = page.getByRole("dialog", { name: "教材來源", exact: true });
          const representative = evidence[identity === "unknown" || index === 0 ? index : index + 1];
          expect(resolved.at(-1)).toBe(representative.evidence_id);
          await expect(source.getByRole("link", { name: "開啟 PDF 來源頁" })).toHaveAttribute("href", `/v1/artifacts/${artifactId}#page=${representative.normalized_page ?? representative.page}`);
          await page.keyboard.press("Escape");
          await expect(source).toHaveCount(0);
          await expect(links.nth(index)).toBeFocused();
        }
      }
      expect(await detail.evaluate(element => element.scrollWidth <= element.clientWidth + 1)).toBe(true);
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(viewport.width);
      await page.keyboard.press("Escape");
      await expect(detail).toHaveCount(0);
      await expect(search).toBeFocused();
      const edge = await openFocusRelation(page, viewport.width <= 900);
      const relation = page.getByRole("dialog", { name: "關係詳情", exact: true });
      await expect(relation).toContainText(view.relations[0].learner_reason);
      await expect(relation.getByRole("button", { name: /PDF 第|查看第|原始教材第/ })).toHaveCount(count);
      await relation.getByRole("button", { name: "關閉關係詳情" }).click();
      await expect(edge).toBeFocused();
      await openMapConcept(page);
      await detail.getByRole("button", { name: "關閉概念詳情" }).click();
      await expect(page.locator(".concept-flow-node.is-focus")).toBeFocused();
    });
  }
}

for (const mode of ["複習重點"] as const) {
  test(`${mode} resumes the selected review concept through the existing study action`, async ({ page }) => {
    await routes(page, structureView(), () => ({ ...progress,
      concept_states: progress.concept_states.map((state, index) => index ? state : { ...state, status: "needs_review", weak_claim_ids: [firstClaim] }),
    }));
    await page.route(`**/v1/materials/${materialId}`, route => json(route, {
      schema: "material-library-item/v3", material_id: materialId, source_artifact_id: artifactId,
      display_name: "Data structures.pdf", size_bytes: 100, created_at: run.created_at, latest_attempt: run,
      available_structures: [{ run_id: runId, knowledge_structure_revision: structureRevision, created_at: run.created_at, status: "succeeded" }],
      study_sessions: [{ ...session(), run_id: runId }],
    }));
    await page.goto(`/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}`);
    await page.getByRole("tab", { name: mode, exact: true }).click();
    await expect(page.locator(".review-context")).toContainText("Stack");
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await page.getByRole("button", { name: "繼續這個概念", exact: true }).click();
    await expect(page).toHaveURL(new RegExp(`/study-sessions/${sessionId}$`));
  });
}

function learningMap(count: number, reordered = false, longNames = false) {
  const view = workspaceView(count, longNames);
  if (!longNames) view.concepts.slice(0, 4).forEach((concept, index) => { concept.label = ["A", "B", "C", "D"][index]; });
  view.concepts[3].aliases = ["navigation-target"];
  const sections = [0, 1].map(index => ({ section_id: `section:sha256:${String(index + 7).repeat(64)}`,
    title: `Section ${index ? "B" : "A"}`, order: index, heading_evidence_id: null, concept_ids: [] as string[] }));
  view.concepts.forEach((concept, index) => {
    const group = index < 4 ? index % 2 : 2 + Math.floor((index - 4) / 6);
    sections[group] ??= { section_id: `section:sha256:${(group + 50).toString(16).padStart(64, "0")}`, title: `Section ${group + 1}`, order: group, heading_evidence_id: null, concept_ids: [] };
    concept.section_ids = [sections[group].section_id];
    sections[group].concept_ids.push(concept.concept_id);
  });
  view.document_tree.sections = sections;
  const order = reordered ? [2, 0, 1, ...Array.from({ length: count - 3 }, (_, index) => index + 3)] : Array.from({ length: count }, (_, index) => index);
  view.initial_learning_path = order.map((index, position) => ({ position: position + 1, concept_id: view.concepts[index].concept_id,
    reason: position === 1 || position === 2 ? "prerequisite" : "document_order" }));
  const seed = view.relations[0];
  view.relations = order.slice(1).map((target, index) => ({ ...seed,
    relation_id: `relation:sha256:${(index + 300).toString(16).padStart(64, "0")}`,
    source_concept_id: view.concepts[order[index]].concept_id, target_concept_id: view.concepts[target].concept_id,
    type: index < 2 ? "prerequisite" : "example", inference_basis: index < 2 ? "dependency" : "instantiation",
    context_refs: view.concepts[target].section_ids, learner_reason: "教材中的直接關係。",
  }));
  return view;
}

async function learningMapRoutes(page: Page, view: ReturnType<typeof structureView>, hasProgress: boolean, action = "advance", currentIndex = 2, nextIndex = 3) {
  const currentId = view.concepts[currentIndex].concept_id;
  const saved = { ...session(), current_concept_id: currentId };
  const snapshot = { ...progress, current_concept_id: currentId,
    concept_states: view.concepts.map((concept, index) => ({ ...progress.concept_states[0], concept_id: concept.concept_id, label: concept.label,
      status: index < 2 ? "mastered" : index === 2 || index === 5 ? "learning" : index === 4 ? "needs_review" : "not_started",
      mastered_claim_ids: index < 2 ? [concept.claims[0].claim_id] : [], weak_claim_ids: index === 4 ? [concept.claims[0].claim_id] : [] })),
    next_action: { ...progress.next_action, action, target_concept_id: view.concepts[nextIndex].concept_id, target_claim_id: null },
  };
  await routes(page, view, () => snapshot);
  await page.route(`**/v1/materials/${materialId}`, route => json(route, {
    schema: "material-library-item/v3", material_id: materialId, source_artifact_id: artifactId, display_name: "Navigation.pdf", size_bytes: 100,
    created_at: run.created_at, latest_attempt: run, available_structures: [{ run_id: runId, knowledge_structure_revision: structureRevision, status: "succeeded", created_at: run.created_at }],
    study_sessions: hasProgress ? [{ ...saved, run_id: runId }] : [],
  }));
  await page.route("**/v1/materials/*/knowledge-structures/*/study-sessions/*/resume?*", route => json(route, {
    schema: "study-resume/v5", assessment_sets: [], selected_set_id: null, session: saved, run_id: runId, source_artifact_id: artifactId, knowledge_structure: view,
    progress: snapshot,
  }));
}

for (const action of ["advance", "review_prerequisite", "resume", "assess", "no_safe", "complete", "defer"]) {
  test(`learning navigation only marks a supplied concept-navigation next action: ${action}`, async ({ page }) => {
    const view = learningMap(8);
    await learningMapRoutes(page, view, true, action);
    await page.goto(`/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}`);
    await page.getByRole("button", { name: "學習導覽", exact: true }).click();
    await expect(page.locator(".navigator-current")).toHaveCount(1);
    await openMapConcept(page, view.concepts[5].label);
    const nav = page.getByRole("navigation", { name: "學習導覽", includeHidden: true });
    await expect(nav.locator(".is-next-suggested")).toHaveCount(["advance", "review_prerequisite", "resume"].includes(action) ? 1 : 0);
    if (["advance", "review_prerequisite", "resume"].includes(action)) await expect(navigationConcept(page, "D")).toContainText("下一步");
    await expect(navigationConcept(page, "C")).toContainText("目前學習");
    await expect(navigationConcept(page, view.concepts[5].label)).toHaveAttribute("aria-current", "true");
    await expect(navigationConcept(page, view.concepts[5].label)).not.toContainText("目前學習");
  });
}

test("map tabs cycle and missing path references still fail the strict API contract", async ({ page }) => {
  const view = learningMap(8, true);
  await learningMapRoutes(page, view, false);
  const map = `/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}`;
  await page.goto(map);
  const tabs = page.getByRole("tablist", { name: "知識地圖檢視" }).getByRole("tab");
  await expect(tabs).toHaveText(["概念地圖", "複習重點"]);
  await expect(page.getByRole("tab", { name: "學習順序", exact: true })).toHaveCount(0);
  await tabs.first().focus();
  for (const name of ["複習重點", "概念地圖"]) {
    await page.keyboard.press("ArrowRight"); await expect(page.getByRole("tab", { name, exact: true })).toBeFocused();
    await expect(page.getByRole("tab", { name, exact: true })).toHaveAttribute("aria-selected", "true");
  }
  await page.keyboard.press("End"); await expect(tabs.last()).toBeFocused();
  await page.keyboard.press("Home"); await expect(tabs.first()).toBeFocused();
  await page.keyboard.press("ArrowLeft"); await expect(tabs.last()).toBeFocused();
  view.initial_learning_path[0].concept_id = `concept:sha256:${"f".repeat(64)}`;
  await page.goto(map);
  await expect(page.getByRole("heading", { name: "無法讀取知識地圖", exact: true })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "學習導覽" })).toHaveCount(0);
});


for (const viewport of [{ width: 1536, height: 1024 }, { width: 1366, height: 768 }, { width: 1100, height: 800 }, { width: 390, height: 844 }]) {
  test(`review workspace weak claims sources and study target at ${viewport.width}px`, async ({ page }) => {
    await page.setViewportSize(viewport);
    const view = structureView();
    const longText = "教材完整說明，保留必要的條件與定義。".repeat(30);
    view.concepts[1].claims = Array.from({ length: 4 }, (_, i) => ({ ...view.concepts[1].claims[0], claim_id: `claim:sha256:${String(i + 1).repeat(64)}`, text: i === 0 ? longText : `教材重點 ${i + 1}` }));
    for(const claim of view.concepts[1].claims) for(const evidence of claim.evidence) Object.assign(evidence,{source_id:artifactId,source_name:'Synthetic.pdf',normalized_page:2});
    view.concepts[1].claims[0].evidence.push({...view.concepts[1].claims[0].evidence[0],evidence_id:`evidence:sha256:${"9".repeat(64)}`});
    const state = structuredClone(progress);
    state.concept_states.forEach((item, i) => Object.assign(item, { status: "needs_review", attempts: 3, correct_answers: 1, latest_is_correct: false, weak_claim_ids: i ? [view.concepts[i].claims[0].claim_id, view.concepts[i].claims[2].claim_id] : [view.concepts[i].claims[0].claim_id] }));
    await routes(page, view, () => state);
    await page.route('**/evidence/*/source',route=>json(route,{schema:'evidence-source/v1',format:'pdf',original_name:'Synthetic.pdf',original_url:`/v1/artifacts/${artifactId}`,preview_url:`/v1/artifacts/${artifactId}#page=2`,normalized_page:2,accuracy:'exact',origin_locators:[],label:'PDF 第 2 頁'}));
    await page.route(`**/v1/materials/${materialId}`, route => json(route, {
      schema: "material-library-item/v3", material_id: materialId, source_artifact_id: artifactId,
      display_name: "Data structures.pdf", size_bytes: 100, created_at: run.created_at, latest_attempt: run,
      available_structures: [{ run_id: runId, knowledge_structure_revision: structureRevision, created_at: run.created_at, status: "succeeded" }],
      study_sessions: [{ ...session(), run_id: runId }],
    }));
    const creates: any[] = [];
    await page.route(`**/v1/study-sessions/${sessionId}/focus`, route => { creates.push(route.request().postDataJSON()); return json(route, { ...session(), current_concept_id: secondConcept }); });
    await page.goto(`/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}`);
    await page.getByRole("tab", { name: "複習重點", exact: true }).click();
    const list = page.getByRole("navigation", { name: "需要複習的概念" });
    await expect(list.getByRole("button")).toHaveCount(2);
    await expect(list).not.toContainText(longText);
    const url = page.url();
    const array = list.getByRole("button", { name: /Array/ });
    await array.focus(); await page.keyboard.press("Enter");
    await expect(array).toHaveAttribute("aria-current", "true");
    await expect(array).toBeFocused();
    expect(page.url()).toBe(url); expect(creates).toHaveLength(0);
    await expect(page.locator(".review-context h3")).toHaveText("Array");
    await expect(page.locator('.review-context')).toHaveText('Array');
    await expect(page.locator('.review-workspace .map-learning-badge')).toHaveCount(0);
    await expect(page.locator('.review-list button').first()).toHaveText('Stack');
    await expect(array).toHaveText('Array');
    await expect(page.locator('.review-workspace')).not.toContainText(/最近一次作答|已掌握|看過重點後/);
    await expect(page.locator('.review-actions dl')).toHaveCount(0);
    await expect(page.locator('.review-actions button')).toHaveCount(2);
    await expect(page.locator('.review-points > ol > li')).toHaveCount(2);
    await expect(page.locator('.review-points > ol > li > p')).toHaveText([longText,'教材重點 3']);
    await expect(page.locator('.review-full-content')).toHaveCount(0);
    await expect(page.getByText('查看完整教材重點',{exact:true})).toHaveCount(0);
    const points=page.locator('.review-points > ol > li');
    for(const point of await points.all()) {
      const source=point.getByRole('button');await expect(source).toHaveCount(1);
      await source.click();
      await expect(page.getByRole('dialog',{name:'教材來源'}).getByRole('link',{name:'開啟 PDF 來源頁'})).toHaveAttribute('href',`/v1/artifacts/${artifactId}#page=2`);
      await page.keyboard.press('Escape');await expect(source).toBeFocused();
    }
    await list.getByRole('button',{name:'Stack',exact:true}).click();
    await expect(page.locator('.review-points > ol > li')).toHaveCount(1);
    await expect(page.locator('.review-points > ol > li > p')).toHaveText('A stack follows LIFO order.');
    await array.click();
    await expect(page.locator("#map-panel-review .primary-button")).toHaveCount(1);
    await expect(page.getByRole("dialog")).toHaveCount(0);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    const boxes = await Promise.all([".review-list", ".review-context", ".review-actions", ".review-points"].map(selector => page.locator(selector).boundingBox()));
    const [l, c, a, p] = boxes.map(box => box!);
    if (viewport.width >= 1200) { expect(l.x + l.width).toBeLessThan(c.x); expect(c.x + c.width).toBeLessThan(a.x); }
    if (viewport.width <= 900) { expect(l.y + l.height).toBeLessThanOrEqual(c.y); expect(c.y + c.height).toBeLessThanOrEqual(p.y); expect(p.y + p.height).toBeLessThanOrEqual(a.y); }
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({ path: `/tmp/studydy-review-${viewport.width}.png`, fullPage: true });
    await page.getByRole("button", { name: "繼續這個概念", exact: true }).click();
    await expect.poll(() => creates.length).toBe(1);
    expect(creates[0].current_concept_id).toBe(secondConcept);
  });
}

test("review empty state keeps learning navigation without an empty workspace", async ({ page }) => {
  await routes(page);
  await page.goto(`/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}`);
  await page.getByRole("tab", { name: "複習重點", exact: true }).click();
  await expect(page.locator(".review-empty")).toContainText("練習後，幫你找出複習方向");
  await expect(page.locator(".review-workspace")).toHaveCount(0);
  await page.getByRole("button", { name: "查看學習導覽", exact: true }).click();
  await expect(page.getByRole("tab", { name: "概念地圖", exact: true })).toHaveAttribute("aria-selected", "true");
});

for (const count of [0, 30]) test(`review ${count} weak concepts preserves membership and list scrolling`, async ({ page }) => {
  await page.setViewportSize({ width: 1536, height: 1024 });
  const view = workspaceView(32, true);
  const current = view.concepts[0].concept_id;
  const state = { ...progress, current_concept_id: current,
    next_action: { ...progress.next_action, target_concept_id: current, target_claim_id: view.concepts[0].claims[0].claim_id },
    concept_states: view.concepts.map((concept, i) => ({ ...progress.concept_states[0], concept_id: concept.concept_id, label: concept.label, status: i < count ? "needs_review" : "learning", weak_claim_ids: i < count ? [concept.claims[0].claim_id] : [] })) };
  await learningMapRoutes(page, view, true);
  await page.route("**/v1/study-sessions/*/progress", route => json(route, state));
  await page.route("**/v1/materials/*/knowledge-structures/*/study-sessions/*/resume?*", route => json(route, {
    schema: "study-resume/v5", assessment_sets: [], selected_set_id: null, session: { ...session(), current_concept_id: current }, run_id: runId, source_artifact_id: artifactId,
    knowledge_structure: view, progress: state,
  }));
  await page.goto(`/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}`);
  await page.getByRole("tab", { name: "複習重點", exact: true }).click();
  if (!count) {
    await expect(page.locator(".review-empty")).toContainText("目前沒有需要複習的概念");
    await expect(page.locator(".review-workspace")).toHaveCount(0);
  } else {
    const list = page.locator(".review-list ul");
    await expect(list.getByRole("button")).toHaveCount(count);
    expect(await list.evaluate(el => el.scrollHeight > el.clientHeight)).toBe(true);
    await list.getByRole("button").last().click();
    await expect(page.locator(".review-context h3")).toHaveText(view.concepts[count - 1].label);
    await expect(page.locator(".review-points > ol > li")).toHaveCount(1);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  }
});

for (const viewport of [{ width: 1536, height: 1024 }, { width: 1366, height: 768 }, { width: 1024, height: 768 }, { width: 390, height: 844 }]) {
  for (const progressState of ["available", "null", "error"]) test(`map has no global learning summary with ${progressState} progress at ${viewport.width}px`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await learningMapRoutes(page, learningMap(8), progressState !== "null");
    if (progressState === "error") await page.route("**/v1/study-sessions/*/progress", route => json(route, { detail: "Unavailable" }, 503));
    await page.goto(`/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}`);
    if (progressState === "available") await expect(page.locator(".map-learning-badge").first()).toBeVisible();
    for (const tabName of ["概念地圖", "複習重點"]) {
      const tab = page.getByRole("tab", { name: tabName, exact: true });
      await tab.click();
      await expect(tab).toHaveAttribute("aria-selected", "true");
      const panel = page.getByRole("tabpanel");
      await expect(panel).toHaveAttribute("aria-labelledby", await tab.getAttribute("id") as string);
      await expect(page.getByText("學習摘要", { exact: true })).toHaveCount(0);
      await expect(page.locator(".map-summary-container, .map-learning-summary, .summary-progress")).toHaveCount(0);
      await expect(page.locator(".map-facts")).toContainText("8概念");
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      const adjacent = await page.locator(".map-tabs").evaluate(el => el.nextElementSibling?.classList.contains("map-content"));
      expect(adjacent).toBe(true);
      if (tabName === "複習重點") {
        if (progressState === "available") await expect(page.locator(".review-workspace")).toBeVisible();
        else await expect(page.locator(".review-empty")).toContainText(progressState === "error" ? "暫時無法顯示複習重點" : "練習後，幫你找出複習方向");
      }
      await page.evaluate(() => window.scrollTo(0, 0));
      await page.screenshot({ path: `/tmp/studydy-summary-${viewport.width}-${progressState}-${tabName}.png`, fullPage: true });
    }
    await page.getByRole("tab", { name: "複習重點", exact: true }).focus();
    await page.keyboard.press("Home");
    await expect(page.getByRole("tab", { name: "概念地圖", exact: true })).toBeFocused();
  });
}

test("completed persistent state is viewed without create or focus", async ({ page }) => {
  const view = structureView();
  await routes(page, view);
  const state = { ...session("completed") };
  await page.route(`**/v1/materials/${materialId}`, route => json(route, { schema: "material-library-item/v3", material_id: materialId, source_artifact_id: artifactId,
    display_name: "Calculus.pdf", size_bytes: 100, created_at: run.created_at, latest_attempt: run,
    available_structures: [{ run_id: runId, knowledge_structure_revision: structureRevision, created_at: run.created_at, status: "succeeded" }], study_sessions: [{ ...state, run_id: runId }] }));
  await page.route("**/v1/materials/*/knowledge-structures/*/study-sessions/*/resume?*", route => json(route, { schema: "study-resume/v5", assessment_sets: [], selected_set_id: null, session: state, run_id: runId, source_artifact_id: artifactId, knowledge_structure: view,
    progress: { ...progress, next_action: { ...progress.next_action, action: "complete", target_concept_id: null, target_claim_id: null, reason: "all_mastered" } } }));
  const writes: string[] = [];
  page.on("request", request => { if (request.url().includes("/v1/study-sessions") && request.method() !== "GET") writes.push(request.url()); });
  await page.goto(`/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}`);
  await openMapConcept(page, "Array");
  await page.getByRole("button", { name: "查看學習成果", exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`/study-sessions/${sessionId}$`));
  await expect(page.getByRole("heading", { name: "學習已完成", exact: true })).toBeVisible();
  expect(writes).toEqual([]);
});

test("a new structure may create a distinct persistent state without focusing the old revision", async ({ page }) => {
  const nextRevision = `knowledge-structure:sha256:${"9".repeat(64)}`;
  const nextRunId = "55555555-5555-4555-8555-555555555555";
  const nextStateId = "66666666-6666-4666-8666-666666666666";
  const view = { ...structureView(), knowledge_structure_revision: nextRevision };
  await routes(page, view);
  const nextRun = { ...run, run_id: nextRunId, output_binding: { ...run.output_binding, knowledge_structure_revision: nextRevision } };
  await page.route(`**/v1/material-processing-runs/${nextRunId}`, route => json(route, nextRun));
  await page.route(`**/v1/materials/${materialId}`, route => json(route, { schema: "material-library-item/v3", material_id: materialId, source_artifact_id: artifactId,
    display_name: "Reprocessed.pdf", size_bytes: 100, created_at: run.created_at, latest_attempt: nextRun,
    available_structures: [{ run_id: nextRunId, knowledge_structure_revision: nextRevision, created_at: run.created_at, status: "succeeded" }], study_sessions: [{ ...session(), run_id: runId }] }));
  let created = 0;
  const saved = { ...session(), study_session_id: nextStateId, knowledge_structure_revision: nextRevision };
  await page.route("**/v1/study-sessions", route => { created++; expect(route.request().postDataJSON().knowledge_structure_revision).toBe(nextRevision); return json(route, saved, 201); });
  await page.route("**/v1/materials/*/knowledge-structures/*/study-sessions/*/resume?*", route => json(route, { schema: "study-resume/v5", assessment_sets: [], selected_set_id: null, session: saved, run_id: nextRunId, source_artifact_id: artifactId, knowledge_structure: view,
    progress: { ...progress, study_session_id: nextStateId, knowledge_structure_revision: nextRevision } }));
  await page.goto(`/materials/${materialId}/runs/${nextRunId}/knowledge-structures/${encodeURIComponent(nextRevision)}`);
  await openMapConcept(page);
  await page.getByRole("button", { name: "開始學習", exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`/study-sessions/${nextStateId}$`));
  expect(created).toBe(1);
});

test("sectionless concepts remain accessible through navigation", async ({ page }, info) => {
  const view = structureView();
  view.document_tree.sections = [];
  view.concepts.forEach(concept => { concept.section_ids = []; });
  view.relations = [];
  await routes(page, view);
  await page.goto(`/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}`);
  await expect(page.locator(".react-flow__node")).toHaveCount(1);
  await expect(page.getByRole("tab", { name: "總覽", exact: true })).toHaveCount(0);
  await expect(page.getByRole("heading", { level: 1 })).toHaveCount(1);
  await page.screenshot({ path: info.outputPath("embedded-empty.png"), fullPage: true });
});

for (const width of [320, 390, 1366, 1920]) {
  test(`workspace bounds and actions survive ${width}px and 200 percent equivalent reflow`, async ({ page }, info) => {
    await page.setViewportSize({ width, height: 1080 }); await routes(page);
    const map = `/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}`;
    for (const path of [map, `${map}/study-sessions/${sessionId}`]) {
      await page.setViewportSize({ width, height: 1080 });
      await page.goto(path);
      const workspace = page.locator(path === map ? ".map-workspace" : ".study-session-page");
      await expect(workspace).toBeVisible();
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(width);
      if (width >= 1366) {
        // 200% 桌機縮放對應一半 CSS viewport；保留相同字級驗證重排。
        await page.setViewportSize({ width: width / 2, height: 540 });
        expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width / 2);
        await expect(page.getByRole("button", { name: "我的教材", exact: true })).toBeVisible();
      }
      await page.screenshot({ path: info.outputPath(path === map ? "map.png" : "study.png"), fullPage: true });
    }
  });
}

for(const width of [1536,390]) test(`local map normalized source resolver distinguishes original and converted PDF at ${width}px`,async({page},info)=>{
  const resolver=`/v2/materials/${materialId}/knowledge-structures/${structureRevision}/evidence`;
  await page.setViewportSize({width,height:1024});
  await routes(page,{...structureView(),schema:"knowledge-structure-view/v3",source_resolver:resolver} as ReturnType<typeof structureView>);
  let reads=0;
  await page.route("**/v2/materials/*/knowledge-structures/*/evidence/*/source",route=>{
    reads++;return json(route,{schema:"evidence-source/v1",format:"docx",original_name:"notes.docx",original_url:`/v2/artifacts/${artifactId}`,
      preview_url:`/v1/artifacts/${artifactId}#page=1`,normalized_page:1,accuracy:"ambiguous",origin_locators:[{paragraph:2}],label:"轉換後第 1 頁"});
  });
  await page.goto(`/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}`);
  await page.getByRole("button", { name: "適應畫面", exact: true }).click();
  await page.getByRole("button",{name:"教材概念：Stack",exact:true}).click();
  await page.getByRole("button",{name:"查看第 1 頁來源",exact:true}).click();
  const dialog=page.getByRole("dialog",{name:"教材來源",exact:true});
  await expect(dialog).toContainText("轉換後第 1 頁");await expect(dialog).toContainText("可能對應多處");
  await expect(dialog.getByRole("link",{name:"下載原檔"})).toHaveAttribute("href",`/v2/artifacts/${artifactId}`);
  await expect(dialog.getByRole("link",{name:"開啟 PDF 來源頁"})).toHaveAttribute("href",`/v1/artifacts/${artifactId}#page=1`);
  await page.screenshot({path:info.outputPath("source-dialog.png"),fullPage:true});
  await page.keyboard.press("Escape");await expect(dialog).toHaveCount(0);
  await expect(page.getByRole("dialog",{name:"概念詳情",exact:true})).toBeVisible();
  await expect(page.getByRole("button",{name:"查看第 1 頁來源",exact:true})).toBeFocused();expect(reads).toBe(1);
});

async function expectVisibleGraphFits(page: Page) {
  await expect.poll(() => page.locator(".focus-graph").evaluate(graph => {
    const bounds = graph.getBoundingClientRect();
    return [...graph.querySelectorAll(".react-flow__node")].every(node => {
      const box = node.getBoundingClientRect();
      return box.width > 0 && box.left >= bounds.left - 1 && box.right <= bounds.right + 1
        && box.top >= bounds.top - 1 && box.bottom <= bounds.bottom + 1;
    });
  })).toBe(true);
}

test("B04 switching revision discards the previous selection and viewport", async ({ page }) => {
  await page.setViewportSize({ width: 1536, height: 1024 });
  await routes(page, workspaceView(20));
  const base = `/materials/${materialId}/runs/${runId}/knowledge-structures/`;
  await page.goto(`${base}${encodeURIComponent(structureRevision)}`);
  await openMapConcept(page, "Concept 20");
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "適應畫面", exact: true }).click();
  const next = workspaceView(1);
  next.knowledge_structure_revision = `knowledge-structure:sha256:${"9".repeat(64)}`;
  await routes(page, next);
  await page.route(`**/v1/material-processing-runs/${runId}`, route => json(route, {
    ...run, output_binding: { ...run.output_binding, knowledge_structure_revision: next.knowledge_structure_revision },
  }));
  // SPA 導航保留同一頁面環境，驗證不能帶入舊 revision 的選中 ID。
  await page.evaluate(path => { history.pushState(null, "", path); dispatchEvent(new PopStateEvent("popstate")); }, `${base}${encodeURIComponent(next.knowledge_structure_revision)}`);
  await expect(page.locator(".focus-graph")).toBeVisible();
  await expect(page.locator(".react-flow__node")).toHaveCount(1);
  await expect(page.locator(".concept-flow-node.is-focus")).toContainText("Concept 1");
  await expect(page.locator("#map-navigator")).toBeHidden();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expectVisibleGraphFits(page);
});

for (const width of [1920, 1536, 1366, 390]) {
  for (const count of [1, 20, 100, 200]) test(`compact map ${count} concepts at ${width}px renders two hops with controls inside canvas`, async ({ page }, info) => {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 1080 });
    const view = workspaceView(count, true);
    if (count > 1) view.relations = view.relations.filter(edge => edge.target_concept_id !== view.concepts.at(-1)!.concept_id);
    const original = structuredClone(view);
    // 此 fixture 的中心連到 1..8，第二層再加入 9；更遠的鏈不畫出。
    const localIds = new Set(view.concepts.slice(0, Math.min(count, 10)).map(concept => concept.concept_id));
    const localRelations = view.relations.filter(edge => localIds.has(edge.source_concept_id) && localIds.has(edge.target_concept_id));
    const writes: string[] = [], errors: string[] = [];
    await routes(page, view);
    page.on("request", request => { if (/\/v[12]\//.test(request.url()) && !request.url().endsWith("/v1/session/refresh") && request.method() !== "GET") writes.push(request.url()); });
    page.on("pageerror", error => errors.push(error.message));
    await page.goto(`/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}?mode=overview`);
    const graph = page.locator(".focus-graph");
    await expect(page.locator(".react-flow__node")).toHaveCount(localIds.size);
    await expect(page.locator(".concept-flow-edge")).toHaveCount(localRelations.length);
    expect(new Set(await page.locator(".react-flow__node").evaluateAll(nodes => nodes.map(node => node.getAttribute("data-id")))))
      .toEqual(localIds);
    await expectVisibleGraphFits(page);
    if (count === 20) {
      const node = page.locator(".concept-flow-node.is-focus");
      await node.focus(); await page.keyboard.press("Enter");
      await expect(page.getByRole("dialog", { name: "概念詳情" })).toBeVisible();
      await page.keyboard.press("Escape"); await expect(node).toBeFocused();
    }
    await expect(page.locator(".map-tools, .focus-study-action, .focus-context, .focus-graph-header, .graph-count, .overview-index")).toHaveCount(0);
    await expect(page.getByRole("region", { name: "學習入口" })).toHaveCount(0);
    await expect(graph.getByRole("button", { name: /^(放大地圖|縮小地圖|適應畫面|學習導覽)$/ })).toHaveCount(4);
    const canvas = (await graph.boundingBox())!;
    const frame = (await page.locator(".map-view").boundingBox())!;
    expect(Math.abs(canvas.y - frame.y)).toBeLessThan(2);
    expect(Math.abs(canvas.height - frame.height)).toBeLessThan(2);
    for (const name of ["放大地圖", "縮小地圖", "適應畫面", "學習導覽"]) {
      const box = (await graph.getByRole("button", { name, exact: true }).boundingBox())!;
      expect(box.x).toBeGreaterThanOrEqual(canvas.x); expect(box.y).toBeGreaterThanOrEqual(canvas.y);
      expect(box.x + box.width).toBeLessThanOrEqual(canvas.x + canvas.width);
      expect(box.y + box.height).toBeLessThanOrEqual(canvas.y + canvas.height);
    }
    const toggle = graph.getByRole("button", { name: "學習導覽", exact: true });
    await toggle.click();
    await expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(await graph.boundingBox()).toEqual(canvas);
    await navigationConcept(page, view.concepts[0].label).focus(); await page.keyboard.press("Escape");
    await expect(toggle).toBeFocused(); await expect(page.locator("#map-navigator")).toBeHidden();
    if (count === 20) await page.screenshot({ path: info.outputPath("map.png"), fullPage: true });
    await openMapConcept(page, view.concepts.at(-1)!.label);
    const detail = page.getByRole("dialog", { name: "概念詳情" });
    await expect(detail).toContainText(view.concepts.at(-1)!.label);
    await expect(detail.getByRole("region", { name: "學習入口" }).getByRole("button")).toBeEnabled();
    await expect(page.locator(".react-flow__node")).toHaveCount(1);
    await expect(page.locator(".concept-flow-edge")).toHaveCount(0);
    if (count === 20) await page.screenshot({ path: info.outputPath("detail.png"), fullPage: true });
    await page.keyboard.press("Escape");
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await expect(toggle).toBeFocused();
    await expect.poll(async () => (await graph.boundingBox())!.width).toBeCloseTo(canvas.width, 0);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(width);
    expect(view).toEqual(original); expect(writes).toEqual([]); expect(errors).toEqual([]);
  });
}

for (const width of [1536, 390]) test(`compact map navigator and search preserve canonical learning at ${width}px`, async ({ page }) => {
  await page.setViewportSize({ width, height: 1024 });
  const view = learningMap(56, true);
  await learningMapRoutes(page, view, true, "advance", 3, 4);
  const writes: string[] = [];
  page.on("request", request => { if (request.url().includes("/v1/study-sessions") && request.method() !== "GET") writes.push(request.url()); });
  await page.goto(`/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}`);
  await page.getByRole("button", { name: "學習導覽", exact: true }).click();
  const nav = page.getByRole("navigation", { name: "學習導覽", includeHidden: true });
  await expect(nav.locator(".navigator-position")).toHaveText(Array.from({ length: 56 }, (_, i) => String(i + 1)));
  const order = [...view.initial_learning_path].sort((a, b) => a.position - b.position).map(step => view.concepts.find(concept => concept.concept_id === step.concept_id)!.label);
  await expect(nav.locator(".navigator-label")).toHaveText(order);
  await expect(nav.locator(".is-learning-current .navigator-label")).toHaveText(view.concepts[3].label);
  await navigationConcept(page, view.concepts[19].label).click();
  await expect(page.getByRole("dialog", { name: "概念詳情" })).toContainText(view.concepts[19].label);
  await expect(nav.locator(".is-learning-current .navigator-label")).toHaveText(view.concepts[3].label);
  await expect(nav.locator(".is-next-suggested .navigator-label")).toHaveText(view.concepts[4].label);
  await page.keyboard.press("Escape");
  const search = page.getByRole("searchbox", { name: "搜尋概念或關鍵字" });
  await search.fill(view.concepts[30].label); await search.press("Enter");
  await expect(page.getByRole("dialog", { name: "概念詳情" }).getByRole("heading", { name: view.concepts[30].label, exact: true })).toBeVisible();
  await page.keyboard.press("Escape"); await expect(search).toBeFocused();
  expect(writes).toEqual([]);
});

test("compact map retains manual viewport through details, progress reload and resize", async ({ page }) => {
  await page.setViewportSize({ width: 1536, height: 1024 });
  const view = learningMap(20);
  await learningMapRoutes(page, view, true);
  let fail = true;
  await page.route("**/v1/study-sessions/*/progress", async route => {
    if (fail) { fail = false; return json(route, { detail: "Unavailable" }, 503); }
    await route.fallback();
  });
  const writes: string[] = [];
  page.on("request", request => { if (/\/v[12]\//.test(request.url()) && !request.url().endsWith("/v1/session/refresh") && request.method() !== "GET") writes.push(request.url()); });
  await page.goto(`/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}`);
  await expectVisibleGraphFits(page);
  const viewport = page.locator(".react-flow__viewport");
  const zoom = () => viewport.evaluate(el => new DOMMatrix(getComputedStyle(el).transform).a);
  const before = await zoom();
  await page.getByRole("button", { name: "放大地圖", exact: true }).click();
  await expect.poll(zoom).toBeCloseTo(before * 1.2, 4);
  const box = (await page.locator(".focus-graph").boundingBox())!;
  await page.mouse.move(box.x + box.width / 2, box.y + 25); await page.mouse.down();
  await page.mouse.move(box.x + box.width / 2 + 50, box.y + 60, { steps: 5 }); await page.mouse.up();
  await openMapConcept(page);
  await expect.poll(zoom).toBeCloseTo(before * 1.2, 4);
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "重新讀取進度", exact: true }).click();
  await expect(page.locator(".partial-banner")).toHaveCount(0);
  await expect.poll(zoom).toBeCloseTo(before * 1.2, 4);
  await page.setViewportSize({ width: 1366, height: 900 });
  await expect.poll(zoom).toBeCloseTo(before * 1.2, 4);
  await page.getByRole("button", { name: "適應畫面", exact: true }).click();
  await expectVisibleGraphFits(page);
  expect(writes).toEqual([]);
});

test("large map only renders its local neighbourhood and stays responsive when dragging", async ({ page }) => {
  const view = workspaceView(294, true);
  view.relations = view.relations.slice(0, 251);
  await routes(page, view);
  await page.addInitScript(() => {
    let nodeReads = 0;
    Object.defineProperty(window, "mapNodeReads", { get: () => nodeReads });
    const original = Element.prototype.getBoundingClientRect;
    Element.prototype.getBoundingClientRect = function () {
      if (this.classList.contains("react-flow__node")) nodeReads++;
      return original.call(this);
    };
  });
  await page.goto(`/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}`);
  await expect(page.locator(".react-flow__node")).toHaveCount(10);
  await expect(page.locator(".concept-flow-edge")).toHaveCount(9);
  await page.waitForFunction(() => [...document.querySelectorAll<HTMLElement>(".react-flow__node")]
    .every(node => getComputedStyle(node).visibility !== "hidden"));
  await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
  const reads = () => page.evaluate(() => Reflect.get(window, "mapNodeReads") as number);
  // 允許字型／首次排版的少量重測；不得每次尺寸回寫都強制重測整張圖。
  // 此為線性工作量回歸界線，不以機器速度或 FPS 作通用 SLA。
  expect(await reads()).toBeLessThanOrEqual(10 * 4);
  const before = await reads();
  const graph = (await page.locator(".focus-graph").boundingBox())!;
  const viewport = page.locator(".react-flow__viewport");
  const transform = await viewport.getAttribute("style");
  await page.mouse.move(graph.x + graph.width - 50, graph.y + 25); await page.mouse.down();
  await page.mouse.move(graph.x + graph.width - 280, graph.y + 140, { steps: 24 }); await page.mouse.up();
  await expect(viewport).not.toHaveAttribute("style", transform!);
  expect((await reads()) - before).toBeLessThanOrEqual(10 * 4);
  await page.getByRole("button", { name: "學習導覽", exact: true }).click();
  await expect(page.getByRole("navigation", { name: "學習導覽" })).toBeVisible();
});

for (const width of [1536, 390]) test(`two-hop map bounds dense graphs and shows consistent card content at ${width}px`, async ({ page }, info) => {
  await page.setViewportSize({ width, height: 1024 });
  const view = workspaceView(80);
  const seed = view.relations[0];
  view.relations = [];
  const connect = (a: number, b: number) => view.relations.push({ ...seed,
    relation_id: `relation:sha256:${(view.relations.length + 9000).toString(16).padStart(64, "0")}`,
    source_concept_id: view.concepts[a].concept_id, target_concept_id: view.concepts[b].concept_id });
  for (let i = 1; i <= 6; i++) connect(0, i);
  for (let i = 7; i < 80; i++) connect(1 + (i - 7) % 6, i);
  for (let i = 1; i < 30; i++) for (let j = i + 1; j < 30; j++) connect(i, j);
  await learningMapRoutes(page, view, true, "advance", 0, 1);
  const writes: string[] = [];
  page.on("request", request => { if (/\/v[12]\//.test(request.url()) && !request.url().endsWith("/v1/session/refresh") && request.method() !== "GET") writes.push(request.url()); });
  await page.goto(`/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}`);
  await expect(page.locator(".react-flow__node")).toHaveCount(30);
  await expect(page.locator(".concept-flow-edge")).toHaveCount(60);
  await expect(page.locator(".map-limit-note")).toContainText(`30／80 個概念、60／${view.relations.length} 條關係`);
  await expect(page.locator(".map-limit-note")).toContainText("尚有其他相關內容");
  const secondary = page.locator(".concept-flow-node.is-secondary");
  await expect(secondary).toHaveCount(23);
  await expect(secondary.locator("p")).toHaveCount(23);
  await expect(secondary.locator(".map-learning-badge")).toHaveCount(23);
  const summaries = await secondary.evaluateAll(nodes => nodes.map(node => ({
    id: node.getAttribute("data-id"), text: node.querySelector("p")?.textContent,
  })));
  for (const summary of summaries) expect(summary.text).toBe(view.concepts.find(concept => concept.concept_id === summary.id)!.claims[0].text);
  const cardStyle = (element: Element) => ({ width: getComputedStyle(element).width,
    padding: getComputedStyle(element).padding, titleSize: getComputedStyle(element.querySelector("strong")!).fontSize,
    summarySize: getComputedStyle(element.querySelector("p")!).fontSize });
  expect(await secondary.first().evaluate(cardStyle)).toEqual(await page.locator(".concept-flow-node:not(.is-focus):not(.is-secondary)").first().evaluate(cardStyle));
  await expectVisibleGraphFits(page);
  await page.screenshot({ path: info.outputPath("two-hop-limited.png"), fullPage: true });
  const id = await secondary.first().getAttribute("data-id");
  const label = await secondary.first().locator("strong").innerText();
  await secondary.first().focus(); await page.keyboard.press("Enter");
  const detail = page.getByRole("dialog", { name: "概念詳情" });
  await expect(detail.getByRole("heading", { name: label, exact: true })).toBeVisible();
  await expect(detail.getByRole("heading", { name: "教材重點", exact: true })).toBeVisible();
  await expect(page.locator(".concept-flow-node.is-focus")).toHaveAttribute("data-id", id!);
  await expect(page.locator(".concept-flow-node.is-focus p")).toHaveCount(1);
  expect(await page.locator(".react-flow__node").count()).toBeLessThanOrEqual(30);
  expect(await page.locator(".concept-flow-edge").count()).toBeLessThanOrEqual(60);
  await page.keyboard.press("Escape");
  await expect(page.locator(".concept-flow-node.is-focus")).toBeFocused();
  const box = (await page.locator(".focus-graph").boundingBox())!;
  const viewport = page.locator(".react-flow__viewport");
  const before = await viewport.getAttribute("style");
  await page.mouse.move(box.x + box.width / 2, box.y + 60); await page.mouse.down();
  await page.mouse.move(box.x + box.width / 2 + 40, box.y + 130, { steps: 12 }); await page.mouse.up();
  await expect(viewport).not.toHaveAttribute("style", before!);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(width);
  expect(writes).toEqual([]);
});

for (const width of [1536, 390]) test(`learning navigator scrolls to the final concept at ${width}px`, async ({ page }) => {
  await page.setViewportSize({ width, height: width === 390 ? 844 : 960 });
  const view = workspaceView(100, true);
  await routes(page, view);
  await page.goto(`/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}`);
  await page.getByRole("button", { name: "學習導覽", exact: true }).click();
  const list = page.locator('.navigator-list');
  const nav = page.getByRole('navigation', { name: '學習導覽', exact: true });
  await expect.poll(() => list.evaluate(e => e.scrollHeight > e.clientHeight && e.clientHeight > 0)).toBe(true);
  const frame = (await page.locator('#map-navigator').boundingBox())!;
  const content = (await list.boundingBox())!;
  expect(content.y + content.height).toBeLessThanOrEqual(frame.y + frame.height + 1);
  const viewport = page.locator('.react-flow__viewport');
  const transform = await viewport.getAttribute('style');
  await list.hover();
  await page.mouse.wheel(0, 500);
  await expect.poll(() => list.evaluate(e => e.scrollTop)).toBeGreaterThan(0);
  await page.mouse.wheel(0, 100_000);
  await expect.poll(() => list.evaluate(e => Math.abs(e.scrollHeight - e.clientHeight - e.scrollTop))).toBeLessThan(2);
  const last = nav.getByRole('button').last();
  await expect(last).toBeInViewport();
  expect(await viewport.getAttribute('style')).toBe(transform);
  // 滾到最末項後仍可選取，重新開啟時保留選中項可見。
  const label = await last.locator('.navigator-label').textContent();
  await last.click();
  await expect(page.getByRole('dialog', { name: '概念詳情', exact: true })).toContainText(label!);
  await page.keyboard.press('Escape');
  await page.getByRole('button', { name: '學習導覽', exact: true }).click();
  await expect(nav.locator('[aria-current="true"]')).toBeInViewport();
  await nav.getByRole('button').first().focus();
  await expect.poll(() => list.evaluate(e => e.scrollTop)).toBe(0);
  await expect(nav.getByRole('button').first()).toBeInViewport();
});

test('reload restores map presentation while local interaction does not restart authentication',async({page})=>{
  await page.clock.install();
  const view=structureView();const state=structuredClone(progress);
  state.concept_states.forEach((c,i)=>Object.assign(c,{status:'needs_review',weak_claim_ids:[view.concepts[i].claims[0].claim_id]}));
  await routes(page,view,()=>state);
  await page.route(`**/v1/materials/${materialId}`,route=>json(route,{schema:'material-library-item/v3',material_id:materialId,source_artifact_id:artifactId,
    display_name:'Synthetic.pdf',size_bytes:100,created_at:run.created_at,latest_attempt:run,available_structures:[{run_id:runId,knowledge_structure_revision:structureRevision,created_at:run.created_at,status:'succeeded'}],study_sessions:[{...session(),run_id:runId}]}));
  const auth:string[]=[];page.on('request',request=>{if(/\/v1\/session(?:\/refresh)?$/.test(request.url()))auth.push(request.method()+' '+new URL(request.url()).pathname);});
  const path=`/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}`;
  await page.goto(path);await page.getByRole('tab',{name:'複習重點',exact:true}).click();
  await page.locator('.review-list').getByRole('button',{name:'Array',exact:true}).click();
  await expect(page.locator('.review-context h3')).toHaveText('Array');
  await page.evaluate(()=>{for(let i=0;i<5;i++)window.dispatchEvent(new Event('focus'));});
  expect(auth).toEqual(['POST /v1/session/refresh']);
  await page.reload();
  await expect(page.getByRole('tab',{name:'複習重點',exact:true})).toHaveAttribute('aria-selected','true');
  await expect(page.locator('.review-context h3')).toHaveText('Array');
  expect(auth).toEqual(['POST /v1/session/refresh']);
  const context=await page.locator('.review-context').elementHandle();
  await page.clock.fastForward(60*60*1000+1);
  expect(auth).toEqual(['POST /v1/session/refresh']);
  expect(await context!.evaluate(node=>node.isConnected)).toBe(true);
  await expect(page.locator('.review-context h3')).toHaveText('Array');
  await page.getByRole('tab',{name:'概念地圖',exact:true}).click();
  await openMapConcept(page,'Array');
  await page.getByRole('searchbox').fill('Stack');
  await page.reload();
  await expect(page.getByRole('searchbox')).toHaveValue('Stack');
  await expect(page.getByRole('dialog',{name:'概念詳情',exact:true}).getByRole('heading',{name:'Array',exact:true})).toBeVisible();
  await expect(page).toHaveURL(`${browserOrigin}${path}`);
});

test('bootstrap and bfcache keep the page frame and route without exposing unverified private content',async({page})=>{
  await routes(page);
  const path=`/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}`;
  let release!:()=>void;let gate=new Promise<void>(resolve=>{release=resolve;});
  await page.route('**/v1/session/refresh',async route=>{await gate;await json(route,{schema:'learner-identity/v1',learner_id:sessionId});});
  await page.goto(path);
  await expect(page.locator('.app-header')).toBeVisible();
  await expect(page.locator('.auth-form,.map-workspace')).toHaveCount(0);
  await expect(page.getByRole('status')).toHaveText('正在載入…');
  await expect(page).toHaveURL(`${browserOrigin}${path}`);
  release();await page.getByRole('tab',{name:'複習重點',exact:true}).click();
  await page.evaluate(()=>window.dispatchEvent(new PageTransitionEvent('pageshow',{persisted:true})));
  await expect(page).toHaveURL(`${browserOrigin}${path}`);
  await expect(page.getByRole('tab',{name:'複習重點',exact:true})).toHaveAttribute('aria-selected','true');
});

test('review starts its small progress read in parallel and never shows a false empty state',async({page})=>{
  const view=structureView();const state=structuredClone(progress);
  Object.assign(state.concept_states[0],{status:'needs_review',weak_claim_ids:[firstClaim]});
  await routes(page,view,()=>state);
  await page.route(`**/v1/materials/${materialId}`,route=>json(route,{schema:'material-library-item/v3',material_id:materialId,source_artifact_id:artifactId,
    display_name:'Synthetic.pdf',size_bytes:100,created_at:run.created_at,latest_attempt:run,available_structures:[{run_id:runId,knowledge_structure_revision:structureRevision,created_at:run.created_at,status:'succeeded'}],study_sessions:[{...session(),run_id:runId}]}));
  let releaseMap!:()=>void,releaseProgress!:()=>void,progressReads=0,resumes=0;
  const mapGate=new Promise<void>(resolve=>{releaseMap=resolve;});
  const progressGate=new Promise<void>(resolve=>{releaseProgress=resolve;});
  await page.route(`**/v1/materials/${materialId}/knowledge-structures/${encodeURIComponent(structureRevision)}`,async route=>{await mapGate;await json(route,view);});
  await page.route('**/v1/study-sessions/*/progress',async route=>{progressReads++;await progressGate;await json(route,state);});
  page.on('request',request=>{if(request.url().includes('/resume?'))resumes++;});
  await page.goto(`/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}`);
  await expect.poll(()=>progressReads).toBe(1);
  releaseMap();await page.getByRole('tab',{name:'複習重點',exact:true}).click();
  await expect(page.getByText('正在讀取複習重點…',{exact:true})).toBeVisible();
  await expect(page.locator('.review-empty')).toHaveCount(0);
  releaseProgress();await expect(page.locator('.review-list button')).toHaveCount(1);
  expect(resumes).toBe(0);
});
