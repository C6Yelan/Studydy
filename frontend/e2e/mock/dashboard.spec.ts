import { expect, test, type Page } from "@playwright/test";
import type { MaterialLibraryItem, StudySessionLink } from "../../src/api/contracts";
import { learningActivity, learningRhythm } from "../fixtures/learning-activity";

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
  await page.route("**/v1/materials/*/study-start", route => {
    const mid = new URL(route.request().url()).pathname.split("/")[3];
    const body = route.request().postDataJSON();
    const material = items().find(item => item.material_id === mid)!;
    const structure = material.available_structures.find(item => item.knowledge_structure_revision === body.knowledge_structure_revision)!;
    const stored = material.study_sessions.find(item => item.knowledge_structure_revision === body.knowledge_structure_revision && item.run_id === structure.run_id)!;
    return route.fulfill({ json: { schema: "study-start/v1", starting_level: "some_knowledge", selected_set_id: null,
      session: { ...stored, schema: "study-session/v1", material_id: mid, deferred_concept_ids: [], no_safe_claim_ids: [], event_watermark: 0,
        completed_at: stored.status === "completed" ? stored.started_at : null } } });
  });
  await page.route("**/v1/materials", (route) => {
    expect(route.request().method()).toBe("GET");
    return route.fulfill({ json: { schema: "material-library/v1", materials: items() } });
  });
}

test.beforeEach(async ({ page }) => {
  await page.route(/\/v[12]\//, (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/v1/learning-goals") return route.fulfill({ json: { schema: "learning-goals/v1", today: "2026-10-24", goals: [] } });
    if (["/v1/session", "/v1/session/refresh"].includes(path)) {
      expect(route.request().method()).toBe(path.endsWith("/refresh") ? "POST" : "GET");
      return route.fulfill({ json: { schema: "learner-identity/v1", learner_id: learnerId } });
    }
    expect(route.request().method()).toBe("GET");
    if (path === "/v1/topics") return route.fulfill({ json: { topics: [] } });
    if (path === "/v1/source-capabilities")
      return route.fulfill({ json: { schema: "source-capabilities/v2", max_request_bytes: 104857600, max_images: 50, max_media_seconds: 7200, youtube: false, quality_notice: "PDF", formats: [] } });
    // 概念數、進度、概念卡與 Podcast 只是首頁補充資訊；讀不到時首頁仍須可用。
    return route.fulfill({
      status: 404,
      json: { ...unavailable, reason_code: "RESOURCE_NOT_FOUND", retryable: false },
    });
  });
  await mockLibrary(page, () => []);
});

// 多份教材：每份都用長檔名，讓標籤撐到最大寬度；第一份學習中。
function manyMaterials(count: number): MaterialLibraryItem[] {
  return Array.from({ length: count }, (_, index) => ({
    ...(index === 0 ? withStudy() : material),
    material_id: uuid(100 + index),
    display_name: `第 ${index + 1} 份｜${longName}`,
    created_at: new Date(Date.UTC(2026, 8, 20 - index)).toISOString(),
  }));
}

// 快速存取的列以「動作：標題」為名，例如「繼續學習：有界重試策略」。
const quick = (page: Page) => page.getByRole("region", { name: "快速存取" });
const quickButton = (page: Page, action: string) => quick(page).getByRole("button", { name: new RegExp(`^${action}：`) });
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
    const foot = quick(page);
    if (!action) {
      await expect(foot.getByRole("button")).toHaveCount(0);
      await expect(page.getByRole("button", { name: new RegExp(`^${longName.slice(0, 10)}.*建立未完成`) })).toBeVisible();
      return;
    }
    await quickButton(page, action).focus();
    await page.keyboard.press("Enter");
    const expected = state === "empty" ? "/upload" : state === "map" ? mapPath : studyPath;
    await expect.poll(() => new URL(page.url()).pathname).toBe(expected);
  });
}

test("dashboard island opens the head knowledge map", async ({ page }) => {
  await mockLibrary(page, () => [withStudy("active")]);
  await page.goto("/");
  const island = islands(page);
  await island.getByRole("button", { name: `${longName}，學習中` }).click();
  await expect.poll(() => new URL(page.url()).pathname).toBe(mapPath);
});

for (const [width, height] of [[2560, 1440], [1536, 1024], [1366, 768], [1024, 768], [390, 844], [320, 844]]) {
  test(`dashboard long filename fits without page overflow at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height });
    await mockLibrary(page, () => [withStudy()]);
    await page.route("**/v1/learning-activity", (route) => route.fulfill({ json: learningActivity({}) }));
    await page.goto("/");
    await expect(quickButton(page, "繼續學習")).toBeVisible();
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
  await quick(page).getByRole("button", { name: "繼續學習：Linear Algebra.pdf", exact: true }).click();
  await expect
    .poll(() => new URL(page.url()).pathname)
    .toBe(studyPath.replace(materialId, secondId));
  items = items.filter((item) => item.material_id !== secondId);
  await page.goto("/");
  await expect(quick(page).getByRole("button", { name: "查看學習成果：Calculus.pdf", exact: true })).toBeVisible();
  items = items.filter((item) => item.display_name !== "Calculus.pdf");
  await page.reload();
  await expect(islands(page)).toHaveCount(1);
  await expect(quickButton(page, "繼續學習")).toHaveCount(0);
  await expect(quickButton(page, "開啟知識地圖")).toBeVisible();
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
    "成就",
    "個人檔案",
  ]);
  await expect(page.getByRole("button", { name: "登出", exact: true })).toBeVisible();
  await quickButton(page, "繼續學習").click();
  await expect
    .poll(() => new URL(page.url()).pathname)
    .toBe(
      `/materials/${materialId}/runs/${headRunId}/knowledge-structures/${encodeURIComponent(headRevision)}/study-sessions/${headSession.study_session_id}`,
    );
});

test("home shows the level in the header; achievements and reviews have their own pages", async ({ page }) => {
  await mockLibrary(page, () => [withStudy()]);
  await page.route("**/v1/learning-activity", (route) => route.fulfill({ json: learningActivity({
    total: 245, level: [2, 145, 150], streak: 2,
    quests: [{ kind: "mark_cards", progress: 0, target: 5, done: false }],
  }) }));
  await page.goto("/");
  await expect(page.getByRole("region", { name: "今日任務" })).toBeVisible();
  // 等級只在全站頁首（連續天數左邊）；徽章、學習節奏、學習總覽、Podcast 與概念卡複習都不在首頁。
  for (const name of ["第 2 級", "成就總覽", "學習節奏", "學習總覽", "Podcast", "概念卡複習"])
    await expect(page.getByRole("region", { name, exact: true })).toHaveCount(0);
  await expect(page.locator(".dashboard-greeting button", { hasText: "第 2 級" })).toHaveCount(0);
  const pill = page.locator(".app-header").getByRole("button", { name: "第 2 級，本級 145 / 150 XP，連續學習 2 天，查看成就" });
  await expect(pill.locator(".header-level-shield")).toHaveText("2");
  await expect(pill.locator(".header-level-meter")).toHaveText("145/150");
  // 盾牌在連續天數左邊。
  const shield = (await pill.locator(".header-level").boundingBox())!;
  const streak = (await pill.locator(".header-streak").boundingBox())!;
  expect(shield.x + shield.width).toBeLessThanOrEqual(streak.x);
  await pill.click();
  await expect(page).toHaveURL(/\/achievements$/);
  await expect(page.getByRole("region", { name: "第 2 級" })).toBeVisible();
  await page.goto("/");
  await page.getByRole("button", { name: "去標記：標記 5 張概念卡" }).click();
  await expect(page).toHaveURL(/\/concept-cards$/);
});

test("quick access resumes the most recently listened podcast that is not finished", async ({ page }) => {
  await mockLibrary(page, () => [withStudy()]);
  const podcast = (seed: number, name: string) => ({
    podcast_id: uuid(seed), material_id: materialId, material_name: longName, knowledge_structure_revision: revision,
    name, delivery: "solo", video_enabled: false, concept_ids: [`concept:sha256:${"b".repeat(64)}`], status: "ready", error_code: null,
    version: 1, created_at: "2026-09-12T00:00:00Z", episode_count: 2, completed_episodes: 2, duration_seconds: 600, is_current_revision: true,
  });
  // 30 號最新但兩集都聽完；31 號是聽到一半中最新的；32 號較舊。
  const progress: Record<string, unknown> = {
    [uuid(30)]: { current: { episode_index: 1, position_seconds: 90, updated_at: "2026-10-23T09:00:00Z" },
      episodes: [{ episode_index: 0, position_seconds: 300, completed: true }, { episode_index: 1, position_seconds: 400, completed: true }] },
    [uuid(31)]: { current: { episode_index: 1, position_seconds: 125, updated_at: "2026-10-22T09:00:00Z" },
      episodes: [{ episode_index: 0, position_seconds: 300, completed: true }, { episode_index: 1, position_seconds: 125, completed: false }] },
    [uuid(32)]: { current: { episode_index: 0, position_seconds: 10, updated_at: "2026-10-21T09:00:00Z" }, episodes: [] },
  };
  await page.route("**/v1/podcasts", (route) => route.fulfill({ json: { schema: "podcast-list/v1",
    podcasts: [podcast(30, "聽完的"), podcast(31, "聽到一半"), podcast(32, "較舊的")] } }));
  await page.route("**/v1/podcasts/*/progress", (route) => {
    const id = new URL(route.request().url()).pathname.split("/").at(-2)!;
    return route.fulfill({ json: { schema: "podcast-progress/v1", podcast_id: id, credited_seconds: 0, ...progress[id] as object } });
  });
  await page.goto("/");
  const resume = page.getByRole("region", { name: "快速存取" }).getByRole("button", { name: /繼續收聽/ });
  await expect(resume).toContainText("聽到一半");
  await expect(resume).toContainText("第 2 集 · 停在 2:05");
  await resume.click();
  await expect.poll(() => new URL(page.url()).pathname).toBe(`/materials/${materialId}/podcasts/${uuid(31)}`);
});

test("home shows the material count without page totals or a date line", async ({ page }) => {
  await mockLibrary(page, () => [withStudy(), { ...material, material_id: uuid(7), latest_attempt: null }]);
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("歡迎回來！今天想探索哪座島？");
  await expect(page.locator(".archipelago-head span")).toHaveText("2 份教材");
  await expect(page.locator(".dashboard-greeting .eyebrow")).toHaveCount(0);
});

test("profile rhythm switches periods and pages back through earlier records", async ({ page }) => {
  const requests: string[] = [];
  await page.route("**/v1/learning-activity/rhythm?*", (route) => {
    const url = new URL(route.request().url());
    requests.push(url.search);
    const period = url.searchParams.get("period") as "week" | "month" | "quarter" | "year";
    const offset = Number(url.searchParams.get("offset"));
    return route.fulfill({ json: learningRhythm({ period, offset, streak: 3, firstDay: "2026-09-02", days: {
      "2026-09-02": { answers: 4, correct: 4, active: true },
      "2026-10-19": { answers: 2, correct: 1, active: true }, "2026-10-20": { listened_seconds: 59 },
      "2026-10-21": { answers: 5, correct: 4, card_reviews: 6, listened_seconds: 600, active: true },
    } }) });
  });
  await page.goto("/profile");
  const rhythm = page.getByRole("region", { name: "學習節奏" });
  await expect(rhythm.getByText("連續 3 天")).toBeVisible();
  await expect(rhythm.getByRole("button", { name: "週", exact: true })).toHaveAttribute("aria-pressed", "true");
  await expect(rhythm.locator(".rhythm-pager b")).toHaveText("2026/10/19 – 10/25");
  const bars = rhythm.locator(".rhythm-bars button");
  await expect(bars).toHaveCount(7);
  await expect(rhythm.getByRole("button", { name: "星期三 10/21（今天）：作答 5 題，答對 4 題，標記 6 張卡，收聽 10 分鐘" })).toHaveClass(/is-today/);
  // 收聽不滿 1 分鐘不顯示分鐘數，也不算當天活躍。
  await expect(rhythm.getByRole("button", { name: "星期二 10/20：沒有學習紀錄" })).not.toHaveClass(/is-active/);
  await expect(rhythm.getByRole("button", { name: "星期四 10/22：尚未到" })).toHaveClass(/is-future/);
  await expect(rhythm.locator(".rhythm-summary")).toHaveText("這段期間作答 7 題（答對率 71%） · 標記 6 張卡 · 收聽 10 分鐘 · 學習 2 天");
  // 一格一根長條，高度是合計學習量（5 題＋6 張＋10 分鐘 = 21 為滿格，10/19 的 2 題約 10%）。
  const fullDay = rhythm.getByRole("button", { name: /^星期三 10\/21/ });
  await expect(fullDay.locator(".rhythm-bar i")).toHaveAttribute("style", "height: 100%;");
  await expect(rhythm.getByRole("button", { name: /^星期一 10\/19/ }).locator(".rhythm-bar i")).toHaveAttribute("style", /height: 9\.5\d*%;/);
  // 按長條在摘要列看細節，再按一次回到區間摘要。
  await fullDay.click();
  await expect(fullDay).toHaveAttribute("aria-pressed", "true");
  await expect(rhythm.locator(".rhythm-summary")).toHaveText("星期三 10/21（今天）：作答 5 題，答對 4 題，標記 6 張卡，收聽 10 分鐘");
  await fullDay.click();
  await expect(rhythm.locator(".rhythm-summary")).toHaveText("這段期間作答 7 題（答對率 71%） · 標記 6 張卡 · 收聽 10 分鐘 · 學習 2 天");
  await expect(rhythm.locator(".rhythm-legend")).toHaveCount(0);
  await expect(rhythm.getByRole("button", { name: "下一個週" })).toBeDisabled();
  // 往前翻到最早紀錄所在的那一頁就停。
  await rhythm.getByRole("button", { name: "上一個週" }).click();
  await expect(rhythm.locator(".rhythm-pager b")).toHaveText("2026/10/12 – 10/18");
  await expect(rhythm.locator(".rhythm-summary")).toHaveText("這段期間沒有學習紀錄。");
  await rhythm.getByRole("button", { name: "回到本週" }).click();
  await expect(rhythm.locator(".rhythm-pager b")).toHaveText("2026/10/19 – 10/25");
  // 月：每天一條；往前一個月就是最早紀錄所在的月，不能再往前。
  await rhythm.getByRole("button", { name: "月", exact: true }).click();
  await expect(rhythm.locator(".rhythm-pager b")).toHaveText("2026 年 10 月");
  await expect(bars).toHaveCount(31);
  await rhythm.getByRole("button", { name: "上一個月" }).click();
  await expect(rhythm.locator(".rhythm-pager b")).toHaveText("2026 年 9 月");
  await expect(rhythm.getByRole("button", { name: "9/2（週三）：作答 4 題，答對 4 題" })).toHaveClass(/is-active/);
  await expect(rhythm.getByRole("button", { name: "上一個月" })).toBeDisabled();
  // 近 3 個月：13 週的每週彙總。
  await rhythm.getByRole("button", { name: "近 3 個月" }).click();
  await expect(bars).toHaveCount(13);
  await expect(rhythm.getByRole("button", { name: "10/19–10/25 這週：作答 7 題，答對 5 題，標記 6 張卡，收聽 10 分鐘，學習 2 天" })).toHaveClass(/is-today/);
  // 近一年：52 週依自然月分組，一個月一根（頭尾月份只含部分天數）。
  await rhythm.getByRole("button", { name: "近一年" }).click();
  await expect(bars).toHaveCount(13);
  await expect(rhythm.getByRole("button", { name: "2026 年 10 月：作答 7 題，答對 5 題，標記 6 張卡，收聽 10 分鐘，學習 2 天" })).toHaveClass(/is-today/);
  await expect(rhythm.getByRole("button", { name: "2026 年 9 月：作答 4 題，答對 4 題，學習 1 天" })).toHaveClass(/is-active/);
  expect(requests).toEqual(["?period=week&offset=0", "?period=week&offset=1", "?period=week&offset=0", "?period=month&offset=0",
    "?period=month&offset=1", "?period=quarter&offset=0", "?period=year&offset=0"]);
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
});

test("profile rhythm does not page from a stale range after a failed read", async ({ page }) => {
  let fail = false;
  await page.route("**/v1/learning-activity/rhythm?*", (route) => {
    const url = new URL(route.request().url());
    if (fail) return route.fulfill({ status: 503, json: { schema: "api-error/v1", request_id: "rhythm", reason_code: "STORAGE_UNAVAILABLE", retryable: true, message: "Retry" } });
    return route.fulfill({ json: learningRhythm({ period: "week", offset: Number(url.searchParams.get("offset")), firstDay: "2026-09-02", days: {} }) });
  });
  await page.goto("/profile");
  const rhythm = page.getByRole("region", { name: "學習節奏" });
  await expect(rhythm.locator(".rhythm-pager b")).toHaveText("2026/10/19 – 10/25");
  fail = true;
  await rhythm.getByRole("button", { name: "上一個週" }).click();
  await expect(rhythm.getByRole("alert")).toContainText("無法讀取學習節奏");
  // 失敗的是上一週，頁碼列不能還停在本週，也不能依本週的資料繼續翻。
  await expect(rhythm.locator(".rhythm-pager b")).toHaveText("—");
  await expect(rhythm.getByRole("button", { name: "上一個週" })).toBeDisabled();
  await expect(rhythm.getByRole("button", { name: "下一個週" })).toBeDisabled();
  await expect(rhythm.getByRole("button", { name: "回到本週" })).toBeDisabled();
  fail = false;
  await rhythm.getByRole("button", { name: "重新讀取" }).click();
  await expect(rhythm.locator(".rhythm-pager b")).toHaveText("2026/10/12 – 10/18");
  await expect(rhythm.getByRole("button", { name: "上一個週" })).toBeEnabled();
  await expect(rhythm.getByRole("button", { name: "下一個週" })).toBeEnabled();
});

test("profile keeps devices in their own card and fits a wide window without scrolling", async ({ page }) => {
  await page.setViewportSize({ width: 1920, height: 1080 });
  await mockLibrary(page, () => [withStudy()]);
  await page.route("**/v1/learning-activity", (route) => route.fulfill({ json: learningActivity({ total: 245, level: [2, 145, 150] }) }));
  await page.route("**/v1/learning-activity/rhythm?*", (route) => route.fulfill({ json: learningRhythm({}) }));
  const now = Date.now();
  await page.route("**/v1/account/sessions", (route) => route.fulfill({ json: { schema: "account-sessions/v2", sessions: [
    { session_id: "11111111-1111-4111-8111-111111111111", device_label: "Chrome · Windows", created_at: new Date(now - 864e5).toISOString(), last_used_at: new Date(now).toISOString(), current: true },
    { session_id: "22222222-2222-4222-8222-222222222222", device_label: "Safari · iPhone", created_at: new Date(now - 3 * 864e5).toISOString(), last_used_at: new Date(now - 2 * 36e5).toISOString(), current: false },
  ] } }));
  await page.goto("/profile");
  const devices = page.getByRole("region", { name: "登入裝置" });
  await expect(devices.getByRole("listitem")).toHaveText([/Chrome · Windows這個裝置/, /Safari · iPhone最後使用 2 小時前/]);
  const settings = page.getByRole("region", { name: "帳號設定" });
  await expect(settings.getByRole("heading", { level: 3 })).toHaveText(["個人名稱", "修改密碼"]);
  await expect(settings.getByRole("region", { name: "登入裝置" })).toHaveCount(0);
  // 名稱下方不再有等級／XP 摘要。
  await expect(page.locator(".profile-head")).not.toContainText("XP");
  // 寬螢幕：左欄由上而下為總覽、節奏、登入裝置，帳號設定在右欄；整頁不捲動。
  const box = async (name: string) => (await page.getByRole("region", { name, exact: true }).boundingBox())!;
  const [overview, rhythm, device, account] = [await box("學習總覽"), await box("學習節奏"), await box("登入裝置"), await box("帳號設定")];
  expect(overview.y).toBeLessThan(rhythm.y);
  expect(rhythm.y).toBeLessThan(device.y);
  expect(account.x).toBeGreaterThan(device.x + device.width);
  expect(await page.evaluate(() => document.documentElement.scrollHeight - innerHeight)).toBeLessThanOrEqual(0);
  // 窄螢幕：帳號設定排在節奏之後、登入裝置之前，跳轉按鈕可直接到帳號設定。
  await page.setViewportSize({ width: 390, height: 844 });
  const [narrowRhythm, narrowAccount, narrowDevice] = [await box("學習節奏"), await box("帳號設定"), await box("登入裝置")];
  expect(narrowRhythm.y).toBeLessThan(narrowAccount.y);
  expect(narrowAccount.y).toBeLessThan(narrowDevice.y);
  await page.getByRole("button", { name: "帳號設定" }).click();
  await expect(page.locator("#account-settings-title")).toBeInViewport();
});

test("achievements level card, XP rules and profile overview show real numbers without inventing any", async ({ page }) => {
  await mockLibrary(page, () => [withStudy(), { ...material, material_id: uuid(7) }]);
  await page.route("**/v1/learning-activity", (route) => route.fulfill({ json: learningActivity({
    streak: 4, total: 245, todayXp: 32, level: [2, 145, 200],
    sources: { answers: 145, concepts: 30, cards: 20, podcast: 15, goals: 15, difficulties: 20, diagnostics: 0 },
  }) }));
  await page.goto("/");
  const pill = page.getByRole("button", { name: "第 2 級，本級 145 / 200 XP，連續學習 4 天，查看成就" });
  await pill.click();
  await expect(page).toHaveURL(/\/achievements$/);
  const level = page.getByRole("region", { name: "第 2 級" });
  await expect(level.getByText("累計 245 XP · 今天 +32")).toBeVisible();
  await expect(level.getByRole("progressbar", { name: "第 2 級經驗值" })).toHaveAttribute("aria-valuetext", "145 / 200 XP");
  await expect(level.getByText("再 55 XP 升到第 3 級")).toBeVisible();
  // 經驗值說明列出每種來源的規則與已獲得的 XP。
  const rules = page.getByRole("region", { name: "經驗值怎麼算" });
  const sources = rules.locator(".xp-sources > li");
  await expect(sources).toHaveCount(7);
  await expect(sources.filter({ hasText: "作答" })).toContainText(/\+10答對\+3答錯145已獲得/);
  await expect(sources.filter({ hasText: "收聽 Podcast" })).toContainText("每天最多 +60");
  await expect(sources.filter({ hasText: "目標與關卡" })).toContainText(/\+15每關\+50準時達標15已獲得/);
  await expect(sources.filter({ hasText: "起始程度診斷" })).toContainText(/\+20每份教材0已獲得/);
  // 來源比例長條依實際 XP 分段，沒有 XP 的來源不畫。
  await expect(rules.getByRole("img")).toHaveAttribute("aria-label", "作答 145 XP、完成概念 30 XP、標記概念卡 20 XP、收聽 Podcast 15 XP、目標與關卡 15 XP、克服難點 20 XP、起始程度診斷 0 XP");
  await expect(rules.locator(".xp-mix-bar > i")).toHaveCount(6);
  await page.getByRole("button", { name: /^個人檔案：/ }).click();
  await expect(page).toHaveURL(/\/profile$/);
  await expect(page.getByRole("region", { name: "第 2 級" })).toHaveCount(0);
  const stats = page.getByRole("region", { name: "學習總覽" }).locator(".profile-stat-list");
  await expect(stats).toHaveAttribute("aria-busy", "false");
  await expect(stats.locator("small")).toHaveText(["份教材", "學習中的教材", "已完成概念", "累計作答", "累計答對率"]);
  // 概念進度摘要讀不到時只算得出 0 個已完成概念；沒有作答時答對率顯示「—」。
  await expect(stats.locator("strong")).toHaveText(["2", "1", "0", "0", "—"]);
  await expect(stats.locator("button, a, [role=button], [tabindex]")).toHaveCount(0);
  // 手機頁首：盾牌與細進度條仍在，數字收起。
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(pill.locator(".header-level-shield")).toBeVisible();
  await expect(pill.locator(".header-level-meter small")).toBeHidden();
  const header = (await pill.boundingBox())!;
  expect(header.x + header.width).toBeLessThanOrEqual(390);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
});

test("profile overview shows dashes when the library cannot be read", async ({ page }) => {
  await page.route("**/v1/materials", (route) => route.fulfill({ status: 503, json: unavailable }));
  await page.route("**/v1/learning-activity", (route) => route.fulfill({ json: learningActivity({ answers: [10, 7] }) }));
  await page.goto("/profile");
  const stats = page.getByRole("region", { name: "學習總覽" }).locator(".profile-stat-list strong");
  // 作答數與答對率來自學習活動，不受教材清單失敗影響。
  await expect(stats).toHaveText(["—", "—", "—", "10", "70%"]);
});

test("today quests show done state and open the matching place", async ({ page }) => {
  await mockLibrary(page, () => [withStudy()]);
  const podcastId = uuid(9);
  await page.route("**/v1/learning-activity", (route) => route.fulfill({ json: learningActivity({
    quests: [
      { kind: "mark_cards", progress: 2, target: 5, done: false },
      { kind: "continue_study", progress: 0, target: 1, done: false, material_id: materialId, study_session_id: sessionId },
      { kind: "finish_podcast_episode", progress: 1, target: 1, done: true, material_id: materialId, podcast_id: podcastId, episode_index: 0 },
    ],
  }) }));
  await page.goto("/");
  const quests = page.getByRole("region", { name: "今日任務" });
  await expect(quests.getByText("1 / 3")).toBeVisible();
  await expect(quests.getByRole("listitem")).toHaveText([/標記 5 張概念卡已完成 2 \/ 5/, /完成 1 次題組作答今天還沒完成/, /聽完 1 集還沒聽完的 Podcast今天已完成/]);
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
  await expect(page.getByRole("dialog", { name: /升級了！/ })).toHaveCount(0);
  expect(await page.evaluate((key) => localStorage.getItem(key), `studydy.level-seen:${learnerId}`)).toBe("3");
  await page.evaluate((key) => localStorage.setItem(key, "2"), `studydy.level-seen:${learnerId}`);
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.reload();
  const celebration = page.getByRole("dialog", { name: /升級了！/ });
  await expect(celebration).toContainText("第 3 級");
  await expect(celebration).toHaveAttribute("aria-modal", "true");
  await expect(celebration.getByRole("button", { name: "太好了" })).toBeFocused();
  await expect(page.locator(".level-up-card")).toHaveCSS("animation-name", "none");
  await celebration.getByRole("button", { name: "太好了" }).click();
  await expect(celebration).toHaveCount(0);
  await page.reload();
  await expect(page.getByRole("button", { name: /第 3 級/ }).first()).toBeVisible();
  await expect(page.getByRole("dialog", { name: /升級了！/ })).toHaveCount(0);
  // 等級下降（例如刪除教材）不播放，也不覆蓋較高的已看等級。
  level = 2;
  await page.reload();
  await expect(page.getByRole("button", { name: /第 2 級/ }).first()).toBeVisible();
  await expect(page.getByRole("dialog", { name: /升級了！/ })).toHaveCount(0);
});

test("achievements list earned badges above unearned ones, one cell per badge", async ({ page }) => {
  await page.route("**/v1/learning-activity", (route) => route.fulfill({ json: learningActivity({
    level: [6, 0, 600],
    badges: {
      first_answer: { earned_on: "2026-10-16" }, answers_100: { earned_on: "2026-10-18" }, answers_300: { progress: 124 }, answers_1000: { progress: 124 }, answers_5000: { progress: 124 },
      streak_3: { earned: true }, concepts_25: { progress: 3 },
      // 前端不認得的代號不顯示，也不算進數量。
      future_badge: { earned_on: "2026-10-20" },
    },
  }) }));
  await page.goto("/achievements");
  const wall = page.getByRole("region", { name: "成就總覽" });
  // 已取得：作答 2 層、連續 1 層與第 6 級的等級 I；分母是 106 個成就扣掉成就鏈尚未揭曉的 5 步。
  await expect(wall.locator(":scope > header .badge-count")).toHaveText("4 / 101");
  await expect(wall).not.toContainText("future_badge");
  const earned = page.getByRole("region", { name: "已取得" }).locator(".badge-cell");
  await expect(earned).toHaveCount(4);
  await expect(earned.locator("b")).toHaveText([/初次作答/, /百題挑戰/, /三日不間斷/, /進階探險家/]);
  // 系列名稱與層級是格內小字；已取得的格子顯示條件與日期。
  await expect(earned.filter({ hasText: "百題挑戰" })).toContainText(/作答 · 銀・II.*累計作答 100 題.*2026\/10\/18 取得/);
  await expect(earned.filter({ hasText: "三日不間斷" })).toContainText("已取得");
  const locked = page.getByRole("region", { name: "未取得" });
  await expect(locked.locator(":scope > header .badge-count")).toHaveText("97");
  const next = locked.locator(".badge-cell").filter({ hasText: "題海航行者" });
  await expect(next).toContainText(/作答 · 金・III.*累計作答 300 題/);
  await expect(next.getByRole("progressbar")).toHaveAttribute("aria-valuetext", "124 / 300 題");
  await expect(locked.locator(".badge-cell").filter({ hasText: "資深航海士" }).getByRole("progressbar")).toHaveAttribute("aria-valuetext", "目前第 6 級");
  // 已取得都排在未取得之前。
  const earnedBox = (await page.getByRole("region", { name: "已取得" }).boundingBox())!;
  expect(earnedBox.y + earnedBox.height).toBeLessThanOrEqual((await locked.boundingBox())!.y);
  await page.setViewportSize({ width: 390, height: 844 });
  const box = await wall.boundingBox();
  expect(box!.x).toBeGreaterThanOrEqual(0);
  expect(box!.x + box!.width).toBeLessThanOrEqual(390);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
});

test("hidden achievements show only hints and achievement chains reveal one step at a time", async ({ page }) => {
  let badges: Record<string, { progress?: number; earned_on?: string }> = { night_owl: { earned_on: "2026-10-19" }, combo_10: { progress: 7 }, answers_300: { progress: 1 } };
  await page.route("**/v1/learning-activity", (route) => route.fulfill({ json: learningActivity({ badges }) }));
  await page.goto("/achievements");
  const earned = page.getByRole("region", { name: "已取得" });
  const locked = page.getByRole("region", { name: "未取得" });
  // 已取得的隱藏成就：揭曉名稱、條件與日期，與一般成就放在同一區。
  await expect(earned.locator(".badge-cell").filter({ hasText: "夜貓子" })).toContainText(/特殊成就.*凌晨 0:00～4:59 作答累計 10 題.*2026\/10\/19 取得/);
  // 未取得：只有問號、稀有度與提示，不洩漏名稱、條件或進度。
  const combo = locked.locator(".badge-cell").filter({ hasText: "一口氣，一題都不能錯。" });
  await expect(combo).toContainText(/成就鏈 · 第 1 步.*？？？/);
  await expect(combo).not.toContainText("連對王");
  await expect(combo).not.toContainText("連續答對");
  await expect(combo.getByRole("progressbar")).toHaveCount(0);
  await expect(locked.locator(".badge-cell").filter({ hasText: "很厚、很厚的一本。" }).locator(".badge-rarity")).toHaveText("傳說");
  // 成就鏈後面的步驟完全不顯示，也不提示還有幾個（當作彩蛋）。
  await expect(locked).not.toContainText("十題只是開始。");
  // 依稀有度的取得數：夜貓子是普通。
  await expect(page.getByRole("list", { name: "依稀有度的取得數" }).getByRole("listitem").first()).toContainText(/^1/);
  // 取得第一步後，第二步以「？？？」與提示出現，第三步仍隱藏。
  badges = { ...badges, combo_10: { earned_on: "2026-10-20" }, combo_25: { progress: 12 } };
  await page.reload();
  await expect(earned.locator(".badge-cell").filter({ hasText: "連對王" })).toContainText("成就鏈 · 第 1 步");
  await expect(locked.locator(".badge-cell").filter({ hasText: "十題只是開始。" })).toContainText(/成就鏈 · 第 2 步.*？？？/);
  await expect(locked).not.toContainText("連對達人");
  await expect(locked).not.toContainText("再遠一點");
});

test("v7 goal, difficulty, media, combo and easter-egg achievements reuse the series and hidden cells", async ({ page }) => {
  await page.route("**/v1/learning-activity", (route) => route.fulfill({ json: learningActivity({ badges: {
    first_stage: { earned_on: "2026-10-20" }, stages_10: { progress: 1 }, difficulties_5: { progress: 2 },
    upload_youtube: { earned_on: "2026-10-19" }, export_kinds_4: { progress: 3 }, stage_streak_7: { progress: 4 },
    nemesis: { earned_on: "2026-10-21" },
  } }) }));
  await page.goto("/achievements");
  const earned = page.getByRole("region", { name: "已取得" }).locator(".badge-cell");
  const locked = page.getByRole("region", { name: "未取得" }).locator(".badge-cell");
  // 多層系列沿用層級小字；單一成就只顯示系列名稱。
  await expect(earned.filter({ hasText: "第一關" })).toContainText(/目標與關卡 · 銅・I.*第一次完成分天關卡.*2026\/10\/20 取得/);
  await expect(earned.filter({ hasText: "YouTube 教材" })).toContainText(/多元教材.*第一次用 YouTube 連結建立教材/);
  await expect(locked.filter({ hasText: "十關闖將" }).getByRole("progressbar")).toHaveAttribute("aria-valuetext", "1 / 10 關");
  await expect(locked.filter({ hasText: "難點剋星" }).getByRole("progressbar")).toHaveAttribute("aria-valuetext", "2 / 5 個");
  await expect(locked.filter({ hasText: "整理收藏家" }).getByRole("progressbar")).toHaveAttribute("aria-valuetext", "3 / 4 種");
  await expect(locked.filter({ hasText: "一週闖關" })).toContainText(/組合成就.*連續 7 天都完成分天關卡/);
  // 彩蛋：取得後才揭曉；未取得只有提示。
  await expect(earned.filter({ hasText: "宿敵" })).toContainText(/特殊成就.*錯誤率曾達 70%/);
  const onTime = locked.filter({ hasText: "說到做到。" });
  await expect(onTime).toContainText("？？？");
  await expect(onTime).not.toContainText("準時達陣");
  await expect(onTime.getByRole("progressbar")).toHaveCount(0);
});

test("new badge notice shows once per badge after the first visit in this browser", async ({ page }) => {
  let earned: Record<string, { earned_on: string }> = { first_answer: { earned_on: "2026-10-16" } };
  await page.route("**/v1/learning-activity", (route) => route.fulfill({ json: learningActivity({ badges: earned }) }));
  const key = `studydy.badges-seen:${learnerId}`;
  // 第一次在此瀏覽器讀到時只記下既有徽章，不提示。
  await page.goto("/");
  await expect(page.locator(".header-progress")).toBeVisible();
  await expect(page.getByRole("dialog", { name: "獲得新徽章" })).toHaveCount(0);
  expect(JSON.parse(await page.evaluate((name) => localStorage.getItem(name) ?? "null", key))).toEqual(["first_answer"]);
  earned = { ...earned, first_concept: { earned_on: "2026-10-22" } };
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.reload();
  const notice = page.getByRole("dialog", { name: "獲得新徽章" });
  await expect(notice).toContainText("點亮概念");
  await expect(notice).not.toContainText("初次作答");
  await expect(notice.locator(".badge-medal")).toHaveCSS("animation-name", "none");
  await notice.getByRole("button", { name: "太好了" }).click();
  await expect(notice).toHaveCount(0);
  await page.reload();
  await expect(page.locator(".header-progress")).toBeVisible();
  await expect(page.getByRole("dialog", { name: "獲得新徽章" })).toHaveCount(0);
});

test("level-up and badge dialogs open one after another, close by Escape or backdrop and return focus", async ({ page }) => {
  let level = 2;
  let earned: Record<string, { earned_on: string }> = { first_answer: { earned_on: "2026-10-16" } };
  await page.route("**/v1/learning-activity", (route) => route.fulfill({ json: learningActivity({ total: level * 100, level: [level, 0, 300], badges: earned }) }));
  await page.clock.install();
  await page.goto("/");
  const progress = page.locator(".header-progress");
  await expect(progress).toBeVisible();
  // 第一次讀取只記下等級與徽章；之後從頁首按鈕上重新讀取到升級與新徽章。
  await progress.focus();
  level = 3;
  earned = { ...earned, first_concept: { earned_on: "2026-10-22" } };
  await page.clock.runFor(31_000);
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  const levelDialog = page.getByRole("dialog", { name: "升級了！第 3 級" });
  const badgeDialog = page.getByRole("dialog", { name: "獲得新徽章" });
  await expect(levelDialog).toHaveAttribute("aria-modal", "true");
  await expect(levelDialog.getByRole("button", { name: "太好了" })).toBeFocused();
  // 同時升級與取得徽章時，先只顯示升級。
  await expect(badgeDialog).toHaveCount(0);
  // 強制回應的對話框不自動關閉（WCAG 2.2.1），等再久也要由使用者關閉。
  await page.clock.runFor(60_000);
  await expect(levelDialog).toBeVisible();
  await expect(badgeDialog).toHaveCount(0);
  await page.keyboard.press("Escape");
  await expect(levelDialog).toHaveCount(0);
  await expect(badgeDialog).toContainText("點亮概念");
  await expect(badgeDialog.getByRole("button", { name: "太好了" })).toBeFocused();
  // 點卡片外的背景關閉，焦點回到開啟前的頁首按鈕。
  await page.mouse.click(5, 5);
  await expect(badgeDialog).toHaveCount(0);
  await expect(progress).toBeFocused();
});

test("dashboard hides learning numbers when learning activity is unavailable", async ({ page }) => {
  await mockLibrary(page, () => [withStudy()]);
  await page.goto("/");
  await expect(quickButton(page, "繼續學習")).toBeVisible();
  await expect(page.getByRole("region", { name: "今日任務" })).toHaveCount(0);
  await expect(page.locator(".header-progress")).toHaveCount(0);
  await page.goto("/achievements");
  await expect(page.getByText("無法讀取學習成就")).toBeVisible();
  await expect(page.locator(".dashboard-level, .badge-collection")).toHaveCount(0);
  await page.goto("/profile");
  await expect(page.getByText("無法讀取學習統計")).toBeVisible();
  // 帳號設定不受影響。
  await expect(page.getByRole("region", { name: "帳號設定" }).getByRole("button", { name: "儲存名稱" })).toBeEnabled();
});

for (const count of [3, 6, 9, 24])
  for (const [width, height] of [[1440, 900], [1024, 768], [390, 844]]) {
    test(`dashboard map centers ${count} islands and labels never overlap at ${width}px`, async ({ page }) => {
      await page.setViewportSize({ width, height });
      await mockLibrary(page, () => manyMaterials(count));
      await page.goto("/");
      await expect(islands(page)).toHaveCount(count);
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
      // 小地圖 viewBox 是完整群島 bounds；初始視角以此中心置中。
      const viewport = (await page.locator(".island-map-viewport").boundingBox())!;
      const [x, y, w, h] = (await page.locator(".island-map-minimap").getAttribute("viewBox"))!.split(" ").map(Number);
      const view = await mapView(page);
      expect(view.x + (x + w / 2) * view.k).toBeCloseTo(viewport.width / 2, 0);
      expect(view.y + (y + h / 2) * view.k).toBeCloseTo(viewport.height / 2, 0);
      if (count === 24) {
        expect(Math.min((viewport.width - 48) / w, (viewport.height - 48) / h)).toBeLessThan(0.5);
        expect(view.k).toBe(0.5);
      }
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
  const art = (await islands(page).filter({ hasText: "第 1 份｜" }).locator(".map-island-art").boundingBox())!;
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
  const foot = (await quick(page).boundingBox())!;
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
  await expect(islands(page).filter({ hasText: longName }).locator(".progress-ring")).toContainText("5");
  expect(requested.some((path) => path.includes(`/progress-summary?study_session_id=${sessionId}`))).toBe(true);
  expect(requested.filter((path) => /\/outline|\/map-progress/.test(path))).toEqual([]);
});

for (const [width, height, fits] of [[1920, 950, true], [2560, 1300, true], [1440, 760, true], [1366, 680, false], [1024, 768, false]] as const)
  test(`home ${fits ? "fits the window without scrolling" : "keeps a readable map and scrolls"} at ${width}×${height}`, async ({ page }) => {
    await page.setViewportSize({ width, height });
    await mockLibrary(page, () => manyMaterials(9));
    await page.route("**/v1/learning-activity", (route) => route.fulfill({ json: learningActivity({
      quests: [{ kind: "mark_cards", progress: 0, target: 5, done: false }, { kind: "continue_study", progress: 0, target: 1, done: false }],
    }) }));
    await page.goto("/");
    await expect(islands(page)).toHaveCount(9);
    await expect(page.getByRole("region", { name: "今日任務" })).toBeVisible();
    const overflow = await page.evaluate(() => document.documentElement.scrollHeight - innerHeight);
    const map = (await page.locator(".island-map-viewport").boundingBox())!;
    if (fits) {
      expect(overflow).toBeLessThanOrEqual(0);
      await expect(quickButton(page, "繼續學習")).toBeInViewport({ ratio: 1 });
    } else {
      // 較小的螢幕：地圖維持最小高度，頁面照常捲動。
      expect(overflow).toBeGreaterThan(0);
      expect(map.height).toBeGreaterThanOrEqual(380);
    }
  });

test("achievements scroll inside the overview on desktop while the page and level column stay put", async ({ page }) => {
  await page.setViewportSize({ width: 1920, height: 900 });
  await page.route("**/v1/learning-activity", (route) => route.fulfill({ json: learningActivity({}) }));
  await page.goto("/achievements");
  const overview = page.getByRole("region", { name: "成就總覽" });
  await expect(overview).toBeVisible();
  // 頁面剛好填滿視窗；成就總覽內容比區域高，由它自己捲動。
  expect(await page.evaluate(() => document.documentElement.scrollHeight - innerHeight)).toBeLessThanOrEqual(0);
  expect(await overview.evaluate((element) => element.scrollHeight > element.clientHeight)).toBe(true);
  const header = (await page.locator(".app-header").boundingBox())!;
  const level = (await page.locator(".level-xp").boundingBox())!;
  const box = (await overview.boundingBox())!;
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.wheel(0, 600);
  await expect.poll(() => overview.evaluate((element) => element.scrollTop)).toBeGreaterThan(0);
  // 只有成就區移動：頁面、頁首與左欄位置不變。
  expect(await page.evaluate(() => window.scrollY)).toBe(0);
  expect((await page.locator(".app-header").boundingBox())!.y).toBe(header.y);
  expect((await page.locator(".level-xp").boundingBox())!.y).toBe(level.y);
  // 手機兩欄上下排列，維持整頁捲動。
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollHeight > innerHeight)).toBe(true);
  expect(await overview.evaluate((element) => getComputedStyle(element).overflowY)).not.toBe("auto");
});
