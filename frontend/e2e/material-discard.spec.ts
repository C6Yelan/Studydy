import { expect, test, type Page } from "@playwright/test";
import type { MaterialLibraryItem } from "../src/api/contracts";

const id = (value: number) => `10000000-0000-4000-8000-${String(value).padStart(12, "0")}`;
const revision = `knowledge-structure:sha256:${"a".repeat(64)}`;
const created = "2026-09-12T12:00:00Z";
const longName = "資料結構補充講義_" + "VeryLongUnbrokenMaterialFilename".repeat(4) + ".pdf";
function item(index: number, state: "no-run" | "failed" | "cancelled" | "running" | "pending" | "succeeded" | "partial", map = false, study = false): MaterialLibraryItem {
  const success = state === "succeeded" || state === "partial";
  return { schema: "material-library-item/v3", material_id: id(index), source_artifact_id: id(index+100), display_name: index === 4 ? longName : `資料結構 第 ${index} 章.pdf`, size_bytes: 1024, created_at: created,
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
      await menu.click(); await card.getByRole("button", { name: "刪除教材", exact: true }).click();
      const confirmation = card.getByRole("form", { name: "刪除教材確認" });
      const text = await confirmation.innerText();
      expect(text.includes("知識地圖")).toBe(items[index].available_structures.length > 0);
      expect(text.includes("學習紀錄、題目與作答")).toBe(items[index].study_sessions.length > 0);
      expect(text.includes("會先停止")).toBe(["pending", "running"].includes(items[index].latest_attempt?.status ?? ""));
      expect(text).not.toMatch(/處理紀錄|轉換產物|學習進度/);
      await page.keyboard.press("Escape"); await expect(menu).toBeFocused();
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
    await expect(confirm).toContainText("這份教材、知識地圖，以及相關的學習紀錄、題目與作答");
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
  await page.goto("/materials"); await page.getByRole("searchbox").fill(target.display_name);
  const card = page.getByRole("article");
  await card.getByRole("button", { name: /^管理「/ }).click(); await card.getByRole("button", { name: "刪除教材", exact: true }).click();
  await expect(card.getByRole("form")).toContainText("目前的教材更新會先停止");
  await card.getByRole("button", { name: "確認刪除", exact: true }).click();
  await expect(card.getByRole("status")).toHaveText("正在刪除…");
  await expect(card.getByRole("button", { name: "查看進度", exact: true })).toBeDisabled();
  await expect(card.getByRole("link")).toHaveCount(0);
  await expect(card.locator(".material-management-menu")).toHaveCount(0);
  const previous = reads; items = items.slice(1); await page.clock.runFor(3000);
  await expect.poll(() => reads).toBeGreaterThan(previous);
  await expect(page.locator(".library-search-empty")).toBeVisible();
  await expect(page.getByRole("searchbox")).toHaveValue(target.display_name);
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

for (const touch of [false, true]) test.describe(`management dismissal with ${touch ? "touch" : "mouse"}`, () => {
  test.use({ viewport: touch ? { width: 390, height: 844 } : { width: 1536, height: 1024 }, hasTouch: touch });
  test("outside interaction, exclusive opening and menu actions preserve focus", async ({ page }) => {
    await setup(page);
    let firstItem = item(1, "succeeded", true, true);
    const secondItem = item(2, "no-run");
    let renames = 0, deletes = 0;
    await page.route("**/v1/materials", route => route.fulfill({ json: { schema: "material-library/v2", materials: [firstItem, secondItem] } }));
    await page.route(`**/v1/materials/${firstItem.material_id}/rename`, route => {
      renames++;
      expect(route.request().postDataJSON()).toEqual({ schema: "material-rename/v1", display_name: "資料結構導論" });
      firstItem = { ...firstItem, display_name: "資料結構導論" };
      return route.fulfill({ json: firstItem });
    });
    await page.route(`**/v1/materials/${firstItem.material_id}`, route => {
      if (route.request().method() === "DELETE") deletes++;
      return route.fulfill({ json: firstItem });
    });
    await page.route(`**/v2/materials/${firstItem.material_id}/sources`, route => route.fulfill({ json: { schema: "material-sources/v1", material_id: firstItem.material_id, sources: [] } }));
    await page.goto("/materials");
    const cards = page.getByRole("article"), first = cards.nth(0), second = cards.nth(1);
    const menu = first.locator("details"), otherMenu = second.locator("details");
    const trigger = menu.locator("summary"), otherTrigger = otherMenu.locator("summary");
    const activate = async (locator: ReturnType<Page["locator"]>, position?: { x: number; y: number }) => {
      if (touch) await locator.tap({ position }); else await locator.click({ position });
    };
    await expect(cards).toHaveCount(2);
    const before = await first.boundingBox();
    const closed = async () => {
      await expect(menu).toHaveJSProperty("open", false);
      await expect(menu.locator("button").first()).toBeHidden();
    };
    for (const outside of [
      { locator: page.locator(".app-main"), position: { x: 5, y: 5 } },
      { locator: first.getByRole("heading") },
      { locator: second.getByRole("heading") },
      { locator: page.getByRole("searchbox") },
      { locator: page.locator(".app-sidebar"), position: { x: 2, y: 2 } },
      { locator: page.locator(".app-header"), position: { x: 200, y: 5 } },
    ]) {
      await activate(trigger);
      await expect(menu).toHaveJSProperty("open", true);
      await expect(menu.getByRole("button", { name: "管理教材", exact: true })).toBeVisible();
      await activate(outside.locator, outside.position);
      await closed();
    }
    await activate(trigger); await activate(otherTrigger);
    await closed(); await expect(otherMenu).toHaveJSProperty("open", true);
    await expect(page.locator(".material-management-menu[open]")).toHaveCount(1);
    await activate(trigger);
    await expect(otherMenu).toHaveJSProperty("open", false);
    await expect(menu).toHaveJSProperty("open", true);
    // 選單內移動焦點不關閉，Escape 回到原 trigger。
    await menu.getByRole("button", { name: "重新命名", exact: true }).focus();
    await expect(menu).toHaveJSProperty("open", true);
    await page.keyboard.press("Escape"); await closed(); await expect(trigger).toBeFocused();
    await page.keyboard.press("Enter"); await expect(menu).toHaveJSProperty("open", true);
    await page.getByRole("searchbox").focus(); await closed();
    await expect(page.getByRole("searchbox")).toBeFocused();
    await trigger.scrollIntoViewIfNeeded();
    const after = await first.boundingBox();
    expect(after!.width).toBe(before!.width); expect(after!.height).toBe(before!.height);

    await activate(trigger);
    await activate(menu.getByRole("button", { name: "重新命名", exact: true }));
    await closed();
    const input = first.getByRole("textbox", { name: "教材名稱", exact: true });
    await expect(input).toBeFocused(); await input.fill("資料結構導論");
    await activate(first.getByRole("button", { name: "儲存", exact: true }));
    await expect(first.getByRole("heading")).toHaveText("資料結構導論");
    await expect(trigger).toBeFocused(); expect(renames).toBe(1);
    await activate(trigger);
    await activate(menu.getByRole("button", { name: "刪除教材", exact: true }));
    await closed();
    await expect(first.getByRole("form", { name: "刪除教材確認" })).toBeVisible();
    await expect(first.getByRole("button", { name: "取消", exact: true })).toBeFocused();
    await page.keyboard.press("Escape"); await expect(trigger).toBeFocused(); expect(deletes).toBe(0);
    await activate(trigger);
    await activate(menu.getByRole("button", { name: "管理教材", exact: true }));
    await expect(page).toHaveURL(new RegExp(`/materials/${firstItem.material_id}/sources$`));
    await expect(page.locator(".material-management-menu[open]")).toHaveCount(0);
  });
});
