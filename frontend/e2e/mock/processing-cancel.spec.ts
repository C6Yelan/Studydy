import { expect, test, type Page, type Route } from "@playwright/test";
import type {
  MaterialDiscardView,
  MaterialOutputBinding,
  MaterialProcessingRunView,
} from "../../src/api/contracts";

import { run as publishedRun } from "../fixtures/knowledge-map";

const materialId = "11111111-1111-4111-8111-111111111111";
const runId = "22222222-2222-4222-8222-222222222222";
const learnerId = "33333333-3333-4333-8333-333333333333";
const artifactId = "44444444-4444-4444-8444-444444444444";
const runPath = `/materials/${materialId}/runs/${runId}`;
const currentTime = new Date("2026-09-12T12:00:00Z");
const runningRun: MaterialProcessingRunView = {
  schema: "material-processing-run/v1",
  run_id: runId,
  material_id: materialId,
  source_artifact_id: artifactId,
  status: "running",
  progress_stage: "evidence",
  completed_pages: 3,
  total_pages: 45,
  cancel_requested_at: null,
  output_binding: null,
  error_code: null,
  created_at: "2026-09-12T11:59:00Z",
  updated_at: "2026-09-12T11:59:59Z",
  completed_at: null,
};
const cancelRequestedRun = (run = runningRun): MaterialProcessingRunView => ({
  ...run,
  cancel_requested_at: currentTime.toISOString(),
  updated_at: currentTime.toISOString(),
});
const removedMaterial: MaterialDiscardView = {
  schema: "material-discard/v1",
  material_id: materialId,
  state: "removed",
};
const removingMaterial: MaterialDiscardView = { ...removedMaterial, state: "removing" };
const apiFailure = (reason_code: string) => ({
  schema: "api-error/v1",
  request_id: materialId,
  reason_code,
  retryable: reason_code === "STORAGE_UNAVAILABLE",
  message: "Request could not be completed.",
});

test.beforeEach(async ({ page }) => {
  await page.clock.install({ time: currentTime });
  await page.clock.pauseAt(currentTime);
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
        throw new Error(`Unexpected cancellation request: ${request.method()} ${path}`);
    }
  });
  page.on("dialog", () => {
    throw new Error("native confirmation is not allowed");
  });
});

async function mockRun(page: Page, getRun: () => MaterialProcessingRunView | null) {
  let reads = 0;
  await page.route(`**/v1/material-processing-runs/${runId}`, (route) => {
    expect(route.request().method()).toBe("GET");
    reads++;
    const run = getRun();
    return run
      ? route.fulfill({ json: run })
      : route.fulfill({ status: 404, json: apiFailure("RESOURCE_NOT_FOUND") });
  });
  return () => reads;
}

async function mockDiscard(page: Page, respond: (route: Route, attempt: number) => Promise<void>) {
  let deletes = 0;
  await page.route(`**/v1/materials/${materialId}`, (route) => {
    const request = route.request();
    expect(request.method()).toBe("DELETE");
    return respond(route, ++deletes);
  });
  return () => deletes;
}

async function openDiscardConfirmation(page: Page) {
  await page.getByRole("button", { name: "取消並刪除教材", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "確定要取消處理並刪除這份教材嗎？", exact: true }),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "繼續處理", exact: true })).toBeFocused();
  await expect(page.locator(".cancel-confirmation")).toContainText(
    "既有的知識地圖、學習進度、題目與作答紀錄",
  );
}

async function confirmDiscard(page: Page) {
  await openDiscardConfirmation(page);
  await page.getByRole("button", { name: "確認刪除", exact: true }).click();
}

for (const viewport of [
  { width: 1536, height: 1024 },
  { width: 390, height: 844 },
]) {
  test(`pending cancel-and-remove requires confirmation and leaves no card at ${viewport.width}px`, async ({
    page,
  }) => {
    await page.setViewportSize(viewport);
    await mockRun(page, () => ({
      ...runningRun,
      status: "pending",
      progress_stage: "queued",
      completed_pages: 0,
      total_pages: null,
    }));
    const deletes = await mockDiscard(page, (route) =>
      route.fulfill({ status: 202, json: removedMaterial }),
    );
    await page.goto(runPath);
    await openDiscardConfirmation(page);
    expect(deletes()).toBe(0);
    await page.getByRole("button", { name: "繼續處理", exact: true }).click();
    await expect(page.getByRole("button", { name: "取消並刪除教材", exact: true })).toBeFocused();
    await openDiscardConfirmation(page);
    await page.keyboard.press("Tab");
    await expect(page.getByRole("button", { name: "確認刪除", exact: true })).toBeFocused();
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(viewport.width);
    await page.keyboard.press("Escape");
    await expect(page.getByRole("button", { name: "取消並刪除教材", exact: true })).toBeFocused();
    expect(deletes()).toBe(0);
    await confirmDiscard(page);
    await expect(page).toHaveURL(/\/materials$/);
    await expect(page.getByRole("heading", { name: "尚未有學習教材", exact: true })).toBeVisible();
    expect(deletes()).toBe(1);
  });
}

test("accepted discard keeps polling, survives reload and treats the later 404 as removed", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1536, height: 1024 });
  let state = runningRun;
  let missing = false;
  let release!: () => void;
  const pending = new Promise<void>((resolve) => {
    release = resolve;
  });
  const reads = await mockRun(page, () => (missing ? null : state));
  const deletes = await mockDiscard(page, async (route) => {
    await pending;
    state = cancelRequestedRun(state);
    await route.fulfill({ status: 202, json: removingMaterial });
  });
  await page.goto(runPath);
  await openDiscardConfirmation(page);
  await page.getByRole("button", { name: "確認刪除", exact: true }).evaluate((button) => {
    (button as HTMLButtonElement).click();
    (button as HTMLButtonElement).click();
  });
  await expect(page.getByText("正在送出刪除要求…", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "確認刪除", exact: true })).toBeDisabled();
  await expect.poll(deletes).toBe(1);
  const initialReads = reads();
  await page.clock.runFor(3_000);
  await expect.poll(reads).toBeGreaterThan(initialReads);
  release();
  await expect(
    page.getByRole("heading", { name: "正在取消並刪除教材", exact: true }),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "取消並刪除教材", exact: true })).toHaveCount(0);
  await expect(page).toHaveURL(new RegExp(`/runs/${runId}$`));
  await page.reload();
  await expect(
    page.getByRole("heading", { name: "正在取消並刪除教材", exact: true }),
  ).toBeVisible();
  state = { ...state, status: "cancelled", completed_at: currentTime.toISOString() };
  await page.clock.runFor(1500);
  await expect(page.getByRole("heading", { name: "正在刪除教材…", exact: true })).toBeVisible();
  missing = true;
  await page.clock.runFor(1500);
  await expect(page).toHaveURL(/\/materials$/);
  await expect(page.getByRole("heading", { name: "無法讀取處理狀態", exact: true })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "尚未有學習教材", exact: true })).toBeVisible();
  expect(deletes()).toBe(1);
});

test("publishing finishes safely while accepted deletion keeps polling to purge", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1536, height: 1024 });
  let state = runningRun;
  let gone = false;
  await mockRun(page, () => (gone ? null : state));
  const deletes = await mockDiscard(page, (route) => {
    state = { ...runningRun, progress_stage: "publishing", completed_pages: 45 };
    return route.fulfill({ status: 202, json: removingMaterial });
  });
  await page.goto(runPath);
  await confirmDiscard(page);
  await expect(
    page.getByRole("heading", { name: "正在取消並刪除教材", exact: true }),
  ).toBeVisible();
  await expect(page.getByRole("alert")).toHaveCount(0);
  state = {
    ...state,
    status: "succeeded",
    progress_stage: "completed",
    completed_at: currentTime.toISOString(),
    output_binding: { ...publishedRun.output_binding, page_count: 45 } as MaterialOutputBinding,
  };
  await page.clock.runFor(1500);
  await expect(page.getByRole("heading", { name: "正在刪除教材…", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "開啟知識地圖", exact: true })).toHaveCount(0);
  gone = true;
  await page.clock.runFor(1500);
  await expect(page).toHaveURL(/\/materials$/);
  await expect(page.getByRole("heading", { name: "尚未有學習教材", exact: true })).toBeVisible();
  expect(deletes()).toBe(1);
});

test("semantics DELETE 503 keeps processing and polling; confirmation can retry", async ({
  page,
}) => {
  const reads = await mockRun(page, () => ({ ...runningRun, progress_stage: "semantics" }));
  const deletes = await mockDiscard(page, (route, attempt) =>
    attempt === 1
      ? route.fulfill({ status: 503, json: apiFailure("STORAGE_UNAVAILABLE") })
      : route.fulfill({ status: 202, json: removingMaterial }),
  );
  await page.goto(runPath);
  await confirmDiscard(page);
  await expect(page.getByRole("heading", { name: "正在分析教材", exact: true })).toBeVisible();
  await expect(page.getByRole("alert")).toContainText("無法送出刪除要求");
  await expect(page.getByRole("button", { name: "確認刪除", exact: true })).toBeEnabled();
  expect(deletes()).toBe(1);
  const initialReads = reads();
  await page.clock.runFor(3_000);
  await expect.poll(reads).toBeGreaterThan(initialReads);
  await page.getByRole("button", { name: "確認刪除", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "正在取消並刪除教材", exact: true }),
  ).toBeVisible();
  await expect(page.getByRole("alert")).toHaveCount(0);
  expect(deletes()).toBe(2);
});

test("an ordinary GET 404 remains a read failure without accepted discard", async ({ page }) => {
  await mockRun(page, () => null);
  await page.goto(runPath);
  await expect(page.getByRole("heading", { name: "無法讀取處理狀態", exact: true })).toBeVisible();
  await expect(page).toHaveURL(new RegExp(`/runs/${runId}$`));
});

test("cancelled run stops polling and requires explicit removal", async ({ page }) => {
  const reads = await mockRun(page, () => ({
    ...cancelRequestedRun(),
    status: "cancelled",
    completed_at: currentTime.toISOString(),
  }));
  const deletes = await mockDiscard(page, (route) =>
    route.fulfill({ status: 202, json: removedMaterial }),
  );
  await page.goto(runPath);
  await expect(page.getByRole("heading", { name: "已取消教材處理", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "取消並刪除教材", exact: true })).toHaveCount(0);
  await page.clock.runFor(5_000);
  expect(reads()).toBe(1);
  expect(deletes()).toBe(0);
  await page.getByRole("button", { name: "刪除教材", exact: true }).click();
  await page.getByRole("button", { name: "確認刪除", exact: true }).click();
  await expect(page).toHaveURL(/\/materials$/);
  await expect(page.getByRole("heading", { name: "尚未有學習教材", exact: true })).toBeVisible();
  expect(deletes()).toBe(1);
});

test("persisted GET discard intent takes precedence over late DELETE transport failure", async ({
  page,
}) => {
  let state = runningRun;
  let release!: () => void;
  const pending = new Promise<void>((resolve) => {
    release = resolve;
  });
  await mockRun(page, () => state);
  const deletes = await mockDiscard(page, async (route) => {
    state = cancelRequestedRun();
    await pending;
    await route.abort("connectionreset");
  });
  await page.goto(runPath);
  await confirmDiscard(page);
  await expect.poll(deletes).toBe(1);
  await page.clock.runFor(1500);
  await expect(
    page.getByRole("heading", { name: "正在取消並刪除教材", exact: true }),
  ).toBeVisible();
  const settled = page.waitForEvent(
    "requestfailed",
    (request) =>
      request.method() === "DELETE" &&
      new URL(request.url()).pathname === `/v1/materials/${materialId}`,
  );
  release();
  await settled;
  // 等送出流程結束後再檢查，避免在 catch 處理失敗之前就通過斷言。
  await expect(page.getByText("正在送出刪除要求…", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("alert")).toHaveCount(0);
  await expect(
    page.getByRole("heading", { name: "正在取消並刪除教材", exact: true }),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "取消並刪除教材", exact: true })).toHaveCount(0);
  expect(deletes()).toBe(1);
});
