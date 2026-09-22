import { expect, test, type Page } from "@playwright/test";
import type { MaterialProcessingRunView } from "../src/api/contracts";

const mid = "11111111-1111-4111-8111-111111111111";
const rid = "22222222-2222-4222-8222-222222222222";
const path = `/materials/${mid}/runs/${rid}`;
const time = new Date("2026-09-12T12:00:00Z");
const base: MaterialProcessingRunView = { schema: "material-processing-run/v6", run_id: rid, material_id: mid, source_artifact_id: mid,
  status: "running", progress_stage: "evidence", completed_pages: 3, total_pages: 45, cancel_requested_at: null,
  output_binding: null, error_code: null, created_at: "2026-09-12T11:59:00Z", updated_at: "2026-09-12T11:59:59Z", completed_at: null };
const requested = (run = base): MaterialProcessingRunView => ({ ...run, cancel_requested_at: time.toISOString(), updated_at: time.toISOString() });
const removed = { schema: "material-discard/v1", material_id: mid, state: "removed" };
const removing = { ...removed, state: "removing" };
const failure = (reason_code: string) => ({ schema: "api-error/v1", request_id: mid, reason_code, retryable: reason_code === "STORAGE_UNAVAILABLE", message: "Request could not be completed." });

async function setup(page: Page) {
  await page.clock.install({ time }); await page.clock.pauseAt(time);
  await page.route("**/v1/session/refresh", route => route.fulfill({ json: { schema: "learner-identity/v1", learner_id: "33333333-3333-4333-8333-333333333333" } }));
  await page.route("**/v1/session", route => route.fulfill({ json: { schema: "learner-identity/v1", learner_id: mid } }));
  await page.route("**/v1/materials", route => route.fulfill({ json: { schema: "material-library/v2", materials: [] } }));
}
async function confirm(page: Page) {
  await page.getByRole("button", { name: "取消並刪除教材", exact: true }).click();
  await expect(page.getByRole("heading", { name: "確定要取消處理並刪除這份教材嗎？", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "繼續處理", exact: true })).toBeFocused();
  await expect(page.locator(".cancel-confirmation")).toContainText("既有的知識地圖、學習進度、題目與作答紀錄");
  await page.getByRole("button", { name: "確認刪除", exact: true }).click();
}

for (const viewport of [{ width: 1536, height: 1024 }, { width: 390, height: 844 }]) {
  test(`pending cancel-and-remove requires confirmation and leaves no card at ${viewport.width}px`, async ({ page }) => {
    await page.setViewportSize(viewport); await setup(page);
    let deletes = 0;
    await page.route(`**/v1/material-processing-runs/${rid}`, route => route.fulfill({ json: { ...base, status: "pending", progress_stage: "queued", completed_pages: 0, total_pages: null } }));
    await page.route(`**/v1/materials/${mid}`, route => {
      expect(route.request().method()).toBe("DELETE"); expect(route.request().postData()).toBeNull();
      expect(route.request().headers()["idempotency-key"]).toBeUndefined();
      expect(route.request().headers().origin).toBe(new URL(page.url()).origin);
      deletes++; return route.fulfill({ status: 202, json: removed });
    });
    page.on("dialog", () => { throw new Error("native confirmation is not allowed"); });
    await page.goto(path);
    await page.getByRole("button", { name: "取消並刪除教材", exact: true }).click();
    expect(deletes).toBe(0);
    await expect(page.getByRole("button", { name: "繼續處理", exact: true })).toBeFocused();
    await page.getByRole("button", { name: "繼續處理", exact: true }).click();
    await expect(page.getByRole("button", { name: "取消並刪除教材", exact: true })).toBeFocused();
    await page.getByRole("button", { name: "取消並刪除教材", exact: true }).click();
    await page.keyboard.press("Tab"); await expect(page.getByRole("button", { name: "確認刪除", exact: true })).toBeFocused();
    await page.screenshot({ path: `/tmp/studydy-discard/${viewport.width}-processing-confirm.png`, fullPage: true });
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(viewport.width);
    await page.keyboard.press("Escape");
    await expect(page.getByRole("button", { name: "取消並刪除教材", exact: true })).toBeFocused();
    expect(deletes).toBe(0);
    await confirm(page);
    await expect(page).toHaveURL(/\/materials$/);
    await expect(page.getByRole("heading", { name: "尚未有學習教材", exact: true })).toBeVisible();
    expect(deletes).toBe(1);
  });
}

for (const stage of ["evidence", "semantics"] as const) {
  test(`${stage} discard stays running, survives reload and treats the later 404 as removed`, async ({ page }) => {
    await page.setViewportSize({ width: 1536, height: 1024 }); await setup(page);
    let state: MaterialProcessingRunView = { ...base, progress_stage: stage };
    let reads = 0; let deletes = 0; let missing = false;
    let release!: () => void; const pending = new Promise<void>(resolve => { release = resolve; });
    await page.route(`**/v1/material-processing-runs/${rid}`, route => {
      reads++;
      return missing ? route.fulfill({ status: 404, json: failure("RESOURCE_NOT_FOUND") }) : route.fulfill({ json: state });
    });
    await page.route(`**/v1/materials/${mid}`, async route => {
      deletes++; await pending; state = requested(state); await route.fulfill({ status: 202, json: removing });
    });
    await page.goto(path);
    await page.getByRole("button", { name: "取消並刪除教材", exact: true }).click();
    await page.getByRole("button", { name: "確認刪除", exact: true }).evaluate(button => { (button as HTMLButtonElement).click(); (button as HTMLButtonElement).click(); });
    await expect(page.getByText("正在送出刪除要求…", { exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "確認刪除", exact: true })).toBeDisabled();
    await expect.poll(() => deletes).toBe(1);
    await page.clock.runFor(1500); await expect.poll(() => reads).toBe(2);
    release();
    await expect(page.getByRole("heading", { name: "正在取消並刪除教材", exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "取消並刪除教材", exact: true })).toHaveCount(0);
    await expect(page).toHaveURL(new RegExp(`/runs/${rid}$`));
    await page.screenshot({ path: `/tmp/studydy-discard/1536-${stage}-removing.png`, fullPage: true });
    await page.reload();
    await expect(page.getByRole("heading", { name: "正在取消並刪除教材", exact: true })).toBeVisible();
    state = { ...state, status: "cancelled", completed_at: time.toISOString() };
    await page.clock.runFor(1500);
    await expect(page.getByRole("heading", { name: "正在刪除教材…", exact: true })).toBeVisible();
    missing = true;
    await page.clock.runFor(1500);
    await expect(page).toHaveURL(/\/materials$/);
    await expect(page.getByRole("heading", { name: "無法讀取處理狀態", exact: true })).toHaveCount(0);
    expect(deletes).toBe(1);
  });
}

test("publishing finishes safely while accepted deletion keeps polling to purge", async ({ page }) => {
  await page.setViewportSize({ width: 1536, height: 1024 }); await setup(page);
  let state = base, gone = false;
  await page.route(`**/v1/material-processing-runs/${rid}`, route => gone ? route.fulfill({ status: 404, json: failure("RESOURCE_NOT_FOUND") }) : route.fulfill({ json: state }));
  await page.route(`**/v1/materials/${mid}`, route => {
    state = { ...base, progress_stage: "publishing", completed_pages: 45 };
    return route.fulfill({ status: 202, json: removing });
  });
  await page.goto(path); await confirm(page);
  await expect(page.getByRole("heading", { name: "正在取消並刪除教材", exact: true })).toBeVisible();
  await expect(page.getByRole("alert")).toHaveCount(0);
  state = { ...state, status: "succeeded", progress_stage: "completed", completed_at: time.toISOString(),
    output_binding: { schema: "material-run-output-binding/v4", knowledge_structure_revision: `knowledge-structure:sha256:${"a".repeat(64)}`, runtime_lock_sha256: "b".repeat(64), page_count: 45, processing: "succeeded", quality: "accepted", decision: "retain", reason_codes: [], ocr_calls: 0, semantic_calls: 1 } };
  await page.clock.runFor(1500);
  await expect(page.getByRole("heading", { name: "正在刪除教材…", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "開啟知識地圖", exact: true })).toHaveCount(0);
  gone = true; await page.clock.runFor(1500); await expect(page).toHaveURL(/\/materials$/);
});

test("DELETE 503 keeps processing and polling; confirmation can retry", async ({ page }) => {
  await setup(page);
  let fail = true; let reads = 0;
  await page.route(`**/v1/material-processing-runs/${rid}`, route => { reads++; return route.fulfill({ json: base }); });
  await page.route(`**/v1/materials/${mid}`, route => fail
    ? route.fulfill({ status: 503, json: failure("STORAGE_UNAVAILABLE") })
    : route.fulfill({ status: 202, json: removing }));
  await page.goto(path); await confirm(page);
  await expect(page.getByRole("heading", { name: "正在分析教材", exact: true })).toBeVisible();
  await expect(page.getByRole("alert")).toContainText("無法送出刪除要求");
  await expect(page.getByRole("button", { name: "確認刪除", exact: true })).toBeEnabled();
  await page.clock.runFor(1500); await expect.poll(() => reads).toBe(2);
  fail = false; await page.getByRole("button", { name: "確認刪除", exact: true }).click();
  await expect(page.getByRole("heading", { name: "正在取消並刪除教材", exact: true })).toBeVisible();
});

test("an ordinary GET 404 remains a read failure without accepted discard", async ({ page }) => {
  await setup(page);
  await page.route(`**/v1/material-processing-runs/${rid}`, route => route.fulfill({ status: 404, json: failure("RESOURCE_NOT_FOUND") }));
  await page.goto(path);
  await expect(page.getByRole("heading", { name: "無法讀取處理狀態", exact: true })).toBeVisible();
  await expect(page).toHaveURL(new RegExp(`/runs/${rid}$`));
});

test("legacy cancelled record offers explicit removal without claiming a pending discard", async ({ page }) => {
  await setup(page);
  await page.route(`**/v1/material-processing-runs/${rid}`, route => route.fulfill({ json: { ...requested(), status: "cancelled", completed_at: time.toISOString() } }));
  await page.route(`**/v1/materials/${mid}`, route => route.fulfill({ status: 202, json: removed }));
  await page.goto(path);
  await expect(page.getByRole("heading", { name: "已取消教材處理", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "刪除教材", exact: true }).click();
  await page.getByRole("button", { name: "確認刪除", exact: true }).click();
  await expect(page).toHaveURL(/\/materials$/);
});

test("persisted GET discard intent takes precedence over late DELETE transport failure", async ({ page }) => {
  await setup(page);
  let state = base; let release!: () => void; const pending = new Promise<void>(resolve => { release = resolve; });
  await page.route(`**/v1/material-processing-runs/${rid}`, route => route.fulfill({ json: state }));
  await page.route(`**/v1/materials/${mid}`, async route => {
    state = requested(); await pending;
    await route.fulfill({ status: 503, json: failure("STORAGE_UNAVAILABLE") });
  });
  await page.goto(path); await confirm(page); await page.clock.runFor(1500);
  await expect(page.getByRole("heading", { name: "正在取消並刪除教材", exact: true })).toBeVisible();
  const settled = page.waitForResponse(response => response.request().method() === "DELETE"); release(); await settled;
  await expect(page.getByRole("alert")).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "正在取消並刪除教材", exact: true })).toBeVisible();
});
