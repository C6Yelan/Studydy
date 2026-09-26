import { expect, test } from "@playwright/test";
import {
  browserOrigin,
  materialId,
  runId,
  artifactId,
  structureRevision,
  secondConcept,
  structureView,
  run,
  session,
  progress,
  json,
  mockKnowledgeMapApi,
  navigationConcept,
  openMapConcept,
  workspaceView,
  learningMap,
  mockLearningMapApi,
} from "../fixtures/knowledge-map";

const mapPath = `/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}`;

// 後端以 assess + prerequisite_concept_ids 提示前置觀念，不會產生 review_prerequisite。
for (const [action, marksNext] of [
  ["advance", true],
  ["resume", true],
  ["assess", false],
  ["no_safe", false],
  ["complete", false],
  ["defer", false],
] as const) {
  test(`learning navigation only marks a supplied concept-navigation next action: ${action}`, async ({
    page,
  }) => {
    const view = learningMap(8);
    await mockLearningMapApi(page, view, true, action);
    await page.goto(mapPath);
    await page.getByRole("button", { name: "學習導覽", exact: true }).click();
    await expect(page.locator(".navigator-current")).toHaveCount(1);
    await openMapConcept(page, view.concepts[5].label);
    const nav = page.getByRole("navigation", { name: "學習導覽", includeHidden: true });
    await expect(nav.locator(".is-next-suggested")).toHaveCount(marksNext ? 1 : 0);
    if (marksNext) await expect(navigationConcept(page, "D")).toContainText("下一步");
    await expect(navigationConcept(page, "C")).toContainText("目前學習");
    const selected = navigationConcept(page, view.concepts[5].label);
    await expect(selected).toHaveAttribute("aria-current", "true");
    await expect(selected).not.toContainText("目前學習");
  });
}

test("map tabs cycle and missing path references still fail the strict API contract", async ({
  page,
}) => {
  const view = learningMap(8, true);
  await mockLearningMapApi(page, view, false);
  await page.goto(mapPath);
  const tabs = page.getByRole("tablist", { name: "知識地圖檢視" }).getByRole("tab");
  await expect(tabs).toHaveText(["概念地圖", "複習重點"]);
  await expect(page.getByRole("tab", { name: "學習順序", exact: true })).toHaveCount(0);
  await tabs.first().focus();
  for (const [key, name] of [
    ["ArrowRight", "複習重點"],
    ["ArrowRight", "概念地圖"],
    ["End", "複習重點"],
    ["Home", "概念地圖"],
    ["ArrowLeft", "複習重點"],
  ]) {
    await page.keyboard.press(key);
    const selected = tabs.filter({ hasText: name });
    await expect(selected).toBeFocused();
    await expect(selected).toHaveAttribute("aria-selected", "true");
    await expect(selected).toHaveAttribute("tabindex", "0");
  }
  view.initial_learning_path[0].concept_id = `concept:sha256:${"f".repeat(64)}`;
  await page.goto(mapPath);
  await expect(page.getByRole("heading", { name: "無法讀取知識地圖", exact: true })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "學習導覽" })).toHaveCount(0);
});

test("sectionless concepts remain selectable through navigation", async ({ page }) => {
  const view = structureView();
  view.document_tree.sections = [];
  view.concepts.forEach((concept) => {
    concept.section_ids = [];
  });
  view.relations = [];
  await mockKnowledgeMapApi(page, view);
  await page.goto(mapPath);
  await expect(page.locator(".react-flow__node")).toHaveCount(1);
  await expect(page.getByRole("tab", { name: "總覽", exact: true })).toHaveCount(0);
  await expect(page.getByRole("heading", { level: 1 })).toHaveCount(1);
  const toggle = page.getByRole("button", { name: "學習導覽", exact: true });
  await toggle.click();
  await expect(page.locator(".navigator-label")).toHaveText(["Stack", "Array"]);
  await navigationConcept(page, "Array").click();
  await expect(
    page
      .getByRole("dialog", { name: "概念詳情" })
      .getByRole("heading", { name: "Array", exact: true }),
  ).toBeVisible();
  await expect(page.locator(".concept-flow-node.is-focus")).toHaveAttribute(
    "data-id",
    secondConcept,
  );
  await page.keyboard.press("Escape");
  await expect(toggle).toBeFocused();
});

for (const width of [1536, 390])
  test(`compact map navigator and search preserve canonical learning at ${width}px`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 1024 });
    const view = learningMap(56, true);
    await mockLearningMapApi(page, view, true, "advance", 3, 4);
    const writes: string[] = [];
    page.on("request", (request) => {
      if (request.url().includes("/v1/study-sessions") && request.method() !== "GET")
        writes.push(request.url());
    });
    await page.goto(mapPath);
    await page.getByRole("button", { name: "學習導覽", exact: true }).click();
    const nav = page.getByRole("navigation", { name: "學習導覽", includeHidden: true });
    const current = nav.locator(".is-learning-current .navigator-label");
    const suggested = nav.locator(".is-next-suggested .navigator-label");
    await expect(nav.locator(".navigator-position")).toHaveText(
      Array.from({ length: 56 }, (_, i) => String(i + 1)),
    );
    const order = [...view.initial_learning_path]
      .sort((a, b) => a.position - b.position)
      .map(
        (step) => view.concepts.find((concept) => concept.concept_id === step.concept_id)!.label,
      );
    await expect(nav.locator(".navigator-label")).toHaveText(order);
    await expect(current).toHaveText(view.concepts[3].label);
    await navigationConcept(page, view.concepts[19].label).click();
    await expect(page.getByRole("dialog", { name: "概念詳情" })).toContainText(
      view.concepts[19].label,
    );
    await expect(current).toHaveText(view.concepts[3].label);
    await expect(suggested).toHaveText(view.concepts[4].label);
    await page.keyboard.press("Escape");
    const search = page.getByRole("searchbox", { name: "搜尋概念或關鍵字" });
    await search.fill(view.concepts[30].label);
    await search.press("Enter");
    await expect(
      page
        .getByRole("dialog", { name: "概念詳情" })
        .getByRole("heading", { name: view.concepts[30].label, exact: true }),
    ).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(search).toBeFocused();
    await expect(current).toHaveText(view.concepts[3].label);
    await expect(suggested).toHaveText(view.concepts[4].label);
    expect(writes).toEqual([]);
  });

for (const width of [1536, 390])
  test(`learning navigator scrolls to the final concept at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 960 });
    const view = workspaceView(100, true);
    await mockKnowledgeMapApi(page, view);
    await page.goto(mapPath);
    await page.getByRole("button", { name: "學習導覽", exact: true }).click();
    const list = page.locator(".navigator-list");
    const nav = page.getByRole("navigation", { name: "學習導覽", exact: true });
    await expect(nav.getByRole("button")).toHaveCount(view.concepts.length);
    await expect
      .poll(() => list.evaluate((e) => e.scrollHeight > e.clientHeight && e.clientHeight > 0))
      .toBe(true);
    const frame = (await page.locator("#map-navigator").boundingBox())!;
    const content = (await list.boundingBox())!;
    expect(content.y + content.height).toBeLessThanOrEqual(frame.y + frame.height + 1);
    const viewport = page.locator(".react-flow__viewport");
    const transform = await viewport.getAttribute("style");
    await list.hover();
    await page.mouse.wheel(0, 500);
    await expect.poll(() => list.evaluate((e) => e.scrollTop)).toBeGreaterThan(0);
    await page.mouse.wheel(0, 100_000);
    await expect
      .poll(() => list.evaluate((e) => Math.abs(e.scrollHeight - e.clientHeight - e.scrollTop)))
      .toBeLessThan(2);
    const lastConcept = view.concepts.at(-1)!;
    const last = navigationConcept(page, lastConcept.label);
    await expect(last).toBeInViewport();
    expect(await viewport.getAttribute("style")).toBe(transform);
    // 滾到最末項後仍可選取，重新開啟時保留選中項可見。
    await last.click();
    await expect(
      page
        .getByRole("dialog", { name: "概念詳情", exact: true })
        .getByRole("heading", { name: lastConcept.label, exact: true }),
    ).toBeVisible();
    await page.keyboard.press("Escape");
    await page.getByRole("button", { name: "學習導覽", exact: true }).click();
    await expect(nav.locator('[aria-current="true"]')).toBeInViewport();
    await nav.getByRole("button").first().focus();
    await expect.poll(() => list.evaluate((e) => e.scrollTop)).toBe(0);
    await expect(nav.getByRole("button").first()).toBeInViewport();
  });

test("reload restores map presentation while local interaction does not restart authentication", async ({
  page,
}) => {
  await page.clock.install();
  const view = structureView();
  const state = structuredClone(progress);
  state.concept_states.forEach((c, i) =>
    Object.assign(c, {
      status: "needs_review",
      weak_claim_ids: [view.concepts[i].claims[0].claim_id],
    }),
  );
  await mockKnowledgeMapApi(page, view, () => state);
  await page.route(`**/v1/materials/${materialId}`, (route) =>
    json(route, {
      schema: "material-library-item/v1",
      material_id: materialId,
      source_artifact_id: artifactId,
      display_name: "Synthetic.pdf",
      size_bytes: 100,
      created_at: run.created_at,
      latest_attempt: run,
      available_structures: [
        {
          run_id: runId,
          knowledge_structure_revision: structureRevision,
          created_at: run.created_at,
          status: "succeeded",
        },
      ],
      study_sessions: [{ ...session(), run_id: runId }],
    }),
  );
  const auth: string[] = [];
  page.on("request", (request) => {
    if (/\/v1\/session(?:\/refresh)?$/.test(request.url()))
      auth.push(request.method() + " " + new URL(request.url()).pathname);
  });
  await page.goto(mapPath);
  await page.getByRole("tab", { name: "複習重點", exact: true }).click();
  await page.locator(".review-list").getByRole("button", { name: "Array", exact: true }).click();
  await expect(page.locator(".review-context h3")).toHaveText("Array");
  await page.evaluate(() => {
    for (let i = 0; i < 5; i++) window.dispatchEvent(new Event("focus"));
  });
  await page.reload();
  await expect(page.getByRole("tab", { name: "複習重點", exact: true })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  await expect(page.locator(".review-context h3")).toHaveText("Array");
  const context = await page.locator(".review-context").elementHandle();
  await page.clock.fastForward(60 * 60 * 1000 + 1);
  expect(await context!.evaluate((node) => node.isConnected)).toBe(true);
  await expect(page.locator(".review-context h3")).toHaveText("Array");
  await page.getByRole("tab", { name: "概念地圖", exact: true }).click();
  await openMapConcept(page, "Array");
  await page.getByRole("searchbox").fill("Stack");
  await page.reload();
  await expect(page.getByRole("searchbox")).toHaveValue("Stack");
  await expect(
    page
      .getByRole("dialog", { name: "概念詳情", exact: true })
      .getByRole("heading", { name: "Array", exact: true }),
  ).toBeVisible();
  await expect(page).toHaveURL(`${browserOrigin}${mapPath}`);
  // 累積整段操作的請求，包含最後一次保留詳情與搜尋文字的重新整理。
  expect(auth).toEqual(["POST /v1/session/refresh"]);
});
