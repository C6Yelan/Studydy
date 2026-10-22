import { expect, test } from "@playwright/test";
import { learningActivity } from "../fixtures/learning-activity";

const id = "11111111-1111-4111-8111-111111111111";
const identity = { schema: "learner-identity/v1", learner_id: id };
const responses: Record<string, unknown> = {
  "/v1/session/refresh": identity,
  "/v1/session": identity,
  "/v1/source-capabilities": {
    schema: "source-capabilities/v1",
    formats: [{ extension: ".pdf", media_type: "application/pdf", max_bytes: 104857600 }],
    quality_notice: "PDF",
  },
  "/v1/topics": { topics: [] },
  "/v1/materials": { schema: "material-library/v1", materials: [] },
  "/v1/card-sets": { schema: "card-set-list/v1", card_sets: [] },
  "/v1/podcasts": { schema: "podcast-list/v1", podcasts: [] },
  "/v1/account/sessions": { schema: "account-sessions/v1", active_sessions: 2 },
  // 較長名稱，檢查頁首在各寬度不溢出。
  "/v1/profile": { schema: "learner-profile/v1", display_name: "林以安同學的學習帳號" },
  // 有學習紀錄的頁首進度、等級、今日任務與本週節奏，讓各寬度一併檢查頁首與首頁右欄不溢出。
  "/v1/learning-activity": learningActivity({
    streak: 128, total: 12450, todayXp: 85, level: [15, 1450, 1500],
    sources: { answers: 9000, concepts: 2400, cards: 650, podcast: 400 },
    days: [[120, 300, 3600], [0, 0, 0], [35, 20, 900], [0, 0, 0], [0, 0, 0], [0, 0, 0], [0, 0, 0]]
      .map(([answers, card_reviews, listened_seconds]) => ({
        answers, correct: Math.floor(answers * 0.8), card_reviews, listened_seconds, active: answers + card_reviews > 0,
      })),
    quests: [
      { kind: "review_due_cards", progress: 3, target: 12, done: false },
      { kind: "continue_study", progress: 1, target: 1, done: true },
      { kind: "finish_podcast_episode", progress: 0, target: 1, done: false },
    ],
    // 多個已取得徽章與長條件的下一個徽章，一併檢查徽章卡不溢出。
    badges: Object.fromEntries(["first_answer", "answers_100", "first_concept", "concepts_25", "first_material", "cards_100", "streak_3", "streak_7"]
      .map((id) => [id, { earned_on: "2026-10-16" }])),
  }),
};

test.beforeEach(async ({ page }) => {
  await page.route(/\/v[12]\//, (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (!responses[path]) throw new Error(`Unexpected layout request: ${request.method()} ${path}`);
    expect(request.method()).toBe(path === "/v1/session/refresh" ? "POST" : "GET");
    return route.fulfill({ json: responses[path] });
  });
});

// 寬度取各斷點兩側（720／1100／1200／1280／1600／1800）與最寬 2560px。
for (const width of [2560, 1920, 1366, 1281, 1100, 1024, 721, 390, 320]) {
  test(`standard frames stay centered without clipping at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 1080 });
    for (const [path, heading] of [
      ["/", "歡迎回來！今天想探索哪座島？"],
      ["/profile", "林以安同學的學習帳號"],
      ["/achievements", "成就"],
      ["/materials", "我的教材"],
      ["/upload", "上傳教材"],
    ]) {
      await page.goto(path);
      const frame = page.locator(".app-main > section");
      await expect(
        frame.getByRole("heading", { name: heading, level: 1, exact: true }),
      ).toBeVisible();
      if (path === "/profile") {
        await expect(page.getByRole("region", { name: "學習總覽" })).toBeVisible();
        await expect(page.locator(".dashboard-stats")).toHaveAttribute("aria-busy", "false");
        await expect(page.getByRole("region", { name: "本週學習節奏" })).toBeVisible();
      }
      if (path === "/achievements") {
        // 8 個指定的層級加上第 15 級已達的等級 I、II 層。
        await expect(page.getByRole("region", { name: "成就總覽" }).locator(":scope > header .badge-count")).toHaveText("10 / 31");
        await expect(page.getByRole("region", { name: "經驗值怎麼算" })).toBeVisible();
      }
      if (path === "/") {
        await expect(page.getByRole("region", { name: "今日任務" })).toBeVisible();
        // 頁首學習進度與品牌、帳號工具不重疊，也不超出視窗。
        const pill = page.getByRole("button", { name: "連續學習 128 天，第 15 級，累計 12450 XP，查看成就" });
        await expect(pill).toBeVisible();
        const header = await page.evaluate(() => {
          const brand = document.querySelector(".header-start")!.getBoundingClientRect();
          const controls = document.querySelector(".account-controls")!.getBoundingClientRect();
          const items = Array.from(document.querySelectorAll(".account-controls > *")).map((e) => e.getBoundingClientRect()).filter((box) => box.width > 0);
          const nav = document.querySelector(".main-nav")!.getBoundingClientRect();
          return {
            // 手機隱藏中央導覽（寬度 0）時只比對品牌。
            gap: Math.min(controls.left - Math.max(brand.right, nav.width ? nav.right : 0), nav.width ? nav.left - brand.right : Infinity),
            right: controls.right, width: innerWidth,
            overlaps: items.some((box, index) => index > 0 && box.left < items[index - 1].right - 0.5),
          };
        });
        expect(header.gap).toBeGreaterThanOrEqual(0);
        expect(header.right).toBeLessThanOrEqual(header.width);
        expect(header.overlaps).toBe(false);
      }
      if (path === "/upload")
        await expect(page.getByLabel("選擇教材檔案", { exact: true })).toBeEnabled();
      const layout = await frame.evaluate((element) => {
        const main = element.parentElement!;
        const bounds = main.getBoundingClientRect();
        const css = getComputedStyle(main);
        const box = element.getBoundingClientRect();
        return {
          centerOffset: box.x + box.width / 2 - (bounds.x + bounds.width / 2),
          width: box.width,
          usableWidth: bounds.width - parseFloat(css.paddingLeft) - parseFloat(css.paddingRight),
          top: box.y,
          headingTop: element.firstElementChild!.getBoundingClientRect().y,
          documentWidth: document.documentElement.scrollWidth,
          clipped: Array.from(element.querySelectorAll("h1, h2, p, button, strong"))
            .filter((e) => e.scrollWidth > e.clientWidth + 1)
            .map((e) => e.textContent),
        };
      });
      expect(Math.abs(layout.centerOffset)).toBeLessThanOrEqual(2);
      // 不鎖定版面寬度數值：不超出可用寬度，且在寬螢幕上確實用到足夠的寬度。
      expect(layout.width).toBeLessThanOrEqual(layout.usableWidth + 1);
      expect(layout.width).toBeGreaterThanOrEqual(Math.min(layout.usableWidth, 1200) - 1);
      if (path === "/upload")
        expect((await page.locator(".file-drop").boundingBox())!.width).toBeLessThanOrEqual(880);
      expect(layout.headingTop).toBeCloseTo(layout.top, 0);
      expect(layout.documentWidth).toBe(width);
      expect(layout.clipped).toEqual([]);
    }
  });
}

test("full-page failure uses the reading frame and keyboard retry without writes", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1920, height: 1080 });
  let reads = 0;
  await page.route("**/v1/materials", (route) => {
    expect(route.request().method()).toBe("GET");
    reads++;
    return route.fulfill({
      status: 503,
      json: {
        schema: "api-error/v1",
        request_id: id,
        reason_code: "STORAGE_UNAVAILABLE",
        retryable: true,
        message: "Request could not be completed.",
      },
    });
  });
  await page.goto("/materials");
  const state = page.locator(".state-view--page");
  await expect(state.getByRole("heading", { level: 1 })).toHaveText("無法讀取教材");
  await expect(state).toContainText("資料服務暫時無法使用");
  expect(reads).toBe(1);
  expect((await state.boundingBox())!.width).toBeLessThanOrEqual(880);
  await state.getByRole("button", { name: "重新讀取" }).focus();
  await page.keyboard.press("Enter");
  await expect.poll(() => reads).toBe(2);
  await expect(state.getByRole("heading", { level: 1 })).toHaveText("無法讀取教材");
  await expect(state).toContainText("資料服務暫時無法使用");
});
