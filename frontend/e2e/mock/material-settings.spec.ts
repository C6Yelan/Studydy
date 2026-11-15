import { expect, test, type Page } from "@playwright/test";
import type { MaterialLibraryItem, VersionMovePreview } from "../../src/api/contracts";
import { artifactId, materialId, run, runId, structureRevision, mockKnowledgeMapApi, json, emptyVersionMovePreview } from "../fixtures/knowledge-map";

// 教材設定：重新命名、刪除、回到更新前內容；沒有地圖的教材也能進設定頁。
const settingsPath = `/materials/${materialId}/settings`;
const previousMoveId = "77777777-7777-4777-8777-777777777777";

function material(name: string, hasMap: boolean): MaterialLibraryItem {
  return {
    schema: "material-library-item/v1", material_id: materialId, source_artifact_id: artifactId,
    display_name: name, size_bytes: 100, created_at: "2026-10-02T00:00:00Z", source_count: 3, source_totals: { page_count: 42, image_count: 0, media_seconds: null },
    head_revision: hasMap ? structureRevision : undefined,
    latest_attempt: (hasMap ? run : { run_id: runId, status: "failed", progress_stage: "semantics", completed_pages: 0, total_pages: 4,
      error_code: "NO_USABLE_EVIDENCE", cancel_requested_at: null, created_at: "2026-10-02T00:00:00Z" }) as MaterialLibraryItem["latest_attempt"],
    study_sessions: [],
    available_structures: hasMap ? [{ run_id: runId, knowledge_structure_revision: structureRevision, created_at: "2026-10-20T00:00:00Z", status: "succeeded" }] : [],
  };
}

async function mockSettings(page: Page, state: { item: MaterialLibraryItem; preview?: VersionMovePreview }) {
  await mockKnowledgeMapApi(page);
  await page.route(`**/v1/materials/${materialId}`, (route) => route.request().method() === "DELETE"
    ? json(route, { schema: "material-discard/v1", material_id: materialId, state: "removed" }) : json(route, state.item));
  await page.route(`**/v1/materials/${materialId}/version-move`, (route) => json(route, state.preview ?? emptyVersionMovePreview(materialId, structureRevision)));
  await page.route("**/v1/card-reviews", (route) => json(route, { schema: "card-reviews/v2", forgot_count: 0, sets: [], marks: [], cards: [] }));
  await page.route("**/v1/materials", (route) => json(route, { schema: "material-library/v1", materials: [] }));
}

test("rename saves the new name and the side rail shows it", async ({ page }) => {
  const state = { item: material("舊名稱.pdf", true) };
  await mockSettings(page, state);
  const posts: unknown[] = [];
  await page.route(`**/v1/materials/${materialId}/rename`, (route) => {
    posts.push(route.request().postDataJSON());
    state.item = material("新的名稱", true);
    return json(route, state.item);
  });
  await page.goto(settingsPath);
  await expect(page.getByRole("heading", { name: "教材設定", exact: true })).toBeVisible();
  await expect(page.getByText("3 個檔案")).toBeVisible();
  await expect(page.getByText("42 頁")).toBeVisible();
  await expect(page.getByText("2026/10/2")).toBeVisible();
  const input = page.getByRole("textbox", { name: "教材名稱" });
  const save = page.getByRole("button", { name: "儲存", exact: true });
  await expect(save).toBeDisabled();
  await input.fill("");
  await expect(page.getByText("名稱需為 1–40 個字")).toBeVisible();
  await expect(save).toBeDisabled();
  await input.fill("新的名稱");
  await save.click();
  await expect(page.getByRole("status").filter({ hasText: "已儲存" })).toBeVisible();
  expect(posts).toEqual([{ schema: "material-rename/v1", display_name: "新的名稱" }]);
  await expect(page.getByRole("navigation", { name: "教材導覽" }).locator("xpath=..")).toContainText("新的名稱");
});

test("delete asks for confirmation, keeps on cancel, and returns to the islands after confirming", async ({ page }) => {
  await mockSettings(page, { item: material("要刪除的教材.pdf", true) });
  const deletes: string[] = [];
  page.on("request", (request) => { if (request.method() === "DELETE") deletes.push(new URL(request.url()).pathname); });
  await page.goto(settingsPath);
  await page.getByRole("button", { name: "刪除教材", exact: true }).click();
  const confirmation = page.getByRole("group", { name: "刪除教材確認" });
  await expect(confirmation).toContainText("確定刪除教材與相關資料？");
  await confirmation.getByRole("button", { name: "保留", exact: true }).click();
  await expect(confirmation).toHaveCount(0);
  expect(deletes).toEqual([]);
  await page.getByRole("button", { name: "刪除教材", exact: true }).click();
  await page.getByRole("group", { name: "刪除教材確認" }).getByRole("button", { name: "確認刪除" }).click();
  await expect(page).toHaveURL(/\/materials$/);
  expect(deletes).toEqual([`/v1/materials/${materialId}`]);
});

test("returning to the content before the update is offered on the settings page, with a link there from the notice", async ({ page }) => {
  const preview: VersionMovePreview = { ...emptyVersionMovePreview(materialId, structureRevision),
    previous: { move_id: previousMoveId, revision: `knowledge-structure:sha256:${"f".repeat(64)}`, card_set_ids: [], study_session_id: null },
    automatic_move: { status: "completed", move_id: previousMoveId, error_code: null } };
  await mockSettings(page, { item: material("資料結構.pdf", true), preview });
  await page.route(`**/v1/materials/${materialId}/version-move/return`, (route) => json(route, {
    schema: "version-move-return/v1", move_id: previousMoveId, revision: preview.previous!.revision, card_set_ids: [], study_session_id: null }));
  // 教材頁上方的提示列只有連到設定頁的按鈕。
  await page.goto(`/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}`);
  const notice = page.getByRole("region", { name: "教材已更新" });
  await expect(notice.getByRole("button")).toHaveCount(1);
  await notice.getByRole("link", { name: "到教材設定" }).click();
  await expect(page).toHaveURL(new RegExp(`${settingsPath}$`));
  await page.getByRole("button", { name: "回到更新前內容", exact: true }).click();
  await expect(page.getByRole("dialog", { name: "回到更新前的內容" })).toBeVisible();
});

test("a material without a map can still open its settings, and the source link works", async ({ page }) => {
  await mockSettings(page, { item: material("分析失敗的教材.pdf", false) });
  await page.goto(settingsPath);
  await expect(page.getByRole("heading", { name: "教材設定", exact: true })).toBeVisible();
  await expect(page.getByText("教材還沒有知識地圖")).toBeVisible();
  await expect(page.getByText("不滿意可以回到更新前的內容")).toHaveCount(0);
  await expect(page.getByRole("link", { name: "新增來源", exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "知識地圖" })).toHaveAttribute("aria-disabled", "true");
  await expect(page.getByRole("link", { name: "教材設定" })).toHaveAttribute("aria-current", "page");
  await page.getByRole("link", { name: "查看原始檔案" }).click();
  await expect(page).toHaveURL(new RegExp(`/materials/${materialId}/sources$`));
});

test("a failed read after delete was accepted shows a retry, and the retry returns to the islands once the material is gone", async ({ page }) => {
  const state = { item: material("刪除中的教材.pdf", true) };
  await mockSettings(page, state);
  let removing = false;
  let failNext = true;
  const reads: number[] = [];
  const apiError = (route: Parameters<typeof json>[0], status: number, reason: string) => json(route, {
    schema: "api-error/v1", request_id: materialId, reason_code: reason, retryable: false, message: "Request could not be completed." }, status);
  await page.route(`**/v1/materials/${materialId}`, (route) => {
    if (route.request().method() === "DELETE") { removing = true; return json(route, { schema: "material-discard/v1", material_id: materialId, state: "removing" }); }
    if (!removing) return json(route, state.item);
    reads.push(Date.now());
    if (failNext) { failNext = false; return apiError(route, 503, "STORAGE_UNAVAILABLE"); }
    return apiError(route, 404, "RESOURCE_NOT_FOUND");
  });
  await page.goto(settingsPath);
  await page.getByRole("button", { name: "刪除教材", exact: true }).click();
  await page.getByRole("group", { name: "刪除教材確認" }).getByRole("button", { name: "確認刪除" }).click();
  await expect(page.getByRole("alert")).toBeVisible();
  const retry = page.getByRole("button", { name: "重新讀取", exact: true });
  await expect(retry).toBeVisible();
  // 不做背景重試：失敗後只有按「重新讀取」才會再讀。
  const failedReads = reads.length;
  await page.waitForTimeout(2500);
  expect(reads).toHaveLength(failedReads);
  await retry.click();
  await expect(page).toHaveURL(/\/materials$/);
});
