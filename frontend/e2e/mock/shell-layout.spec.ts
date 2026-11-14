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

test("site pages share the left rail and phones use the bottom tab bar", async ({ page }) => {
  for (const width of [1536, 1024, 390]) {
    await page.setViewportSize({ width, height: 844 });
    for (const [path, selector, current] of [
      ["/", ".dashboard-content", "首頁"],
      ["/materials", ".library-empty", "我的群島"],
    ] as const) {
      await page.goto(path);
      await expect(page.locator(selector)).toBeVisible();
      const rail = page.getByRole("navigation", { name: "主要導覽", exact: true });
      const phoneNav = page.getByRole("navigation", { name: "手機導覽", exact: true });
      // 新增教材、個人檔案與搜尋在每個寬度都看得到（手機在頂端那一列）。
      await expect(rail.getByRole("link", { name: "新增教材", exact: true })).toBeVisible();
      await expect(rail.getByRole("link", { name: "個人檔案與設定", exact: true })).toBeVisible();
      await expect(page.getByRole("button", { name: "搜尋概念、教材、卡組或 Podcast" })).toBeVisible();
      if (width > 720) {
        await expect(phoneNav).toBeHidden();
        for (const name of ["首頁", "我的群島", "翻卡片", "Podcast", "成就", "新增教材", "個人檔案與設定"])
          await expect(rail.getByRole("link", { name, exact: true })).toBeVisible();
        // 平板收成圖示列（名稱留給輔助科技），桌機是完整側欄。
        expect((await page.locator(".rail").boundingBox())!.width).toBe(width > 1024 ? 236 : 76);
        await expect(rail.getByRole("link", { name: current, exact: true })).toHaveAttribute("aria-current", "page");
        await expect(rail.getByRole("link", { name: current, exact: true })).toBeVisible();
        // 側欄貼齊左邊、填滿視窗高度，內容在它右邊。
        const box = (await page.locator(".rail").boundingBox())!;
        expect(box.x).toBe(0);
        expect(box.height).toBe(844);
        expect((await page.locator(selector).boundingBox())!.x).toBeGreaterThanOrEqual(box.width);
      } else {
        await expect(phoneNav).toBeVisible();
        await expect(rail.getByRole("link", { name: current, exact: true })).toBeHidden();
        await expect(phoneNav.getByRole("link")).toHaveText(["首頁", "群島", "翻卡片", "Podcast", "成就"]);
        await expect(phoneNav.getByRole("link", { name: current === "我的群島" ? "群島" : current, exact: true })).toHaveAttribute("aria-current", "page");
      }
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(width);
    }
  }
});

test("rail marks the current page, opens every entry and shows the forgotten card count", async ({ page }) => {
  let forgot: number | null = 5;
  await page.route("**/v1/card-reviews", (route) => {
    expect(route.request().method()).toBe("GET");
    if (forgot === null) return json(route, { schema: "api-error/v1", request_id: sessionId, reason_code: "STORAGE_UNAVAILABLE", retryable: true, message: "x" }, 503);
    return json(route, { schema: "card-reviews/v2", forgot_count: forgot, sets: [], marks: [], cards: [] });
  });
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/");
  const rail = page.getByRole("navigation", { name: "主要導覽", exact: true });
  const cards = rail.getByRole("link", { name: /^翻卡片/ });
  await expect(cards).toHaveAccessibleName(/^翻卡片\s*，5 張不熟$/);
  await expect(cards).toContainText("5");
  for (const [name, path] of [
    ["我的群島", "/materials"], ["Podcast", "/podcasts"], ["成就", "/achievements"],
    ["新增教材", "/upload"], ["個人檔案與設定", "/profile"], ["首頁", "/"],
  ] as const) {
    await rail.getByRole("link", { name, exact: true }).click();
    await expect.poll(() => new URL(page.url()).pathname).toBe(path);
    await expect(rail.getByRole("link", { name, exact: true })).toHaveAttribute("aria-current", "page");
    await expect(rail.locator('[aria-current="page"]')).toHaveCount(1);
  }
  // 換頁時重讀；讀不到就不顯示數字。
  forgot = null;
  await cards.click();
  await expect.poll(() => new URL(page.url()).pathname).toBe("/concept-cards");
  await expect(rail.getByRole("link", { name: "翻卡片", exact: true })).toHaveAttribute("aria-current", "page");
  await expect(rail.getByRole("link", { name: "翻卡片", exact: true })).not.toContainText(/\d/);
  // 視窗回到前景時也重讀。
  forgot = 2;
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await expect(rail.getByRole("link", { name: /^翻卡片\s*，2 張不熟$/ })).toBeVisible();
  // 品牌回首頁。
  await page.getByRole("link", { name: "返回 Studydy 首頁" }).click();
  await expect.poll(() => new URL(page.url()).pathname).toBe("/");
});

test("inside a material one material header replaces the site rail and phone tab bar", async ({ page }) => {
  for (const width of [1536, 390]) {
    await page.setViewportSize({ width, height: 844 });
    for (const { path, selector } of workspaces) {
      await page.goto(path);
      await expect(page.locator(selector)).toBeVisible();
      const header = page.locator(".material-header");
      await expect(header).toBeVisible();
      // 教材內只有一列：返回、教材、分頁與問答；沒有全站側欄、搜尋與手機底部頁籤。
      await expect(page.locator(".rail")).toBeHidden();
      await expect(page.getByRole("navigation", { name: "主要導覽", exact: true })).toBeHidden();
      await expect(page.getByRole("navigation", { name: "手機導覽", exact: true })).toBeHidden();
      await expect(page.getByRole("button", { name: "搜尋概念、教材、卡組或 Podcast" })).toBeHidden();
      await expect(header.getByRole("button", { name: path === studyPath ? "返回學習與測驗" : "回到我的教材", exact: true })).toBeVisible();
      await expect(header.getByRole("tablist", { name: "教材學習內容" })).toBeVisible();
      await expect(header.getByRole("button", { name: "Studydy AI BOT", exact: true })).toBeVisible();
      const box = (await header.boundingBox())!;
      expect(box.y).toBeLessThanOrEqual(12);
      if (width > 720) expect(box.height).toBeLessThanOrEqual(60);
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
      await page.getByRole("button", { name: path === studyPath ? "返回學習與測驗" : "回到我的教材", exact: true }).click();
      if (path === studyPath) {
        await expect(page).toHaveURL(mapPath);
        await expect(page.getByRole("tab", { name: "學習與測驗" })).toHaveAttribute("aria-selected", "true");
      } else {
        await expect(page).toHaveURL(/\/materials$/);
        await expect(page.getByRole("heading", { name: "我的教材", exact: true })).toBeVisible();
      }
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
  await expect(page.getByRole("navigation", { name: "主要導覽", exact: true })).toBeVisible();
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
  await expect(page.getByRole("navigation", { name: "主要導覽", exact: true })).toBeVisible();
  await expect(page.locator(".map-workspace")).toHaveCount(0);
  releaseRestore();
  await expect(page).toHaveURL(`${browserOrigin}${mapPath}`);
  await expect(page.getByRole("tab", { name: "學習與測驗", exact: true })).toHaveAttribute(
    "aria-selected",
    "true",
  );
});
