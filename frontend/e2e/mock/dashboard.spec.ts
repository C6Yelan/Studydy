import { expect, test, type Page } from "@playwright/test";
import type { MaterialLibraryItem, StudySessionLink } from "../../src/api/contracts";

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
    const main = (await page.locator(".dashboard-primary").boundingBox())!;
    const side = (await page.locator(".dashboard-side").boundingBox())!;
    if (width > 1200) {
      expect(side.x).toBeGreaterThanOrEqual(main.x + main.width + 20);
      expect(Math.abs(main.y - side.y)).toBeLessThan(2);
    } else expect(side.y).toBeGreaterThanOrEqual(main.y + main.height);
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
