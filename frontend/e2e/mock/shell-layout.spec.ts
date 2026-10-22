import { expect, test } from "@playwright/test";
import {
  browserOrigin,
  materialId,
  runId,
  sessionId,
  structureRevision,
  json,
  mockKnowledgeMapApi,
} from "../fixtures/knowledge-map";

const mapPath = `/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}`;
const studyPath = `${mapPath}/study-sessions/${sessionId}`;
const workspaces = [
  { path: mapPath, selector: ".map-workspace" },
  { path: studyPath, selector: ".study-session-page" },
];

test.beforeEach(async ({ page }) => {
  await mockKnowledgeMapApi(page);
  await page.route("**/v1/materials", (route) => {
    expect(route.request().method()).toBe("GET");
    return json(route, { schema: "material-library/v1", materials: [] });
  });
});

test("every page shares the top navigation and phones use the bottom tab bar", async ({ page }) => {
  for (const width of [1536, 390]) {
    await page.setViewportSize({ width, height: 844 });
    for (const [path, selector, current] of [
      ["/", ".dashboard-content", "首頁"],
      ["/materials", ".library-empty", "我的教材"],
      [mapPath, ".map-workspace", "我的教材"],
      [studyPath, ".study-session-page", "我的教材"],
    ] as const) {
      await page.goto(path);
      await expect(page.locator(selector)).toBeVisible();
      const header = (await page.locator(".app-header").boundingBox())!;
      expect(header.height).toBe(width > 720 ? 70 : 56);
      const desktopNav = page.getByRole("navigation", { name: "主要導覽", exact: true });
      const phoneNav = page.getByRole("navigation", { name: "手機導覽", exact: true });
      if (width > 720) {
        await expect(desktopNav).toBeVisible();
        await expect(phoneNav).toBeHidden();
        await expect(desktopNav.getByRole("link", { name: current, exact: true })).toHaveAttribute("aria-current", "page");
      } else {
        await expect(desktopNav).toBeHidden();
        await expect(phoneNav).toBeVisible();
        await expect(phoneNav.getByRole("link", { name: current === "我的教材" ? "教材" : current, exact: true })).toHaveAttribute("aria-current", "page");
        await expect(phoneNav.getByRole("button", { name: "上傳教材", exact: true })).toBeVisible();
      }
      if (selector === ".map-workspace" || selector === ".study-session-page")
        await expect(page.getByRole("button", { name: "回到我的教材", exact: true })).toBeVisible();
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(width);
    }
  }
});

for (const width of [320, 1366, 1920]) {
  test(`workspace navigation remains usable from ${width}px`, async ({ page }) => {
    for (const { path, selector } of workspaces) {
      await page.setViewportSize({ width, height: 1080 });
      await page.goto(path);
      await expect(page.locator(selector)).toBeVisible();
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(width);
      if (width >= 1366) {
        // 200% 桌機縮放對應一半 CSS viewport；保留相同字級驗證重排。
        await page.setViewportSize({ width: width / 2, height: 540 });
        expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(
          width / 2,
        );
      }
      await page.getByRole("button", { name: "回到我的教材", exact: true }).click();
      await expect(page).toHaveURL(/\/materials$/);
      await expect(page.getByRole("heading", { name: "我的教材", exact: true })).toBeVisible();
    }
  });
}

test("bootstrap and persisted pageshow preserve the frame, route and selected map tab", async ({
  page,
}) => {
  let releaseBootstrap!: () => void;
  const bootstrapGate = new Promise<void>((resolve) => {
    releaseBootstrap = resolve;
  });
  let releaseRestore!: () => void;
  const restoreGate = new Promise<void>((resolve) => {
    releaseRestore = resolve;
  });
  let mapReads = 0;
  await page.route(
    `**/v1/materials/${materialId}/knowledge-structures/${encodeURIComponent(structureRevision)}/outline`,
    async (route) => {
      expect(route.request().method()).toBe("GET");
      mapReads++;
      if (mapReads > 1) await restoreGate;
      await route.fallback();
    },
  );
  await page.route("**/v1/session/refresh", async (route) => {
    expect(route.request().method()).toBe("POST");
    await bootstrapGate;
    await json(route, { schema: "learner-identity/v1", learner_id: sessionId });
  });
  await page.goto(mapPath);
  await expect(page.locator(".app-header")).toBeVisible();
  await expect(page.locator(".auth-form,.map-workspace")).toHaveCount(0);
  await expect(page.getByRole("status")).toHaveText("正在載入…");
  await expect(page).toHaveURL(`${browserOrigin}${mapPath}`);
  expect(mapReads).toBe(0);
  releaseBootstrap();
  await page.getByRole("tab", { name: "學習與測驗", exact: true }).click();
  await page.evaluate(() =>
    window.dispatchEvent(new PageTransitionEvent("pageshow", { persisted: true })),
  );
  // 等新 client 發出地圖讀取後再放行，確保檢查的是還原後的畫面。
  await expect.poll(() => mapReads).toBe(2);
  await expect(page.locator(".app-header")).toBeVisible();
  await expect(page.locator(".map-workspace")).toHaveCount(0);
  releaseRestore();
  await expect(page).toHaveURL(`${browserOrigin}${mapPath}`);
  await expect(page.getByRole("tab", { name: "學習與測驗", exact: true })).toHaveAttribute(
    "aria-selected",
    "true",
  );
});
