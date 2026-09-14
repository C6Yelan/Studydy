import { expect, test, type Page } from "@playwright/test";
import type { MaterialLibraryItem, MaterialProcessingRunView } from "../src/api/contracts";

const materialId = "11111111-1111-4111-8111-111111111111";
const oldId = "22222222-2222-4222-8222-222222222222";
const sourceId = "33333333-3333-4333-8333-333333333333";
const newId = "44444444-4444-4444-8444-444444444444";
const stamp = "2026-09-12T12:00:00Z";
const oldRun: MaterialProcessingRunView = { schema: "material-processing-run/v5", material_id: materialId, run_id: oldId, source_artifact_id: sourceId,
  status: "failed", progress_stage: "semantics", completed_pages: 2, total_pages: 8, created_at: stamp, updated_at: stamp, completed_at: stamp,
  cancel_requested_at: null, error_code: "NO_USABLE_EVIDENCE", output_binding: null };
const newRun: MaterialProcessingRunView = { ...oldRun, run_id: newId, status: "pending", progress_stage: "queued", completed_pages: 0, total_pages: null, completed_at: null, error_code: null };
const item: MaterialLibraryItem = { schema: "material-library-item/v2", material_id: materialId, source_artifact_id: sourceId,
  display_name: "需要重新整理的教材.pdf", size_bytes: 4096, created_at: stamp, latest_attempt: oldRun, available_structures: [], study_sessions: [] };
async function setup(page: Page) {
  await page.route("**/v1/session", route => route.fulfill({ json: { schema: "learner-identity/v1", learner_id: materialId } }));
  await page.route("**/v1/session/refresh", route => route.fulfill({ status: 204 }));
  await page.route("**/v1/materials", route => route.fulfill({ json: { schema: "material-library/v2", materials: [item] } }));
  await page.route(`**/v1/material-processing-runs/${oldId}`, route => route.fulfill({ json: oldRun }));
  await page.route(`**/v1/material-processing-runs/${newId}`, route => route.fulfill({ json: newRun }));
}
for (const viewport of [{ width: 1536, height: 1024 }, { width: 1366, height: 768 }, { width: 1024, height: 768 }, { width: 390, height: 844 }]) {
  for (const context of ["collection", "run", "initial"]) test(`material recovery ${context} uses one intent and new run at ${viewport.width}px`, async ({ page }) => {
    await page.setViewportSize(viewport); await setup(page);
    if (context === "initial") await page.route("**/v1/materials", route => route.fulfill({ json: { schema: "material-library/v2", materials: [{ ...item, latest_attempt: null }] } }));
    const originalPath = context === "run" ? `/materials/${materialId}/runs/${oldId}` : "/materials";
    const keys: string[] = []; const bodies: unknown[] = []; const serverRuns = new Map<string, string>();
    let release!: () => void;
    const response = new Promise<void>(resolve => { release = resolve; });
    await page.route("**/v1/material-processing-runs", async route => {
      const key = route.request().headers()["idempotency-key"];
      keys.push(key); bodies.push(route.request().postDataJSON());
      serverRuns.set(key, newId); // Server already created the run before the first response is lost.
      if (keys.length === 1 && context !== "initial") {
        await response;
        return route.fulfill({ status: 503, json: { schema: "api-error/v1", request_id: materialId, reason_code: "STORAGE_UNAVAILABLE", retryable: true, message: "Request could not be completed." } });
      }
      return route.fulfill({ status: 201, json: newRun });
    });
    await page.goto(originalPath);
    const label = context === "initial" ? "開始整理教材" : "重新處理教材";
    const button = page.getByRole("button", { name: label, exact: true });
    await expect(button).toHaveClass("primary-button");
    await expect(page.getByRole("button", { name: "返回我的教材", exact: true })).toHaveCount(0);
    await page.screenshot({ path: `/tmp/studydy-recovery-${viewport.width}-${context}-ready.png`, fullPage: true });
    await button.focus();
    await button.evaluate(el => { (el as HTMLButtonElement).click(); (el as HTMLButtonElement).click(); });
    if (context !== "initial") {
      await expect.poll(() => keys.length).toBe(1);
      await expect(page.getByRole("button", { name: "正在重新處理…", exact: true })).toBeDisabled();
      await page.screenshot({ path: `/tmp/studydy-recovery-${viewport.width}-${context}-busy.png`, fullPage: true });
      release();
      await expect(page.locator(".material-recovery-error")).toContainText("無法重新開始處理，請再試一次。");
      expect(new URL(page.url()).pathname).toBe(originalPath);
      await expect(button).toBeFocused();
      if (context === "run") await expect(page.locator(".failure-progress")).toContainText("最後安全進度");
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      await page.screenshot({ path: `/tmp/studydy-recovery-${viewport.width}-${context}-error.png`, fullPage: true });
      await button.click();
      expect(keys[1]).toBe(keys[0]);
    }
    await expect(page).toHaveURL(new RegExp(`/materials/${materialId}/runs/${newId}$`));
    await expect(page.getByRole("heading", { name: "等待開始處理", exact: true })).toBeVisible();
    expect(serverRuns.size).toBe(1);
    expect(keys[0]).toMatch(/^[a-f0-9-]{36}$/);
    for (const body of bodies) expect(body).toEqual({ schema: "material-processing-create/v1", material_id: materialId, source_artifact_id: sourceId });
    expect(newId).not.toBe(oldId);
    await page.goto(`/materials/${materialId}/runs/${oldId}`);
    await expect(page.getByRole("heading", { name: "教材處理失敗", exact: true })).toBeVisible();
  });
}

test("run read failure reloads and returns to its material detail", async ({ page }) => {
  await setup(page); let fail = true;
  await page.route(`**/v1/material-processing-runs/${oldId}`, route => fail ? route.fulfill({ status: 503, json: { schema: "api-error/v1", request_id: materialId, reason_code: "STORAGE_UNAVAILABLE", retryable: true, message: "Request could not be completed." } }) : route.fulfill({ json: oldRun }));
  await page.goto(`/materials/${materialId}/runs/${oldId}`);
  await expect(page.getByRole("heading", { name: "無法讀取處理狀態" })).toBeVisible();
  await expect(page.getByRole("button", { name: "返回教材庫" })).toBeVisible();
  fail = false; await page.getByRole("button", { name: "重新讀取", exact: true }).click();
  await expect(page.getByRole("heading", { name: "教材處理失敗", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "返回教材庫" }).click();
  await expect(page).toHaveURL(/\/materials$/);
});

for (const status of ["active", "completed"] as const) test(`failed collection prioritizes ${status} saved learning over recovery`, async ({ page }) => {
  await setup(page);
  const revision = `knowledge-structure:sha256:${"a".repeat(64)}`;
  const saved: MaterialLibraryItem = { ...item, available_structures: [{ run_id: newId, knowledge_structure_revision: revision, status: "succeeded", created_at: stamp }],
    study_sessions: [{ study_session_id: sourceId, run_id: newId, knowledge_structure_revision: revision, status, started_at: stamp, current_concept_id: null }] };
  await page.route("**/v1/materials", route => route.fulfill({ json: { schema: "material-library/v2", materials: [saved] } }));
  await page.goto("/materials");
  const actions = page.locator(".library-item .state-actions");
  await expect(actions.locator(".primary-button")).toHaveText(status === "active" ? "繼續學習" : "查看學習成果");
  await expect(actions.getByRole("button", { name: "開啟知識地圖", exact: true })).toHaveClass("secondary-button");
  await expect(actions.getByRole("button", { name: "重新處理教材", exact: true })).toHaveCount(status === "active" ? 1 : 0);
  if (status === "active") await expect(actions.getByRole("button", { name: "重新處理教材", exact: true })).toHaveClass("secondary-button");
  await expect(actions.getByRole("button", { name: "查看失敗詳情", exact: true })).toHaveClass("text-button");
  saved.latest_attempt = newRun;
  await page.reload();
  await expect(actions.getByRole("button")).toHaveText(["查看處理狀態"]);
});
