import { expect, test, type Page } from "@playwright/test";
import type { MaterialLibraryItem, StudySessionLink } from "../../src/api/contracts";

const uuid = (seed: number) => `00000000-0000-4000-8000-${String(seed).padStart(12, "0")}`;
const materialId = uuid(1);
const runId = uuid(2);
const sessionId = uuid(3);
const learnerId = uuid(4);
const revision = `knowledge-structure:sha256:${"a".repeat(64)}`;
const studyPath = `/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(revision)}/study-sessions/${sessionId}`;
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
    if (path === "/v1/source-capabilities") {
      expect(route.request().method()).toBe("GET");
      return route.fulfill({
        json: { schema: "source-capabilities/v1", quality_notice: "PDF", formats: [] },
      });
    }
    // 此檔驗證首頁導向，目的頁的資料流程由 study／upload spec 負責。
    return route.fulfill({
      status: 404,
      json: { ...unavailable, reason_code: "RESOURCE_NOT_FOUND", retryable: false },
    });
  });
  await mockLibrary(page, () => []);
});

// 狀態與統計各驗一次；版面另以長檔名涵蓋每個 CSS 分支。
for (const [state, items, counts] of [
  ["empty", [], ["0", "0", "0", "0"]],
  ["map", [material], ["1", "1", "0", "0"]],
  ["unpublished", [{ ...material, available_structures: [] }], ["1", "0", "0", "0"]],
  ["active", [withStudy("active")], ["1", "1", "1", "0"]],
  ["no_safe", [withStudy("no_safe")], ["1", "1", "1", "0"]],
  ["completed", [withStudy("completed")], ["1", "1", "1", "1"]],
] as const) {
  test(`dashboard ${state} shows material counts and the matching study action`, async ({
    page,
  }) => {
    await mockLibrary(page, () => items);
    await page.goto("/");
    await expect(page.locator(".dashboard-stat strong")).toHaveText([...counts]);
    await expect(page.locator(".dashboard-stats")).toHaveAttribute("aria-busy", "false");
    const hasStudy = items.some((item) => item.study_sessions.length > 0);
    await expect(page.locator(".dashboard-resume")).toHaveCount(hasStudy ? 1 : 0);
    if (hasStudy) {
      await expect(page.locator(".dashboard-resume p")).toHaveText(longName);
      const action = page.getByRole("button", {
        name: state === "completed" ? "查看學習成果" : "繼續學習",
        exact: true,
      });
      await action.focus();
      await page.keyboard.press("Enter");
      await expect.poll(() => new URL(page.url()).pathname).toBe(studyPath);
    } else {
      await expect(page.getByRole("button", { name: "上傳教材", exact: true })).toBeEnabled();
      if (state === "empty") {
        await page.getByRole("button", { name: "上傳教材", exact: true }).click();
        await expect(page).toHaveURL(/\/upload$/);
        await expect(page.getByRole("heading", { name: "上傳教材", exact: true })).toBeVisible();
      }
    }
  });
}

for (const [width, height, statColumns, featureColumns] of [
  [1536, 1024, 4, 1],
  [1366, 768, 4, 2],
  [1024, 768, 2, 2],
  [390, 844, 2, 1],
  [320, 844, 1, 1],
]) {
  test(`dashboard long filename and responsive columns fit ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height });
    await mockLibrary(page, () => [withStudy()]);
    await page.goto("/");
    await expect(page.locator(".dashboard-resume p")).toHaveText(longName);
    await expect(page.getByRole("button", { name: "繼續學習", exact: true })).toBeVisible();
    const help = page.getByRole("complementary", { name: "Studydy 學習協助" });
    await expect(help).toBeVisible();
    const main = (await page.locator(".dashboard-primary").boundingBox())!;
    const rail = (await help.boundingBox())!;
    if (width >= 1440) {
      expect(rail.x).toBeGreaterThanOrEqual(main.x + main.width + 20);
      expect(rail.width).toBeGreaterThanOrEqual(280);
      expect(rail.width).toBeLessThanOrEqual(320);
      expect(Math.abs(main.y - rail.y)).toBeLessThan(2);
    } else expect(rail.y).toBeGreaterThanOrEqual(main.y + main.height);
    for (const [selector, columns] of [
      [".dashboard-stats", statColumns],
      [".dashboard-features", featureColumns],
    ] as const)
      expect(
        await page
          .locator(selector)
          .evaluate((el) => getComputedStyle(el).gridTemplateColumns.split(" ").length),
      ).toBe(columns);
    const illustration = page.locator(".hero-illustration");
    if (width > 600) {
      await expect(illustration).toBeVisible();
      await expect
        .poll(() =>
          illustration
            .locator("img")
            .evaluate((img: HTMLImageElement) => img.complete && img.naturalWidth > 0),
        )
        .toBe(true);
    } else await expect(illustration).toBeHidden();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
      true,
    );
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
  await expect(stats.locator("strong")).toHaveText(["—", "—", "—", "—"]);
  await expect(stats.locator("small")).toHaveText(Array(4).fill("正在讀取…"));
  await expect(page.locator(".dashboard-hero")).toBeVisible();
  await expect(page.getByRole("button", { name: "上傳教材", exact: true })).toBeEnabled();
  release();
  await expect(stats.locator("strong")).toHaveText(["1", "1", "0", "0"]);
  await expect(stats).toHaveAttribute("aria-busy", "false");
});

test("dashboard read failure keeps hero and overview and recovers without reload", async ({
  page,
}) => {
  await page.route(
    "**/v1/materials",
    (route) => route.fulfill({ status: 503, json: unavailable }),
    { times: 1 },
  );
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "歡迎回來！", exact: true })).toBeVisible();
  await expect(page.getByRole("alert")).toContainText("資料服務暫時無法使用");
  const stats = page.locator(".dashboard-stats");
  await expect(stats.locator("strong")).toHaveText(["—", "—", "—", "—"]);
  await expect(stats.locator("small")).toHaveText(Array(4).fill("暫時無法讀取"));
  await expect(stats).toHaveAttribute("aria-busy", "false");
  await expect(page.locator(".dashboard-hero")).toBeVisible();
  await expect(page.getByRole("button", { name: "上傳教材", exact: true })).toBeEnabled();
  await page.getByRole("button", { name: "重新讀取", exact: true }).click();
  await expect(stats.locator("strong")).toHaveText(["0", "0", "0", "0"]);
  await expect(page.getByRole("alert")).toHaveCount(0);
  await page
    .getByRole("navigation", { name: "主要導覽" })
    .getByRole("button", { name: "教材庫", exact: true })
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
  await expect(page.locator(".dashboard-resume p")).toHaveText("Linear Algebra.pdf");
  await page.getByRole("button", { name: "繼續學習", exact: true }).click();
  await expect
    .poll(() => new URL(page.url()).pathname)
    .toBe(studyPath.replace(materialId, secondId));
  items = items.filter((item) => item.material_id !== secondId);
  await page.goto("/");
  await expect(page.locator(".dashboard-resume p")).toHaveText("Calculus.pdf");
  items = items.filter((item) => item.display_name !== "Calculus.pdf");
  await page.reload();
  await expect(page.locator(".dashboard-resume")).toHaveCount(0);
  await expect(page.locator(".dashboard-stat strong")).toHaveText(["1", "1", "0", "0"]);
  await expect(page.getByRole("button", { name: "繼續學習", exact: true })).toHaveCount(0);
});

test("dashboard stats count each material once using its head and remain outside keyboard navigation", async ({
  page,
}) => {
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
        {
          ...material.available_structures[0],
          run_id: headRunId,
          knowledge_structure_revision: headRevision,
        },
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
  await expect(
    page.getByRole("heading", { name: "歡迎回來！", level: 1, exact: true }),
  ).toBeVisible();
  await expect(page.getByRole("navigation", { name: "主要導覽" }).getByRole("button")).toHaveText([
    "首頁",
    "我的教材",
  ]);
  await expect(page.locator(".nav-unavailable")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "登出", exact: true })).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "建立你的知識地圖", level: 2, exact: true }),
  ).toBeVisible();
  await expect(page.locator(".dashboard-hero").getByRole("button")).toHaveText(["上傳教材"]);
  await expect(page.getByRole("button", { name: "前往我的教材", exact: true })).toHaveCount(0);
  await expect(page.locator(".dashboard-help article")).toHaveCount(4);
  await expect(page.locator(".dashboard-help").getByRole("button")).toHaveCount(0);
  const stats = page.locator(".dashboard-stats");
  await expect(stats.locator("article.dashboard-stat")).toHaveCount(4);
  await expect(stats.locator(".dashboard-stat > svg")).toHaveCount(0);
  await expect(stats.locator(".stat-icon svg")).toHaveCount(4);
  await expect(stats.locator(".stat-copy > span")).toHaveText([
    "教材",
    "知識地圖",
    "已開始學習",
    "已完成",
  ]);
  await expect(stats.locator("strong")).toHaveText(["1", "1", "1", "0"]);
  await expect(stats.locator("small")).toHaveText([
    "已保存的教材",
    "可開啟地圖的教材",
    "有學習紀錄的教材",
    "已完成學習的教材",
  ]);
  for (const stat of await stats.locator(".dashboard-stat").all()) {
    await expect(stat).toHaveJSProperty("tabIndex", -1);
    await expect(stat.locator("button, a, [role=button], [tabindex]")).toHaveCount(0);
    const border = await stat.evaluate((el) => getComputedStyle(el).borderColor);
    await stat.hover();
    expect(await stat.evaluate((el) => getComputedStyle(el).borderColor)).toBe(border);
    expect(await stat.evaluate((el) => getComputedStyle(el).cursor)).not.toBe("pointer");
    await stat.click();
    expect(new URL(page.url()).pathname).toBe("/");
  }
  await page.getByRole("button", { name: "上傳教材", exact: true }).focus();
  await page.keyboard.press("Tab");
  await expect(page.getByRole("button", { name: "繼續學習", exact: true })).toBeFocused();
  await page.keyboard.press("Shift+Tab");
  await expect(page.getByRole("button", { name: "上傳教材", exact: true })).toBeFocused();
  await page.keyboard.press("Tab");
  await page.keyboard.press("Enter");
  await expect
    .poll(() => new URL(page.url()).pathname)
    .toBe(
      `/materials/${materialId}/runs/${headRunId}/knowledge-structures/${encodeURIComponent(headRevision)}/study-sessions/${headSession.study_session_id}`,
    );
});
