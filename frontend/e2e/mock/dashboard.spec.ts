import { expect, test, type Page } from "@playwright/test";
import type { MaterialLibraryItem, StudySessionLink } from "../../src/api/contracts";
import { learningActivity } from "../fixtures/learning-activity";

const uuid = (seed: number) => `00000000-0000-4000-8000-${String(seed).padStart(12, "0")}`;
const materialId = uuid(1);
const runId = uuid(2);
const sessionId = uuid(3);
const learnerId = uuid(4);
const revision = `knowledge-structure:sha256:${"a".repeat(64)}`;
const mapPath = `/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(revision)}`;
const studyPath = `${mapPath}/study-sessions/${sessionId}`;
const longName =
  "資料結構與演算法：堆疊、佇列、遞迴與樹狀結構的概念整理及練習講義_" +
  "LongMaterialFilename".repeat(5) +
  ".pdf";
const material: MaterialLibraryItem = {
  schema: "material-library-item/v1",
  material_id: materialId,
  source_artifact_id: uuid(6),
  display_name: longName,
  size_bytes: 100,
  created_at: "2026-09-12T00:00:00Z",
  latest_attempt: null,
  available_structures: [
    {
      run_id: runId,
      knowledge_structure_revision: revision,
      created_at: "2026-09-12T00:00:00Z",
      status: "succeeded",
    },
  ],
  study_sessions: [],
};
const active: StudySessionLink = {
  study_session_id: sessionId,
  run_id: runId,
  knowledge_structure_revision: revision,
  status: "active",
  started_at: "2026-09-12T01:00:00Z",
  current_concept_id: null,
};
const unavailable = {
  schema: "api-error/v1",
  request_id: learnerId,
  reason_code: "STORAGE_UNAVAILABLE",
  retryable: true,
  message: "Request could not be completed.",
};

function withStudy(status: StudySessionLink["status"] = "active"): MaterialLibraryItem {
  return { ...material, study_sessions: [{ ...active, status }] };
}

async function mockLibrary(page: Page, items: () => readonly MaterialLibraryItem[]) {
  await page.route("**/v1/materials", (route) => {
    expect(route.request().method()).toBe("GET");
    return route.fulfill({ json: { schema: "material-library/v1", materials: items() } });
  });
}

test.beforeEach(async ({ page }) => {
  await page.route(/\/v[12]\//, (route) => {
    const path = new URL(route.request().url()).pathname;
    if (["/v1/session", "/v1/session/refresh"].includes(path)) {
      expect(route.request().method()).toBe(path.endsWith("/refresh") ? "POST" : "GET");
      return route.fulfill({ json: { schema: "learner-identity/v1", learner_id: learnerId } });
    }
    expect(route.request().method()).toBe("GET");
    if (path === "/v1/topics") return route.fulfill({ json: { topics: [] } });
    if (path === "/v1/source-capabilities")
      return route.fulfill({ json: { schema: "source-capabilities/v1", quality_notice: "PDF", formats: [] } });
    // 概念數、進度、概念卡與 Podcast 只是首頁補充資訊；讀不到時首頁仍須可用。
    return route.fulfill({
      status: 404,
      json: { ...unavailable, reason_code: "RESOURCE_NOT_FOUND", retryable: false },
    });
  });
  await mockLibrary(page, () => []);
});

for (const [state, items, counts, action] of [
  ["empty", [], ["0", "0", "0"], "上傳教材"],
  ["map", [material], ["1", "0", "0"], "開啟知識地圖"],
  ["unpublished", [{ ...material, available_structures: [] }], ["1", "0", "0"], null],
  ["active", [withStudy("active")], ["1", "1", "0"], "繼續學習"],
  ["no_safe", [withStudy("no_safe")], ["1", "1", "0"], "繼續學習"],
  ["completed", [withStudy("completed")], ["1", "0", "0"], "查看學習成果"],
] as const) {
  test(`dashboard ${state} shows counts, island status, and the matching next step`, async ({ page }) => {
    await mockLibrary(page, () => items);
    await page.goto("/");
    const stats = page.locator(".dashboard-stats");
    await expect(stats).toHaveAttribute("aria-busy", "false");
    await expect(stats.locator("strong")).toHaveText([...counts]);
    await expect(page.locator(".archipelago-island")).toHaveCount(items.length);
    const foot = page.locator(".archipelago-foot");
    if (!action) {
      await expect(foot.getByRole("button")).toHaveCount(0);
      await expect(page.getByRole("button", { name: new RegExp(`^${longName.slice(0, 10)}.*建立未完成`) })).toBeVisible();
      return;
    }
    await foot.getByRole("button", { name: action, exact: true }).focus();
    await page.keyboard.press("Enter");
    const expected = state === "empty" ? "/upload" : state === "map" ? mapPath : studyPath;
    await expect.poll(() => new URL(page.url()).pathname).toBe(expected);
  });
}

test("dashboard island opens the head knowledge map and marks the current material", async ({ page }) => {
  await mockLibrary(page, () => [withStudy("active")]);
  await page.goto("/");
  const island = page.locator(".archipelago-island");
  await expect(island).toHaveClass(/is-focus/);
  await expect(island.locator(".island-pin")).toHaveText("你在這裡");
  await island.getByRole("button", { name: `${longName}，學習中` }).click();
  await expect.poll(() => new URL(page.url()).pathname).toBe(mapPath);
});

for (const [width, height] of [[1536, 1024], [1366, 768], [1024, 768], [390, 844], [320, 844]]) {
  test(`dashboard long filename fits without page overflow at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height });
    await mockLibrary(page, () => [withStudy()]);
    await page.goto("/");
    await expect(page.getByRole("button", { name: "繼續學習", exact: true })).toBeVisible();
    const flag = page.locator(".island-flag-text b");
    await expect(flag).toHaveText(longName);
    expect(await flag.evaluate((element) => element.getBoundingClientRect().width)).toBeLessThanOrEqual(221);
    if (width > 720) {
      const main = (await page.locator(".dashboard-primary").boundingBox())!;
      const side = (await page.locator(".dashboard-side").boundingBox())!;
      if (width > 1200) {
        expect(side.x).toBeGreaterThanOrEqual(main.x + main.width + 20);
        expect(Math.abs(main.y - side.y)).toBeLessThan(2);
      } else expect(side.y).toBeGreaterThanOrEqual(main.y + main.height);
    } else {
      // 手機：群島在最上方，其餘卡片依序排在下面。
      const islands = (await page.locator(".archipelago").boundingBox())!;
      const overview = (await page.locator(".dashboard-overview").boundingBox())!;
      expect(overview.y).toBeGreaterThanOrEqual(islands.y + islands.height);
    }
    await expect
      .poll(() => page.locator(".archipelago-foot img").evaluate((img: HTMLImageElement) => img.complete && img.naturalWidth > 0))
      .toBe(true);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  });
}

test("dashboard loading keeps unknown counts until the library arrives", async ({ page }) => {
  let release!: () => void;
  const pending = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route("**/v1/materials", async (route) => {
    await pending;
    await route.fulfill({ json: { schema: "material-library/v1", materials: [material] } });
  });
  await page.goto("/");
  const stats = page.locator(".dashboard-stats");
  await expect(stats).toHaveAttribute("aria-busy", "true");
  await expect(stats.locator("strong")).toHaveText(["—", "—", "—"]);
  await expect(page.locator(".archipelago-stage")).toHaveAttribute("aria-busy", "true");
  await expect(page.getByRole("button", { name: "上傳教材", exact: true }).first()).toBeEnabled();
  release();
  await expect(stats.locator("strong")).toHaveText(["1", "0", "0"]);
  await expect(stats).toHaveAttribute("aria-busy", "false");
});

test("dashboard read failure keeps actions and recovers without reload", async ({ page }) => {
  await page.route(
    "**/v1/materials",
    (route) => route.fulfill({ status: 503, json: unavailable }),
    { times: 1 },
  );
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("歡迎回來！今天想探索哪座島？");
  await expect(page.getByRole("alert")).toContainText("資料服務暫時無法使用");
  const stats = page.locator(".dashboard-stats");
  await expect(stats.locator("strong")).toHaveText(["—", "—", "—"]);
  await expect(stats).toHaveAttribute("aria-busy", "false");
  await expect(page.getByRole("button", { name: "上傳教材", exact: true })).toBeEnabled();
  await page.getByRole("button", { name: "重新讀取", exact: true }).click();
  await expect(stats.locator("strong")).toHaveText(["0", "0", "0"]);
  await expect(page.getByRole("alert")).toHaveCount(0);
  await page
    .getByRole("navigation", { name: "主要導覽" })
    .getByRole("button", { name: "我的教材", exact: true })
    .click();
  await expect(page).toHaveURL(/\/materials$/);
  await expect(page.getByRole("region", { name: "空教材引導" })).toBeVisible();
});

test("dashboard prefers active study over newer completion and ignores mismatched run or revision", async ({
  page,
}) => {
  const secondId = uuid(5);
  let items: MaterialLibraryItem[] = [
    {
      ...withStudy("completed"),
      display_name: "Calculus.pdf",
      study_sessions: [{ ...active, status: "completed", started_at: "2026-09-14T00:00:00Z" }],
    },
    { ...withStudy("no_safe"), material_id: secondId, display_name: "Linear Algebra.pdf" },
    {
      ...material,
      material_id: uuid(11),
      display_name: "Probability.pdf",
      study_sessions: [
        {
          ...active,
          knowledge_structure_revision: `knowledge-structure:sha256:${"b".repeat(64)}`,
          started_at: "2026-09-15T00:00:00Z",
        },
        { ...active, run_id: secondId, started_at: "2026-09-16T00:00:00Z" },
      ],
    },
  ];
  await mockLibrary(page, () => items);
  await page.goto("/");
  await expect(page.locator(".guide-bubble small")).toHaveText("上次停在 · Linear Algebra.pdf");
  await page.getByRole("button", { name: "繼續學習", exact: true }).click();
  await expect
    .poll(() => new URL(page.url()).pathname)
    .toBe(studyPath.replace(materialId, secondId));
  items = items.filter((item) => item.material_id !== secondId);
  await page.goto("/");
  await expect(page.locator(".guide-bubble small")).toHaveText("已完成 · Calculus.pdf");
  await expect(page.getByRole("button", { name: "查看學習成果", exact: true })).toBeVisible();
  items = items.filter((item) => item.display_name !== "Calculus.pdf");
  await page.reload();
  await expect(page.locator(".dashboard-stat strong")).toHaveText(["1", "0", "0"]);
  await expect(page.getByRole("button", { name: "繼續學習", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "開啟知識地圖", exact: true })).toBeVisible();
});

test("dashboard uses each material's head study and keeps stats out of keyboard navigation", async ({ page }) => {
  const headRevision = `knowledge-structure:sha256:${"b".repeat(64)}`;
  const headRunId = uuid(7);
  const headSession = {
    ...active,
    study_session_id: uuid(8),
    run_id: headRunId,
    knowledge_structure_revision: headRevision,
  };
  await mockLibrary(page, () => [
    {
      ...material,
      head_revision: headRevision,
      available_structures: [
        material.available_structures[0],
        { ...material.available_structures[0], run_id: headRunId, knowledge_structure_revision: headRevision },
      ],
      study_sessions: [
        active,
        { ...headSession, study_session_id: uuid(9), run_id: runId },
        headSession,
        { ...headSession, study_session_id: uuid(10) },
      ],
    },
  ]);
  await page.goto("/");
  await expect(page.getByRole("navigation", { name: "主要導覽" }).getByRole("button")).toHaveText([
    "首頁",
    "我的教材",
    "概念卡",
    "Podcast",
  ]);
  await expect(page.getByRole("button", { name: "登出", exact: true })).toBeVisible();
  const stats = page.locator(".dashboard-stats");
  await expect(stats.locator("strong")).toHaveText(["1", "1", "0"]);
  await expect(stats.locator("small")).toHaveText(["份教材", "學習中的教材", "已完成概念"]);
  await expect(stats.locator("button, a, [role=button], [tabindex]")).toHaveCount(0);
  await page.getByRole("button", { name: "繼續學習", exact: true }).click();
  await expect
    .poll(() => new URL(page.url()).pathname)
    .toBe(
      `/materials/${materialId}/runs/${headRunId}/knowledge-structures/${encodeURIComponent(headRevision)}/study-sessions/${headSession.study_session_id}`,
    );
});

test("dashboard week rhythm shows answers, card reviews and listening per day", async ({ page }) => {
  await page.route("**/v1/learning-activity", (route) => route.fulfill({ json: learningActivity({
    streak: 3, total: 40,
    days: [
      { answers: 2, correct: 1, active: true }, { listened_seconds: 59 }, { answers: 5, correct: 4, card_reviews: 6, listened_seconds: 600, active: true },
    ],
  }) }));
  await page.goto("/");
  const week = page.getByRole("region", { name: "本週學習節奏" });
  await expect(week.getByText("連續 3 天")).toBeVisible();
  await expect(week.getByRole("listitem")).toHaveCount(7);
  await expect(week.getByRole("listitem", { name: "星期三 10/21（今天）：作答 5 題，答對 4 題，複習 6 張卡，收聽 10 分鐘" })).toHaveClass(/is-today/);
  // 收聽不滿 1 分鐘不顯示分鐘數，也不算當天活躍。
  await expect(week.getByRole("listitem", { name: "星期二 10/20：沒有學習紀錄" })).not.toHaveClass(/is-active/);
  await expect(week.getByRole("listitem", { name: "星期四 10/22：尚未到" })).toHaveClass(/is-future/);
  await expect(week.locator(".week-summary")).toHaveText("本週作答 7 題（答對率 71%） · 複習 6 張卡 · 收聽 10 分鐘");
});

test("level card and header show XP from learning activity without inventing numbers", async ({ page }) => {
  await page.route("**/v1/learning-activity", (route) => route.fulfill({ json: learningActivity({
    streak: 4, total: 245, todayXp: 32, level: [2, 145, 200],
    sources: { answers: 180, concepts: 30, cards: 20, podcast: 15 },
  }) }));
  await page.goto("/");
  const level = page.getByRole("region", { name: "第 2 級" });
  await expect(level.getByText("累計 245 XP · 今天 +32")).toBeVisible();
  await expect(level.getByRole("progressbar", { name: "第 2 級經驗值" })).toHaveAttribute("aria-valuetext", "145 / 200 XP");
  await expect(level.getByText("再 55 XP 升到第 3 級")).toBeVisible();
  await expect(level.getByRole("list", { name: "經驗值來源" }).getByRole("listitem")).toHaveText(["作答 180", "概念完成 30", "概念卡 20", "Podcast 15"]);
  const pill = page.getByRole("button", { name: "連續學習 4 天，第 2 級，累計 245 XP" });
  await expect(pill).toContainText("Lv 2");
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(pill.locator(".header-xp")).toHaveText("245 XP");
  await expect(pill.locator(".header-level")).toBeHidden();
  // 沒有任務時不顯示今日任務。
  await expect(page.getByRole("region", { name: "今日任務" })).toHaveCount(0);
});

test("today quests show done state and open the matching place", async ({ page }) => {
  await mockLibrary(page, () => [withStudy()]);
  const podcastId = uuid(9);
  await page.route("**/v1/learning-activity", (route) => route.fulfill({ json: learningActivity({
    quests: [
      { kind: "review_new_cards", progress: 2, target: 5, done: false },
      { kind: "continue_study", progress: 0, target: 1, done: false, material_id: materialId, study_session_id: sessionId },
      { kind: "finish_podcast_episode", progress: 1, target: 1, done: true, material_id: materialId, podcast_id: podcastId, episode_index: 0 },
    ],
  }) }));
  await page.goto("/");
  const quests = page.getByRole("region", { name: "今日任務" });
  await expect(quests.getByText("1 / 3")).toBeVisible();
  await expect(quests.getByRole("listitem")).toHaveText([/複習 5 張新的概念卡已完成 2 \/ 5/, /完成 1 次題組作答今天還沒完成/, /聽完 1 集還沒聽完的 Podcast今天已完成/]);
  // 已完成的任務不顯示動作按鈕。
  await expect(quests.getByRole("button")).toHaveCount(2);
  await quests.getByRole("button", { name: "繼續學習：完成 1 次題組作答" }).click();
  await expect.poll(() => new URL(page.url()).pathname).toBe(studyPath);
});

test("level-up plays once when the level is above the last level seen in this browser", async ({ page }) => {
  let level = 3;
  await page.route("**/v1/learning-activity", (route) => route.fulfill({ json: learningActivity({
    total: level === 3 ? 320 : 100, level: level === 3 ? [3, 20, 300] : [2, 0, 200],
  }) }));
  // 第一次看到等級只記下，不播放。
  await page.goto("/");
  await expect(page.getByRole("button", { name: /第 3 級/ })).toBeVisible();
  await expect(page.getByRole("status").filter({ hasText: "升級了！" })).toHaveCount(0);
  expect(await page.evaluate((key) => localStorage.getItem(key), `studydy.level-seen:${learnerId}`)).toBe("3");
  await page.evaluate((key) => localStorage.setItem(key, "2"), `studydy.level-seen:${learnerId}`);
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.reload();
  const celebration = page.getByRole("status").filter({ hasText: "升級了！" });
  await expect(celebration).toContainText("第 3 級");
  await expect(page.locator(".level-up-card")).toHaveCSS("animation-name", "none");
  await celebration.getByRole("button", { name: "太好了" }).click();
  await expect(celebration).toHaveCount(0);
  await page.reload();
  await expect(page.getByRole("button", { name: /第 3 級/ })).toBeVisible();
  await expect(page.getByRole("status").filter({ hasText: "升級了！" })).toHaveCount(0);
  // 等級下降（例如刪除教材）不播放，也不覆蓋較高的已看等級。
  level = 2;
  await page.reload();
  await expect(page.getByRole("button", { name: /第 2 級/ })).toBeVisible();
  await expect(page.getByRole("status").filter({ hasText: "升級了！" })).toHaveCount(0);
});

test("badge shelf and wall show earned dates and real progress", async ({ page }) => {
  await page.route("**/v1/learning-activity", (route) => route.fulfill({ json: learningActivity({
    badges: {
      first_answer: { earned_on: "2026-10-16" }, streak_3: { earned: true }, answers_100: { progress: 42 },
      // 前端不認得的代號不顯示。
      future_badge: { earned_on: "2026-10-20" },
    },
  }) }));
  await page.goto("/");
  const shelf = page.getByRole("region", { name: "我的徽章" });
  await expect(shelf.locator(".badge-count")).toHaveText("2 / 10");
  await expect(shelf.getByRole("list", { name: "已取得的徽章" }).getByRole("listitem")).toHaveText(["初次作答", "三日不間斷"]);
  // 下一個徽章取進度比例最高的未取得徽章（42/100 高於第 1 級 / 5）。
  await expect(shelf.locator(".badge-next")).toContainText("百題挑戰");
  await expect(shelf.locator(".badge-next").getByRole("progressbar")).toHaveAttribute("aria-valuetext", "42 / 100 題");
  await shelf.getByRole("button", { name: "全部徽章" }).click();
  const wall = page.getByRole("dialog", { name: "我的徽章" });
  await expect(wall.getByText("已取得 2 / 10 個")).toBeVisible();
  const items = wall.locator(".badge-wall > li");
  await expect(items).toHaveCount(10);
  await expect(items.nth(0)).toContainText("2026/10/16 取得");
  await expect(items.filter({ hasText: "三日不間斷" })).toContainText("已取得");
  await expect(items.filter({ hasText: "進階探險家" }).getByRole("progressbar")).toHaveAttribute("aria-valuetext", "目前第 1 級");
  await expect(items.filter({ hasText: "用耳朵學習" }).getByRole("progressbar")).toHaveAttribute("aria-valuetext", "0 / 30 分鐘");
  await page.setViewportSize({ width: 390, height: 844 });
  const box = await wall.boundingBox();
  expect(box!.x).toBeGreaterThanOrEqual(0);
  expect(box!.x + box!.width).toBeLessThanOrEqual(390);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  await page.keyboard.press("Escape");
  await expect(wall).toBeHidden();
  await expect(shelf.getByRole("button", { name: "全部徽章" })).toBeFocused();
});

test("new badge notice shows once per badge after the first visit in this browser", async ({ page }) => {
  let earned: Record<string, { earned_on: string }> = { first_answer: { earned_on: "2026-10-16" } };
  await page.route("**/v1/learning-activity", (route) => route.fulfill({ json: learningActivity({ badges: earned }) }));
  const key = `studydy.badges-seen:${learnerId}`;
  // 第一次在此瀏覽器讀到時只記下既有徽章，不提示。
  await page.goto("/");
  await expect(page.getByRole("region", { name: "我的徽章" })).toBeVisible();
  await expect(page.getByRole("status").filter({ hasText: "獲得新徽章！" })).toHaveCount(0);
  expect(JSON.parse(await page.evaluate((name) => localStorage.getItem(name) ?? "null", key))).toEqual(["first_answer"]);
  earned = { ...earned, first_concept: { earned_on: "2026-10-22" } };
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.reload();
  const notice = page.getByRole("status").filter({ hasText: "獲得新徽章！" });
  await expect(notice).toContainText("點亮概念");
  await expect(notice).not.toContainText("初次作答");
  await expect(notice.locator(".badge-medal")).toHaveCSS("animation-name", "none");
  await notice.getByRole("button", { name: "太好了" }).click();
  await expect(notice).toHaveCount(0);
  await page.reload();
  await expect(page.getByRole("region", { name: "我的徽章" })).toBeVisible();
  await expect(page.getByRole("status").filter({ hasText: "獲得新徽章！" })).toHaveCount(0);
});

test("dashboard hides the week rhythm when learning activity is unavailable", async ({ page }) => {
  await mockLibrary(page, () => [withStudy()]);
  await page.goto("/");
  await expect(page.getByRole("button", { name: "繼續學習", exact: true })).toBeVisible();
  await expect(page.getByRole("region", { name: "本週學習節奏" })).toHaveCount(0);
  await expect(page.getByRole("region", { name: "今日任務" })).toHaveCount(0);
  await expect(page.locator(".dashboard-level, .dashboard-badges, .header-progress")).toHaveCount(0);
});

// 多份教材：每份都用長檔名，讓標籤撐到最大寬度；第一份學習中，帶「你在這裡」。
function manyMaterials(count: number): MaterialLibraryItem[] {
  return Array.from({ length: count }, (_, index) => ({
    ...(index === 0 ? withStudy() : material),
    material_id: uuid(100 + index),
    display_name: `第 ${index + 1} 份｜${longName}`,
    created_at: `2026-09-${String(20 - index).padStart(2, "0")}T00:00:00Z`,
  }));
}

for (const count of [3, 6, 9])
  for (const [width, height] of [[1440, 900], [1024, 768], [390, 844]]) {
    test(`dashboard island labels never overlap with ${count} materials at ${width}px`, async ({ page }) => {
      await page.setViewportSize({ width, height });
      await mockLibrary(page, () => manyMaterials(count));
      await page.goto("/");
      await expect(page.locator(".archipelago-island")).toHaveCount(Math.min(count, 6));
      await expect(page.locator(".island-pin")).toHaveCount(1);
      const boxes = await page.locator(".island-flag, .island-pin").evaluateAll((elements) =>
        elements.map((element) => {
          const { left, top, right, bottom } = element.getBoundingClientRect();
          return { left, top, right, bottom, name: element.textContent ?? "" };
        }),
      );
      const stage = (await page.locator(".archipelago-stage").boundingBox())!;
      for (const [index, box] of boxes.entries()) {
        // 標籤不可超出地圖範圍（會被裁掉）。
        expect(box.left).toBeGreaterThanOrEqual(stage.x - 1);
        expect(box.top).toBeGreaterThanOrEqual(stage.y - 1);
        expect(box.right).toBeLessThanOrEqual(stage.x + stage.width + 1);
        for (const other of boxes.slice(index + 1)) {
          const overlaps = box.left < other.right && other.left < box.right && box.top < other.bottom && other.top < box.bottom;
          expect(overlaps, `${box.name} / ${other.name}`).toBe(false);
        }
      }
    });
  }

test("phone swipe hint counts the islands on the map and points to the full list", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  let count = 9;
  await mockLibrary(page, () => manyMaterials(count));
  await page.goto("/");
  await expect(page.locator(".archipelago-head span")).toHaveText("9 份教材");
  await expect(page.locator(".archipelago-island")).toHaveCount(6);
  const hint = page.locator(".archipelago-swipe-hint");
  await expect(hint).toHaveText("滑動看地圖上 6 座島 · 全部 9 份在下方清單");
  await expect(page.getByRole("list", { name: "全部教材島嶼" }).getByRole("listitem")).toHaveCount(9);
  await expect(page.getByRole("list", { name: "全部教材島嶼" }).getByRole("listitem").last()).toBeAttached();
  const box = (await hint.boundingBox())!;
  expect(box.x).toBeGreaterThanOrEqual(0);
  expect(box.x + box.width).toBeLessThanOrEqual(390);
  expect(box.height).toBeLessThan(30);
  // 桌機地圖放不下時改用「還有 N 座島嶼」，數字同樣以地圖上的島為準。
  await page.setViewportSize({ width: 1440, height: 900 });
  await expect(hint).toBeHidden();
  await expect(page.getByRole("button", { name: "還有 3 座島嶼" })).toBeVisible();
  count = 4;
  await page.setViewportSize({ width: 390, height: 844 });
  await page.reload();
  await expect(hint).toHaveText("左右滑動看全部 4 座島");
  await expect(page.getByRole("button", { name: /還有 \d+ 座島嶼/ })).toHaveCount(0);
});

test("dashboard islands read the progress summary instead of the full outline", async ({ page }) => {
  const second = { ...material, material_id: uuid(7), display_name: "Second.pdf", created_at: "2026-09-11T00:00:00Z" };
  const requested: string[] = [];
  page.on("request", (request) => requested.push(new URL(request.url()).pathname + new URL(request.url()).search));
  await page.route(/\/progress-summary/, (route) => {
    const url = new URL(route.request().url());
    const id = url.pathname.split("/")[3];
    const session = url.searchParams.get("study_session_id");
    return route.fulfill({ json: {
      schema: "progress-summary/v1", material_id: id, knowledge_structure_revision: revision, study_session_id: session,
      concept_count: session ? 12 : 40, completed_count: session ? 5 : 0,
      current_concept_id: session ? `concept:sha256:${"b".repeat(64)}` : null, current_concept_label: session ? "堆疊與佇列" : null,
    } });
  });
  await mockLibrary(page, () => [withStudy("active"), second]);
  await page.goto("/");
  await expect(page.getByRole("button", { name: `${longName}，學習中：堆疊與佇列` })).toBeVisible();
  await expect(page.getByRole("button", { name: "Second.pdf，尚未開始 · 40 個概念" })).toBeVisible();
  await expect(page.locator(".archipelago-island.is-focus .progress-ring")).toContainText("5");
  expect(requested.some((path) => path.includes(`/progress-summary?study_session_id=${sessionId}`))).toBe(true);
  expect(requested.filter((path) => /\/outline|\/map-progress/.test(path))).toEqual([]);
});
