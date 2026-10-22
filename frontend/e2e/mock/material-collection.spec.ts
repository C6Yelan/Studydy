import { expect, test, type Page } from "@playwright/test";
import type {
  MaterialLibraryItem,
  MaterialAttemptView,
  StudySessionLink,
} from "../../src/api/contracts";

const materialId = "11111111-1111-4111-8111-111111111111";
const publishedRun = "22222222-2222-4222-8222-222222222222";
const latestRun = "33333333-3333-4333-8333-333333333333";
const studyId = "44444444-4444-4444-8444-444444444444";
const revision = `knowledge-structure:sha256:${"a".repeat(64)}`;
const base: MaterialLibraryItem = {
  schema: "material-library-item/v1",
  material_id: materialId,
  source_artifact_id: materialId,
  display_name: "資料結構講義.pdf",
  size_bytes: 1200,
  created_at: "2026-09-12T00:00:00Z",
  latest_attempt: null,
  available_structures: [],
  study_sessions: [],
};
const run: MaterialAttemptView = {
  cancel_requested_at: null,
  run_id: latestRun,
  status: "running",
  progress_stage: "semantics",
  completed_pages: 2,
  total_pages: 8,
  error_code: null,
  created_at: "2026-09-12T01:00:00Z",
};
const published = {
  run_id: publishedRun,
  knowledge_structure_revision: revision,
  status: "succeeded" as const,
  created_at: "2026-09-12T00:30:00Z",
};
const active: StudySessionLink = {
  study_session_id: studyId,
  run_id: publishedRun,
  knowledge_structure_revision: revision,
  status: "active",
  started_at: "2026-09-12T02:00:00Z",
  current_concept_id: null,
};
const states = [
  "empty",
  "uploaded",
  "pending",
  "running",
  "failed",
  "failed-map",
  "map",
  "partial",
  "active",
  "completed",
  "multiple",
  "long-name",
  "loading",
  "failure",
] as const;
type State = (typeof states)[number];

function material(state: State): MaterialLibraryItem {
  const item = structuredClone(base);
  if (["pending", "running", "failed", "failed-map"].includes(state))
    item.latest_attempt = {
      ...run,
      status: state.startsWith("failed") ? "failed" : state === "pending" ? "pending" : "running",
      progress_stage: state === "pending" ? "queued" : "semantics",
      error_code: state.startsWith("failed") ? "STORAGE_UNAVAILABLE" : null,
    };
  if (["map", "partial", "failed-map", "active", "completed", "long-name"].includes(state)) {
    item.available_structures = [
      { ...published, status: state === "partial" ? "partial" : "succeeded" },
    ];
    item.latest_attempt ??= {
      ...run,
      run_id: publishedRun,
      status: state === "partial" ? "partial" : "succeeded",
      progress_stage: "completed",
      completed_pages: 8,
    };
  }
  if (["active", "completed", "long-name"].includes(state))
    item.study_sessions = [{ ...active, status: state === "completed" ? "completed" : "active" }];
  if (state === "long-name")
    item.display_name = "資料結構與演算法_" + "VeryLongMaterialFilename".repeat(6) + ".pdf";
  return item;
}

function materials(state: State): MaterialLibraryItem[] {
  if (["empty", "loading", "failure"].includes(state)) return [];
  if (state === "multiple")
    return (["uploaded", "running", "failed", "failed-map", "active", "completed"] as const).map(
      (value, index) => ({
        ...material(value),
        material_id: `${String(index + 1).padStart(8, "0")}-1111-4111-8111-111111111111`,
        display_name: [
          "演算法課堂筆記.pdf",
          "本週課程：遞迴與樹狀結構的概念整理和練習題.pdf",
          "陣列.pdf",
          "堆疊講義.pdf",
          "佇列與練習.pdf",
          "圖論學習筆記.pdf",
        ][index],
      }),
    );
  return [material(state)];
}

test.beforeEach(async ({ page }) => {
  await page.route(/\/v[12]\//, (route) => {
    const path = new URL(route.request().url()).pathname;
    if (["/v1/session", "/v1/session/refresh"].includes(path)) {
      expect(route.request().method()).toBe(path.endsWith("/refresh") ? "POST" : "GET");
      return route.fulfill({
        json: { schema: "learner-identity/v1", learner_id: "99999999-9999-4999-8999-999999999999" },
      });
    }
    if (path === "/v1/topics") return route.fulfill({json:{topics:[]}});
    // 目的頁的資料流程由各自 spec 驗證，此檔只驗證列表導向。
    return route.fulfill({
      status: 404,
      json: {
        schema: "api-error/v1",
        request_id: materialId,
        reason_code: "RESOURCE_NOT_FOUND",
        retryable: false,
        message: "Request could not be completed.",
      },
    });
  });
});

async function mockLibrary(page: Page, readItems: () => MaterialLibraryItem[]) {
  let reads = 0;
  await page.route("**/v1/materials", (route) => {
    expect(route.request().method()).toBe("GET");
    reads++;
    return route.fulfill({ json: { schema: "material-library/v1", materials: readItems() } });
  });
  return () => reads;
}

// 各狀態的操作是獨立預期值；版面欄數另由下方 geometry 案例驗證。
const expectedActions: Record<State, string[][]> = {
  empty: [],
  loading: [],
  failure: [],
  uploaded: [["建立知識地圖"]],
  pending: [["查看進度"]],
  running: [["查看進度"]],
  failed: [["查看問題"]],
  "failed-map": [["開啟知識地圖", "查看問題"]],
  map: [["開啟知識地圖"]],
  partial: [["開啟知識地圖"]],
  active: [["繼續學習", "開啟知識地圖"]],
  completed: [["查看學習成果", "開啟知識地圖"]],
  "long-name": [["繼續學習", "開啟知識地圖"]],
  multiple: [
    ["建立知識地圖"],
    ["查看進度"],
    ["查看問題"],
    ["開啟知識地圖", "查看問題"],
    ["繼續學習", "開啟知識地圖"],
    ["查看學習成果", "開啟知識地圖"],
  ],
};

for (const state of states) {
  test(`material collection ${state} exposes the matching actions`, async ({ page }) => {
    const items = materials(state);
    await mockLibrary(page, () => items);
    let releaseRead!: () => void;
    const gate = new Promise<void>((resolve) => {
      releaseRead = resolve;
    });
    if (state === "loading" || state === "failure")
      await page.route(
        "**/v1/materials",
        async (route) => {
          expect(route.request().method()).toBe("GET");
          if (state === "loading") {
            await gate;
            return route.fallback();
          }
          return route.fulfill({
            status: 503,
            json: {
              schema: "api-error/v1",
              request_id: materialId,
              reason_code: "STORAGE_UNAVAILABLE",
              retryable: true,
              message: "Request could not be completed.",
            },
          });
        },
        { times: 1 },
      );
    await page.goto("/materials");
    if (state === "loading" || state === "failure") {
      await expect(state === "loading" ? page.getByRole("status").filter({hasText:"正在讀取教材庫"}) : page.getByRole("heading", {name:"無法讀取教材",exact:true})).toBeVisible();
      if (state === "loading") releaseRead();
      else {
        await expect(
          page.getByText("資料服務暫時無法使用，請稍後再試。", { exact: true }),
        ).toBeVisible();
        await page.getByRole("button", { name: "重新讀取", exact: true }).click();
      }
    }
    const cards = page.locator(".library-item");
    await expect(cards).toHaveCount(items.length);
    if (!items.length) await expect(page.locator(".library-empty")).toBeVisible();
    for (const [index, item] of items.entries()) {
      const card = cards.nth(index);
      const actions = [...expectedActions[state][index], ...(item.available_structures.length ? ["概念卡", "Podcast"] : [])];
      await expect(card.getByRole("heading")).toHaveText(item.display_name);
      await expect(card.getByRole("button", { name: item.display_name, exact: true })).toHaveCount(
        0,
      );
      await expect(card.locator(".state-actions > :is(button, a)")).toHaveText(actions);
      await expect(card.locator(".primary-button")).toHaveText(actions[0]);
      // 只有知識地圖、概念卡、Podcast 入口是連結，不出現來源檔案等連結。
      await expect(card.locator("a:not(.state-actions a)")).toHaveCount(0);
      await expect(card.locator("details:not(.material-management-menu)")).toHaveCount(0);
      await expect(card.getByRole("region", { name: "學習紀錄" })).toHaveCount(0);
    }
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
      true,
    );
  });
}

test("latest structure controls exact saved learning binding, never an older state", async ({
  page,
}) => {
  const newerRun = "55555555-5555-4555-8555-555555555555";
  const newerRevision = `knowledge-structure:sha256:${"b".repeat(64)}`;
  const item = material("active");
  item.available_structures.push({
    ...published,
    run_id: newerRun,
    knowledge_structure_revision: newerRevision,
  });
  item.head_revision = newerRevision;
  item.study_sessions.push(
    {
      ...active,
      study_session_id: "66666666-6666-4666-8666-666666666666",
      knowledge_structure_revision: newerRevision,
    },
    { ...active, study_session_id: "77777777-7777-4777-8777-777777777777", run_id: newerRun },
  );
  await mockLibrary(page, () => [item]);
  await page.goto("/materials");
  await expect(page.getByRole("article").locator(".primary-button")).toHaveText("開啟知識地圖");
  await expect(page.getByRole("button", { name: "繼續學習", exact: true })).toHaveCount(0);
  await page.getByRole("link", { name: "開啟知識地圖", exact: true }).click();
  await expect
    .poll(() => new URL(page.url()).pathname)
    .toBe(
      `/materials/${materialId}/runs/${newerRun}/knowledge-structures/${encodeURIComponent(newerRevision)}`,
    );
  item.study_sessions.push({
    ...active,
    study_session_id: "88888888-8888-4888-8888-888888888888",
    run_id: newerRun,
    knowledge_structure_revision: newerRevision,
  });
  await page.goto("/materials");
  await page.getByRole("article").getByRole("button", { name: "繼續學習", exact: true }).click();
  await expect
    .poll(() => new URL(page.url()).pathname)
    .toBe(
      `/materials/${materialId}/runs/${newerRun}/knowledge-structures/${encodeURIComponent(newerRevision)}/study-sessions/88888888-8888-4888-8888-888888888888`,
    );
});

test("direct hub polls an active run until a usable map is published", async ({ page }) => {
  await page.clock.install();
  await page.clock.pauseAt(new Date());
  let publishedNow = false;
  const reads = await mockLibrary(page, () => [material(publishedNow ? "map" : "pending")]);
  await page.goto("/materials");
  await expect(page.getByRole("article").locator(".primary-button")).toHaveText("查看進度");
  publishedNow = true;
  await page.clock.runFor(3000);
  await expect(page.getByRole("article").locator(".primary-button")).toHaveText("開啟知識地圖");
  expect(reads()).toBe(2);
  const stopped = reads();
  await page.clock.runFor(6000);
  expect(reads()).toBe(stopped);
});

for (const viewport of [
  { width: 1920, height: 1080, phone: false },
  { width: 1536, height: 1024, phone: false },
  { width: 1024, height: 768, phone: false },
  { width: 390, height: 844, phone: true },
])
  test(`bookshelf geometry and expanded management at ${viewport.width}px`, async ({ page }) => {
    await page.setViewportSize(viewport);
    const names = [
      // 取消側欄後卡片變寬，名稱需超過兩行才能驗證截斷。
      "微積分曲線描繪與函數極值：導數、凹凸性、漸近線及綜合應用練習講義（含例題詳解、課後習題與跨章節解題方法，附期末複習重點與常見錯誤整理）",
      "資料結構與演算法",
      "線性代數",
      "程式設計",
      "離散數學",
    ];
    const items = (["uploaded", "active", "map", "active", "map"] as const).map((state, index) => ({
      ...material(state),
      material_id: `10000000-1111-4111-8111-${String(index + 1).padStart(12, "0")}`,
      display_name: names[index],
      source_count: index === 1 || index === 2 ? 3 : 1,
    }));
    await mockLibrary(page, () => items);
    await page.goto("/materials");
    const cards = page.locator(".library-item");
    await expect(cards).toHaveCount(5);
    await expect(cards.nth(0).locator(".primary-button")).toHaveText("建立知識地圖");
    for (const index of [1, 3]) {
      await expect(cards.nth(index).locator(".primary-button")).toHaveText("繼續學習");
      await expect(cards.nth(index).getByRole("link", { name: "開啟知識地圖",exact:true})).toBeVisible();
    }
    for (const index of [2, 4])
      await expect(cards.nth(index).locator(".primary-button")).toHaveText("開啟知識地圖");
    const geometry = await cards.evaluateAll((elements) =>
      elements.map((card) => {
        const box = card.getBoundingClientRect();
        const rect = (selector: string) => card.querySelector(selector)!.getBoundingClientRect();
        return {
          x: box.x,
          y: box.y,
          width: box.width,
          height: box.height,
          actionY: rect("fieldset").y,
          titleOffset: rect("h2").y - box.y,
          buttonBottom: box.bottom - rect("fieldset").bottom,
        };
      }),
    );
    // 標題不預留第二行，單行與兩行標題的卡片高度不同；同一列仍等高、按鈕對齊。
    for (const key of ["width", "titleOffset", "buttonBottom"] as const) {
      const values = geometry.map((box) => box[key]);
      expect(Math.max(...values) - Math.min(...values), key).toBeLessThanOrEqual(1);
    }
    // 不鎖定欄數：手機單欄；其他寬度至少兩欄，卡片維持可讀寬度、不被放大。
    const firstRow = geometry.filter((box) => Math.abs(box.y - geometry[0].y) < 1);
    if (viewport.phone) expect(firstRow).toHaveLength(1);
    else {
      expect(firstRow.length).toBeGreaterThanOrEqual(2);
      expect(geometry[0].width).toBeGreaterThanOrEqual(280);
      expect(geometry[0].width).toBeLessThanOrEqual(520);
    }
    for (const box of geometry) {
      for (const other of geometry.filter((other) => Math.abs(other.y - box.y) < 1)) {
        expect(Math.abs(box.actionY - other.actionY)).toBeLessThanOrEqual(1);
        expect(Math.abs(box.height - other.height)).toBeLessThanOrEqual(1);
      }
    }
    const frame = (await page.locator(".material-library").boundingBox())!;
    if (viewport.width === 1920) expect(frame.width).toBeGreaterThan(1500);
    if (viewport.phone) expect(geometry[0].width).toBeCloseTo(frame.width, 0);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
      true,
    );
    await expect(cards.first().getByRole("heading")).toHaveAttribute("title", names[0]);
    expect(
      await cards
        .first()
        .getByRole("heading")
        .evaluate((element) => element.scrollHeight > element.clientHeight),
    ).toBe(true);

    const card = cards.first();
    await card.getByRole("button", { name: `管理「${names[0]}」`, exact: true }).click();
    await expect(card.getByRole("button", { name: "來源與更新", exact: true })).toBeVisible();
    await card.getByRole("button", { name: "重新命名", exact: true }).click();
    await expect(card.getByRole("textbox", { name: "教材名稱", exact: true })).toHaveValue(
      names[0],
    );
    await expect(card.getByRole("button", { name: "儲存", exact: true })).toBeVisible();
    const form = (await card.getByRole("form").boundingBox())!;
    const expanded = (await card.boundingBox())!;
    expect(form.y + form.height).toBeLessThanOrEqual(expanded.y + expanded.height);
    await card.getByRole("button", { name: "取消", exact: true }).click();
    await card.getByRole("button", { name: `管理「${names[0]}」`, exact: true }).click();
    await card.getByRole("button", { name: "刪除教材", exact: true }).click();
    await expect(card.getByRole("button", { name: "確認刪除", exact: true })).toBeVisible();
    const confirmation = (await card.getByRole("form").boundingBox())!;
    const deleting = (await card.boundingBox())!;
    expect(confirmation.y + confirmation.height).toBeLessThanOrEqual(deleting.y + deleting.height);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
      true,
    );
    await card.getByRole("button", { name: "取消", exact: true }).click();
  });
