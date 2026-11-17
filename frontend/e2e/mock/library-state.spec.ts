import { expect, test } from "@playwright/test";
import {
  materialId,
  runId,
  sessionId,
  structureRevision,
  run,
  json,
  mockKnowledgeMapApi,
} from "../fixtures/knowledge-map";

test.beforeEach(async ({ page }) => {
  await mockKnowledgeMapApi(page);
  await page.route("**/v1/materials", (route) => {
    expect(route.request().method()).toBe("GET");
    return json(route, { schema: "material-library/v1", materials: [] });
  });
});

test("library loading, read failure and empty state retain usable actions", async ({ page }) => {
  let releaseRead!: () => void;
  const ready = new Promise<void>((resolve) => {
    releaseRead = resolve;
  });
  let reads = 0;
  page.on("request", (request) => {
    if (new URL(request.url()).pathname === "/v1/materials") reads++;
  });
  await page.route(
    "**/v1/materials",
    async (route) => {
      expect(route.request().method()).toBe("GET");
      await ready;
      return json(
        route,
        {
          schema: "api-error/v1",
          request_id: sessionId,
          reason_code: "STORAGE_UNAVAILABLE",
          retryable: true,
          message: "Request could not be completed.",
        },
        503,
      );
    },
    { times: 1 },
  );
  await page.goto("/materials");
  await expect(page.getByRole("status")).toHaveText("正在讀取我的群島");
  // 讀取中以我的群島版型的骨架佔位；偏好減少動態時光澤動畫停止。
  const skeleton = page.locator(".page-skeleton.is-library");
  await expect(skeleton).toHaveAttribute("aria-busy", "true");
  await expect(skeleton.locator(".skeleton-card").first()).toBeVisible();
  await page.emulateMedia({ reducedMotion: "reduce" });
  await expect.poll(() => skeleton.locator(".skeleton-bone").first().evaluate((element) => getComputedStyle(element).animationName)).toBe("none");
  await page.emulateMedia({ reducedMotion: "no-preference" });
  expect(reads).toBe(1);
  releaseRead();
  await expect(page.getByRole("heading", { name: "無法讀取教材", exact: true })).toBeVisible();
  await expect(page.getByText("資料服務暫時無法使用，請稍後再試。", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "重新讀取", exact: true }).click();
  await expect(page.getByRole("heading", { name: "尚未有學習教材", exact: true })).toBeVisible();
  expect(reads).toBe(2);
  await expect(page.getByRole("heading", { name: "無法讀取教材", exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "上傳第一份教材", exact: true }).click();
  await expect(page).toHaveURL(/\/upload$/);
  await expect(page.getByRole("heading", { name: "新增一座島", exact: true })).toBeVisible();
  await expect(page.getByLabel("選擇教材檔案", { exact: true })).toBeEnabled();
});

test("reopen rejects a run from a different Knowledge Structure revision", async ({ page }) => {
  await page.route(`**/v1/material-processing-runs/${runId}`, (route) => {
    expect(route.request().method()).toBe("GET");
    return json(route, {
      ...run,
      output_binding: {
        ...run.output_binding,
        knowledge_structure_revision: `knowledge-structure:sha256:${"7".repeat(64)}`,
      },
    });
  });
  await page.goto(
    `/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}`,
  );
  await expect(page.getByRole("heading", { name: "無法讀取知識地圖", exact: true })).toBeVisible();
  await expect(page.locator(".map-workspace")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "查看學習成果", exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "返回我的群島", exact: true }).click();
  await expect(page).toHaveURL(/\/materials$/);
  await expect(page.getByRole("heading", { name: "尚未有學習教材", exact: true })).toBeVisible();
});

test("failed topic cancellation shows the error inside that topic card and keeps the library", async ({ page }) => {
  const topic = { topic_id: "77777777-7777-4777-8777-777777777777", material_id: null, request: "尚未搜尋的 Agent 規劃",
    proposal: { title: "停止與求助", goals: [], topics: [] }, version: 2, status: "ready", research_id: null, error_code: null };
  let fail = true;
  await page.route("**/v1/topics", (route) => json(route, { topics: topic.status === "cancelled" ? [] : [topic] }));
  await page.route(`**/v1/topics/${topic.topic_id}/actions`, (route) => fail
    ? json(route, { schema: "api-error/v1", request_id: sessionId, reason_code: "TOPIC_CONFLICT", retryable: false, message: "Conflict" }, 409)
    : (topic.status = "cancelled", json(route, topic)));
  await page.goto("/materials");
  const card = page.getByRole("article", { name: "停止與求助" });
  await card.getByRole("button", { name: "取消建立", exact: true }).click();
  await card.getByRole("button", { name: "取消建立", exact: true }).click();
  await expect(card.getByRole("alert")).toBeVisible();
  await expect(page.getByRole("heading", { name: "無法讀取教材" })).toHaveCount(0);
  await expect(card).toBeVisible();
  fail = false;
  await card.getByRole("button", { name: "取消建立", exact: true }).click();
  await expect(card).toHaveCount(0);
});
