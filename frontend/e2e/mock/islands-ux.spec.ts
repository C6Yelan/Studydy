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
    await expect(page.getByRole("heading", { name: "這座島不在地圖上", level: 1 })).toBeVisible();
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
  await page.getByRole("button", { name: "回到我的群島", exact: true }).click();
  await expect(page).toHaveURL(/\/materials$/);
});

test("phone material tabs stay at the bottom and never cover the map controls", async ({ page }) => {
  await mockKnowledgeMapApi(page, structureView());
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(mapPath);
  const nav = page.getByRole("navigation", { name: "教材導覽", exact: true });
  await expect(nav.getByRole("link", { name: "知識地圖", exact: true })).toHaveAttribute("aria-current", "page");
  // 問小幫手收在「更多」裡；打開後同樣可以切到原始檔案。
  await nav.getByRole("button", { name: "更多", exact: true }).click();
  await expect(nav.getByRole("button", { name: "問小幫手", exact: true })).toBeVisible();
  await nav.getByRole("link", { name: "原始檔案", exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`/materials/${materialId}/sources$`));
  await expect(nav.getByRole("button", { name: "更多", exact: true })).toHaveAttribute("aria-expanded", "false");
  // 教材內沒有全站底部導覽；地圖縮放按鈕完整留在教材頁籤上方。
  await page.goto(mapPath);
  await page.evaluate(() => window.scrollTo(0, 0));
  await expect(page.getByRole("navigation", { name: "手機導覽" })).toHaveCount(0);
  const zoomOut = (await page.getByRole("button", { name: "縮小地圖" }).boundingBox())!;
  expect(zoomOut.y + zoomOut.height).toBeLessThanOrEqual((await nav.boundingBox())!.y);
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
    await expect(page.getByRole("heading", { name: "我的群島", level: 1 })).toBeVisible();
    await expect(page.locator(".library-subtitle b")).toHaveText("2 座島");
    for (const name of ["作業系統", "資料結構"]) {
      const card = page.getByRole("article", { name });
      const actions = card.locator(".library-actions");
      const box = (await actions.boundingBox())!;
      const primary = (await actions.locator(".primary-button").boundingBox())!;
      expect(Math.abs(primary.width - box.width)).toBeLessThanOrEqual(1);
      const cards = (await card.getByRole("link", { name: "概念卡片", exact: true }).boundingBox())!;
      const podcast = (await card.getByRole("link", { name: "Podcast", exact: true }).boundingBox())!;
      expect(Math.abs(cards.y - podcast.y)).toBeLessThanOrEqual(1);
      expect(Math.abs(cards.height - podcast.height)).toBeLessThanOrEqual(1);
      // 三個小圖示在主要按鈕上方一列。
      expect(cards.y + cards.height).toBeLessThanOrEqual(primary.y + 1);
    }
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(width);
  }
  // 點島嶼圖可直接開啟知識地圖。
  await page.getByRole("article", { name: "資料結構" }).locator(".library-island").click();
  await expect(page).toHaveURL(new RegExp(`/materials/${materialId}/runs/${runId}/knowledge-structures/`));
});
