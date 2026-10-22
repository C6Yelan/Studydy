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

// 多份教材：每份都用長檔名，讓標籤撐到最大寬度；第一份學習中，帶「你在這裡」。
function manyMaterials(count: number): MaterialLibraryItem[] {
  return Array.from({ length: count }, (_, index) => ({
    ...(index === 0 ? withStudy() : material),
    material_id: uuid(100 + index),
    display_name: `第 ${index + 1} 份｜${longName}`,
    created_at: `2026-09-${String(20 - index).padStart(2, "0")}T00:00:00Z`,
  }));
}

const islands = (page: Page) => page.locator(".map-island:not(.is-new)");
const mapView = (page: Page) => page.locator(".island-map-viewport").evaluate((element: HTMLElement) => ({
  x: parseFloat(element.style.getPropertyValue("--tx")),
  y: parseFloat(element.style.getPropertyValue("--ty")),
  k: parseFloat(element.style.getPropertyValue("--k")),
}));

for (const [state, items, action] of [
  ["empty", [], "上傳教材"],
  ["map", [material], "開啟知識地圖"],
  ["unpublished", [{ ...material, available_structures: [] }], null],
  ["active", [withStudy("active")], "繼續學習"],
  ["no_safe", [withStudy("no_safe")], "繼續學習"],
  ["completed", [withStudy("completed")], "查看學習成果"],
] as const) {
  test(`dashboard ${state} shows island status and the matching next step`, async ({ page }) => {
    await mockLibrary(page, () => items);
    await page.goto("/");
    await expect(page.locator(".island-map-viewport")).toHaveAttribute("aria-busy", "false");
    await expect(islands(page)).toHaveCount(items.length);
    // 地圖上永遠有「發現新島嶼」入口。
    await expect(page.locator(".map-island.is-new").getByRole("button", { name: /發現新島嶼/ })).toBeVisible();
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
  const island = islands(page);
  await expect(island).toHaveClass(/is-focus/);
  await expect(island.locator(".island-pin")).toHaveText("你在這裡");
  await island.getByRole("button", { name: `${longName}，學習中` }).click();
  await expect.poll(() => new URL(page.url()).pathname).toBe(mapPath);
});

for (const [width, height] of [[2560, 1440], [1536, 1024], [1366, 768], [1024, 768], [390, 844], [320, 844]]) {
  test(`dashboard long filename fits without page overflow at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height });
    await mockLibrary(page, () => [withStudy()]);
    await page.route("**/v1/learning-activity", (route) => route.fulfill({ json: learningActivity({}) }));
    await page.goto("/");
    await expect(page.getByRole("button", { name: "繼續學習", exact: true })).toBeVisible();
    const flag = page.locator(".map-island .island-flag-text b");
    await expect(flag).toHaveText(longName);
    expect(await flag.evaluate((element) => element.getBoundingClientRect().width)).toBeLessThanOrEqual(221);
    const map = (await page.locator(".archipelago").boundingBox())!;
    const side = (await page.locator(".dashboard-side").boundingBox())!;
    if (width > 1200) {
      expect(side.x).toBeGreaterThanOrEqual(map.x + map.width + 20);
      expect(Math.abs(map.y - side.y)).toBeLessThan(2);
      // 寬螢幕：多出的寬度給地圖，右欄維持窄欄。
      if (width >= 2560) {
        expect(side.width).toBeLessThanOrEqual(400);
        expect(map.width).toBeGreaterThan(side.width * 3);
      }
    } else expect(side.y).toBeGreaterThanOrEqual(map.y + map.height);
    await expect
      .poll(() => page.locator(".archipelago-foot img").evaluate((img: HTMLImageElement) => img.complete && img.naturalWidth > 0))
      .toBe(true);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  });
}

test("dashboard loading keeps the map busy until the library arrives", async ({ page }) => {
  let release!: () => void;
  const pending = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route("**/v1/materials", async (route) => {
    await pending;
    await route.fulfill({ json: { schema: "material-library/v1", materials: [material] } });
  });
  await page.goto("/");
  const map = page.locator(".island-map-viewport");
  await expect(map).toHaveAttribute("aria-busy", "true");
  await expect(page.locator(".island-map-skeleton")).toBeVisible();
  await expect(page.getByRole("button", { name: "上傳教材", exact: true }).first()).toBeEnabled();
  release();
  await expect(map).toHaveAttribute("aria-busy", "false");
  await expect(page.locator(".archipelago-head span")).toHaveText("1 份教材");
  await expect(islands(page)).toHaveCount(1);
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
  await expect(page.locator(".island-map")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "上傳教材", exact: true })).toBeEnabled();
  await page.getByRole("button", { name: "重新讀取", exact: true }).click();
  await expect(page.locator(".archipelago-head span")).toHaveText("0 份教材");
  await expect(page.getByRole("alert")).toHaveCount(0);
  await page
    .getByRole("navigation", { name: "主要導覽" })
    .getByRole("link", { name: "我的教材", exact: true })
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
  await expect(islands(page)).toHaveCount(1);
  await expect(page.getByRole("button", { name: "繼續學習", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "開啟知識地圖", exact: true })).toBeVisible();
});

test("dashboard uses each material's head study", async ({ page }) => {
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
  await expect(page.getByRole("navigation", { name: "主要導覽" }).getByRole("link")).toHaveText([
    "首頁",
    "我的教材",
    "概念卡",
    "Podcast",
  ]);
  await expect(page.getByRole("button", { name: "登出", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "繼續學習", exact: true }).click();
  await expect
    .poll(() => new URL(page.url()).pathname)
    .toBe(
      `/materials/${materialId}/runs/${headRunId}/knowledge-structures/${encodeURIComponent(headRevision)}/study-sessions/${headSession.study_session_id}`,
    );
});

test("home keeps only quests beside the map; achievements and reviews have their own pages", async ({ page }) => {
  await mockLibrary(page, () => [withStudy()]);
  await page.route("**/v1/learning-activity", (route) => route.fulfill({ json: learningActivity({
    total: 245, level: [2, 145, 200],
    quests: [{ kind: "review_new_cards", progress: 0, target: 5, done: false }],
  }) }));
  await page.goto("/");
  await expect(page.getByRole("region", { name: "今日任務" })).toBeVisible();
  // 等級、徽章、本週節奏、學習總覽、Podcast 與概念卡複習都不在首頁。
  for (const name of ["第 2 級", "我的徽章", "本週學習節奏", "學習總覽", "Podcast", "概念卡複習"])
    await expect(page.getByRole("region", { name, exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: /第 2 級 · 245 XP/ }).click();
  await expect(page).toHaveURL(/\/achievements$/);
  await expect(page.getByRole("region", { name: "第 2 級" })).toBeVisible();
  await page.goto("/");
  await page.getByRole("button", { name: "開始複習：複習 5 張新的概念卡" }).click();
  await expect(page).toHaveURL(/\/concept-cards$/);
});

test("home shows the material count without page totals or a date line", async ({ page }) => {
  await mockLibrary(page, () => [withStudy(), { ...material, material_id: uuid(7), latest_attempt: null }]);
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("歡迎回來！今天想探索哪座島？");
  await expect(page.locator(".archipelago-head span")).toHaveText("2 份教材");
  await expect(page.locator(".dashboard-greeting .eyebrow")).toHaveCount(0);
});

test("profile week rhythm shows answers, card reviews and listening per day", async ({ page }) => {
  await page.route("**/v1/learning-activity", (route) => route.fulfill({ json: learningActivity({
    streak: 3, total: 40,
    days: [
      { answers: 2, correct: 1, active: true }, { listened_seconds: 59 }, { answers: 5, correct: 4, card_reviews: 6, listened_seconds: 600, active: true },
    ],
  }) }));
  await page.goto("/profile");
  const week = page.getByRole("region", { name: "本週學習節奏" });
  await expect(week.getByText("連續 3 天")).toBeVisible();
  await expect(week.getByRole("listitem")).toHaveCount(7);
  await expect(week.getByRole("listitem", { name: "星期三 10/21（今天）：作答 5 題，答對 4 題，複習 6 張卡，收聽 10 分鐘" })).toHaveClass(/is-today/);
  // 收聽不滿 1 分鐘不顯示分鐘數，也不算當天活躍。
  await expect(week.getByRole("listitem", { name: "星期二 10/20：沒有學習紀錄" })).not.toHaveClass(/is-active/);
  await expect(week.getByRole("listitem", { name: "星期四 10/22：尚未到" })).toHaveClass(/is-future/);
  await expect(week.locator(".week-summary")).toHaveText("本週作答 7 題（答對率 71%） · 複習 6 張卡 · 收聽 10 分鐘");
});

test("achievements level card, XP rules and profile overview show real numbers without inventing any", async ({ page }) => {
  await mockLibrary(page, () => [withStudy(), { ...material, material_id: uuid(7) }]);
  await page.route("**/v1/learning-activity", (route) => route.fulfill({ json: learningActivity({
    streak: 4, total: 245, todayXp: 32, level: [2, 145, 200],
    sources: { answers: 180, concepts: 30, cards: 20, podcast: 15 },
  }) }));
  await page.goto("/");
  const pill = page.getByRole("button", { name: "連續學習 4 天，第 2 級，累計 245 XP，查看成就" });
  await expect(pill).toContainText("Lv 2");
  await pill.click();
  await expect(page).toHaveURL(/\/achievements$/);
  const level = page.getByRole("region", { name: "第 2 級" });
  await expect(level.getByText("累計 245 XP · 今天 +32")).toBeVisible();
  await expect(level.getByRole("progressbar", { name: "第 2 級經驗值" })).toHaveAttribute("aria-valuetext", "145 / 200 XP");
  await expect(level.getByText("再 55 XP 升到第 3 級")).toBeVisible();
  await expect(level.getByRole("list", { name: "經驗值來源" }).getByRole("listitem")).toHaveText(["作答 180", "概念完成 30", "概念卡 20", "Podcast 15"]);
  // 經驗值說明列出每種來源的規則與已獲得的 XP，以及升級公式與目前差距。
  const rules = page.getByRole("region", { name: "經驗值怎麼算" });
  await expect(rules.getByRole("listitem")).toHaveCount(4);
  await expect(rules.getByRole("listitem").filter({ hasText: "作答" })).toContainText(/答對 \+10、答錯 \+3.*180/);
  await expect(rules.getByRole("listitem").filter({ hasText: "收聽 Podcast" })).toContainText("每天最多 +60");
  await expect(rules).toContainText("第 n 級升到下一級需要 100×n XP");
  await expect(rules).toContainText("目前第 2 級，再 55 XP 升到第 3 級");
  await page.getByRole("button", { name: /^個人檔案：/ }).click();
  await expect(page).toHaveURL(/\/profile$/);
  await expect(page.getByRole("region", { name: "第 2 級" })).toHaveCount(0);
  const stats = page.getByRole("region", { name: "學習總覽" }).locator(".dashboard-stats");
  await expect(stats).toHaveAttribute("aria-busy", "false");
  await expect(stats.locator("small")).toHaveText(["份教材", "學習中的教材", "已完成概念", "累計作答", "答對率"]);
  // 概念進度摘要讀不到時只算得出 0 個已完成概念；沒有作答時答對率顯示「—」。
  await expect(stats.locator("strong")).toHaveText(["2", "1", "0", "0", "—"]);
  await expect(stats.locator("button, a, [role=button], [tabindex]")).toHaveCount(0);
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(pill.locator(".header-xp")).toHaveText("245 XP");
  await expect(pill.locator(".header-level")).toBeHidden();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
});

test("profile overview shows dashes when the library cannot be read", async ({ page }) => {
  await page.route("**/v1/materials", (route) => route.fulfill({ status: 503, json: unavailable }));
  await page.route("**/v1/learning-activity", (route) => route.fulfill({ json: learningActivity({ days: [{ answers: 10, correct: 7, active: true }] }) }));
  await page.goto("/profile");
  const stats = page.getByRole("region", { name: "學習總覽" }).locator(".dashboard-stats strong");
  // 作答數與答對率來自學習活動，不受教材清單失敗影響。
  await expect(stats).toHaveText(["—", "—", "—", "10", "70%"]);
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
  await expect(page.getByRole("button", { name: /第 3 級/ }).first()).toBeVisible();
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
  await expect(page.getByRole("button", { name: /第 3 級/ }).first()).toBeVisible();
  await expect(page.getByRole("status").filter({ hasText: "升級了！" })).toHaveCount(0);
  // 等級下降（例如刪除教材）不播放，也不覆蓋較高的已看等級。
  level = 2;
  await page.reload();
  await expect(page.getByRole("button", { name: /第 2 級/ }).first()).toBeVisible();
  await expect(page.getByRole("status").filter({ hasText: "升級了！" })).toHaveCount(0);
});

test("achievements group badge tiers into series with earned dates and next-tier progress", async ({ page }) => {
  await page.route("**/v1/learning-activity", (route) => route.fulfill({ json: learningActivity({
    level: [6, 0, 600],
    badges: {
      first_answer: { earned_on: "2026-10-16" }, answers_100: { earned_on: "2026-10-18" }, answers_300: { progress: 124 }, answers_1000: { progress: 124 },
      streak_3: { earned: true }, concepts_25: { progress: 3 },
      // 前端不認得的代號不顯示，也不算進數量。
      future_badge: { earned_on: "2026-10-20" },
    },
  }) }));
  await page.goto("/achievements");
  const wall = page.getByRole("region", { name: "我的徽章" });
  // 第 6 級：等級系列已取得 level_5；加上作答 2 層與連續 1 層，共 4 / 24。
  await expect(wall.locator(".badge-count")).toHaveText("4 / 24");
  await expect(wall).not.toContainText("future_badge");
  const series = wall.locator(".badge-wall > li");
  await expect(series).toHaveCount(7);
  // 作答系列：目前為第 II 層（銀），下一層是題海航行者，進度 124 / 300。
  const answers = series.filter({ hasText: "作答 ·" });
  await expect(answers).toHaveClass(/is-tier-2/);
  await expect(answers.locator(".badge-series-text > b")).toHaveText("百題挑戰");
  await expect(answers.locator(".badge-series-count")).toHaveText("2 / 4");
  await expect(answers).toContainText("下一層：題海航行者 · 累計作答 300 題");
  await expect(answers.getByRole("progressbar")).toHaveAttribute("aria-valuetext", "124 / 300 題");
  await answers.locator("summary").click();
  await expect(answers.locator(".badge-series-tiers li")).toHaveText([/初次作答.*2026\/10\/16 取得/, /百題挑戰.*2026\/10\/18 取得/, /題海航行者.*124 \/ 300 題/, /千題大師.*124 \/ 1000 題/]);
  // 尚未取得任何層級的系列顯示第一層的條件與進度。
  const cards = series.filter({ hasText: "概念卡複習 ·" });
  await expect(cards).toHaveClass(/is-locked/);
  await expect(cards).toContainText("取得條件：累計複習概念卡 100 次");
  const levels = series.filter({ hasText: "等級 ·" });
  await expect(levels.locator(".badge-series-text > b")).toHaveText("進階探險家");
  await expect(levels.getByRole("progressbar")).toHaveAttribute("aria-valuetext", "目前第 6 級");
  // 最接近的下一個徽章：進度比例最高的未取得層級（等級 6/10 高於作答 124/300）。
  await expect(wall.locator(".badge-next")).toContainText("資深航海士");
  await page.setViewportSize({ width: 390, height: 844 });
  const box = await wall.boundingBox();
  expect(box!.x).toBeGreaterThanOrEqual(0);
  expect(box!.x + box!.width).toBeLessThanOrEqual(390);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
});

test("new badge notice shows once per badge after the first visit in this browser", async ({ page }) => {
  let earned: Record<string, { earned_on: string }> = { first_answer: { earned_on: "2026-10-16" } };
  await page.route("**/v1/learning-activity", (route) => route.fulfill({ json: learningActivity({ badges: earned }) }));
  const key = `studydy.badges-seen:${learnerId}`;
  // 第一次在此瀏覽器讀到時只記下既有徽章，不提示。
  await page.goto("/");
  await expect(page.locator(".header-progress")).toBeVisible();
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
  await expect(page.locator(".header-progress")).toBeVisible();
  await expect(page.getByRole("status").filter({ hasText: "獲得新徽章！" })).toHaveCount(0);
});

test("dashboard hides learning numbers when learning activity is unavailable", async ({ page }) => {
  await mockLibrary(page, () => [withStudy()]);
  await page.goto("/");
  await expect(page.getByRole("button", { name: "繼續學習", exact: true })).toBeVisible();
  await expect(page.getByRole("region", { name: "今日任務" })).toHaveCount(0);
  await expect(page.locator(".dashboard-achievements, .header-progress")).toHaveCount(0);
  await page.goto("/achievements");
  await expect(page.getByText("無法讀取學習成就")).toBeVisible();
  await expect(page.locator(".dashboard-level, .badge-collection")).toHaveCount(0);
  await page.goto("/profile");
  await expect(page.getByText("無法讀取學習統計")).toBeVisible();
  // 帳號設定不受影響。
  await expect(page.getByRole("region", { name: "帳號設定" }).getByRole("button", { name: "儲存名稱" })).toBeEnabled();
});

for (const count of [3, 6, 9])
  for (const [width, height] of [[1440, 900], [1024, 768], [390, 844]]) {
    test(`dashboard map shows all ${count} islands and labels never overlap at ${width}px`, async ({ page }) => {
      await page.setViewportSize({ width, height });
      await mockLibrary(page, () => manyMaterials(count));
      await page.goto("/");
      await expect(islands(page)).toHaveCount(count);
      await expect(page.locator(".island-pin")).toHaveCount(1);
      const boxes = await page.locator(".map-island .island-flag").evaluateAll((elements) =>
        elements.map((element) => {
          const { left, top, right, bottom } = element.getBoundingClientRect();
          return { left, top, right, bottom, name: element.textContent ?? "" };
        }),
      );
      for (const [index, box] of boxes.entries())
        for (const other of boxes.slice(index + 1)) {
          const overlaps = box.left < other.right && other.left < box.right && box.top < other.bottom && other.top < box.bottom;
          expect(overlaps, `${box.name} / ${other.name}`).toBe(false);
        }
      // 初始全覽：學習中的島一定在畫面內。
      const viewport = (await page.locator(".island-map-viewport").boundingBox())!;
      const focus = (await page.locator(".map-island.is-focus .island-flag").boundingBox())!;
      expect(focus.x).toBeGreaterThanOrEqual(viewport.x);
      expect(focus.x + focus.width).toBeLessThanOrEqual(viewport.x + viewport.width);
    });
  }

test("dashboard map pans, zooms and keeps a drag from opening an island", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await mockLibrary(page, () => manyMaterials(9));
  await page.goto("/");
  await expect(islands(page)).toHaveCount(9);
  const viewport = (await page.locator(".island-map-viewport").boundingBox())!;
  const start = await mapView(page);
  // 拖曳海面：地圖跟著移動。
  await page.mouse.move(viewport.x + 40, viewport.y + 40);
  await page.mouse.down();
  await page.mouse.move(viewport.x + 140, viewport.y + 90, { steps: 6 });
  await page.mouse.up();
  const dragged = await mapView(page);
  expect(dragged.x - start.x).toBeCloseTo(100, 0);
  expect(dragged.y - start.y).toBeCloseTo(50, 0);
  await expect(page.locator(".island-map-hint")).toHaveCSS("opacity", "0");
  // 從島上開始拖曳不算點擊。
  const art = (await page.locator(".map-island.is-focus .map-island-art").boundingBox())!;
  await page.mouse.move(art.x + art.width / 2, art.y + art.height / 2);
  await page.mouse.down();
  await page.mouse.move(art.x + art.width / 2 + 80, art.y + art.height / 2, { steps: 6 });
  await page.mouse.up();
  await expect(page).toHaveURL(/\/$/);
  // 滾輪以游標為中心放大：游標下的地圖點不移動（先回到全覽，避免碰到地圖邊界的限制）。
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.getByRole("button", { name: "回到全覽" }).click();
  const before = await mapView(page);
  const cursor = { x: viewport.width * 0.45, y: viewport.height * 0.45 };
  await page.mouse.move(viewport.x + cursor.x, viewport.y + cursor.y);
  await page.mouse.wheel(0, -300);
  const zoomed = await mapView(page);
  expect(zoomed.k).toBeGreaterThan(before.k);
  expect((cursor.x - zoomed.x) / zoomed.k).toBeCloseTo((cursor.x - before.x) / before.k, -1);
  // 按鈕與鍵盤：縮小、放大、回到全覽。
  await page.getByRole("button", { name: "縮小" }).click();
  expect((await mapView(page)).k).toBeLessThan(zoomed.k);
  await page.getByRole("button", { name: "回到全覽" }).click();
  expect(await mapView(page)).toEqual(start);
  await page.locator(".island-map-viewport").focus();
  await page.keyboard.press("+");
  expect((await mapView(page)).k).toBeGreaterThan(start.k);
  await page.keyboard.press("0");
  expect(await mapView(page)).toEqual(start);
  // 小地圖：點左上角，畫面移到地圖左上方。
  const minimap = (await page.getByRole("img", { name: "群島縮圖，點選可移動地圖" }).boundingBox())!;
  await page.mouse.click(minimap.x + 4, minimap.y + 4);
  const moved = await mapView(page);
  expect(moved.x).toBeGreaterThan(start.x);
  expect(moved.y).toBeGreaterThan(start.y);
  // 縮放有上限：放大到底後再按也不再變大。
  for (let step = 0; step < 12; step++) await page.getByRole("button", { name: "放大" }).click();
  const capped = (await mapView(page)).k;
  await page.getByRole("button", { name: "放大" }).click();
  expect((await mapView(page)).k).toBe(capped);
  // 點島嶼才開啟。
  await page.getByRole("button", { name: "回到全覽" }).click();
  await islands(page).first().getByRole("button").click();
  await expect(page).not.toHaveURL(/\/$/);
});

test("phone map keeps the full island list and hides the minimap", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await mockLibrary(page, () => manyMaterials(9));
  await page.goto("/");
  await expect(page.locator(".archipelago-head span")).toHaveText("9 份教材");
  await expect(islands(page)).toHaveCount(9);
  await expect(page.locator(".island-map-hint")).toHaveText("拖曳移動 · 滾輪或雙指縮放");
  await expect(page.locator(".island-map-minimap")).toBeHidden();
  await expect(page.getByRole("list", { name: "全部教材島嶼" }).getByRole("listitem")).toHaveCount(9);
  // 繼續學習在清單之前，不被長清單推到下面。
  const foot = (await page.locator(".archipelago-foot").boundingBox())!;
  const list = (await page.getByRole("list", { name: "全部教材島嶼" }).boundingBox())!;
  expect(foot.y).toBeLessThan(list.y);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  await page.setViewportSize({ width: 1440, height: 900 });
  await expect(page.getByRole("list", { name: "全部教材島嶼" })).toBeHidden();
  await expect(page.locator(".island-map-minimap")).toBeVisible();
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
  await expect(page.locator(".map-island.is-focus .progress-ring")).toContainText("5");
  expect(requested.some((path) => path.includes(`/progress-summary?study_session_id=${sessionId}`))).toBe(true);
  expect(requested.filter((path) => /\/outline|\/map-progress/.test(path))).toEqual([]);
});

for (const [width, height, fits] of [[1920, 950, true], [2560, 1300, true], [1440, 760, true], [1366, 680, false], [1024, 768, false]] as const)
  test(`home ${fits ? "fits the window without scrolling" : "keeps a readable map and scrolls"} at ${width}×${height}`, async ({ page }) => {
    await page.setViewportSize({ width, height });
    await mockLibrary(page, () => manyMaterials(9));
    await page.route("**/v1/learning-activity", (route) => route.fulfill({ json: learningActivity({
      quests: [{ kind: "review_new_cards", progress: 0, target: 5, done: false }, { kind: "continue_study", progress: 0, target: 1, done: false }],
    }) }));
    await page.goto("/");
    await expect(islands(page)).toHaveCount(9);
    await expect(page.getByRole("region", { name: "今日任務" })).toBeVisible();
    const overflow = await page.evaluate(() => document.documentElement.scrollHeight - innerHeight);
    const map = (await page.locator(".island-map-viewport").boundingBox())!;
    if (fits) {
      expect(overflow).toBeLessThanOrEqual(0);
      await expect(page.getByRole("button", { name: "繼續學習", exact: true })).toBeInViewport({ ratio: 1 });
    } else {
      // 較小的螢幕：地圖維持最小高度，頁面照常捲動。
      expect(overflow).toBeGreaterThan(0);
      expect(map.height).toBeGreaterThanOrEqual(380);
    }
  });
