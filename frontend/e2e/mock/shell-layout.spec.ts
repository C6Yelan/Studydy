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
import { learningActivity } from "../fixtures/learning-activity";

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
        await expect(phoneNav.getByRole("link")).toHaveText(["首頁", "我的群島", "翻卡片", "Podcast", "成就"]);
        await expect(phoneNav.getByRole("link", { name: current, exact: true })).toHaveAttribute("aria-current", "page");
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
        // 沒選過時大桌機是完整側欄（地圖也一樣），平板都是圖示列。
        expect((await page.locator(".rail").boundingBox())!.width).toBe(width > 1024 ? 236 : 76);
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

test("the rail only changes with the toggle, keeps the choice across pages and names its icons", async ({ page }) => {
  await page.setViewportSize({ width: 1536, height: 900 });
  await page.goto(mapPath);
  await expect(page.locator(".map-workspace")).toBeVisible();
  const rail = page.locator(".rail");
  const railWidth = async () => (await rail.boundingBox())!.width;
  const overflow = () => rail.evaluate((element) => element.scrollWidth - element.clientWidth);
  expect(await railWidth()).toBe(236);
  await page.getByRole("button", { name: "收合側欄", exact: true }).click();
  await expect(page.getByRole("button", { name: "展開側欄", exact: true })).toHaveAttribute("aria-expanded", "false");
  expect(await railWidth()).toBe(64);
  expect(await overflow()).toBe(0);
  // 滑鼠滑過收合的側欄不展開，只出現名稱提示。
  const contentX = (await page.locator(".map-workspace").boundingBox())!.x;
  await page.getByRole("link", { name: "學習進度", exact: true }).hover();
  await page.mouse.move(30, 500, { steps: 5 });
  await page.getByRole("link", { name: "學習進度", exact: true }).hover();
  await expect(page.locator(".rail-tip")).toHaveText("學習進度");
  expect(await railWidth()).toBe(64);
  expect((await page.locator(".map-workspace").boundingBox())!.x).toBe(contentX);
  // 換頁與重新整理後仍是收合。
  await page.getByRole("link", { name: "學習進度", exact: true }).click();
  await expect(page).toHaveURL(progressPath);
  await expect(page.locator(".review-diagnosis")).toBeVisible();
  expect(await railWidth()).toBe(64);
  await page.goto("/");
  await expect(page.getByRole("navigation", { name: "主要導覽", exact: true })).toBeVisible();
  expect(await railWidth()).toBe(64);
  await page.reload();
  await expect(page.getByRole("navigation", { name: "主要導覽", exact: true })).toBeVisible();
  expect(await railWidth()).toBe(64);
  // 鍵盤移到圖示也有名稱提示，側欄不展開、不出現橫向捲軸。
  await page.mouse.move(1200, 600);
  await page.getByRole("link", { name: "Podcast", exact: true }).focus();
  await page.keyboard.press("Tab");
  await expect(page.getByRole("link", { name: "成就", exact: true })).toBeFocused();
  await expect(page.locator(".rail-tip")).toHaveText("成就");
  expect(await railWidth()).toBe(64);
  expect(await overflow()).toBe(0);
  await page.getByRole("button", { name: "展開側欄", exact: true }).click();
  expect(await railWidth()).toBe(236);
  await page.goto(mapPath);
  await expect(page.locator(".map-workspace")).toBeVisible();
  expect(await railWidth()).toBe(236);
  // 平板的圖示列同樣有名稱提示，沒有橫向捲軸。
  await page.setViewportSize({ width: 1024, height: 768 });
  expect(await railWidth()).toBe(76);
  expect(await overflow()).toBe(0);
  await page.getByRole("link", { name: "學習進度", exact: true }).hover();
  await expect(page.locator(".rail-tip")).toHaveText("學習進度");
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

test("earned-badge toast sits at the bottom right and never covers the rail or the phone tab bar", async ({ page }) => {
  await page.route("**/v1/learning-activity", (route) => route.fulfill({ json: learningActivity({ badges: { first_answer: { earned_on: "2026-10-16" } } }) }));
  await page.route("**/v1/learning-activity/celebrations/claim", (route) => route.fulfill({ json: { schema: "celebration-claim/v1", level: null, badge_ids: ["first_answer"] } }));
  type Box = { x: number; y: number; width: number; height: number };
  const apart = (a: Box, b: Box) => a.x + a.width <= b.x || b.x + b.width <= a.x || a.y + a.height <= b.y || b.y + b.height <= a.y;
  for (const [width, height, path, tabBar] of [
    [1440, 900, "/materials", ".rail"], [1440, 900, mapPath, ".rail"], [390, 844, "/materials", ".phone-nav"], [390, 844, mapPath, ".material-rail-nav"],
  ] as const) {
    await page.setViewportSize({ width, height });
    await page.goto(path);
    const toast = page.getByRole("status", { name: "獲得新徽章" });
    await expect(toast).toBeVisible();
    await page.waitForTimeout(400);
    const box = (await toast.boundingBox())!;
    expect(box.x + box.width).toBeGreaterThan(width - 60);
    expect(box.x + box.width).toBeLessThanOrEqual(width);
    expect(apart(box, (await page.locator(tabBar).first().boundingBox())!)).toBe(true);
    if (width > 720) for (const name of ["我的群島", "知識地圖"]) {
      const link = page.getByRole("link", { name, exact: true }).first();
      if (await link.isVisible()) expect(apart(box, (await link.boundingBox())!)).toBe(true);
    }
  }
});

test("phone map fills the screen above the tab bar without extra page scrolling", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(mapPath);
  await expect(page.locator(".map-workspace")).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollHeight)).toBe(844);
  const map = (await page.locator(".map-workspace").boundingBox())!;
  const tabs = (await page.getByRole("navigation", { name: "教材導覽", exact: true }).boundingBox())!;
  expect(map.y + map.height).toBeLessThanOrEqual(tabs.y);
});

test("phone More panel closes on outside tap and after choosing an item", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(mapPath);
  const nav = page.getByRole("navigation", { name: "教材導覽", exact: true });
  const more = nav.getByRole("button", { name: "更多", exact: true });
  await more.click();
  await expect(more).toHaveAttribute("aria-expanded", "true");
  await page.locator(".material-rail-name").click();
  await expect(more).toHaveAttribute("aria-expanded", "false");
  await more.click();
  await nav.getByRole("button", { name: "問小幫手", exact: true }).click();
  await expect(page.getByRole("dialog", { name: "小幫手" })).toBeVisible();
  await expect(more).toHaveAttribute("aria-expanded", "false");
  await page.keyboard.press("Escape");
  await more.click();
  await nav.getByRole("link", { name: "我的筆記", exact: true }).click();
  await expect(more).toHaveAttribute("aria-expanded", "false");
});

test("material rail leads back to My Islands, on desktop and phone", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto(mapPath);
  const back = page.getByRole("link", { name: "我的群島", exact: true });
  await expect(back).toBeVisible();
  expect((await back.boundingBox())!.height).toBeGreaterThanOrEqual(44);
  await back.click();
  await expect.poll(() => new URL(page.url()).pathname).toBe("/materials");
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(mapPath);
  const phoneBack = page.getByRole("link", { name: "我的群島", exact: true });
  const box = (await phoneBack.boundingBox())!;
  expect(box.height).toBeGreaterThanOrEqual(44);
  expect(box.y).toBeLessThan(80);
  await phoneBack.click();
  await expect.poll(() => new URL(page.url()).pathname).toBe("/materials");
});
