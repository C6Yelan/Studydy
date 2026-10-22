import { expect, test } from "@playwright/test";
import type { MaterialLibraryItem } from "../../src/api/contracts";
import { json, materialId, mockKnowledgeMapApi, run, runId, session, sessionId, structureRevision, structureView } from "../fixtures/knowledge-map";

// 全站體驗檢查的回歸：找不到頁、舊網址別名、手機教材分頁列與教材卡按鈕對齊。

const mapPath = `/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}`;
const otherMaterial = "99999999-9999-4999-8999-999999999999";

for (const width of [1440, 390]) {
  test(`unknown address shows the not-found page instead of silently opening home (${width})`, async ({ page }) => {
    await mockKnowledgeMapApi(page, structureView());
    await page.setViewportSize({ width, height: 844 });
    await page.goto("/foo-bar");
    await expect(page.getByRole("heading", { name: "找不到這個頁面", level: 1 })).toBeVisible();
    await expect(page).toHaveURL(/\/foo-bar$/);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(width);
    await page.getByRole("button", { name: "回到首頁", exact: true }).click();
    await expect(page).toHaveURL(/\/$/);
  });
}

test("missing or foreign material shows a not-found page without delete or confirmation steps", async ({ page }) => {
  await mockKnowledgeMapApi(page, structureView());
  await page.route((url) => url.pathname.startsWith(`/v1/materials/${otherMaterial}`), (route) => json(route, {
    schema: "api-error/v1", request_id: sessionId, reason_code: "RESOURCE_NOT_FOUND", retryable: false, message: "Request could not be completed.",
  }, 404));
  for (const path of [`/materials/${otherMaterial}/sources`, `/materials/${otherMaterial}/concept-cards`, `/materials/${otherMaterial}`]) {
    await page.goto(path);
    await expect(page.getByRole("heading", { name: "找不到這份教材", level: 1 })).toBeVisible();
    await expect(page.getByRole("button", { name: "刪除教材" })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "重新讀取" })).toHaveCount(0);
    await expect(page.getByText("從教材到知識地圖")).toHaveCount(0);
  }
  await page.getByRole("button", { name: "回到我的教材", exact: true }).click();
  await expect(page).toHaveURL(/\/materials$/);
});

test("old and shorthand addresses open the real page", async ({ page }) => {
  await mockKnowledgeMapApi(page, structureView());
  await page.goto(`/materials/${materialId}/map`);
  await expect(page).toHaveURL(new RegExp(`/materials/${materialId}/runs/${runId}/knowledge-structures/`));
  await expect(page.getByRole("tab", { name: "知識地圖", exact: true })).toHaveAttribute("aria-selected", "true");
  await page.goto("/cards");
  await expect(page).toHaveURL(/\/concept-cards$/);
});

test("phone material tabs stay scrollable and the labelled Q&A entry never covers them", async ({ page }) => {
  await mockKnowledgeMapApi(page, structureView());
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(mapPath);
  const tabs = page.getByRole("tablist", { name: "教材學習內容" });
  const ask = page.getByRole("button", { name: "教材問答", exact: true });
  await expect(ask).toBeVisible();
  await expect(ask).toContainText("問答");
  const list = (await tabs.boundingBox())!, button = (await ask.boundingBox())!;
  expect(list.x + list.width).toBeLessThanOrEqual(button.x + 0.5);
  // 還有分頁在右側時，分頁列右緣淡出提示可以滑動。
  await expect(tabs).toHaveClass(/can-scroll-right/);
  await page.getByRole("tab", { name: "教材來源", exact: true }).focus();
  await page.keyboard.press("End");
  await expect(page).toHaveURL(new RegExp(`/materials/${materialId}/sources$`));
  // 地圖縮放按鈕留在底部導覽之上。
  await page.goto(mapPath);
  await page.evaluate(() => window.scrollTo(0, 0));
  const zoomOut = (await page.getByRole("button", { name: "縮小地圖" }).boundingBox())!;
  const nav = (await page.getByRole("navigation", { name: "手機導覽" }).boundingBox())!;
  expect(zoomOut.y + zoomOut.height).toBeLessThanOrEqual(nav.y);
});

test("material cards keep the main action on its own row and secondary entries aligned", async ({ page }) => {
  await mockKnowledgeMapApi(page, structureView());
  const structure = { run_id: runId, knowledge_structure_revision: structureRevision, status: "succeeded", created_at: run.created_at };
  const base: MaterialLibraryItem = {
    schema: "material-library-item/v1", material_id: materialId, source_artifact_id: null, display_name: "資料結構",
    size_bytes: 100, created_at: run.created_at, latest_attempt: null, available_structures: [structure], study_sessions: [],
  };
  const studying: MaterialLibraryItem = {
    ...base, material_id: otherMaterial, display_name: "作業系統",
    study_sessions: [{ ...session(), run_id: runId, study_session_id: sessionId }] as unknown as MaterialLibraryItem["study_sessions"],
  };
  await page.route((url) => url.pathname === "/v1/materials", (route) =>
    json(route, { schema: "material-library/v1", materials: [studying, base] }));
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 900 });
    await page.goto("/materials");
    await expect(page.getByRole("heading", { name: "我的教材", level: 1 })).toBeVisible();
    await expect(page.locator(".collection-summary")).toHaveText("已保存 2 份教材");
    for (const name of ["作業系統", "資料結構"]) {
      const card = page.getByRole("article", { name });
      const actions = card.locator(".library-actions");
      const box = (await actions.boundingBox())!;
      const primary = (await actions.locator(".primary-button").boundingBox())!;
      expect(Math.abs(primary.width - box.width)).toBeLessThanOrEqual(1);
      const cards = (await card.getByRole("button", { name: "概念卡", exact: true }).boundingBox())!;
      const podcast = (await card.getByRole("button", { name: "Podcast", exact: true }).boundingBox())!;
      expect(Math.abs(cards.y - podcast.y)).toBeLessThanOrEqual(1);
      expect(Math.abs(cards.height - podcast.height)).toBeLessThanOrEqual(1);
      expect(cards.y).toBeGreaterThan(primary.y + primary.height - 1);
    }
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(width);
    await page.screenshot({ path: test.info().outputPath(`library-${width}.png`), fullPage: true });
  }
  // 點島嶼圖可直接開啟知識地圖。
  await page.getByRole("article", { name: "資料結構" }).locator(".library-island").click();
  await expect(page).toHaveURL(new RegExp(`/materials/${materialId}/runs/${runId}/knowledge-structures/`));
});
