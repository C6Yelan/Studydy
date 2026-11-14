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
const progressPath = `${mapPath}/progress`;
const workspaces = [
  { path: mapPath, selector: ".map-workspace" },
  { path: progressPath, selector: ".review-diagnosis" },
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

test("inside a material the material rail replaces the site rail and phone tab bar", async ({ page }) => {
  for (const width of [1536, 1024, 390]) {
    await page.setViewportSize({ width, height: 844 });
    for (const { path, selector, current } of [
      { path: mapPath, selector: ".map-workspace", current: "知識地圖" },
      { path: progressPath, selector: ".review-diagnosis", current: "學習進度" },
    ]) {
      await page.goto(path);
      await expect(page.locator(selector)).toBeVisible();
      const nav = page.getByRole("navigation", { name: "教材導覽", exact: true });
      await expect(nav).toBeVisible();
      // 教材內沒有全站側欄、搜尋與全站手機頁籤。
      await expect(page.getByRole("navigation", { name: "主要導覽", exact: true })).toHaveCount(0);
      await expect(page.getByRole("navigation", { name: "手機導覽", exact: true })).toHaveCount(0);
      await expect(page.getByRole("button", { name: "搜尋概念、教材、卡組或 Podcast" })).toHaveCount(0);
      await expect(nav.getByRole("link", { name: current, exact: true })).toHaveAttribute("aria-current", "page");
      await expect(nav.locator('[aria-current="page"]')).toHaveCount(1);
      if (width > 720) {
        for (const name of ["知識地圖", "學習進度", "翻卡片", "聽 Podcast", "我的筆記", "找更多資料", "原始檔案", "教材設定"])
          await expect(nav.getByRole("link", { name, exact: true })).toBeVisible();
        await expect(nav.getByRole("button", { name: "問小幫手", exact: true })).toBeVisible();
        await expect(nav.getByRole("button", { name: "更多", exact: true })).toBeHidden();
        // 地圖預設收成約 64px 圖示列；其他頁桌機是完整側欄，平板都是圖示列。
        expect((await page.locator(".rail").boundingBox())!.width).toBe(width > 1024 ? path === mapPath ? 64 : 236 : 76);
        if (width > 1024 && path === progressPath) await expect(page.locator(".material-rail-name")).toHaveText("目前教材Data structures.pdf");
        expect((await page.locator(selector).boundingBox())!.x).toBeGreaterThanOrEqual((await page.locator(".rail").boundingBox())!.width);
      } else {
        // 手機：底部四個常用項目加「更多」，其餘項目在「更多」裡，Esc 收起。
        const more = nav.getByRole("button", { name: "更多", exact: true });
        await expect(nav.getByRole("link", { name: "知識地圖", exact: true })).toBeVisible();
        await expect(nav.getByRole("link", { name: "我的筆記", exact: true })).toBeHidden();
        const bar = (await nav.boundingBox())!;
        expect(bar.y + bar.height).toBeGreaterThan(844 - 90);
        await more.click();
        await expect(more).toHaveAttribute("aria-expanded", "true");
        for (const name of ["我的筆記", "找更多資料", "原始檔案", "教材設定"])
          await expect(nav.getByRole("link", { name, exact: true })).toBeVisible();
        await expect(nav.getByRole("button", { name: "問小幫手", exact: true })).toBeVisible();
        await page.keyboard.press("Escape");
        await expect(more).toHaveAttribute("aria-expanded", "false");
        await expect(more).toBeFocused();
        await expect(nav.getByRole("link", { name: "我的筆記", exact: true })).toBeHidden();
      }
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(width);
    }
  }
});

test("material rail counts this material's forgotten cards and only opens sources and settings without a map", async ({ page }) => {
  await page.route((url) => url.pathname === "/v1/card-reviews", (route) => {
    expect(new URL(route.request().url()).searchParams.get("material_id")).toBe(materialId);
    return json(route, { schema: "card-reviews/v2", forgot_count: 3, sets: [], marks: [], cards: [] });
  });
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto(progressPath);
  const nav = page.getByRole("navigation", { name: "教材導覽", exact: true });
  await expect(nav.getByRole("link", { name: /^翻卡片\s*，3 張不熟$/ })).toBeVisible();
  // 沒有知識地圖的教材（確認來源中）：只開放原始檔案與教材設定。
  await page.route((url) => url.pathname === `/v1/materials/${materialId}`, (route) => json(route, {
    schema: "material-library-item/v1", material_id: materialId, source_artifact_id: sessionId, display_name: "還沒分析的教材",
    size_bytes: 100, created_at: "2026-10-04T00:00:00Z", head_revision: null, latest_attempt: null, available_structures: [], study_sessions: [],
  }));
  await page.goto(`/materials/${materialId}/settings`);
  await expect(page.locator(".material-rail-name")).toHaveText("目前教材還沒分析的教材");
  for (const name of ["知識地圖", "學習進度", /^翻卡片/, "聽 Podcast", "我的筆記", "找更多資料"])
    await expect(nav.getByRole("link", { name, exact: typeof name === "string" })).toHaveAttribute("aria-disabled", "true");
  await expect(nav.getByRole("button", { name: "問小幫手", exact: true })).toHaveCount(0);
  await expect(nav.getByRole("link", { name: "教材設定", exact: true })).toHaveAttribute("aria-current", "page");
  await nav.getByRole("link", { name: "原始檔案", exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`/materials/${materialId}/sources$`));
});

test("map, card and player rails start collapsed; hover overlays and the toggle pins them open", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto(mapPath);
  await expect(page.locator(".map-workspace")).toBeVisible();
  const rail = page.locator(".rail");
  const toggle = page.getByRole("button", { name: "展開側欄", exact: true });
  await expect(toggle).toHaveAttribute("aria-expanded", "false");
  expect((await rail.boundingBox())!.width).toBe(64);
  const contentX = (await page.locator(".map-workspace").boundingBox())!.x;
  // 滑過時疊在內容上展開，內容不移動。
  await rail.hover();
  await expect.poll(async () => (await rail.boundingBox())!.width).toBe(236);
  await expect(page.getByRole("link", { name: "學習進度", exact: true })).toBeVisible();
  expect((await page.locator(".map-workspace").boundingBox())!.x).toBe(contentX);
  await toggle.click();
  const collapse = page.getByRole("button", { name: "收合側欄", exact: true });
  await expect(collapse).toHaveAttribute("aria-expanded", "true");
  await page.mouse.move(1200, 600);
  await expect.poll(async () => (await page.locator(".map-workspace").boundingBox())!.x).toBeGreaterThanOrEqual(236);
  expect((await rail.boundingBox())!.width).toBe(236);
  await collapse.click();
  await page.mouse.move(1200, 600);
  await expect.poll(async () => (await rail.boundingBox())!.width).toBe(64);
  // 其他教材頁預設展開，沒有收合按鈕。
  await page.getByRole("link", { name: "學習進度", exact: true }).click();
  await expect(page).toHaveURL(progressPath);
  await page.mouse.move(1200, 600);
  await expect.poll(async () => (await rail.boundingBox())!.width).toBe(236);
  await expect(page.getByRole("button", { name: /^(展開|收合)側欄$/ })).toHaveCount(0);
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
      const nav = page.getByRole("navigation", { name: "教材導覽", exact: true });
      const target = path === progressPath ? "知識地圖" : "學習進度";
      await nav.getByRole("link", { name: target, exact: true }).click();
      await expect(page).toHaveURL(path === progressPath ? mapPath : progressPath);
      await expect(nav.getByRole("link", { name: target, exact: true })).toHaveAttribute("aria-current", "page");
      // 品牌回首頁，離開教材。
      await page.getByRole("link", { name: "返回 Studydy 首頁" }).click();
      await expect(page).toHaveURL(`${browserOrigin}/`);
      await expect(page.getByRole("navigation", { name: "教材導覽", exact: true })).toHaveCount(0);
    }
  });
}

test("bootstrap and persisted pageshow preserve the frame and route", async ({
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
  await page.getByRole("navigation", { name: "教材導覽", exact: true }).getByRole("link", { name: "學習進度", exact: true }).click();
  await expect(page).toHaveURL(`${browserOrigin}${progressPath}`);
  await page.evaluate(() =>
    window.dispatchEvent(new PageTransitionEvent("pageshow", { persisted: true })),
  );
  // 等新 client 發出地圖讀取後再放行，確保檢查的是還原後的畫面。
  await expect.poll(() => mapReads).toBe(2);
  // 已記得登入時直接用新 client 還原，外框（教材側欄）保持顯示，內容等讀取完成。
  await expect(page.getByRole("navigation", { name: "教材導覽", exact: true })).toBeVisible();
  await expect(page.locator(".map-workspace")).toHaveCount(0);
  releaseRestore();
  await expect(page).toHaveURL(`${browserOrigin}${progressPath}`);
  await expect(page.getByRole("link", { name: "學習進度", exact: true })).toHaveAttribute("aria-current", "page");
  await expect(page.locator(".review-diagnosis")).toBeVisible();
});
