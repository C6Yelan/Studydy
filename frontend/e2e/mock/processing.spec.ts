import { expect, test, type Page, type Route } from "@playwright/test";
import type { MaterialProcessingRunView } from "../../src/api/contracts";

import { materialStageCountLabel } from "../../src/features/material-flow/material-flow";
import { structureView, mockKnowledgeMapApi } from "../fixtures/knowledge-map";
import { emptyLearningActivity } from "../fixtures/learning-activity";

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

const uploadedSource = {
  origin: "upload",
  source_id: "66666666-6666-4666-8666-666666666666",
  normalization_id: "77777777-7777-4777-8777-777777777777",
  original_artifact_id: artifactId,
  original_name: "網路概論.pdf",
  media_type: "application/pdf",
  status: "ready",
  normalized_artifact_id: artifactId,
  page_count: 45,
  error_code: null,
};

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
  ["indexing", { ...semantics, progress_stage: "indexing", completed_units: 20, total_units: 100 }, null, 20, 4],
  ["publishing", publishing, null, null, 5],
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
      case `/v1/materials/${materialId}`:
        return route.fulfill({json:{schema:'material-library-item/v1',material_id:materialId,source_artifact_id:artifactId,display_name:'合成測試教材',size_bytes:1000,created_at:base.created_at,latest_attempt:base,available_structures:[],study_sessions:[]}});
      case "/v1/materials":
        return route.fulfill({ json: { schema: "material-library/v1", materials: [] } });
      case "/v1/topics":
        return route.fulfill({ json: { topics: [] } });
      // 頁首帳號設定讀取名稱；這些案例不涉及名稱。
      case "/v1/profile":
        return route.fulfill({ json: { schema: "learner-profile/v1", display_name: null } });
      // 頁首學習進度；這些案例不涉及學習紀錄。
      case "/v1/learning-activity":
        return route.fulfill({ json: emptyLearningActivity });
      case "/v1/account/storage":
        return route.fulfill({ json: { schema: "account-storage/v2", used_bytes: 0, reserved_bytes: 0, limit_bytes: 5368709120, remaining_bytes: 5368709120, breakdown: { material_files_bytes: 0, podcast_audio_bytes: 0, podcast_video_bytes: 0, analysis_bytes: 0, reserved_bytes: 0 }, largest_materials: [] } });
      default:
        throw new Error(`Unexpected processing request: ${request.method()} ${path}`);
    }
  });
  await mockSources(page);
});

// 處理頁依教材來源決定步驟列；預設是上傳的教材。
async function mockSources(page: Page, sources = [uploadedSource]) {
  await page.route(`**/v1/materials/${materialId}/sources`, (route) =>
    route.fulfill({ json: { schema: "material-sources/v1", material_id: materialId, sources } }));
}

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
    await expect(processing.getByRole("heading", { name: `第 ${step + 1} / 6 階段`, exact: true })).toBeVisible();
    const stage = processing.locator(".processing-current");
    if (current === null) {
      await expect(stage.getByRole("heading")).toHaveText("目前狀態");
      await expect(stage.getByRole("progressbar")).toBeVisible();
      await expect(stage.getByRole("progressbar")).not.toHaveAttribute("aria-valuenow");
      await expect(stage).toContainText(
        "進行中",
      );
      const indicator = stage.locator(".processing-status-indicator");
      await expect(indicator).toHaveAttribute("aria-hidden", "true");
      await expect(indicator).not.toHaveAttribute("role");
      await expect(processing.locator(".processing-status")).toHaveAttribute("aria-live", "polite");
    } else {
      await expect(stage.getByRole("heading")).toHaveText("本階段進度");
      await expect(stage.getByRole("progressbar")).toHaveAttribute("aria-valuenow", String(current));
      await expect(stage.getByRole("progressbar")).toHaveAttribute(
        "aria-label",
        `本階段進度 ${current}%`,
      );
      await expect(stage.locator(".stage-pages")).toHaveText(
        materialStageCountLabel(run),
      );
      await expect(stage.locator(".processing-status-indicator")).toHaveCount(1);
    }
    const steps = processing.locator(".status-timeline > li");
    await expect(steps).toHaveCount(6);
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
    await mockSources(page);
    await mockRun(page, (route) => route.fulfill({ json: run }));
    await page.goto(runPath);
    const processing = page.locator(".processing-page");
    await expect(processing.getByRole("heading", { level: 1 })).toHaveText("教材整理完成");
    await expect(processing.locator(".flow-result h2")).toHaveText(
      "知識地圖已準備完成",
    );
    await expect(processing.locator(".processing-summary")).toContainText("共處理 45 頁");
    await expect(processing.locator(".processing-summary ul > li")).toHaveText([
      "可回查的概念與學習重點",
      "教材中的概念關係",
      "教材建議學習順序",
    ]);
    await expect(processing.locator(".status-badge")).toHaveText(
      "處理完成",
    );
    await expect(processing.locator(".status-badge")).toHaveClass(
      "status-badge is-success",
    );
    await expect(processing.getByText("由 AI 整理，每個重點都可點開原文對照。",{exact:true})).toBeVisible();
    await expect(processing).not.toContainText(/待確認|待複核|需要你確認|請確認內容/);
    await expect(processing.getByRole("progressbar")).toHaveCount(0);
    await expect(
      processing.getByRole("button", { name: "取消並刪除教材", exact: true }),
    ).toHaveCount(0);
    await expect(processing.locator(".learning-steps > li.is-complete")).toHaveCount(3);
    const openMap = processing.getByRole("button", { name: "開啟知識地圖", exact: true });
    await expect(openMap).toHaveClass("primary-button");
    // 進度斷言完成後恢復計時，讓地圖的 lazy/Suspense 轉場正常完成。
    await page.clock.resume();
    await openMap.click();
    await expect(page).toHaveURL(
      new RegExp(`${runPath}/knowledge-structures/${encodeURIComponent(revision)}$`),
    );
    await expect(page.getByRole("tablist", { name: "教材學習內容" }).getByRole("tab", { name: "知識地圖", exact: true })).toHaveAttribute("aria-selected", "true");
    await expect(page.getByRole("region", { name: "知識地圖工作區", exact: true })).toBeVisible();
    await expect(page.locator(".concept-flow-node")).toHaveCount(view.concepts.length);
  });

test("analysis failure preserves its last progress and shows the error code", async ({
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
  await expect(processing.locator("code")).toHaveText("NO_USABLE_EVIDENCE");
  await expect(processing.locator("code")).toBeVisible();
  await page.clock.runFor(5_000);
  expect(reads()).toBe(1);
  await processing.getByRole("button", { name: "回到我的教材", exact: true }).click();
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
      const active = name === "active";
      if (active) await expect(processing.locator(".processing-grid > section")).toHaveCount(2);
      else await expect(processing.locator(".flow-result")).toHaveCount(1);
      const layout = await processing.evaluate((element) => {
        const main = element.parentElement!;
        const css = getComputedStyle(main);
        return {
          width: element.getBoundingClientRect().width,
          usable:
            main.getBoundingClientRect().width -
            parseFloat(css.paddingLeft) -
            parseFloat(css.paddingRight),
          cards: Array.from(element.querySelectorAll(".processing-grid > section, .flow-result"), (card) =>
            card.getBoundingClientRect().toJSON(),
          ),
          steps: element.querySelector(".learning-steps")!.getBoundingClientRect().toJSON(),
          clipped: Array.from(element.querySelectorAll("h1, h2, h3, p, button, strong"))
            .filter((e) => e.scrollWidth > e.clientWidth + 1)
            .map((e) => e.textContent),
          documentWidth: document.documentElement.scrollWidth,
        };
      });
      // 處理頁是一般頁面：填滿可用寬度，最寬到全站外框上限 --application-frame-max（1600px）。
      expect(layout.width).toBeCloseTo(Math.min(layout.usable, 1600), 0);
      const stepsBottom = layout.steps.y + layout.steps.height;
      for (const card of layout.cards) {
        expect(card.width).toBeGreaterThan(0);
        expect(card.height).toBeGreaterThan(0);
        expect(card.y).toBeGreaterThanOrEqual(stepsBottom);
      }
      // 主要內容在第一屏：處理卡（或完成卡）緊接在步驟列下方，不被其他區塊推到下面。
      expect(layout.cards[0].y - stepsBottom).toBeLessThanOrEqual(48);
      if (!active) {
        const openMap = await processing.getByRole("button", { name: "開啟知識地圖", exact: true }).boundingBox();
        expect(openMap!.y + openMap!.height).toBeLessThanOrEqual(844);
      }
      if (active) {
        const [content, timeline] = layout.cards;
        if (width > 920) {
          expect(content.y).toBeCloseTo(timeline.y, 0);
          expect(timeline.x).toBeGreaterThan(content.x + content.width);
        } else expect(timeline.y).toBeGreaterThanOrEqual(content.y + content.height);
        const progress = await processing.locator(".processing-status").boundingBox();
        const sources = await processing.locator(".processing-sources").boundingBox();
        expect(progress!.y + progress!.height).toBeLessThanOrEqual(sources!.y);
        expect(progress!.y).toBeGreaterThanOrEqual(layout.cards[0].y);
        expect(progress!.y + progress!.height).toBeLessThanOrEqual(844);
      }
      expect(layout.documentWidth).toBe(width);
      expect(layout.clipped).toEqual([]);
    });

for (const origin of ["research", "mixed", "unreadable"] as const)
  test(`processing steps follow the material origin (${origin})`, async ({ page }) => {
    const research = { ...uploadedSource, origin: "research" };
    if (origin === "unreadable")
      await page.route(`**/v1/materials/${materialId}/sources`, (route) => route.fulfill({ status: 503, json: unavailable }));
    else await mockSources(page, origin === "mixed" ? [research, uploadedSource] : [research]);
    await mockRun(page, (route) => route.fulfill({ json: completed("succeeded") }));
    await page.goto(runPath);
    const processing = page.locator(".processing-page");
    await expect(processing.getByRole("button", { name: "開啟知識地圖", exact: true })).toBeVisible();
    if (origin === "research") {
      // 從主題建立的教材重試後也回到這頁，步驟列沿用主題流程。
      await expect(processing.locator(".learning-steps > li")).toHaveText(["學習需求", "確認觀念與細節", "選取來源", "建立教材"].map((label) => new RegExp(label)));
      await expect(processing.locator(".learning-steps > li.is-complete")).toHaveCount(4);
    } else await expect(processing.locator(".learning-steps")).toHaveCount(0);
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
    processing.getByRole("progressbar", { name: "本階段進度 6%", exact: true }),
  ).toBeVisible();
});

test("polling uses backend progress, stops at terminal, and clears on unmount", async ({
  page,
}) => {
  let server = base;
  const reads = await mockRun(page, (route) => route.fulfill({ json: server }));
  await page.goto(runPath);
  const overall = page.getByRole("progressbar", { name: /^本階段進度/ });
  await expect(overall).toHaveAttribute("aria-valuenow", "6");
  await page.clock.runFor(1_000);
  expect(reads()).toBe(1);
  await expect(overall).toHaveAttribute("aria-valuenow", "6");
  await expect(page.locator(".processing-times")).toContainText("3 秒");
  await page.clock.runFor(3_000);
  await expect.poll(reads).toBeGreaterThanOrEqual(2);
  await expect(overall).toHaveAttribute("aria-valuenow", "6");
  server = semantics;
  await page.clock.runFor(3_000);
  await expect(overall).toHaveAttribute("aria-valuenow", "44");
  server = completed("succeeded");
  await page.clock.runFor(3_000);
  await expect(page.getByRole("heading", { name: "教材整理完成", exact: true })).toBeVisible();
  const terminalReads = reads();
  await page.clock.runFor(6_000);
  expect(reads()).toBe(terminalReads);
  server = base;
  await page.goto(runPath);
  await expect(overall).toHaveAttribute("aria-valuenow", "6");
  await page.getByRole("navigation", { name: "主要導覽" }).getByRole("link", { name: "我的教材", exact: true }).click();
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
    page.getByRole("progressbar", { name: "本階段進度 6%", exact: true }),
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
 await expect(page.getByText(/已完成的處理進度已保存/)).toBeVisible();
 await expect(page.locator('.failure-progress')).toContainText('368 / 368 頁');
});

for (const width of [1280, 390]) test(`large review progress survives reload and stale polls at ${width}px`, async ({page}) => {
  await page.setViewportSize({width, height:844});
  let server: MaterialProcessingRunView = {...base, progress_stage:"review", completed_pages:368, total_pages:368,
    completed_units:31,total_units:49,updated_at:"2026-09-12T12:00:00Z",
    source_names:Array.from({length:24},(_,i)=>`公開範例教材 ${i+1}.pdf`)};
  await mockRun(page, route=>route.fulfill({json:server}));
  await page.goto(runPath);
  await expect(page.getByRole("heading",{name:"第 4 / 6 階段",exact:true})).toBeVisible();
  await expect(page.locator(".stage-pages")).toHaveText("已完成 63%");
  await expect(page.locator(".overall-heading")).not.toContainText("%");
  await expect(page.locator(".processing-sources li").first()).toBeHidden();
  await page.reload();
  await expect(page.locator(".stage-pages")).toHaveText("已完成 63%");
  const current=server;
  server={...semantics,updated_at:"2026-09-12T11:59:59Z"};
  await page.clock.runFor(3000);
  await expect(page.locator(".stage-pages")).toHaveText("已完成 63%");
  await page.locator(".processing-sources summary").click();
  expect(await page.locator(".processing-sources ol").evaluate(e=>e.clientHeight)).toBeLessThanOrEqual(160);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBe(width);
  server={...current,status:"failed",error_code:"REVIEW_RESPONSE_INVALID",completed_at:"2026-09-12T12:00:02Z",updated_at:"2026-09-12T12:00:02Z"};
  await page.clock.runFor(3000);
  await expect(page.locator(".failure-progress")).toContainText("檢查與修正知識結構，已完成 63%");
});

test("cancelled review retains validated batches without declaring completion", async ({page})=>{
  await mockRun(page,route=>route.fulfill({json:{...base,status:"cancelled",progress_stage:"review",completed_units:18,total_units:49,
    cancel_requested_at:clockTime.toISOString(),completed_at:clockTime.toISOString()}}));
  await page.goto(runPath);
  await expect(page.getByRole("heading",{name:"已取消教材處理",exact:true})).toBeVisible();
  await expect(page.locator(".failure-progress")).toContainText("已完成 36%");
  await expect(page.getByRole("progressbar")).toHaveCount(0);
});

for (const width of [1280, 390]) test(`failed review explains the cause and preserves resume at ${width}px`, async ({page}) => {
  await page.setViewportSize({width, height:844});
  await page.route(`**/v1/materials/${materialId}`, route => route.fulfill({json:{
    schema:'material-library-item/v1', material_id:materialId, source_artifact_id:artifactId,
    display_name:'合成教材', size_bytes:1000, created_at:base.created_at,
    latest_attempt:null, study_sessions:[], available_structures:[], head_revision:revision,
  }}));
  await mockRun(page, route => route.fulfill({json: {...base, base_revision:revision,
    status:'failed', progress_stage:'review', completed_pages:1000, total_pages:1000,
    completed_units:87, total_units:224, completed_at:clockTime.toISOString(),
    error_code:'REVIEW_METADATA_REASON_INVALID', analysis_saved:true,
    source_names:Array.from({length:41},(_,i)=>`合成來源 ${i+1}.pdf`)}}));
  await page.goto(runPath);
  await expect(page.getByRole('heading',{name:'這次更新未完成',exact:true})).toBeVisible();
  await expect(page.getByText(/模型將概念標為非教學內容，但沒有提供允許的分類理由/)).toBeVisible();
  await expect(page.getByText('REVIEW_METADATA_REASON_INVALID',{exact:true})).toBeVisible();
  await expect(page.locator('.failure-progress')).toContainText('38%');
  await expect(page.getByRole('button',{name:'接續已保存的分析',exact:true})).toBeVisible();
  await expect(page.getByText(/已完成的處理進度已保存/)).toBeVisible();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});


test('processing failure keeps recovery order and completion keeps its source notice across widths',async({page},testInfo)=>{
 let current:MaterialProcessingRunView={...base,source_names:['合成測試教材.pdf']};
 await mockRun(page,route=>route.fulfill({json:current}));
 for(const state of ['running','failed','partial'] as const){
  current=state==='partial'?completed('partial'):{...base,status:state,source_names:['合成測試教材.pdf'],analysis_saved:state==='failed',input_source_set_id:artifactId,completed_at:state==='failed'?clockTime.toISOString():null,error_code:state==='failed'?'STORAGE_UNAVAILABLE':null};
  await page.goto(runPath);await expect(page.locator('.run-material-return')).toContainText('合成測試教材');
  if(state==='failed'){
   const card=page.locator('.run-failure-content');await expect(card.getByRole('button',{name:'接續已保存的分析'})).toBeVisible();
   const order=await card.evaluate(element=>{
    const progress=element.querySelector('.failure-progress')!, action=element.querySelector('.run-failure-actions')!, code=element.querySelector('.run-failure-code')!;
    return !!(progress.compareDocumentPosition(action)&Node.DOCUMENT_POSITION_FOLLOWING)&&!!(action.compareDocumentPosition(code)&Node.DOCUMENT_POSITION_FOLLOWING);
   });expect(order).toBe(true);
   await expect(card.locator('.run-failure-code')).toContainText('STORAGE_UNAVAILABLE');
  }
  for(const [width,height] of [[1440,900],[1300,900],[1200,900],[1024,768],[390,844]]){
   await page.setViewportSize({width,height});await page.screenshot({path:testInfo.outputPath(`processing-${state}-${width}.png`),animations:'disabled'});
   expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  }
 }
});
