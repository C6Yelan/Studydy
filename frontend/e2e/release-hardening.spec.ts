import { expect, test, type Page, type Route } from "@playwright/test";

import {
  adaptiveView,
  apiError,
  assessmentView,
  contextView,
  learningStateView,
  mapRevision,
  mapView,
  materialId,
  runId,
  runView,
  sessionView,
  studySessionId,
  weaknessView,
} from "./fixtures/learning";

async function fulfillJson(route: Route, json: unknown, status = 200) {
  await route.fulfill({ status, json });
}

async function publicMapRoutes(page: Page, view = mapView()) {
  await page.route("**/v1/session/refresh", (route) => route.fulfill({ status: 204 }));
  await page.route(`**/v1/material-processing-runs/${runId}`, (route) => fulfillJson(route, runView()));
  await page.route("**/v1/materials/*/knowledge-maps/**", (route) => fulfillJson(route, view));
}

async function activeSessionRoutes(page: Page, currentConceptId: string | null = sessionView().current_formal_concept_id) {
  await publicMapRoutes(page);
  await page.route(`**/v1/study-sessions/${studySessionId}`, (route) => fulfillJson(route, sessionView({ current_formal_concept_id: currentConceptId })));
  await page.route(`**/v1/study-sessions/${studySessionId}/context`, (route) => fulfillJson(route, contextView({ current_formal_concept_id: currentConceptId })));
  await page.route(`**/v1/study-sessions/${studySessionId}/learning-state`, (route) => fulfillJson(route, learningStateView()));
  await page.route(`**/v1/study-sessions/${studySessionId}/weakness`, (route) => fulfillJson(route, weaknessView({ currentConceptId })));
  await page.route(`**/v1/study-sessions/${studySessionId}/adaptive-plan`, (route) => fulfillJson(route, adaptiveView({ currentConceptId })));
}

function emptyMap() {
  const view = mapView();
  view.status = {
    processing: "failed",
    quality: "needs_review",
    decision: "reject",
    reason_codes: ["NO_USABLE_CONCEPT"],
  };
  view.concepts = [];
  view.document_tree = {
    root: { material_ref: view.material_ref, section_ids: [] },
    sections: [],
  };
  view.initial_learning_path = [];
  Object.assign(view.concept_diagnostics, {
    possible_pairs: 0,
    source_concepts_before: 0,
    canonical_concepts_after: 0,
    coverage_before: 0,
    coverage_after: 0,
  });
  return view;
}

function partialMap() {
  const view = mapView();
  view.status.processing = "partial";
  view.excluded_pages = [{
    page_ref: `page:sha256:${"f".repeat(64)}`,
    page_number: 3,
    page_evidence_id: null,
    last_stage: "page_evidence",
    processing: "failed",
    quality: "needs_review",
    decision: "reject",
    reason_codes: ["NO_USABLE_EVIDENCE"],
  }];
  return view;
}

function longRevision(prefix: string, index: number) {
  return `${prefix}:sha256:${index.toString(16).padStart(64, "0")}`;
}

function largeMap() {
  const view = mapView();
  view.concepts = Array.from({ length: 30 }, (_, index) => {
    const number = index + 1;
    return {
      formal_concept_id: longRevision("formal-concept", number),
      label: `大型地圖概念 ${number}`,
      aliases: [],
      claims: [{
        claim_id: longRevision("claim", number),
        text: `大型地圖概念 ${number} 的教材重點。`,
        evidence: [{
          evidence_id: longRevision("evidence", number),
          page_ref: longRevision("page", number),
          page_number: number,
          kind: "paragraph",
          region: { coordinate_space: "unrotated_pdf_points" as const, bbox: [40, 50, 260, 90] as [number, number, number, number] },
        }],
      }],
      source_concept_ids: [longRevision("concept", number)],
      source_page_numbers: [number],
      supplementary_resources: [],
      quality: "needs_review" as const,
      decision: "review" as const,
      reason_codes: ["FORMAL_CONCEPT_REVIEW_REQUIRED"],
    };
  });
  Object.assign(view.concept_diagnostics, {
    possible_pairs: 435,
    source_concepts_before: 30,
    canonical_concepts_after: 30,
    coverage_before: 30,
    coverage_after: 30,
  });
  view.document_tree = {
    root: {
      material_ref: view.material_ref,
      section_ids: view.concepts.map((_, index) =>
        longRevision("document-section", index + 1)),
    },
    sections: view.concepts.map((concept, index) => ({
      section_id: longRevision("document-section", index + 1),
      label: `大型教材單元 ${index + 1}`,
      label_source: "heading",
      heading_evidence_id: concept.claims[0].evidence[0].evidence_id,
      source_order: {
        evidence_id: concept.claims[0].evidence[0].evidence_id,
        page_ref: concept.claims[0].evidence[0].page_ref,
        page_number: concept.source_page_numbers[0],
        reading_order: 0,
      },
      concept_ids: [concept.formal_concept_id],
    })),
  };
  view.initial_learning_path = view.concepts.map((concept, index) => ({
    step_number: index + 1,
    formal_concept_id: concept.formal_concept_id,
    placement_reason: `依教材第 ${index + 1} 頁的首次 Claim Evidence 安排。`,
    order_basis: {
      prerequisite_constraint_ids: [],
      section_id: longRevision("document-section", index + 1),
      page_ref: concept.claims[0].evidence[0].page_ref,
      page_number: index + 1,
      reading_order: 0,
      evidence_id: concept.claims[0].evidence[0].evidence_id,
    },
  }));
  return view;
}

test("retryable failure 可在原畫面恢復，fatal/empty/partial 狀態 truthful", async ({ page }) => {
  await page.route("**/v1/session/refresh", (route) => route.fulfill({ status: 204 }));
  let runCalls = 0;
  let recover = false;
  await page.route(`**/v1/material-processing-runs/${runId}`, (route) => {
    runCalls += 1;
    return recover
      ? fulfillJson(route, {
          ...runView(),
          status: "running",
          progress_stage: "concept_generation",
          completed_pages: 1,
          total_pages: 2,
          output_binding: null,
          completed_at: null,
        })
      : fulfillJson(route, apiError("STORAGE_UNAVAILABLE", true), 503);
  });
  await page.goto(`/materials/${materialId}/runs/${runId}`);
  await expect(page.getByRole("heading", { name: "無法讀取處理狀態" })).toBeVisible();
  if (process.env.STUDYDY_CAPTURE_ACCEPTANCE === "1") {
    await page.screenshot({ path: "../docs_local/p07_acceptance/16_failure_retryable.png", fullPage: true });
  }
  recover = true;
  await page.getByRole("button", { name: "重新讀取" }).click();
  await expect(page.getByRole("heading", { name: "正在分析完整教材" })).toBeVisible();
  expect(runCalls).toBeGreaterThanOrEqual(2);

  await page.unroute(`**/v1/material-processing-runs/${runId}`);
  await page.route(`**/v1/material-processing-runs/${runId}`, (route) => fulfillJson(route, runView()));
  await page.route("**/v1/materials/*/knowledge-maps/**", (route) => fulfillJson(route, emptyMap()));
  await page.goto(`/materials/${materialId}/runs/${runId}/knowledge-maps/${encodeURIComponent(mapRevision)}`);
  await expect(page.getByRole("heading", { name: "知識地圖目前是空的" })).toBeVisible();
  if (process.env.STUDYDY_CAPTURE_ACCEPTANCE === "1") {
    await page.locator("img").evaluateAll(async (images) => Promise.all(images.map((item) => item.decode().catch(() => undefined))));
    await page.screenshot({ path: "../docs_local/p07_acceptance/17_empty.png", fullPage: true });
  }

  await page.unroute("**/v1/materials/*/knowledge-maps/**");
  await page.route("**/v1/materials/*/knowledge-maps/**", (route) => fulfillJson(route, partialMap()));
  await page.goto(`/materials/${materialId}/runs/${runId}/knowledge-maps/${encodeURIComponent(mapRevision)}`);
  await expect(page.getByText("部分教材內容未安全納入")).toBeVisible();
  await page.getByRole("tab", { name: "內容複核" }).click();
  await expect(page.getByText("1 個頁面未安全納入")).toBeVisible();
});

test("large Map 與 narrow assessment 無 blocking overflow，tabs 支援鍵盤", async ({ page }) => {
  await publicMapRoutes(page, largeMap());
  await page.goto(`/materials/${materialId}/runs/${runId}/knowledge-maps/${encodeURIComponent(mapRevision)}`);
  await page.getByRole("tab", { name: "總覽" }).click();
  await expect(page.locator(".concept-card")).toHaveCount(30);
  const overviewTab = page.getByRole("tab", { name: "總覽" });
  await overviewTab.focus();
  await overviewTab.press("ArrowRight");
  await expect(page.getByRole("tab", { name: "內容複核" })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("tabpanel")).toHaveAttribute("aria-labelledby", "map-tab-review");

  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("tab", { name: "總覽" }).click();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await expect(page.locator(".concept-card").first()).toBeVisible();

  await activeSessionRoutes(page);
  await page.route(`**/v1/study-sessions/${studySessionId}/assessments`, (route) => fulfillJson(route, assessmentView(1), 201));
  await page.goto(`/materials/${materialId}/runs/${runId}/knowledge-maps/${encodeURIComponent(mapRevision)}/study-sessions/${studySessionId}`);
  await page.getByRole("button", { name: "開始評量" }).click();
  await expect(page.getByRole("radio")).toHaveCount(4);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  for (const option of await page.locator(".assessment-options label").all()) {
    const box = await option.boundingBox();
    expect(box).not.toBeNull();
    expect(box!.x).toBeGreaterThanOrEqual(0);
    expect(box!.x + box!.width).toBeLessThanOrEqual(390);
  }
});

test("expired app session 自動建立新 cookie session，無 current Concept 顯示安全 empty state", async ({ page }) => {
  let refreshCalls = 0;
  let createCalls = 0;
  await page.route("**/v1/session/refresh", (route) => {
    refreshCalls += 1;
    return fulfillJson(route, apiError("SESSION_REQUIRED"), 401);
  });
  await page.route("**/v1/session", (route) => {
    createCalls += 1;
    return route.fulfill({ status: 204 });
  });
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "上傳學習資料" })).toBeVisible();
  expect(refreshCalls).toBe(1);
  expect(createCalls).toBe(1);

  await page.unroute("**/v1/session/refresh");
  await page.route("**/v1/session/refresh", (route) => route.fulfill({ status: 204 }));
  await activeSessionRoutes(page, null);
  await page.goto(`/materials/${materialId}/runs/${runId}/knowledge-maps/${encodeURIComponent(mapRevision)}/study-sessions/${studySessionId}`);
  await expect(page.getByRole("heading", { name: "目前沒有學習內容" })).toBeVisible();
  await expect(page.getByRole("main")).toBeVisible();
  await expect(page.getByRole("navigation", { name: "主要導覽" })).toBeVisible();
});
