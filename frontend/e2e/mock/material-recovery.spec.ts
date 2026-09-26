import { expect, test } from "@playwright/test";
import type { MaterialLibraryItem, MaterialProcessingRunView } from "../../src/api/contracts";

const materialId = "11111111-1111-4111-8111-111111111111";
const oldId = "22222222-2222-4222-8222-222222222222";
const sourceId = "33333333-3333-4333-8333-333333333333";
const newId = "44444444-4444-4444-8444-444444444444";
const stamp = "2026-09-12T12:00:00Z";
const oldRun: MaterialProcessingRunView = {
  schema: "material-processing-run/v1",
  material_id: materialId,
  run_id: oldId,
  source_artifact_id: sourceId,
  status: "failed",
  progress_stage: "semantics",
  completed_pages: 2,
  total_pages: 8,
  created_at: stamp,
  updated_at: stamp,
  completed_at: stamp,
  cancel_requested_at: null,
  error_code: "NO_USABLE_EVIDENCE",
  output_binding: null,
};
const newRun: MaterialProcessingRunView = {
  ...oldRun,
  run_id: newId,
  status: "pending",
  progress_stage: "queued",
  completed_pages: 0,
  total_pages: null,
  completed_at: null,
  error_code: null,
};
const item: MaterialLibraryItem = {
  schema: "material-library-item/v1",
  material_id: materialId,
  source_artifact_id: sourceId,
  display_name: "資料結構講義.pdf",
  size_bytes: 4096,
  created_at: stamp,
  latest_attempt: oldRun,
  available_structures: [],
  study_sessions: [],
};

const oldPath = `/materials/${materialId}/runs/${oldId}`;
const newPath = `/materials/${materialId}/runs/${newId}`;
const intentKey = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const unavailable = {
  schema: "api-error/v1",
  request_id: materialId,
  reason_code: "STORAGE_UNAVAILABLE",
  retryable: true,
  message: "Request could not be completed.",
};

test.beforeEach(async ({ page }) => {
  const responses: Record<string, unknown> = {
    "/v1/session": {
      schema: "learner-identity/v1",
      learner_id: "99999999-9999-4999-8999-999999999999",
    },
    "/v1/session/refresh": {
      schema: "learner-identity/v1",
      learner_id: "99999999-9999-4999-8999-999999999999",
    },
    "/v1/materials": { schema: "material-library/v1", materials: [item] },
    [`/v1/material-processing-runs/${oldId}`]: oldRun,
    [`/v1/material-processing-runs/${newId}`]: newRun,
  };
  await page.route(/\/v[12]\//, (route) => {
    const path = new URL(route.request().url()).pathname;
    if (!responses[path])
      throw new Error(`Unexpected recovery request: ${route.request().method()} ${path}`);
    expect(route.request().method()).toBe(path === "/v1/session/refresh" ? "POST" : "GET");
    return route.fulfill({ json: responses[path] });
  });
});

for (const width of [1536, 390])
  test(`saved analysis failure offers continuation without claiming a published map at ${width}px`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 844 });
    const saved: MaterialProcessingRunView = {
      ...oldRun,
      input_source_set_id: "77777777-7777-4777-8777-777777777777",
      analysis_saved: true,
      error_code: "KNOWLEDGE_STRUCTURE_INVALID",
      completed_pages: 90,
      total_pages: 90,
    };
    await page.route(`**/v1/material-processing-runs/${oldId}`, (route) =>
      route.fulfill({ json: saved }),
    );
    const retryKeys: string[] = [];
    await page.route(`**/v1/material-processing-runs/${oldId}/retry`, (route) => {
      expect(route.request().method()).toBe("POST");
      retryKeys.push(route.request().headers()["idempotency-key"]);
      expect(route.request().postData()).toBeNull();
      return retryKeys.length === 1
        ? route.fulfill({
            status: 503,
            json: unavailable,
          })
        : route.fulfill({ status: 202, json: newRun });
    });
    await page.goto(oldPath);
    await expect(page.getByRole("heading", { name: "教材處理失敗", exact: true })).toBeVisible();
    await expect(page.getByText("已完成的分析批次保存在本機。", { exact: false })).toBeVisible();
    await expect(page.getByText("最後記錄進度：", { exact: false })).toContainText("90 / 90 頁");
    await expect(page.getByRole("button", { name: "開啟知識地圖", exact: true })).toHaveCount(0);
    await page.getByRole("button", { name: "接續已保存的分析", exact: true }).click();
    await expect(page.locator(".material-recovery-error")).toBeVisible();
    await expect(page.getByRole("button", { name: "接續已保存的分析", exact: true })).toBeFocused();
    await page.getByRole("button", { name: "接續已保存的分析", exact: true }).click();
    await expect(page).toHaveURL(new RegExp(`${newPath}$`));
    expect(retryKeys).toHaveLength(2);
    expect(retryKeys[0]).toMatch(intentKey);
    expect(retryKeys[0]).toBe(retryKeys[1]);
    await expect(page.getByRole("heading", { name: "等待開始處理", exact: true })).toBeVisible();
    await page.goto(oldPath);
    await expect(page.getByText("最後記錄進度：", { exact: false })).toContainText("90 / 90 頁");
  });

for (const viewport of [
  { width: 1536, height: 1024 },
  { width: 390, height: 844 },
]) {
  for (const context of ["collection", "run"] as const)
    test(`material recovery ${context} uses one intent and new run at ${viewport.width}px`, async ({
      page,
    }) => {
      await page.setViewportSize(viewport);
      let originalPath = context === "run" ? oldPath : "/materials";
      const keys: string[] = [];
      const bodies: unknown[] = [];
      const serverRuns = new Map<string, MaterialProcessingRunView>();
      let release!: () => void;
      const response = new Promise<void>((resolve) => {
        release = resolve;
      });
      await page.route(`**/v1/material-processing-runs/${oldId}/retry`, async (route) => {
        expect(route.request().method()).toBe("POST");
        const key = route.request().headers()["idempotency-key"];
        keys.push(key);
        bodies.push(route.request().postData());
        if (!serverRuns.has(key)) serverRuns.set(key, newRun);
        // 工作已建立，但第一個回應在傳輸途中遺失。
        if (keys.length === 1) {
          await response;
          return route.abort("connectionreset");
        }
        return route.fulfill({ status: 202, json: serverRuns.get(key) });
      });
      await page.goto(originalPath);
      if (context === "collection") {
        await expect(page.getByText("知識地圖建立失敗", { exact: true })).toBeVisible();
        await page.getByRole("button", { name: "查看問題", exact: true }).click();
        originalPath = oldPath;
        await expect(page).toHaveURL(new RegExp(originalPath + "$"));
      }
      const button = page.getByRole("button", { name: "重新分析原來源", exact: true });
      await expect(button).toHaveClass("primary-button");
      await expect(page.getByRole("button", { name: "返回我的教材", exact: true })).toHaveCount(0);
      await button.focus();
      await button.evaluate((el) => {
        (el as HTMLButtonElement).click();
        (el as HTMLButtonElement).click();
      });
      await expect.poll(() => keys.length).toBe(1);
      await expect(page.getByRole("button", { name: "正在重新處理…", exact: true })).toBeDisabled();
      release();
      await expect(page.locator(".material-recovery-error")).toContainText(
        "無法重新開始處理，請再試一次。",
      );
      expect(new URL(page.url()).pathname).toBe(originalPath);
      await expect(button).toBeFocused();
      if (context === "run")
        await expect(page.locator(".failure-progress")).toContainText("最後記錄進度");
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
        true,
      );
      await button.click();
      await expect(page).toHaveURL(new RegExp(`${newPath}$`));
      await expect(page.getByRole("heading", { name: "等待開始處理", exact: true })).toBeVisible();
      expect(serverRuns.size).toBe(1);
      expect(keys).toHaveLength(2);
      expect(keys[0]).toMatch(intentKey);
      expect(keys[1]).toBe(keys[0]);
      for (const body of bodies) expect(body).toBeNull();
      await page.goto(oldPath);
      await expect(page.getByRole("heading", { name: "教材處理失敗", exact: true })).toBeVisible();
    });
}

test("run read failure retries and returns to the material collection", async ({ page }) => {
  await page.route(
    `**/v1/material-processing-runs/${oldId}`,
    (route) => route.fulfill({ status: 503, json: unavailable }),
    { times: 1 },
  );
  await page.goto(oldPath);
  await expect(page.getByRole("heading", { name: "無法讀取處理狀態" })).toBeVisible();
  await expect(page.getByRole("button", { name: "返回教材庫" })).toBeVisible();
  await page.getByRole("button", { name: "重新讀取", exact: true }).click();
  await expect(page.getByRole("heading", { name: "教材處理失敗", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "返回教材庫" }).click();
  await expect(page).toHaveURL(/\/materials$/);
  await expect(page.getByRole("article").getByRole("heading")).toHaveText(item.display_name);
});

for (const status of ["active", "completed"] as const)
  test(`failed collection prioritizes ${status} saved learning over recovery`, async ({ page }) => {
    const revision = `knowledge-structure:sha256:${"a".repeat(64)}`;
    const publishedId = "55555555-5555-4555-8555-555555555555";
    const studyId = "66666666-6666-4666-8666-666666666666";
    const saved: MaterialLibraryItem = {
      ...item,
      available_structures: [
        {
          run_id: publishedId,
          knowledge_structure_revision: revision,
          status: "succeeded",
          created_at: stamp,
        },
      ],
      study_sessions: [
        {
          study_session_id: studyId,
          run_id: publishedId,
          knowledge_structure_revision: revision,
          status,
          started_at: stamp,
          current_concept_id: null,
        },
      ],
    };
    await page.route("**/v1/materials", (route) =>
      route.fulfill({ json: { schema: "material-library/v1", materials: [saved] } }),
    );
    const retries: string[] = [];
    page.on("request", (request) => {
      if (new URL(request.url()).pathname.endsWith("/retry")) retries.push(request.url());
    });
    await page.route(
      `**/v1/materials/${materialId}/knowledge-structures/${encodeURIComponent(revision)}/study-sessions/${studyId}/resume?*`,
      (route) => {
        expect(route.request().method()).toBe("GET");
        expect(new URL(route.request().url()).searchParams.get("run_id")).toBe(publishedId);
        return route.fulfill({
          status: 404,
          json: { ...unavailable, reason_code: "RESOURCE_NOT_FOUND", retryable: false },
        });
      },
    );
    await page.goto("/materials");
    const actions = page.locator(".library-item .state-actions");
    await expect(actions.locator(".primary-button")).toHaveText(
      status === "active" ? "繼續學習" : "查看學習成果",
    );
    await expect(actions.getByRole("button", { name: "開啟知識地圖", exact: true })).toHaveClass(
      "secondary-button",
    );
    await expect(actions.getByRole("button", { name: "重新處理教材", exact: true })).toHaveCount(0);
    await expect(actions.getByRole("button", { name: "查看問題", exact: true })).toHaveClass(
      "text-button",
    );
    saved.latest_attempt = newRun;
    await page.reload();
    await expect(actions.getByRole("button")).toHaveText([
      status === "active" ? "繼續學習" : "查看學習成果",
      "開啟知識地圖",
      "查看進度",
    ]);
    await actions.locator(".primary-button").click();
    await expect(page).toHaveURL(
      new RegExp(
        `/materials/${materialId}/runs/${publishedId}/knowledge-structures/${encodeURIComponent(revision)}/study-sessions/${studyId}$`,
      ),
    );
    expect(retries).toEqual([]);
  });
