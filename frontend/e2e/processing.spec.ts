import { expect, test, type Page } from "@playwright/test";
import type { MaterialProcessingRunView } from "../src/api/contracts";

const materialId = "11111111-1111-4111-8111-111111111111";
const runId = "22222222-2222-4222-8222-222222222222";
const artifactId = "33333333-3333-4333-8333-333333333333";
const revision = `knowledge-structure:sha256:${"a".repeat(64)}`;
const path = `/materials/${materialId}/runs/${runId}`;
const clockTime = new Date("2026-09-12T12:00:00Z");
const base: MaterialProcessingRunView = {
  schema: "material-processing-run/v1", cancel_requested_at: null, material_id: materialId, run_id: runId, source_artifact_id: artifactId,
  status: "running", progress_stage: "evidence", completed_pages: 3, total_pages: 45,
  created_at: "2026-09-12T11:59:58Z", updated_at: "2026-09-12T11:59:59Z", completed_at: null, error_code: null, output_binding: null,
};
function completed(status: "succeeded" | "partial"): MaterialProcessingRunView {
  return { ...base, status, progress_stage: "completed", completed_pages: 45, completed_at: "2026-09-12T12:00:00Z",
    output_binding: { schema: "material-run-output-binding/v1", knowledge_structure_revision: revision,
      runtime_lock_sha256: "b".repeat(64), page_count: 45, processing: status, quality: status === "partial" ? "needs_review" : "accepted",
      decision: "retain", reason_codes: [], ocr_calls: 0, semantic_calls: 1 } };
}
const cases: Record<string, MaterialProcessingRunView> = {
  pending: { ...base, status: "pending", progress_stage: "queued", total_pages: null, completed_pages: 0 },
  evidence: { ...base, source_names: ["網路概論.pdf", "傳輸協定.pptx", "應用整合.pdf"] },
  "evidence-full": { ...base, completed_pages: 45 },
  "semantics-zero": { ...base, progress_stage: "semantics", completed_pages: 0 },
  semantics: { ...base, progress_stage: "semantics", completed_pages: 20 },
  "semantics-36": { ...base, progress_stage: "semantics", completed_pages: 36 },
  "semantics-full": { ...base, progress_stage: "semantics", completed_pages: 45 },
  publishing: { ...base, progress_stage: "publishing", completed_pages: 45 },
  succeeded: completed("succeeded"), partial: completed("partial"),
  failed: { ...base, status: "failed", error_code: "NO_USABLE_EVIDENCE", completed_at: "2026-09-12T12:00:00Z" },
  "api-failure": base, loading: base,
  "long-elapsed": { ...base, created_at: "2026-09-12T10:00:00Z" },
  "unknown-total": { ...base, progress_stage: "queued", completed_pages: 0, total_pages: null },
};
const percentages: Record<string, [number, number | null]> = {
  pending: [0, null], evidence: [3, 7], "evidence-full": [49, 100], "semantics-zero": [49, 0], semantics: [71, 44],
  "semantics-36": [89, 80], "semantics-full": [99, 100], publishing: [99, null], "long-elapsed": [3, 7], "unknown-total": [0, null],
};
async function session(page: Page) {
  await page.route("**/v1/session/refresh", route => route.fulfill({ json: { schema: "learner-identity/v1", learner_id: "33333333-3333-4333-8333-333333333333" } }));
  await page.route("**/v1/session", route => route.fulfill({ json: { schema: "learner-identity/v1", learner_id: materialId } }));
}

for (const viewport of [{ width: 1920, height: 1080 }, { width: 1536, height: 1024 }, { width: 1366, height: 768 }, { width: 390, height: 844 }]) {
  const selected = viewport.width === 1536 ? Object.keys(cases) : ["pending", "evidence", "semantics", "publishing", "succeeded", "partial", "failed"];
  for (const name of selected) {
    test(`processing ${name} at ${viewport.width}px is truthful and usable`, async ({ page }) => {
      await page.setViewportSize(viewport); await page.clock.install({ time: clockTime }); await page.clock.pauseAt(clockTime); await session(page);
      const run = cases[name];
      const productRequests: string[] = [];
      page.on("request", request => { if (/\/v1\/(materials|material-processing-runs)/.test(request.url())) productRequests.push(request.method() + " " + new URL(request.url()).pathname); });
      await page.route(`**/v1/material-processing-runs/${runId}`, route => name === "loading" ? undefined : name === "api-failure"
        ? route.fulfill({ status: 503, json: { schema: "api-error/v1", request_id: materialId, reason_code: "STORAGE_UNAVAILABLE", retryable: true, message: "Request could not be completed." } })
        : route.fulfill({ json: run }));
      await page.goto(path);
      const processing = page.locator(".processing-page.task-page");
      await expect(processing).toBeVisible();
      await expect(page.locator(".sidebar-helper")).toHaveCount(0);
      await expect(processing).not.toContainText(/Material Processing|Processing complete|Claim|三種概念連結|開啟複核地圖|發布可複核結果/);
      await expect(processing.getByRole("button", { name: "取消並刪除教材", exact: true })).toHaveCount(
        name !== "loading" && name !== "api-failure" && (run.status === "pending" || run.status === "running") && run.progress_stage !== "publishing" ? 1 : 0);
      await expect(processing.getByRole("button", { name: "重新分析", exact: true })).toHaveCount(0);
      const usable = await page.locator(".app-main").evaluate(el => {
        const css = getComputedStyle(el);
        return el.getBoundingClientRect().width - parseFloat(css.paddingLeft) - parseFloat(css.paddingRight);
      });
      expect((await processing.boundingBox())!.width).toBeCloseTo(Math.min(usable, 1600), 0);
      if (name === "loading") await expect(processing).toHaveAttribute("aria-live", "polite");
      else if (name === "api-failure") {
        await expect(processing.getByRole("heading", { name: "無法讀取處理狀態", exact: true })).toBeVisible();
        await expect(processing.getByRole("button", { name: "重新讀取", exact: true })).toBeVisible();
      } else if (run.status === "failed") {
        await expect(processing.getByRole("heading", { name: "教材處理失敗", exact: true })).toBeVisible();
        await expect(processing).toContainText("最後記錄進度：整理頁面與教材來源，3 / 45 頁");
        await expect(processing.locator("details")).not.toHaveAttribute("open", "");
        await expect(processing.locator("code")).toBeHidden();
      } else if (run.status === "succeeded" || run.status === "partial") {
        await expect(processing.getByRole("heading", { level: 1 })).toHaveText("教材整理完成");
        await expect(processing.locator(".processing-grid > section.processing-card")).toHaveCount(2);
        await expect(processing.locator(".processing-grid h2")).toHaveText(["處理摘要", "處理流程"]);
        await expect(processing.getByRole("heading", { name: "可查看內容", exact: true })).toBeVisible();
        await expect(processing.locator(".processing-summary")).toContainText("共處理 45 頁");
        await expect(processing.locator(".processing-summary ul > li")).toHaveText(["可回查的概念與學習重點", "教材中的概念關係", "教材建議學習順序"]);
        await expect(processing.locator(".status-badge")).toHaveClass(`status-badge ${run.status === "partial" ? "is-partial" : "is-success"}`);
        await expect(processing.locator(".status-badge")).toHaveText(run.status === "partial" ? "部分結果可用" : "處理完成");
        if (run.status === "partial") await expect(processing.locator(".status-badge svg")).toHaveCount(0);
        await expect(processing.locator(".status-timeline > li")).toHaveCount(4);
        await expect(processing.locator(".status-timeline > li.is-complete")).toHaveCount(4);
        await expect(processing.locator(".status-timeline strong")).toHaveText(["等待處理資源", "整理頁面與教材來源", "建立概念、關係與學習順序", "發布知識地圖"]);
        await expect(processing.locator(".status-timeline p")).toHaveText(Array(4).fill("此階段已完成。"));
        await expect(processing.getByRole("progressbar")).toHaveCount(0);
        await expect(processing.locator(".complete-progress, .processing-stack, .result-summary")).toHaveCount(0);
        await expect(processing).not.toContainText(/100%|已發布內容|處理結果|一切準備完成/);
        await expect(processing.locator("img")).toHaveCount(1);
        await expect(processing.locator('img[src$="processing-complete.png"]')).toHaveCount(0);
        await expect(processing.locator(".completion-bar strong")).toHaveText(run.status === "partial" ? "知識地圖已建立" : "知識地圖已準備完成");
        await expect(processing.locator(".processing-hero > div > p:last-child")).toHaveText(run.status === "partial" ? "知識地圖已建立，可先查看已整理的內容；部分內容未完整整理。" : "知識地圖已準備完成，可以查看概念、關係、來源與建議學習順序。");
        await expect(processing.locator(".completion-bar p")).toHaveText(run.status === "partial" ? "可以查看已整理的概念、關係與來源。" : "可以查看概念、關係、來源與建議學習順序。");
        await expect(processing).not.toContainText(/待確認|待複核|需要你確認|請確認內容/);
        await expect(processing.locator(".processing-grid")).not.toContainText("未完整整理");
        await expect(processing.locator(".completion-bar")).not.toContainText("未完整整理");
        if (run.status === "succeeded") await expect(processing).not.toContainText(/部分結果可用|未完整整理/);
        await expect(processing.getByRole("button", { name: "開啟知識地圖", exact: true })).toHaveClass("primary-button");
        await expect(processing).toContainText("可回查的概念與學習重點");
      } else {
        await expect(processing.getByRole("heading", { name: "整體流程進度（估計）", exact: true })).toBeVisible();
        await expect(processing.getByRole("heading", { name: percentages[name][1] === null ? "目前狀態" : "本階段進度", exact: true })).toBeVisible();
        await expect(processing.getByRole("heading", { name: "處理流程", exact: true })).toBeVisible();
        await expect(processing.locator(".progress-estimate-note")).toHaveText("依處理階段與頁數估算，不代表剩餘時間。");
        for (const oldText of ["整體進度（估計）", "目前階段", "實際處理階段", "已經過", "剩餘時間"]) {
          await expect(processing.getByText(oldText, { exact: true })).toHaveCount(0);
        }
        const cards = await processing.locator(".processing-grid > section").evaluateAll(es => es.map(e => e.getBoundingClientRect().toJSON()));
        if (viewport.width > 920) {
          expect(cards[1].width).toBeCloseTo(320, 0);
          expect(cards[0].width).toBeGreaterThan(cards[1].width);
          expect(cards[1].height).toBeLessThan(cards[0].height);
        }
        if (name === "evidence") {
          const progress = (await processing.locator(".processing-status").boundingBox())!;
          const sources = (await processing.locator(".processing-sources").boundingBox())!;
          expect(progress.y).toBeLessThan(sources.y);
          expect(progress.y).toBeLessThan(350);
        }
        const [overall, current] = percentages[name];
        await expect(processing.getByRole("progressbar", { name: `整體流程進度（估計） ${overall}%`, exact: true })).toHaveAttribute("value", String(overall));
        if (current !== null) {
          await expect(processing.locator(".processing-status-indicator")).toHaveCount(0);
          await expect(processing.getByRole("progressbar", { name: `本階段進度 ${current}%，已完成 ${run.completed_pages} / 45 頁`, exact: true })).toHaveAttribute("value", String(current));
          await expect(processing.getByText(`已完成 ${run.completed_pages} / 45 頁`, { exact: true })).toBeVisible();
        } else {
          const status = processing.locator(".processing-status");
          await expect(status.getByRole("progressbar")).toHaveCount(1);
          await expect(status.getByRole("heading", { name: "本階段進度", exact: true })).toHaveCount(0);
          await expect(status.getByText("等待開始", { exact: true })).toHaveCount(0);
          await expect(status.locator(".indeterminate-progress")).toHaveCount(0);
          const indicator = status.locator(".processing-status-indicator");
          await expect(indicator).toHaveAttribute("aria-hidden", "true");
          for (const attribute of ["role", "aria-valuenow", "aria-valuemin", "aria-valuemax"]) expect(await indicator.getAttribute(attribute)).toBeNull();
          await expect(status).toHaveAttribute("aria-live", "polite");
          await expect(status).toContainText(name === "publishing" ? "發布中" : "排隊中");
          await expect(status).toContainText(name === "publishing" ? "正在整理並發布可開啟的知識地圖。" : "正在等待本機處理資源，開始後會自動更新進度。");
          if (name === "publishing") await expect(processing.locator(".processing-status")).not.toContainText("100%");
        }
        if (name === "long-elapsed") await expect(processing.locator(".processing-times")).toContainText("120 分 0 秒");
        await expect(processing.locator(".processing-times dt")).toHaveText(["已耗時", "最近更新"]);
        await expect(processing).toContainText("可稍後從「我的教材」返回查看");
      }
      if (viewport.width > 620 && name !== "failed" && name !== "api-failure") {
        const hero = await processing.locator(".processing-hero").boundingBox();
        if (!await processing.evaluate(el => el.classList.contains("is-processing"))) expect(hero!.height).toBeGreaterThanOrEqual(104);
        const heading = (await processing.locator("h1").boundingBox())!;
        expect(heading.y).toBeCloseTo(hero!.y, 0);
      }
      if (["evidence", "semantics", "publishing", "succeeded", "partial"].includes(name)) {
        const cards = await processing.locator(".processing-grid > section").evaluateAll(elements => elements.map(element => element.getBoundingClientRect().toJSON()));
        if (viewport.width > 920) { expect(cards[0].y).toBe(cards[1].y); expect(cards[0].y).toBeLessThan(350); }
        else expect(cards[1].y).toBeGreaterThanOrEqual(cards[0].y + cards[0].height);
      }
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(viewport.width);
      expect(await processing.locator("h1, h2, h3, p, button, strong").evaluateAll(elements => elements.filter(element => element.scrollWidth > element.clientWidth + 1).map(element => element.tagName))).toEqual([]);
      await page.screenshot({ path: `/tmp/studydy-processing/${viewport.width}-${name}.png`, fullPage: true });
      expect(productRequests).toEqual([`GET /v1/material-processing-runs/${runId}`]);
      if (run.status === "failed") {
        await processing.getByText("技術資訊", { exact: true }).click(); await expect(processing.locator("code")).toHaveText("NO_USABLE_EVIDENCE"); await expect(processing.locator("code")).toBeVisible();
        await processing.getByRole("button", { name: "返回教材庫", exact: true }).click(); await expect(page).toHaveURL(/\/materials$/);
      }
      if (run.status === "succeeded" || run.status === "partial") {
        await processing.getByRole("button", { name: "開啟知識地圖", exact: true }).click();
        expect(new URL(page.url()).pathname).toBe(`${path}/knowledge-structures/${encodeURIComponent(revision)}`);
      }
    });
  }
}

test("processing polling uses backend values only, stops at terminal, and clears on unmount", async ({ page }) => {
  await page.clock.install({ time: clockTime }); await page.clock.pauseAt(clockTime); await session(page);
  let server = { ...base }; let reads = 0;
  await page.route(`**/v1/material-processing-runs/${runId}`, route => { reads++; return route.fulfill({ json: server }); });
  await page.goto(path); await expect(page.getByRole("progressbar", { name: "整體流程進度（估計） 3%", exact: true })).toBeVisible();
  await page.clock.runFor(1000); expect(reads).toBe(1);
  await expect(page.getByRole("progressbar", { name: "整體流程進度（估計） 3%", exact: true })).toHaveAttribute("value", "3");
  await expect(page.locator(".processing-times")).toContainText("3 秒");
  let response = page.waitForResponse(`**/v1/material-processing-runs/${runId}`); await page.clock.runFor(500); await response; expect(reads).toBe(2);
  await expect(page.getByRole("progressbar", { name: "整體流程進度（估計） 3%", exact: true })).toHaveAttribute("value", "3");
  server = cases.semantics;
  response = page.waitForResponse(`**/v1/material-processing-runs/${runId}`); await page.clock.runFor(1500); await response;
  await expect(page.getByRole("progressbar", { name: "整體流程進度（估計） 71%", exact: true })).toHaveAttribute("value", "71");
  server = completed("succeeded");
  response = page.waitForResponse(`**/v1/material-processing-runs/${runId}`); await page.clock.runFor(1500); await response;
  await expect(page.getByRole("heading", { name: "教材整理完成", exact: true })).toBeVisible(); expect(reads).toBe(4);
  await page.clock.runFor(6001); expect(reads).toBe(4);
  server = base; await page.goto(path); await expect(page.getByRole("progressbar", { name: "整體流程進度（估計） 3%", exact: true })).toBeVisible(); expect(reads).toBe(5);
  await page.getByRole("button", { name: /^(教材庫|我的教材)$/, exact: true }).click(); await expect(page).toHaveURL(/\/materials$/);
  await page.clock.runFor(6001); expect(reads).toBe(5);
});

test("read failure retry only reads the original run and never creates work", async ({ page }) => {
  await session(page);
  let failed = true; const writes: string[] = [];
  page.on("request", request => { if (request.method() !== "GET" && /\/v1\/(materials|material-processing-runs)/.test(request.url())) writes.push(request.url()); });
  await page.route(`**/v1/material-processing-runs/${runId}`, route => failed
    ? route.fulfill({ status: 503, json: { schema: "api-error/v1", request_id: materialId, reason_code: "STORAGE_UNAVAILABLE", retryable: true, message: "Request could not be completed." } })
    : route.fulfill({ json: base }));
  await page.goto(path); await expect(page.getByRole("heading", { name: "無法讀取處理狀態", exact: true })).toBeVisible();
  failed = false; await page.getByRole("button", { name: "重新讀取", exact: true }).click();
  await expect(page.getByRole("progressbar", { name: "整體流程進度（估計） 3%", exact: true })).toBeVisible();
  await expect(page).toHaveURL(new RegExp(path + "$")); expect(writes).toEqual([]);
});


test("processing status respects reduced motion without removing its wording", async ({ page }) => {
  await session(page);
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.route(`**/v1/material-processing-runs/${runId}`, route => route.fulfill({ json: cases.publishing }));
  await page.goto(path);
  const indicator = page.locator(".processing-status-indicator");
  await expect(indicator).toBeVisible();
  expect(await indicator.evaluate(element => getComputedStyle(element).animationName)).toBe("none");
  await expect(page.locator(".processing-status")).toContainText("發布中");
  await page.emulateMedia({ reducedMotion: "no-preference" });
  expect(await indicator.evaluate(element => getComputedStyle(element).animationName)).toBe("spin");
});
