import { expect, test, type Page } from "@playwright/test";
import {
  materialId,
  runId,
  sessionId,
  structureRevision,
  json,
  mockKnowledgeMapApi,
  navigationConcept,
  openMapConcept,
  workspaceView,
  structureView,
  learningMap,
  mockLearningMapApi,
} from "../fixtures/knowledge-map";

const mapPath = `/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}`;

function trackReadOnlyInteractions(page: Page) {
  const writes: string[] = [];
  const errors: string[] = [];
  page.on("request", (request) => {
    const path = new URL(request.url()).pathname;
    if (/^\/v[12]\//.test(path) && path !== "/v1/session/refresh" && request.method() !== "GET")
      writes.push(`${request.method()} ${path}`);
  });
  page.on("pageerror", (error) => errors.push(error.message));
  return () => {
    expect(writes).toEqual([]);
    expect(errors).toEqual([]);
  };
}

async function readViewport(page: Page) {
  return page.locator(".focus-graph").evaluate((graph) => {
    const viewport = graph.querySelector(".react-flow__viewport")!;
    const transform = new DOMMatrix(getComputedStyle(viewport).transform);
    return {
      zoom: transform.a,
      centerX: (graph.clientWidth / 2 - transform.e) / transform.a,
      centerY: (graph.clientHeight / 2 - transform.f) / transform.d,
    };
  });
}

async function expectViewportUnchanged(
  page: Page,
  expected: Awaited<ReturnType<typeof readViewport>>,
) {
  await expect.poll(async () => (await readViewport(page)).zoom).toBeCloseTo(expected.zoom, 4);
  // 比較世界座標中心，畫布變窄／變高時仍保留使用者瀏覽的位置。
  await expect
    .poll(async () => (await readViewport(page)).centerX)
    .toBeCloseTo(expected.centerX, 2);
  await expect
    .poll(async () => (await readViewport(page)).centerY)
    .toBeCloseTo(expected.centerY, 2);
}

async function expectVisibleGraphFits(page: Page) {
  await expect
    .poll(() =>
      page.locator(".focus-graph").evaluate((graph) => {
        const bounds = graph.getBoundingClientRect();
        const nodes = [...graph.querySelectorAll(".react-flow__node")];
        return (
          bounds.width > 0 &&
          bounds.height > 0 &&
          nodes.length > 0 &&
          nodes.every((node) => {
            const box = node.getBoundingClientRect();
            return (
              box.width > 0 &&
              box.height > 0 &&
              getComputedStyle(node).visibility !== "hidden" &&
              box.left >= bounds.left - 1 &&
              box.right <= bounds.right + 1 &&
              box.top >= bounds.top - 1 &&
              box.bottom <= bounds.bottom + 1
            );
          })
        );
      }),
    )
    .toBe(true);
}

test("switching revision discards the previous selection and viewport", async ({ page }) => {
  await page.setViewportSize({ width: 1536, height: 1024 });
  await mockKnowledgeMapApi(page, workspaceView(20));
  const assertReadOnly = trackReadOnlyInteractions(page);
  await page.goto(mapPath);
  await openMapConcept(page, "Concept 20");
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "適應畫面", exact: true }).click();
  await expectVisibleGraphFits(page);
  await page.getByRole("button", { name: "放大地圖", exact: true }).click();
  const next = workspaceView(1);
  next.knowledge_structure_revision = `knowledge-structure:sha256:${"9".repeat(64)}`;
  next.source_resolver = structureView(next.knowledge_structure_revision).source_resolver;
  await mockKnowledgeMapApi(page, next);
  // SPA 導航故意保留舊呈現紀錄，新的 revision 必須忽略它。
  await page.evaluate(
    (path) => {
      history.pushState(history.state, "", path);
      dispatchEvent(new PopStateEvent("popstate"));
    },
    mapPath.replace(
      encodeURIComponent(structureRevision),
      encodeURIComponent(next.knowledge_structure_revision),
    ),
  );
  await expect(page.locator(".focus-graph")).toBeVisible();
  await expect(page.locator(".react-flow__node")).toHaveCount(1);
  await expect(page.locator(".concept-flow-node.is-focus")).toContainText("Concept 1");
  await expect(page.locator("#map-navigator")).toBeHidden();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expectVisibleGraphFits(page);
  await expect(navigationConcept(page, "Concept 1")).toHaveAttribute("aria-current", "true");
  const centered = () =>
    page.locator(".focus-graph").evaluate((graph) => {
      const bounds = graph.getBoundingClientRect();
      const node = graph.querySelector(".react-flow__node")!.getBoundingClientRect();
      return {
        x: node.x + node.width / 2 - (bounds.x + bounds.width / 2),
        y: node.y + node.height / 2 - (bounds.y + bounds.height / 2),
      };
    });
  await expect.poll(async () => Math.abs((await centered()).x)).toBeLessThanOrEqual(1);
  await expect.poll(async () => Math.abs((await centered()).y)).toBeLessThanOrEqual(1);
  assertReadOnly();
});

// 代表尺寸涵蓋大集合、單節點與桌機／手機鍵盤操作。
// 大集合的這個 fixture 都顯示相同十個鄰近節點；密集圖、拖曳與捲動另有案例。
for (const [width, count] of [
  [1920, 200],
  [1536, 20],
  [1366, 100],
  [390, 1],
  [390, 20],
]) {
  test(`compact map ${count} concepts at ${width}px renders two hops with controls inside canvas`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 1080 });
    const view = workspaceView(count, true);
    if (count > 1)
      view.relations = view.relations.filter(
        (edge) => edge.target_concept_id !== view.concepts.at(-1)!.concept_id,
      );
    const original = structuredClone(view);
    // 此 fixture 的中心連到 1..8，第二層再加入 9；更遠的鏈不畫出。
    const localIds = new Set(
      view.concepts.slice(0, Math.min(count, 10)).map((concept) => concept.concept_id),
    );
    const localRelations = view.relations.filter(
      (edge) => localIds.has(edge.source_concept_id) && localIds.has(edge.target_concept_id),
    );
    await mockKnowledgeMapApi(page, view);
    const assertReadOnly = trackReadOnlyInteractions(page);
    await page.goto(mapPath);
    const graph = page.locator(".focus-graph");
    await expect(page.locator(".react-flow__node")).toHaveCount(localIds.size);
    await expect(page.locator(".concept-flow-edge")).toHaveCount(localRelations.length);
    expect(
      new Set(
        await page
          .locator(".react-flow__node")
          .evaluateAll((nodes) => nodes.map((node) => node.getAttribute("data-id"))),
      ),
    ).toEqual(localIds);
    await expectVisibleGraphFits(page);
    if (count === 20) {
      const node = page.locator(".concept-flow-node.is-focus");
      await node.focus();
      await page.keyboard.press("Enter");
      await expect(page.getByRole("dialog", { name: "概念詳情" })).toBeVisible();
      await page.keyboard.press("Escape");
      await expect(node).toBeFocused();
    }
    await expect(
      page.locator(
        ".map-tools, .focus-study-action, .focus-context, .focus-graph-header, .graph-count, .overview-index",
      ),
    ).toHaveCount(0);
    await expect(page.getByRole("region", { name: "學習入口" })).toHaveCount(0);
    await expect(
      graph.getByRole("button", { name: /^(放大地圖|縮小地圖|適應畫面|學習導覽)$/ }),
    ).toHaveCount(4);
    const canvas = (await graph.boundingBox())!;
    const frame = (await page.locator(".map-view").boundingBox())!;
    expect(Math.abs(canvas.y - frame.y)).toBeLessThan(2);
    expect(Math.abs(canvas.height - frame.height)).toBeLessThan(2);
    for (const name of ["放大地圖", "縮小地圖", "適應畫面", "學習導覽"]) {
      const box = (await graph.getByRole("button", { name, exact: true }).boundingBox())!;
      expect(box.x).toBeGreaterThanOrEqual(canvas.x);
      expect(box.y).toBeGreaterThanOrEqual(canvas.y);
      expect(box.x + box.width).toBeLessThanOrEqual(canvas.x + canvas.width);
      expect(box.y + box.height).toBeLessThanOrEqual(canvas.y + canvas.height);
    }
    const toggle = graph.getByRole("button", { name: "學習導覽", exact: true });
    await toggle.click();
    await expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(await graph.boundingBox()).toEqual(canvas);
    await navigationConcept(page, view.concepts[0].label).focus();
    await page.keyboard.press("Escape");
    await expect(toggle).toBeFocused();
    await expect(page.locator("#map-navigator")).toBeHidden();
    await openMapConcept(page, view.concepts.at(-1)!.label);
    const detail = page.getByRole("dialog", { name: "概念詳情" });
    await expect(detail).toContainText(view.concepts.at(-1)!.label);
    await expect(
      detail.getByRole("region", { name: "學習入口" }).getByRole("button"),
    ).toBeEnabled();
    await expect(page.locator(".react-flow__node")).toHaveCount(1);
    await expect(page.locator(".concept-flow-edge")).toHaveCount(0);
    await page.keyboard.press("Escape");
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await expect(toggle).toBeFocused();
    await expect.poll(async () => (await graph.boundingBox())!.width).toBeCloseTo(canvas.width, 0);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(width);
    expect(view).toEqual(original);
    assertReadOnly();
  });
}

test("compact map retains manual viewport through details, progress reload and resize", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1536, height: 1024 });
  const view = learningMap(20);
  await mockLearningMapApi(page, view, true);
  await page.route(
    `**/v1/study-sessions/${sessionId}/progress`,
    (route) =>
      json(
        route,
        {
          schema: "api-error/v1",
          request_id: sessionId,
          reason_code: "STORAGE_UNAVAILABLE",
          retryable: true,
          message: "Request could not be completed.",
        },
        503,
      ),
    { times: 1 },
  );
  const assertReadOnly = trackReadOnlyInteractions(page);
  await page.goto(mapPath);
  await expectVisibleGraphFits(page);
  const before = await readViewport(page);
  await page.getByRole("button", { name: "放大地圖", exact: true }).click();
  await expect.poll(async () => (await readViewport(page)).zoom).toBeCloseTo(before.zoom * 1.2, 4);
  const zoomed = await readViewport(page);
  const box = (await page.locator(".focus-graph").boundingBox())!;
  await page.mouse.move(box.x + box.width / 2, box.y + 25);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width / 2 + 50, box.y + 60, { steps: 5 });
  await page.mouse.up();
  await expect
    .poll(async () => (await readViewport(page)).centerX)
    .toBeCloseTo(zoomed.centerX - 50 / zoomed.zoom, 2);
  await expect
    .poll(async () => (await readViewport(page)).centerY)
    .toBeCloseTo(zoomed.centerY - 35 / zoomed.zoom, 2);
  const panned = await readViewport(page);
  await openMapConcept(page);
  await expectViewportUnchanged(page, panned);
  await page.keyboard.press("Escape");
  await expectViewportUnchanged(page, panned);
  await page.getByRole("button", { name: "重新讀取進度", exact: true }).click();
  await expect(page.locator(".partial-banner")).toHaveCount(0);
  await expectViewportUnchanged(page, panned);
  await page.setViewportSize({ width: 1366, height: 900 });
  await expectViewportUnchanged(page, panned);
  await page.getByRole("button", { name: "適應畫面", exact: true }).click();
  await expectVisibleGraphFits(page);
  assertReadOnly();
});

test("large map only renders its local neighbourhood and stays responsive when dragging", async ({
  page,
}) => {
  const view = workspaceView(294, true);
  view.relations = view.relations.slice(0, 251);
  await mockKnowledgeMapApi(page, view);
  const assertReadOnly = trackReadOnlyInteractions(page);
  await page.addInitScript(() => {
    let nodeReads = 0;
    Object.defineProperty(window, "mapNodeReads", { get: () => nodeReads });
    const original = Element.prototype.getBoundingClientRect;
    Element.prototype.getBoundingClientRect = function () {
      if (this.classList.contains("react-flow__node")) nodeReads++;
      return original.call(this);
    };
  });
  await page.goto(mapPath);
  await expect(page.locator(".react-flow__node")).toHaveCount(10);
  await expect(page.locator(".concept-flow-edge")).toHaveCount(9);
  await page.waitForFunction(() =>
    [...document.querySelectorAll<HTMLElement>(".react-flow__node")].every(
      (node) => getComputedStyle(node).visibility !== "hidden",
    ),
  );
  await page.evaluate(
    () =>
      new Promise<void>((resolve) =>
        requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
      ),
  );
  const reads = () => page.evaluate(() => Reflect.get(window, "mapNodeReads") as number);
  // 允許字型／首次排版的少量重測；不得每次尺寸回寫都強制重測整張圖。
  // 此為線性工作量回歸界線，不以機器速度或 FPS 作通用 SLA。
  expect(await reads()).toBeLessThanOrEqual(10 * 4);
  const before = await reads();
  const graph = (await page.locator(".focus-graph").boundingBox())!;
  const viewport = page.locator(".react-flow__viewport");
  const transform = await viewport.getAttribute("style");
  await page.mouse.move(graph.x + graph.width - 50, graph.y + 25);
  await page.mouse.down();
  await page.mouse.move(graph.x + graph.width - 280, graph.y + 140, { steps: 24 });
  await page.mouse.up();
  await expect(viewport).not.toHaveAttribute("style", transform!);
  expect((await reads()) - before).toBeLessThanOrEqual(10 * 4);
  await page.getByRole("button", { name: "學習導覽", exact: true }).click();
  await expect(page.getByRole("navigation", { name: "學習導覽" })).toBeVisible();
  assertReadOnly();
});

for (const width of [1536, 390])
  test(`two-hop map bounds dense graphs and shows consistent card content at ${width}px`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 1024 });
    const view = workspaceView(80);
    const seed = view.relations[0];
    view.relations = [];
    const connect = (a: number, b: number) =>
      view.relations.push({
        ...seed,
        relation_id: `relation:sha256:${(view.relations.length + 9000).toString(16).padStart(64, "0")}`,
        source_concept_id: view.concepts[a].concept_id,
        target_concept_id: view.concepts[b].concept_id,
      });
    for (let i = 1; i <= 6; i++) connect(0, i);
    for (let i = 7; i < 80; i++) connect(1 + ((i - 7) % 6), i);
    for (let i = 1; i < 30; i++) for (let j = i + 1; j < 30; j++) connect(i, j);
    await mockLearningMapApi(page, view, true, "advance", 0, 1);
    const assertReadOnly = trackReadOnlyInteractions(page);
    await page.goto(mapPath);
    await expect(page.locator(".react-flow__node")).toHaveCount(30);
    await expect(page.locator(".concept-flow-edge")).toHaveCount(60);
    await expect(page.locator(".map-limit-note")).toContainText(
      `30／80 個概念、60／${view.relations.length} 條關係`,
    );
    await expect(page.locator(".map-limit-note")).toContainText("尚有其他相關內容");
    const secondary = page.locator(".concept-flow-node.is-secondary");
    await expect(secondary).toHaveCount(23);
    await expect(secondary.locator("p")).toHaveCount(23);
    await expect(secondary.locator(".map-learning-badge")).toHaveCount(23);
    const summaries = await secondary.evaluateAll((nodes) =>
      nodes.map((node) => ({
        id: node.getAttribute("data-id"),
        text: node.querySelector("p")?.textContent,
      })),
    );
    for (const summary of summaries)
      expect(summary.text).toBe(
        view.concepts.find((concept) => concept.concept_id === summary.id)!.claims[0].text,
      );
    const cardStyle = (element: Element) => ({
      width: getComputedStyle(element).width,
      padding: getComputedStyle(element).padding,
      titleSize: getComputedStyle(element.querySelector("strong")!).fontSize,
      summarySize: getComputedStyle(element.querySelector("p")!).fontSize,
    });
    expect(await secondary.first().evaluate(cardStyle)).toEqual(
      await page
        .locator(".concept-flow-node:not(.is-focus):not(.is-secondary)")
        .first()
        .evaluate(cardStyle),
    );
    await expectVisibleGraphFits(page);
    // 第一個第二層概念是 fixture 中的第 8 個，不從 DOM 自己產生預期值。
    const target = view.concepts[7];
    const targetNode = secondary.filter({ has: page.getByText(target.label, { exact: true }) });
    await expect(targetNode).toHaveAttribute("data-id", target.concept_id);
    await targetNode.focus();
    await page.keyboard.press("Enter");
    const detail = page.getByRole("dialog", { name: "概念詳情" });
    await expect(detail.getByRole("heading", { name: target.label, exact: true })).toBeVisible();
    await expect(detail.getByRole("heading", { name: "教材重點", exact: true })).toBeVisible();
    await expect(page.locator(".concept-flow-node.is-focus")).toHaveAttribute(
      "data-id",
      target.concept_id,
    );
    await expect(page.locator(".concept-flow-node.is-focus p")).toHaveCount(1);
    expect(await page.locator(".react-flow__node").count()).toBeLessThanOrEqual(30);
    expect(await page.locator(".concept-flow-edge").count()).toBeLessThanOrEqual(60);
    await page.keyboard.press("Escape");
    await expect(page.locator(".concept-flow-node.is-focus")).toBeFocused();
    const box = (await page.locator(".focus-graph").boundingBox())!;
    const viewport = page.locator(".react-flow__viewport");
    const before = await viewport.getAttribute("style");
    await page.mouse.move(box.x + box.width / 2, box.y + 60);
    await page.mouse.down();
    await page.mouse.move(box.x + box.width / 2 + 40, box.y + 130, { steps: 12 });
    await page.mouse.up();
    await expect(viewport).not.toHaveAttribute("style", before!);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(width);
    assertReadOnly();
  });
