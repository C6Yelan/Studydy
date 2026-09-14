import { expect, test, type Page } from "@playwright/test";
import type { MaterialLibraryItem } from "../src/api/contracts";

const id = (value: number) => `10000000-0000-4000-8000-${String(value).padStart(12, "0")}`;
const revision = `knowledge-structure:sha256:${"a".repeat(64)}`;
const created = "2026-09-12T12:00:00Z";
const longName = "傳錯的教材_" + "VeryLongUnbrokenMaterialFilename".repeat(4) + ".pdf";
function item(index: number, state: "no-run" | "failed" | "cancelled" | "running" | "pending" | "succeeded" | "partial", map = false, study = false): MaterialLibraryItem {
  const success = state === "succeeded" || state === "partial";
  return { schema: "material-library-item/v2", material_id: id(index), source_artifact_id: id(index+100), display_name: state === "failed" ? longName : `${state}.pdf`, size_bytes: 1024, created_at: created,
    latest_attempt: state === "no-run" ? null : { run_id: id(index+200), status: state,
      progress_stage: success ? "completed" : state === "pending" ? "queued" : "semantics", completed_pages: success ? 4 : 0, total_pages: state === "pending" ? null : 4,
      error_code: state === "failed" ? "NO_USABLE_EVIDENCE" : null, cancel_requested_at: state === "cancelled" ? created : null, created_at: created },
    available_structures: map ? [{ run_id: id(index+300), knowledge_structure_revision: revision, created_at: created, status: state === "partial" ? "partial" : "succeeded" }] : [],
    study_sessions: study ? [{ study_session_id: id(index+400), run_id: id(index+300), knowledge_structure_revision: revision, status: "active", current_concept_id: null, started_at: created }] : [] };
}
async function setup(page: Page) {
  await page.clock.install({ time: new Date(created) }); await page.clock.pauseAt(new Date(created));
  await page.route("**/v1/session/refresh", route => route.fulfill({ status: 204 }));
  await page.route("**/v1/session", route => route.fulfill({ json: { schema: "learner-identity/v1", learner_id: id(900) } }));
}


for (const viewport of [{ width: 1536, height: 1024 }, { width: 1366, height: 768 }, { width: 1024, height: 768 }, { width: 390, height: 844 }]) {
  test(`all material states have keyboard management without changing primary actions at ${viewport.width}px`, async ({ page }) => {
    await page.setViewportSize(viewport); await setup(page);
    const items = [item(1, "no-run"), item(2, "pending"), item(3, "running"), item(4, "failed"), item(5, "partial", true), item(6, "succeeded", true), item(7, "succeeded", true, true), item(8, "cancelled")];
    await page.route("**/v1/materials", route => route.fulfill({ json: { schema: "material-library/v2", materials: items } }));
    await page.goto("/materials");
    const cards = page.getByRole("article");
    await expect(cards).toHaveCount(items.length);
    for (let index = 0; index < items.length; index++) {
      const card = cards.nth(index);
      const menu = card.getByRole("button", { name: `管理「${items[index].display_name}」`, exact: true });
      await expect(menu).toBeVisible();
      await expect(card.locator(".state-actions").getByRole("button", { name: "刪除教材", exact: true })).toHaveCount(0);
      await menu.focus(); await page.keyboard.press("Enter");
      await expect(card.getByRole("button", { name: "重新命名", exact: true })).toBeVisible();
      await expect(card.getByRole("button", { name: "刪除教材", exact: true })).toBeVisible();
      await page.keyboard.press("Escape"); await expect(menu).toBeFocused();
      await expect(card.locator("details")).not.toHaveAttribute("open", "");
    }
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({ path: `/tmp/studydy-management-${viewport.width}-cards.png`, fullPage: true });
  });

  test(`rename updates the current search locally and restores focus at ${viewport.width}px`, async ({ page }) => {
    await page.setViewportSize(viewport); await setup(page);
    let items = [item(1, "succeeded", true, true), item(2, "no-run")]; items[0].display_name = "中文舊教材.pdf";
    let reads = 0, writes = 0, fail = true;
    await page.route("**/v1/materials", route => { reads++; return route.fulfill({ json: { schema: "material-library/v2", materials: items } }); });
    await page.route(`**/v1/materials/${items[0].material_id}/rename`, route => {
      writes++; expect(route.request().headers()["idempotency-key"]).toBeUndefined();
      if (fail) return route.fulfill({ status: 503, json: { schema: "api-error/v1", request_id: id(999), reason_code: "STORAGE_UNAVAILABLE", retryable: true, message: "Request could not be completed." } });
      expect(route.request().postDataJSON()).toEqual({ schema: "material-rename/v1", display_name: "作業系統 第五章" });
      items = [{ ...items[0], display_name: "作業系統 第五章" }, items[1]];
      return route.fulfill({ json: items[0] });
    });
    await page.goto("/materials");
    const search = page.getByRole("searchbox", { name: "搜尋教材名稱" }); await search.fill("中文");
    const card = page.getByRole("article");
    const opener = card.getByRole("button", { name: /^管理「/ });
    await opener.click(); await card.getByRole("button", { name: "重新命名", exact: true }).click();
    const input = card.getByRole("textbox", { name: "教材名稱", exact: true });
    await expect(input).toBeFocused(); await expect(input).toHaveValue("中文舊教材.pdf");
    await input.press("Escape"); await expect(opener).toBeFocused(); expect(writes).toBe(0);
    await opener.click(); await card.getByRole("button", { name: "重新命名", exact: true }).click();
    await input.fill("   "); await expect(card.getByRole("button", { name: "儲存", exact: true })).toBeDisabled();
    await input.fill("  作業系統 第五章  "); await input.press("Enter");
    await expect(card.getByRole("alert")).toContainText("無法重新命名教材"); await expect(input).toBeFocused();
    await page.screenshot({ path: `/tmp/studydy-management-${viewport.width}-rename-error.png`, fullPage: true });
    fail = false; await input.press("Enter");
    await expect(page.locator(".library-search-empty")).toContainText("找不到符合「中文」");
    await expect(search).toHaveValue("中文"); await expect(search).toBeFocused(); expect(reads).toBe(1); expect(writes).toBe(2);
    await search.fill("第五章"); await expect(page.getByRole("article").getByRole("heading")).toHaveText("作業系統 第五章");
  });

  test(`published material confirmation deletes exactly once at ${viewport.width}px`, async ({ page }) => {
    await page.setViewportSize(viewport); await setup(page);
    let items = [item(1, "succeeded", true, true), item(2, "no-run")], deletes = 0;
    let release!: () => void; const pending = new Promise<void>(resolve => { release = resolve; });
    await page.route("**/v1/materials", route => route.fulfill({ json: { schema: "material-library/v2", materials: items } }));
    await page.route(`**/v1/materials/${items[0].material_id}`, async route => {
      expect(route.request().method()).toBe("DELETE"); deletes++; await pending;
      const removed = items[0]; items = items.slice(1);
      return route.fulfill({ status: 202, json: { schema: "material-discard/v1", material_id: removed.material_id, state: "removed" } });
    });
    await page.goto("/materials");
    const card = page.getByRole("article").first();
    const opener = card.getByRole("button", { name: /^管理「/ });
    await opener.click(); await card.getByRole("button", { name: "刪除教材", exact: true }).click();
    const confirm = card.getByRole("form", { name: "刪除教材確認" });
    await expect(confirm).toContainText("原始 PDF、知識地圖、學習進度、題目與作答紀錄");
    await expect(confirm.getByRole("button", { name: "取消", exact: true })).toBeFocused();
    await confirm.getByRole("button", { name: "取消", exact: true }).click(); expect(deletes).toBe(0);
    await opener.click(); await card.getByRole("button", { name: "刪除教材", exact: true }).click();
    await page.screenshot({ path: `/tmp/studydy-management-${viewport.width}-delete-confirm.png`, fullPage: true });
    await confirm.getByRole("button", { name: "確認刪除", exact: true }).evaluate(el => { (el as HTMLButtonElement).click(); (el as HTMLButtonElement).click(); });
    await expect.poll(() => deletes).toBe(1); await expect(confirm.getByRole("button", { name: "正在刪除…", exact: true })).toBeDisabled();
    release(); await expect(page.getByRole("article")).toHaveCount(1); expect(deletes).toBe(1);
  });
}

test("removing publishing material disables all actions and polls until absent", async ({ page }) => {
  await setup(page);
  let items = [item(1, "running", true, true), item(2, "no-run")], reads = 0;
  items[0].latest_attempt!.progress_stage = "publishing";
  const target = items[0];
  await page.route("**/v1/materials", route => { reads++; return route.fulfill({ json: { schema: "material-library/v2", materials: items } }); });
  await page.route(`**/v1/materials/${target.material_id}`, route => route.fulfill({ status: 202, json: { schema: "material-discard/v1", material_id: target.material_id, state: "removing" } }));
  await page.goto("/materials"); await page.getByRole("searchbox").fill("running");
  const card = page.getByRole("article");
  await card.getByRole("button", { name: /^管理「/ }).click(); await card.getByRole("button", { name: "刪除教材", exact: true }).click();
  await expect(card.getByRole("form")).toContainText("目前處理會先安全停止");
  await card.getByRole("button", { name: "確認刪除", exact: true }).click();
  await expect(card.getByRole("status")).toHaveText("正在刪除…");
  await expect(card.getByRole("button", { name: "查看處理狀態", exact: true })).toBeDisabled();
  await expect(card.locator(".material-source-link")).not.toHaveAttribute("href");
  await expect(card.locator(".material-management-menu")).toHaveCount(0);
  const previous = reads; items = items.slice(1); await page.clock.runFor(3000);
  await expect.poll(() => reads).toBeGreaterThan(previous);
  await expect(page.locator(".library-search-empty")).toBeVisible();
  await expect(page.getByRole("searchbox")).toHaveValue("running");
});

test("a pre-rename poll cannot restore the old title into search results", async ({ page }) => {
  await setup(page);
  let target = item(1, "running"); target.display_name = "old title";
  let reads = 0, release!: () => void;
  const pending = new Promise<void>(resolve => { release = resolve; });
  await page.route("**/v1/materials", async route => {
    reads++; const snapshot = structuredClone(target);
    if (reads === 2) await pending;
    return route.fulfill({ json: { schema: "material-library/v2", materials: [snapshot] } });
  });
  await page.route(`**/v1/materials/${target.material_id}/rename`, route => {
    target = { ...target, display_name: "new title" }; return route.fulfill({ json: target });
  });
  await page.goto("/materials"); await page.getByRole("searchbox").fill("old");
  await page.clock.runFor(3000); await expect.poll(() => reads).toBe(2);
  await page.getByRole("button", { name: /^管理「/ }).click(); await page.getByRole("button", { name: "重新命名", exact: true }).click();
  await page.getByRole("textbox", { name: "教材名稱", exact: true }).fill("new title");
  await page.getByRole("button", { name: "儲存", exact: true }).click();
  await expect(page.locator(".library-search-empty")).toBeVisible();
  const response = page.waitForResponse("**/v1/materials"); release(); await response;
  await expect(page.getByRole("article")).toHaveCount(0);
  await expect(page.getByRole("searchbox")).toHaveValue("old");
});
