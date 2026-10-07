import { expect, test, type Page, type Route } from "@playwright/test";
import type { MaterialProcessingRunView } from "../../src/api/contracts";

import { materialStageCountLabel } from "../../src/features/material-flow/material-flow";
import { structureView, mockKnowledgeMapApi } from "../fixtures/knowledge-map";

const materialId = "11111111-1111-4111-8111-111111111111";
const runId = "22222222-2222-4222-8222-222222222222";
const artifactId = "44444444-4444-4444-8444-444444444444";
const learnerId = "55555555-5555-4555-8555-555555555555";
const revision = `knowledge-structure:sha256:${"a".repeat(64)}`;
const runPath = `/materials/${materialId}/runs/${runId}`;
const clockTime = new Date("2026-09-12T12:00:00Z");
const base: MaterialProcessingRunView = {
  schema: "material-processing-run/v1",
  cancel_requested_at: null,
  material_id: materialId,
  run_id: runId,
  source_artifact_id: artifactId,
  status: "running",
  progress_stage: "evidence",
  completed_pages: 3,
  total_pages: 45,
  created_at: "2026-09-12T11:59:58Z",
  updated_at: "2026-09-12T11:59:59Z",
  completed_at: null,
  error_code: null,
  output_binding: null,
};

function completed(status: "succeeded" | "partial"): MaterialProcessingRunView {
  return {
    ...base,
    status,
    progress_stage: "completed",
    completed_pages: 45,
    updated_at: clockTime.toISOString(),
    completed_at: clockTime.toISOString(),
    output_binding: {
      schema: "material-run-output-binding/v1",
      knowledge_structure_revision: revision,
      page_count: 45,
    },
  };
}

const unavailable = {
  schema: "api-error/v1",
  request_id: runId,
  reason_code: "STORAGE_UNAVAILABLE",
  retryable: true,
  message: "Request could not be completed.",
};
const evidence: MaterialProcessingRunView = {
  ...base,
  created_at: "2026-09-12T10:00:00Z",
  source_names: ["網路概論.pdf", "傳輸協定.pptx", "應用整合.pdf"],
};
const semantics: MaterialProcessingRunView = {
  ...base,
  progress_stage: "semantics",
  completed_pages: 45,
  completed_units: 20,
  total_units: 45,
};
const publishing: MaterialProcessingRunView = {
  ...base,
  progress_stage: "publishing",
  completed_pages: 45,
};

// 百分比的完整計算邊界由 material-flow.test.mjs 覆蓋；這裡驗證 UI 的不同呈現。
const activeCases: [
  name: string,
  run: MaterialProcessingRunView,
  overall: number | null,
  current: number | null,
  step: number,
][] = [
  [
    "pending",
    { ...base, status: "pending", progress_stage: "queued", completed_pages: 0, total_pages: null },
    0,
    null,
    0,
  ],
  ["evidence", evidence, null, 6, 1],
  ["semantics", semantics, null, 44, 2],
  ["semantics-full", { ...semantics, completed_units: 45 }, null, 100, 2],
  ["review", { ...semantics, progress_stage: "review", completed_units: 18, total_units: 49 }, null, 36, 3],
  ["publishing", publishing, null, null, 4],
  ["unknown-total", { ...base, completed_pages: 0, total_pages: null }, null, null, 1],
];

test.beforeEach(async ({ page }) => {
  await page.clock.install({ time: clockTime });
  await page.clock.pauseAt(clockTime);
  await page.route(/\/v[12]\//, (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    expect(request.method()).toBe(path === "/v1/session/refresh" ? "POST" : "GET");
    switch (path) {
      case "/v1/session":
      case "/v1/session/refresh":
        return route.fulfill({ json: { schema: "learner-identity/v1", learner_id: learnerId } });
      case "/v1/materials":
        return route.fulfill({ json: { schema: "material-library/v1", materials: [] } });
      default:
        throw new Error(`Unexpected processing request: ${request.method()} ${path}`);
    }
  });
});

async function mockRun(page: Page, respond: (route: Route) => Promise<void>) {
  let reads = 0;
  await page.route(`**/v1/material-processing-runs/${runId}`, (route) => {
    expect(route.request().method()).toBe("GET");
    reads++;
    return respond(route);
  });
  return () => reads;
}

for (const [name, run, overall, current, step] of activeCases)
  test(`processing ${name} shows measured progress and the current stage`, async ({ page }) => {
    const reads = await mockRun(page, (route) => route.fulfill({ json: run }));
    await page.goto(runPath);
    const processing = page.locator(".processing-page");
    await expect(processing.getByRole("heading", { level: 1 })).toHaveText(
      name === "pending" ? "等待開始處理" : "正在分析教材",
    );
    await expect(processing.getByRole("progressbar", { name: /^整體流程進度/ })).toHaveCount(0);
    await expect(processing.getByRole("heading", { name: `第 ${step + 1} / 5 階段`, exact: true })).toBeVisible();
    const stage = processing.locator(".processing-current");
    if (current === null) {
      await expect(stage.getByRole("heading")).toHaveText("目前狀態");
      await expect(stage.getByRole("progressbar")).toHaveCount(0);
      await expect(stage).toContainText(
        "進行中",
      );
      const indicator = stage.locator(".processing-status-indicator");
      await expect(indicator).toHaveAttribute("aria-hidden", "true");
      await expect(indicator).not.toHaveAttribute("role");
      await expect(processing.locator(".processing-status")).toHaveAttribute("aria-live", "polite");
    } else {
      await expect(stage.getByRole("heading")).toHaveText("本階段進度");
      await expect(stage.getByRole("progressbar")).toHaveAttribute("value", String(current));
      await expect(stage.getByRole("progressbar")).toHaveAttribute(
        "aria-label",
        `本階段進度 ${current}%，${materialStageCountLabel(run)}`,
      );
      await expect(stage.locator(".stage-pages")).toHaveText(
        materialStageCountLabel(run),
      );
      await expect(stage.locator(".processing-status-indicator")).toHaveCount(1);
    }
    const steps = processing.locator(".status-timeline > li");
    await expect(steps).toHaveCount(5);
    await expect(steps.nth(step)).toHaveAttribute("aria-current", "step");
    await expect(processing.locator(".status-timeline > li.is-complete")).toHaveCount(step);
    await expect(
      processing.getByRole("button", { name: "取消並刪除教材", exact: true }),
    ).toHaveCount(name === "publishing" ? 0 : 1);
    await expect(processing.getByRole("button", { name: "開啟知識地圖", exact: true })).toHaveCount(
      0,
    );
    if (run.source_names) {
      await expect(processing.locator(".processing-sources")).not.toHaveAttribute("open");
      await processing.locator(".processing-sources summary").click();
      await expect(processing.locator(".processing-sources li")).toHaveText(run.source_names);
      await expect(processing.locator(".processing-times")).toContainText("120 分 0 秒");
    }
    expect(reads()).toBe(1);
  });

for (const status of ["succeeded", "partial"] as const)
  test(`processing ${status} opens its published map without requiring review`, async ({
    page,
  }) => {
    const run = completed(status);
    const view = structureView();
    view.status.processing = status;
    view.status.quality = status === "partial" ? "needs_review" : "accepted";
    await mockKnowledgeMapApi(page, view);
    await mockRun(page, (route) => route.fulfill({ json: run }));
    await page.goto(runPath);
    const processing = page.locator(".processing-page");
    await expect(processing.getByRole("heading", { level: 1 })).toHaveText("教材整理完成");
    await expect(processing.locator(".processing-grid h2")).toHaveText(["處理摘要", "處理流程"]);
    await expect(processing.locator(".processing-summary")).toContainText("共處理 45 頁");
    await expect(processing.locator(".processing-summary ul > li")).toHaveText([
      "可回查的概念與學習重點",
      "教材中的概念關係",
      "教材建議學習順序",
    ]);
    await expect(processing.locator(".status-badge")).toHaveText(
      status === "partial" ? "部分結果可用" : "處理完成",
    );
    await expect(processing.locator(".status-badge")).toHaveClass(
      `status-badge ${status === "partial" ? "is-partial" : "is-success"}`,
    );
    if (status === "partial") {
      await expect(processing.locator(".status-badge svg")).toHaveCount(0);
      await expect(processing.locator(".processing-hero")).toContainText("部分內容未完整整理");
    } else await expect(processing).not.toContainText(/部分結果可用|未完整整理/);
    await expect(processing.locator(".completion-bar strong")).toHaveText(
      status === "partial" ? "知識地圖已建立" : "知識地圖已準備完成",
    );
    await expect(processing).not.toContainText(/待確認|待複核|需要你確認|請確認內容/);
    await expect(processing.getByRole("progressbar")).toHaveCount(0);
    await expect(
      processing.getByRole("button", { name: "取消並刪除教材", exact: true }),
    ).toHaveCount(0);
    await expect(processing.locator(".status-timeline > li.is-complete")).toHaveCount(5);
    await expect(processing.locator(".status-timeline p")).toHaveText(
      Array(5).fill("此階段已完成。"),
    );
    const openMap = processing.getByRole("button", { name: "開啟知識地圖", exact: true });
    await expect(openMap).toHaveClass("primary-button");
    await openMap.click();
    await expect(page).toHaveURL(
      new RegExp(`${runPath}/knowledge-structures/${encodeURIComponent(revision)}$`),
    );
    await expect(page.getByRole("tablist", { name: "教材學習內容" }).getByRole("tab", { name: "概念地圖", exact: true })).toHaveAttribute("aria-selected", "true");
    await expect(page.getByRole("region", { name: "概念地圖工作區", exact: true })).toBeVisible();
    await expect(page.locator(".concept-flow-node")).toHaveCount(view.concepts.length);
  });

test("analysis failure preserves its last progress and hides technical details until expanded", async ({
  page,
}) => {
  const reads = await mockRun(page, (route) =>
    route.fulfill({
      json: {
        ...base,
        status: "failed",
        error_code: "NO_USABLE_EVIDENCE",
        completed_at: clockTime.toISOString(),
      },
    }),
  );
  await page.goto(runPath);
  const processing = page.locator(".processing-page");
  await expect(processing.getByRole("heading", { level: 1 })).toHaveText("教材處理失敗");
  await expect(processing).toContainText("最後記錄進度：整理頁面與教材來源，已完成 3 / 45 頁");
  await expect(
    processing.getByRole("button", { name: "重新分析原來源", exact: true }),
  ).toBeEnabled();
  await expect(processing.getByRole("button", { name: "開啟知識地圖", exact: true })).toHaveCount(
    0,
  );
  await expect(processing.locator("code")).toBeHidden();
  await processing.getByText("技術資訊", { exact: true }).click();
  await expect(processing.locator("code")).toHaveText("NO_USABLE_EVIDENCE");
  await expect(processing.locator("code")).toBeVisible();
  await page.clock.runFor(5_000);
  expect(reads()).toBe(1);
  await processing.getByRole("button", { name: "返回教材庫", exact: true }).click();
  await expect(page).toHaveURL(/\/materials$/);
  await expect(page.getByRole("heading", { name: "尚未有學習教材", exact: true })).toBeVisible();
});

for (const width of [2560, 920, 390])
  for (const [name, run] of [
    ["active", evidence],
    ["completed", completed("partial")],
  ] as const)
    test(`processing ${name} fits its frame without clipping at ${width}px`, async ({ page }) => {
      await page.setViewportSize({ width, height: 844 });
      await mockRun(page, (route) => route.fulfill({ json: run }));
      await page.goto(runPath);
      const processing = page.locator(".processing-page");
      await expect(processing.getByRole("heading", { level: 1 })).toHaveText(
        name === "active" ? "正在分析教材" : "教材整理完成",
      );
      await expect(processing.locator(".processing-grid > section")).toHaveCount(2);
      const layout = await processing.evaluate((element) => {
        const main = element.parentElement!;
        const css = getComputedStyle(main);
        return {
          width: element.getBoundingClientRect().width,
          usable:
            main.getBoundingClientRect().width -
            parseFloat(css.paddingLeft) -
            parseFloat(css.paddingRight),
          cards: Array.from(element.querySelectorAll(".processing-grid > section"), (card) =>
            card.getBoundingClientRect().toJSON(),
          ),
          hero: element.querySelector(".processing-hero")!.getBoundingClientRect().toJSON(),
          heading: element.querySelector("h1")!.getBoundingClientRect().toJSON(),
          clipped: Array.from(element.querySelectorAll("h1, h2, h3, p, button, strong"))
            .filter((e) => e.scrollWidth > e.clientWidth + 1)
            .map((e) => e.textContent),
          documentWidth: document.documentElement.scrollWidth,
        };
      });
      expect(layout.width).toBeGreaterThan(0);
      expect(layout.width).toBeCloseTo(Math.min(layout.usable, 1600), 0);
      expect(layout.cards).toHaveLength(2);
      for (const card of layout.cards) {
        expect(card.width).toBeGreaterThan(0);
        expect(card.height).toBeGreaterThan(0);
      }
      const [content, timeline] = layout.cards;
      if (width > 920) {
        expect(content.y).toBeCloseTo(timeline.y, 0);
        expect(content.y).toBeLessThan(350);
        expect(timeline.x).toBeGreaterThan(content.x + content.width);
        if (name === "active") {
          expect(timeline.width).toBeCloseTo(320, 0);
          expect(timeline.height).toBeGreaterThan(0);
        }
      } else expect(timeline.y).toBeGreaterThanOrEqual(content.y + content.height);
      if (width > 620) expect(layout.heading.y).toBeCloseTo(layout.hero.y, 0);
      if (name === "active") {
        const progress = await processing.locator(".processing-status").boundingBox();
        const sources = await processing.locator(".processing-sources").boundingBox();
        expect(progress!.y + progress!.height).toBeLessThanOrEqual(sources!.y);
        expect(progress!.y).toBeLessThan(350);
      }
      expect(layout.documentWidth).toBe(width);
      expect(layout.clipped).toEqual([]);
      await expect(page.locator(".sidebar-helper")).toHaveCount(0);
    });

test("loading announces its state and resumes when the backend responds", async ({ page }) => {
  let release!: () => void;
  const pending = new Promise<void>((resolve) => {
    release = resolve;
  });
  await mockRun(page, async (route) => {
    await pending;
    await route.fulfill({ json: base });
  });
  await page.goto(runPath);
  const processing = page.locator(".processing-page");
  await expect(processing.getByRole("heading", { level: 1 })).toHaveText("正在讀取處理狀態");
  await expect(processing).toHaveAttribute("aria-live", "polite");
  await expect(processing.getByRole("progressbar")).toHaveCount(0);
  await expect(processing.getByRole("button", { name: "取消並刪除教材", exact: true })).toHaveCount(
    0,
  );
  release();
  await expect(processing.getByRole("heading", { level: 1 })).toHaveText("正在分析教材");
  await expect(
    processing.getByRole("progressbar", { name: "本階段進度 6%，已完成 3 / 45 頁", exact: true }),
  ).toBeVisible();
});

test("polling uses backend progress, stops at terminal, and clears on unmount", async ({
  page,
}) => {
  let server = base;
  const reads = await mockRun(page, (route) => route.fulfill({ json: server }));
  await page.goto(runPath);
  const overall = page.getByRole("progressbar", { name: /^本階段進度/ });
  await expect(overall).toHaveAttribute("value", "6");
  await page.clock.runFor(1_000);
  expect(reads()).toBe(1);
  await expect(overall).toHaveAttribute("value", "6");
  await expect(page.locator(".processing-times")).toContainText("3 秒");
  await page.clock.runFor(3_000);
  await expect.poll(reads).toBeGreaterThanOrEqual(2);
  await expect(overall).toHaveAttribute("value", "6");
  server = semantics;
  await page.clock.runFor(3_000);
  await expect(overall).toHaveAttribute("value", "44");
  server = completed("succeeded");
  await page.clock.runFor(3_000);
  await expect(page.getByRole("heading", { name: "教材整理完成", exact: true })).toBeVisible();
  const terminalReads = reads();
  await page.clock.runFor(6_000);
  expect(reads()).toBe(terminalReads);
  server = base;
  await page.goto(runPath);
  await expect(overall).toHaveAttribute("value", "6");
  await page.getByRole("button", { name: "教材庫", exact: true }).click();
  await expect(page).toHaveURL(/\/materials$/);
  await expect(page.getByRole("heading", { name: "尚未有學習教材", exact: true })).toBeVisible();
  const unmountedReads = reads();
  await page.clock.runFor(6_000);
  expect(reads()).toBe(unmountedReads);
});

test("read failure retries the original run without creating work", async ({ page }) => {
  let failed = true;
  const reads = await mockRun(page, (route) =>
    failed ? route.fulfill({ status: 503, json: unavailable }) : route.fulfill({ json: base }),
  );
  await page.goto(runPath);
  await expect(page.getByRole("heading", { name: "無法讀取處理狀態", exact: true })).toBeVisible();
  await page.clock.runFor(5_000);
  expect(reads()).toBe(1);
  failed = false;
  await page.getByRole("button", { name: "重新讀取", exact: true }).focus();
  await page.keyboard.press("Enter");
  await expect(
    page.getByRole("progressbar", { name: "本階段進度 6%，已完成 3 / 45 頁", exact: true }),
  ).toBeVisible();
  await expect(page).toHaveURL(new RegExp(`${runPath}$`));
  expect(reads()).toBe(2);
});

test("reduced motion preserves publishing status without animation", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await mockRun(page, (route) => route.fulfill({ json: publishing }));
  await page.goto(runPath);
  const indicator = page.locator(".processing-status-indicator");
  await expect(indicator).toBeVisible();
  expect(await indicator.evaluate((element) => getComputedStyle(element).animationName)).toBe(
    "none",
  );
  await expect(page.locator(".processing-status")).toContainText("組裝與發布知識地圖");
  await page.emulateMedia({ reducedMotion: "no-preference" });
  expect(await indicator.evaluate((element) => getComputedStyle(element).animationName)).toBe(
    "spin",
  );
});


test('saved page evidence offers resume after source context failure',async({page})=>{
 await mockRun(page,route=>route.fulfill({json:{...base,status:'failed',progress_stage:'evidence',total_pages:368,completed_pages:368,completed_at:clockTime.toISOString(),error_code:'DOCUMENT_EVIDENCE_INVALID',analysis_saved:true}}));
 await page.goto(runPath);
 await expect(page.getByRole('heading',{name:'教材處理失敗',exact:true})).toBeVisible();
 await expect(page.getByText('教材來源文字或章節結構未通過檢查，尚未發布知識地圖。請檢查來源後重試。')).toBeVisible();
 await expect(page.getByRole('button',{name:'接續已保存的分析',exact:true})).toBeVisible();
 await expect(page.getByText(/已完成的頁面整理或分析進度保存在本機/)).toBeVisible();
 await expect(page.locator('.failure-progress')).toContainText('368 / 368 頁');
});

for (const width of [1280, 390]) test(`large review progress survives reload and stale polls at ${width}px`, async ({page}, info) => {
  await page.setViewportSize({width, height:844});
  let server: MaterialProcessingRunView = {...base, progress_stage:"review", completed_pages:368, total_pages:368,
    completed_units:31,total_units:49,updated_at:"2026-09-12T12:00:00Z",
    source_names:Array.from({length:24},(_,i)=>`公開範例教材 ${i+1}.pdf`)};
  await mockRun(page, route=>route.fulfill({json:server}));
  await page.goto(runPath);
  await expect(page.getByRole("heading",{name:"第 4 / 5 階段",exact:true})).toBeVisible();
  await expect(page.locator(".stage-pages")).toHaveText("已完成 31 / 49 個檢核批次");
  await expect(page.locator(".overall-heading")).not.toContainText("%");
  await expect(page.locator(".processing-sources li").first()).toBeHidden();
  await page.reload();
  await expect(page.locator(".stage-pages")).toHaveText("已完成 31 / 49 個檢核批次");
  const current=server;
  server={...semantics,updated_at:"2026-09-12T11:59:59Z"};
  await page.clock.runFor(3000);
  await expect(page.locator(".stage-pages")).toHaveText("已完成 31 / 49 個檢核批次");
  await page.screenshot({path:info.outputPath(`review-${width}.png`),fullPage:true});
  await page.locator(".processing-sources summary").click();
  expect(await page.locator(".processing-sources ol").evaluate(e=>e.clientHeight)).toBeLessThanOrEqual(160);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBe(width);
  server={...current,status:"failed",error_code:"REVIEW_RESPONSE_INVALID",completed_at:"2026-09-12T12:00:02Z",updated_at:"2026-09-12T12:00:02Z"};
  await page.clock.runFor(3000);
  await expect(page.locator(".failure-progress")).toContainText("檢查與修正知識結構，已完成 31 / 49 個檢核批次");
});

test("cancelled review retains validated batches without declaring completion", async ({page})=>{
  await mockRun(page,route=>route.fulfill({json:{...base,status:"cancelled",progress_stage:"review",completed_units:18,total_units:49,
    cancel_requested_at:clockTime.toISOString(),completed_at:clockTime.toISOString()}}));
  await page.goto(runPath);
  await expect(page.getByRole("heading",{name:"已取消教材處理",exact:true})).toBeVisible();
  await expect(page.locator(".failure-progress")).toContainText("已完成 18 / 49 個檢核批次");
  await expect(page.getByRole("progressbar")).toHaveCount(0);
});
