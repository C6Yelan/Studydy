import { expect, test, type Page } from "@playwright/test";
import type { MaterialLibraryItem, MaterialAttemptView, StudySessionLink } from "../src/api/contracts";

const materialId = "11111111-1111-4111-8111-111111111111";
const publishedRun = "22222222-2222-4222-8222-222222222222";
const latestRun = "33333333-3333-4333-8333-333333333333";
const studyId = "44444444-4444-4444-8444-444444444444";
const revision = `knowledge-structure:sha256:${"a".repeat(64)}`;
const mapPath = `/materials/${materialId}/runs/${publishedRun}/knowledge-structures/${encodeURIComponent(revision)}`;
const base: MaterialLibraryItem = { schema: "material-library-item/v2", material_id: materialId, source_artifact_id: materialId,
  display_name: "資料結構講義.pdf", size_bytes: 1200, created_at: "2026-09-12T00:00:00Z", latest_attempt: null, available_structures: [], study_sessions: [] };
const run: MaterialAttemptView = { cancel_requested_at: null, run_id: latestRun, status: "running", progress_stage: "semantics", completed_pages: 2, total_pages: 8,
  error_code: null, created_at: "2026-09-12T01:00:00Z" };
const published = { run_id: publishedRun, knowledge_structure_revision: revision, status: "succeeded" as const, created_at: "2026-09-12T00:30:00Z" };
const active: StudySessionLink = { study_session_id: studyId, run_id: publishedRun, knowledge_structure_revision: revision,
  status: "active", started_at: "2026-09-12T02:00:00Z", current_concept_id: null };
const states = ["empty", "uploaded", "pending", "running", "failed", "failed-map", "map", "partial", "active", "completed", "multiple", "long-name", "loading", "failure"] as const;
type State = typeof states[number];
function material(state: State): MaterialLibraryItem {
  const item = structuredClone(base);
  if (["pending", "running", "failed", "failed-map"].includes(state)) item.latest_attempt = { ...run,
    status: state.startsWith("failed") ? "failed" : state === "pending" ? "pending" : "running",
    progress_stage: state === "pending" ? "queued" : "semantics", error_code: state.startsWith("failed") ? "STORAGE_UNAVAILABLE" : null };
  if (["map", "partial", "failed-map", "active", "completed", "long-name"].includes(state)) {
    item.available_structures = [{ ...published, status: state === "partial" ? "partial" : "succeeded" }];
    item.latest_attempt ??= { ...run, run_id: publishedRun, status: state === "partial" ? "partial" : "succeeded", progress_stage: "completed", completed_pages: 8 };
  }
  if (["active", "completed", "long-name"].includes(state)) item.study_sessions = [{ ...active, status: state === "completed" ? "completed" : "active" }];
  if (state === "long-name") item.display_name = "資料結構與演算法_" + "VeryLongMaterialFilename".repeat(6) + ".pdf";
  return item;
}
function materials(state: State): MaterialLibraryItem[] {
  if (["empty", "loading", "failure"].includes(state)) return [];
  if (state === "multiple") return (["uploaded", "running", "failed", "failed-map", "active", "completed"] as const).map((value, index) => ({
    ...material(value), material_id: `${String(index + 1).padStart(8, "0")}-1111-4111-8111-111111111111`,
    display_name: ["尚未處理的筆記.pdf", "本週課程：遞迴與樹狀結構的概念整理和練習題.pdf", "陣列.pdf", "堆疊講義.pdf", "佇列與練習.pdf", "已完成的學習筆記.pdf"][index],
  }));
  return [material(state)];
}
async function signedIn(page: Page) {
  await page.route("**/v1/session/refresh", route => route.fulfill({ status: 204 }));
  await page.route("**/v1/session", route => route.fulfill({ json: { schema: "learner-identity/v1", learner_id: materialId } }));
}

for (const viewport of [{ width: 1920, height: 1080 }, { width: 1536, height: 1024 }, { width: 1366, height: 768 }, { width: 390, height: 844 }]) {
  for (const state of states) {
    test(`materials collection ${state} at ${viewport.width}px`, async ({ page }) => {
      await page.setViewportSize(viewport); await signedIn(page);
      let fail = state === "failure";
      let release!: () => void;
      const pending = new Promise<void>(resolve => { release = resolve; });
      const items = materials(state);
      await page.route("**/v1/materials", async route => {
        if (state === "loading") await pending;
        if (fail) return route.fulfill({ status: 503, json: { schema: "api-error/v1", request_id: materialId, reason_code: "STORAGE_UNAVAILABLE",
          retryable: true, message: "Request could not be completed." } });
        return route.fulfill({ json: { schema: "material-library/v2", materials: items } });
      });
      await page.goto("/materials");
      const library = page.locator(".material-library.is-collection");
      await expect(library).toBeVisible();
      await expect(library).not.toHaveClass(/is-maps-only/);
      await expect(page.locator(".sidebar-helper")).toHaveCount(0);
      await expect(page.getByRole("button", { name: "教材庫", exact: true })).toHaveAttribute("aria-current", "page");
      const bounds = await library.boundingBox();
      expect(await library.evaluate(element => getComputedStyle(element).maxWidth)).toBe("1260px");
      if (viewport.width >= 1536) expect(bounds!.width).toBeGreaterThan(1018);
      if (state === "loading") await expect(library.locator(".state-view.is-loading")).toHaveAttribute("aria-live", "polite");
      else if (state === "failure") await expect(library.getByRole("alert")).toContainText("無法讀取教材");
      else {
        await expect(library.getByRole("heading", { name: "我的教材", exact: true, level: 1 })).toBeVisible();
        await expect(library.getByRole("button", { name: "重新整理", exact: true })).toHaveCount(0);
        if (state === "empty") {
          await expect(library.locator(".library-header .state-actions")).toHaveCount(0);
          await expect(library.locator(".library-subtitle")).toHaveText("上傳教材後，可在這裡查看處理結果並接續學習。");
          await expect(library.getByRole("heading", { name: "尚未有學習教材", exact: true })).toBeVisible();
          await expect(library.getByRole("button", { name: "上傳教材", exact: true })).toHaveCount(0);
          await expect(library.locator(".primary-button")).toHaveCount(1);
          await expect(library.locator(".library-empty")).toContainText("先上傳第一份 PDF，讓 Studydy 陪你展開學習。");
          if (viewport.width > 600) {
            const empty = await library.locator(".library-empty").boundingBox();
            expect(empty!.height).toBeGreaterThanOrEqual(360); expect(empty!.height).toBeLessThanOrEqual(420);
          }
          await expect.poll(() => library.locator(".library-empty img").evaluate((img: HTMLImageElement) => img.complete && img.naturalWidth > 0)).toBe(true);
        } else {
          await expect(library.locator(".library-subtitle")).toHaveText(`已保存 ${items.length} 份教材，隨時接續你的學習。`);
          await expect(library.getByRole("button", { name: "上傳教材", exact: true })).toHaveClass("primary-button");
          await expect(library.getByRole("article")).toHaveCount(items.length);
          for (const item of items) {
            const card = library.getByRole("article", { name: item.display_name, exact: true });
            const primary = item.study_sessions[0] ? item.study_sessions[0].status === "completed" ? "查看上次學習" : "接續上次學習"
              : item.available_structures.length ? "開啟知識地圖" : item.latest_attempt ? "查看最新處理" : null;
            await expect(card.locator(".primary-button")).toHaveCount(primary ? 1 : 0);
            if (primary) await expect(card.locator(".primary-button")).toHaveText(primary);
            else await expect(card.locator(".library-state")).toHaveText("最新處理：已上傳，尚未開始處理");
            if (item.latest_attempt?.status === "failed") {
              await expect(card.locator(".library-state")).toHaveText("最新處理：處理失敗");
              if (!item.available_structures.length) await expect(card.getByRole("button", { name: "移除教材", exact: true })).toBeVisible();
            }
          }
          if (["pending", "running"].includes(state)) await expect(library.getByRole("article")).toContainText("已完成 2 頁／共 8 頁");
          if (state === "failed-map") await expect(library.getByRole("article")).toContainText("先前已發布的知識地圖仍可開啟");
          if (["map", "partial", "active", "completed", "long-name"].includes(state)) {
            await expect(library.getByRole("button", { name: "開啟知識地圖", exact: true })).toBeVisible();
            await expect(library.getByRole("article")).not.toContainText("最新處理：");
            await expect(library.getByRole("button", { name: "查看最新處理", exact: true })).toHaveCount(0);
          }
          if (state === "multiple" && viewport.width > 1200) {
            const rows = await library.getByRole("article").evaluateAll(elements => elements.map(element => { const r = element.getBoundingClientRect(); return { top: r.top, height: r.height }; }));
            expect(rows[0].top).toBe(rows[2].top);
            expect(rows[3].top).toBeGreaterThan(rows[0].top);
            expect(new Set(rows.slice(0, 3).map(row => row.height)).size).toBe(1);
          }
        }
      }
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(viewport.width);
      expect(await library.locator("button, h1, h2, p").evaluateAll(elements => elements.filter(element => element.scrollWidth > element.clientWidth + 1).map(element => element.tagName))).toEqual([]);
      await page.screenshot({ path: `/tmp/studydy-material-collection/${viewport.width}-${state}.png`, fullPage: true });
      if (state === "loading" || state === "failure") {
        if (state === "failure") {
          const failedAgain = page.waitForResponse(response => response.url().endsWith("/v1/materials") && response.status() === 503);
          await library.getByRole("button", { name: "重新讀取", exact: true }).click(); await failedAgain;
          await expect(library.getByRole("alert")).toContainText("無法讀取教材");
        }
        fail = false; release();
        if (state === "failure") await library.getByRole("button", { name: "重新讀取", exact: true }).click();
        await expect(library.getByRole("heading", { name: "尚未有學習教材", exact: true })).toBeVisible();
        expect((await library.boundingBox())!.width).toBe(bounds!.width);
      }
      if (state === "empty") {
        const upload = library.getByRole("button", { name: "上傳第一份教材", exact: true });
        await upload.focus(); await page.keyboard.press("Tab"); await page.keyboard.press("Shift+Tab");
        await expect(upload).toBeFocused();
        expect(await upload.evaluate(element => getComputedStyle(element).outlineStyle)).toBe("solid");
        await page.keyboard.press("Enter"); await expect(page).toHaveURL(/\/upload$/);
      } else if (!["multiple", "loading", "failure"].includes(state)) {
        const item = items[0];
        const action = library.getByRole("article").locator(".primary-button");
        const expected = item.study_sessions[0] ? `${mapPath}/study-sessions/${studyId}` : item.available_structures.length ? mapPath
          : item.latest_attempt ? `/materials/${materialId}/runs/${latestRun}` : `/materials/${materialId}`;
        if (await action.count()) await action.click(); else await library.getByRole("button", { name: item.display_name, exact: true }).click();
        expect(new URL(page.url()).pathname).toBe(expected);
      }
    });
  }
}

test("materials polling updates pending/running, stops at terminal and cancels on unmount", async ({ page }) => {
  await page.clock.install(); await page.clock.pauseAt(new Date()); await signedIn(page);
  let reads = 0;
  let running = false;
  await page.route("**/v1/materials", route => {
    reads++;
    return route.fulfill({ json: { schema: "material-library/v2", materials: [material(running ? "running" : reads === 1 ? "pending" : reads === 2 ? "running" : "map")] } });
  });
  await page.goto("/materials");
  await expect(page.getByRole("article")).toBeVisible();
  await page.clock.runFor(2999); expect(reads).toBe(1);
  const refresh = page.waitForResponse("**/v1/materials"); await page.clock.runFor(1); await refresh;
  await expect(page.getByRole("article")).toContainText("正在分析完整教材"); expect(reads).toBe(2);
  const terminal = page.waitForResponse("**/v1/materials"); await page.clock.runFor(3000); await terminal;
  await expect(page.getByRole("article").locator(".primary-button")).toHaveText("開啟知識地圖"); expect(reads).toBe(3);
  await expect(page.getByRole("article")).not.toContainText("最新處理：");
  await expect(page.getByRole("button", { name: "查看最新處理", exact: true })).toHaveCount(0);
  await page.clock.runFor(9001); expect(reads).toBe(3);
  running = true;
  await page.goto("/materials");
  await expect(page.getByRole("article")).toContainText("正在分析完整教材"); expect(reads).toBe(4);
  await page.getByRole("button", { name: "上傳教材", exact: true }).click();
  await expect(page).toHaveURL(/\/upload$/);
  await page.clock.runFor(9001); expect(reads).toBe(4);
});

test("no-safe studies and unpublished completed runs keep collection-only action priority", async ({ page }) => {
  await signedIn(page);
  let item = { ...material("active"), study_sessions: [{ ...active, status: "no_safe" as const }] } as MaterialLibraryItem;
  await page.route("**/v1/materials", route => route.fulfill({ json: { schema: "material-library/v2", materials: [item] } }));
  await page.route(`**/v1/materials/${materialId}`, route => route.fulfill({ json: item }));
  await page.goto("/materials"); await expect(page.getByRole("article").locator(".primary-button")).toHaveText("接續上次學習");
  await expect(page.getByRole("article")).not.toContainText("最新處理：");
  await expect(page.getByRole("button", { name: "開啟知識地圖", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "查看最新處理", exact: true })).toHaveCount(0);
  for (const status of ["succeeded", "partial"] as const) {
    item = { ...base, latest_attempt: { cancel_requested_at: null, ...run, status, progress_stage: "completed", completed_pages: 8 } };
    await page.goto("/materials"); await expect(page.getByRole("article").locator(".primary-button")).toHaveText("查看最新處理");
    await expect(page.locator(".library-state")).toContainText("最新處理：");
    await page.goto(`/materials/${materialId}`);
    await expect(page.locator(".material-library")).not.toHaveClass(/is-collection/);
    await expect(page.locator(".library-header button")).toHaveText(["返回教材庫", "上傳教材"]);
    const reloaded = page.waitForResponse(`**/v1/materials/${materialId}`);
    await page.getByRole("button", { name: "重新整理狀態", exact: true }).click(); await reloaded;
    await expect(page.getByRole("button", { name: "查看處理詳情", exact: true })).toHaveClass("primary-button");
    await expect(page.getByRole("region", { name: "最新處理" })).toBeVisible();
    await expect(page.locator(".sidebar-helper")).toBeVisible();
    await page.screenshot({ path: `/tmp/studydy-material-collection/detail-${status}.png`, fullPage: true });
  }
});

for (const status of ["pending", "running", "failed", "cancelled", "succeeded", "partial"] as const) {
  test(`latest ${status} with only an older map retains its processing action`, async ({ page }) => {
    await signedIn(page);
    const completed = status === "succeeded" || status === "partial";
    const item = { ...base, latest_attempt: { ...run, status,
      progress_stage: completed ? "completed" : status === "pending" ? "queued" : "semantics",
      completed_pages: completed ? 8 : 2, error_code: status === "failed" ? "STORAGE_UNAVAILABLE" : null,
      cancel_requested_at: status === "cancelled" ? run.created_at : null }, available_structures: [published] };
    await page.route("**/v1/materials", route => route.fulfill({ json: { schema: "material-library/v2", materials: [item] } }));
    await page.goto("/materials");
    const card = page.getByRole("article");
    await expect(card.locator(".library-state")).toContainText("最新處理：");
    if (status === "failed" || status === "cancelled") await expect(card.locator(".library-state")).toHaveText(status === "failed" ? "最新處理：處理失敗" : "最新處理：已取消處理");
    if (status === "pending" || status === "running") await expect(card).toContainText("已完成 2 頁／共 8 頁");
    await expect(card.getByRole("button", { name: "開啟知識地圖", exact: true })).toHaveClass("primary-button");
    await expect(card.getByRole("button", { name: "查看最新處理", exact: true })).toHaveClass("secondary-button");
    await card.getByRole("button", { name: "查看最新處理", exact: true }).click();
    expect(new URL(page.url()).pathname).toBe(`/materials/${materialId}/runs/${latestRun}`);
  });
}

for (const status of ["succeeded", "partial"] as const) {
  test(`${status} matching a later published entry hides collection action but preserves detail history`, async ({ page }) => {
    await signedIn(page);
    const item = { ...base, latest_attempt: { ...run, status, progress_stage: "completed", completed_pages: 8 }, available_structures: [published,
      { ...published, run_id: latestRun, status, knowledge_structure_revision: `knowledge-structure:sha256:${"b".repeat(64)}` }] };
    await page.route("**/v1/materials", route => route.fulfill({ json: { schema: "material-library/v2", materials: [item] } }));
    await page.route(`**/v1/materials/${materialId}`, route => route.fulfill({ json: item }));
    await page.goto("/materials");
    await expect(page.getByRole("button", { name: "開啟知識地圖", exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "查看最新處理", exact: true })).toHaveCount(0);
    await expect(page.getByRole("article")).not.toContainText("最新處理：");
    await page.getByRole("button", { name: item.display_name, exact: true }).click();
    await expect(page.getByRole("heading", { name: "教材詳情", exact: true })).toBeVisible();
    await expect(page.getByRole("region", { name: "最新處理" })).toBeVisible();
    await expect(page.getByRole("region", { name: "已發布版本" }).getByRole("button")).toHaveCount(2);
    await expect(page.getByRole("link", { name: "開啟原始 PDF", exact: true })).toBeVisible();
    await page.getByRole("button", { name: "查看處理詳情", exact: true }).click();
    expect(new URL(page.url()).pathname).toBe(`/materials/${materialId}/runs/${latestRun}`);
  });
}

for (const viewport of [{ width: 1536, height: 1024 }, { width: 1366, height: 768 }, { width: 1024, height: 768 }, { width: 390, height: 844 }]) {
  for (const state of ["uploaded", "pending", "running", "failed", "failed-map", "cancelled", "map", "partial", "active", "completed", "records", "long-name"] as const) {
    test(`material detail ${state} at ${viewport.width}px has one contextual action and compact sections`, async ({ page }) => {
      await page.setViewportSize(viewport); await signedIn(page);
      const item = material(state === "cancelled" ? "failed" : state === "records" ? "active" : state);
      if (state === "cancelled") Object.assign(item.latest_attempt!, { status: "cancelled", cancel_requested_at: run.created_at, error_code: null });
      if (state === "records") {
        item.study_sessions.push({ ...active, study_session_id: "55555555-5555-4555-8555-555555555555", status: "completed" });
        item.available_structures.push({ ...published, run_id: latestRun, knowledge_structure_revision: `knowledge-structure:sha256:${"b".repeat(64)}`, status: "partial" });
      }
      await page.route(`**/v1/materials/${materialId}`, route => route.fulfill({ json: item }));
      await page.goto(`/materials/${materialId}`);
      const card = page.locator(".material-detail-card");
      await expect(card).toBeVisible();
      await expect(page.locator(".library-item")).toHaveCount(0);
      await expect(card.locator(".material-detail-identity h2")).toHaveText(item.display_name);
      await expect(card.locator(".material-detail-identity")).toContainText("KiB");
      await expect(page.locator(".library-header .primary-button")).toHaveCount(0);
      const actions = card.locator(".material-detail-actions");
      const expected = item.study_sessions[0] ? (state === "completed" ? "查看上次學習" : "接續上次學習") : item.available_structures.length ? "開啟知識地圖" : item.latest_attempt ? (["pending", "running"].includes(state) ? "查看處理狀態" : "查看處理詳情") : null;
      await expect(actions.locator(".primary-button")).toHaveCount(expected ? 1 : 0);
      if (expected) await expect(actions.locator(".primary-button")).toHaveText(expected);
      await expect(actions.getByRole("link", { name: "開啟原始 PDF" })).toHaveAttribute("href", `/v1/artifacts/${materialId}`);
      await expect(actions.getByRole("button", { name: "移除教材", exact: true })).toHaveCount(0);
      await expect(card.getByText("目前沒有可開啟的已發布知識地圖。", { exact: true })).toHaveCount(0);
      if (state.startsWith("failed")) {
        await expect(card.getByText("教材分析未能安全完成，沒有發布知識地圖。", { exact: true })).toHaveCount(1);
        if (state === "failed-map") await expect(card).toContainText("先前已發布的知識地圖仍可使用。");
      }
      await expect(card.getByRole("region", { name: "學習紀錄" }).getByRole("listitem")).toHaveCount(item.study_sessions.length);
      await expect(card.getByRole("region", { name: "已發布版本" }).getByRole("listitem")).toHaveCount(item.available_structures.length);
      const removable = ["uploaded", "failed", "cancelled"].includes(state);
      await expect(card.locator(".material-detail-danger")).toHaveCount(removable ? 1 : 0);
      if (removable) {
        await card.getByRole("button", { name: "移除教材", exact: true }).click();
        await expect(card.getByRole("button", { name: "保留教材", exact: true })).toBeFocused();
        await page.keyboard.press("Escape");
        await expect(card.getByRole("button", { name: "移除教材", exact: true })).toBeFocused();
      }
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      expect((await card.boundingBox())!.width).toBeLessThanOrEqual(1100);
      await page.evaluate(() => scrollTo(0, 0));
      await page.screenshot({ path: `/tmp/studydy-material-detail-${viewport.width}-${state}.png`, fullPage: true });
      if (expected) {
        await actions.locator(".primary-button").click();
        const destination = item.study_sessions[0] ? `${mapPath}/study-sessions/${studyId}` : item.available_structures.length ? mapPath : `/materials/${materialId}/runs/${latestRun}`;
        await expect.poll(() => new URL(page.url()).pathname).toBe(destination);
      }
    });
  }
}

test("detail removal retains confirmation and exact delete semantics", async ({ page }) => {
  await signedIn(page);
  const item = material("failed"); let deletes = 0;
  await page.route("**/v1/materials", route => route.fulfill({ json: { schema: "material-library/v2", materials: [] } }));
  await page.route(`**/v1/materials/${materialId}`, route => {
    if (route.request().method() === "DELETE") { deletes++; return route.fulfill({ status: 202, json: { schema: "material-discard/v1", material_id: materialId, state: "removed" } }); }
    return route.fulfill({ json: item });
  });
  await page.goto(`/materials/${materialId}`);
  const danger = page.getByRole("region", { name: "移除教材", exact: true });
  await danger.getByRole("button", { name: "移除教材", exact: true }).click();
  expect(deletes).toBe(0);
  await danger.getByRole("button", { name: "確認移除", exact: true }).click();
  await expect(page).toHaveURL(/\/materials$/);
  expect(deletes).toBe(1);
});

test("detail record and version rows preserve exact older bindings", async ({ page }) => {
  await signedIn(page);
  const oldId = "55555555-5555-4555-8555-555555555555";
  const oldRevision = `knowledge-structure:sha256:${"b".repeat(64)}`;
  const item = material("active");
  item.study_sessions.push({ ...active, study_session_id: oldId, status: "completed", run_id: latestRun, knowledge_structure_revision: oldRevision });
  item.available_structures.push({ ...published, run_id: latestRun, knowledge_structure_revision: oldRevision });
  await page.route(`**/v1/materials/${materialId}`, route => route.fulfill({ json: item }));
  const olderMap = `/materials/${materialId}/runs/${latestRun}/knowledge-structures/${encodeURIComponent(oldRevision)}`;
  for (const [label, expected] of [["開啟版本 1", olderMap], ["開啟學習紀錄 1", `${olderMap}/study-sessions/${oldId}`]]) {
    await page.goto(`/materials/${materialId}`);
    await page.getByRole("button", { name: label, exact: true }).click();
    await expect.poll(() => new URL(page.url()).pathname).toBe(expected);
  }
});
