import { expect, test, type Page } from "@playwright/test";
import type { MaterialLibraryItem } from "../src/api/contracts";

const materialId = "11111111-1111-4111-8111-111111111111";
const publishedRun = "22222222-2222-4222-8222-222222222222";
const latestRun = "33333333-3333-4333-8333-333333333333";
const studyId = "44444444-4444-4444-8444-444444444444";
const revision = `knowledge-structure:sha256:${"a".repeat(64)}`;
const longName = "資料結構與演算法_" + "VeryLongUnbrokenMaterialFilename".repeat(5) + ".pdf";
const published: MaterialLibraryItem = {
  schema: "material-library-item/v2", material_id: materialId, source_artifact_id: materialId,
  display_name: "資料結構講義.pdf", size_bytes: 1200, created_at: "2026-09-12T00:00:00Z",
  latest_attempt: { cancel_requested_at: null, run_id: latestRun, status: "failed", progress_stage: "semantics", completed_pages: 0,
    total_pages: 2, error_code: "STORAGE_UNAVAILABLE", created_at: "2026-09-12T02:00:00Z" },
  available_structures: [{ run_id: publishedRun, knowledge_structure_revision: revision, status: "succeeded", created_at: "2026-09-12T01:00:00Z" }],
  study_sessions: [],
};
const active = { study_session_id: studyId, run_id: publishedRun, knowledge_structure_revision: revision,
  status: "active" as const, started_at: "2026-09-12T01:30:00Z", current_concept_id: null };
const withStudy: MaterialLibraryItem = { ...published, study_sessions: [active] };
const unpublished: MaterialLibraryItem = { ...published, available_structures: [], latest_attempt: null };
const mapPath = `/materials/${materialId}/runs/${publishedRun}/knowledge-structures/${encodeURIComponent(revision)}`;

async function session(page: Page) {
  await page.route("**/v1/session/refresh", route => route.fulfill({ status: 204 }));
  await page.route("**/v1/session", route => route.fulfill({ json: { schema: "learner-identity/v1", learner_id: materialId } }));
}

const cases = ["empty", "unpublished", "one", "multiple", "study", "loading", "failure", "long-name"] as const;
function itemsFor(state: typeof cases[number]): MaterialLibraryItem[] {
  if (state === "empty" || state === "loading" || state === "failure") return [];
  if (state === "unpublished") return [unpublished];
  if (state === "study") return [withStudy];
  if (state === "long-name") return [{ ...withStudy, display_name: longName }];
  if (state === "multiple") return [published, { ...withStudy, material_id: studyId, display_name: "遞迴與樹狀結構：課程筆記與複習練習.pdf" },
    { ...published, material_id: latestRun, display_name: "堆疊.pdf", latest_attempt: null }, unpublished];
  return [published];
}

for (const viewport of [{ width: 1920, height: 1080 }, { width: 1536, height: 1024 }, { width: 1366, height: 768 }, { width: 390, height: 844 }]) {
  for (const state of cases) {
    test(`map collection ${state} at ${viewport.width}px`, async ({ page }) => {
      await page.setViewportSize(viewport);
      await session(page);
      let fail = state === "failure";
      let release!: () => void;
      const pending = new Promise<void>(resolve => { release = resolve; });
      await page.route("**/v1/materials", async route => {
        if (state === "loading") await pending;
        if (fail) return route.fulfill({ status: 503, json: { schema: "api-error/v1", request_id: materialId,
          reason_code: "STORAGE_UNAVAILABLE", retryable: true, message: "Request could not be completed." } });
        return route.fulfill({ json: { schema: "material-library/v2", materials: itemsFor(state) } });
      });
      await page.goto("/knowledge-maps");
      const library = page.locator(".material-library.is-maps-only");
      await expect(library).toBeVisible();
      await expect(page.locator(".sidebar-helper")).toHaveCount(0);
      await expect(page.getByRole("button", { name: "知識地圖", exact: true })).toHaveAttribute("aria-current", "page");
      const width = (await library.boundingBox())!.width;
      if (viewport.width >= 1536) expect(width).toBeGreaterThan(1018);
      expect(width).toBeLessThanOrEqual(1260);
      if (state === "loading") {
        await expect(library.getByRole("heading", { name: "正在讀取教材庫", exact: true })).toBeVisible();
        await expect(library.locator(".state-view")).toHaveAttribute("aria-live", "polite");
      } else if (state === "failure") {
        await expect(library.getByRole("alert")).toContainText("無法讀取教材");
        await expect(library.getByRole("button", { name: "重新讀取", exact: true })).toBeVisible();
      } else {
        await expect(library.getByRole("heading", { name: "知識地圖", exact: true, level: 1 })).toBeVisible();
        await expect(library.getByRole("button", { name: "重新整理", exact: true })).toHaveCount(0);
        await expect(library.locator(".library-subtitle")).toHaveText("從已發布的教材地圖開始探索。");
        if (state === "empty" || state === "unpublished") {
          await expect(library.locator(".library-header .state-actions")).toHaveCount(0);
          const empty = library.getByRole("region", { name: "知識地圖引導" });
          await expect(empty.getByRole("heading", { level: 2 })).toHaveText(state === "empty" ? "尚未建立知識地圖" : "尚無可開啟的知識地圖");
          await expect(library.locator(".primary-button")).toHaveCount(1);
          await expect(page.getByRole("button", { name: "上傳教材", exact: true })).toHaveCount(0);
          if (state === "unpublished") {
            await expect(empty).toContainText("已有教材，但目前還沒有已發布的知識地圖");
            await expect(page.getByRole("button", { name: "上傳第一份教材", exact: true })).toHaveCount(0);
          }
          if (viewport.width > 600) {
            const bounds = await empty.boundingBox();
            expect(bounds!.height).toBeGreaterThanOrEqual(360);
            expect(bounds!.height).toBeLessThanOrEqual(420);
          }
          await expect.poll(() => empty.locator("img").evaluate((img: HTMLImageElement) => img.complete && img.naturalWidth > 0)).toBe(true);
        } else {
          await expect(library.locator(".library-header button")).toHaveText(["上傳教材"]);
          const cards = library.getByRole("article");
          await expect(cards).toHaveCount(state === "multiple" ? 3 : 1);
          await expect(cards.first()).toContainText("先前已發布的知識地圖仍可開啟");
          await expect(cards.first().getByRole("button", { name: "開啟知識地圖", exact: true })).toHaveClass("primary-button");
          await expect(cards.first().getByRole("button", { name: "查看最新處理", exact: true })).toHaveClass("secondary-button");
          if (state === "study" || state === "long-name") await expect(cards.first().getByRole("button", { name: "接續上次學習", exact: true })).toHaveClass("secondary-button");
          if (state === "multiple") {
            const rows = await cards.evaluateAll(elements => elements.map(element => { const rect = element.getBoundingClientRect(); return { y: rect.y, height: rect.height }; }));
            if (viewport.width > 1200) {
              expect(new Set(rows.map(row => row.y)).size).toBe(1);
              expect(new Set(rows.map(row => row.height)).size).toBe(1);
            } else expect(rows[1].y).toBeGreaterThan(rows[0].y);
          }
        }
      }
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(viewport.width);
      expect(await library.locator("button, h1, h2, p").evaluateAll(elements => elements.filter(element => element.scrollWidth > element.clientWidth + 1).map(element => element.tagName))).toEqual([]);
      await page.screenshot({ path: `/tmp/studydy-map-library/${viewport.width}-${state}.png`, fullPage: true });
      if (state === "loading") {
        release();
        await expect(library.getByRole("heading", { name: "尚未建立知識地圖", exact: true })).toBeVisible();
        expect((await library.boundingBox())!.width).toBe(width);
      }
      if (state === "failure") {
        fail = false;
        await library.getByRole("button", { name: "重新讀取", exact: true }).click();
        await expect(library.getByRole("heading", { name: "尚未建立知識地圖", exact: true })).toBeVisible();
        expect((await library.boundingBox())!.width).toBe(width);
      }
      if (state === "empty" || state === "unpublished") {
        const action = library.getByRole("button", { name: state === "empty" ? "上傳第一份教材" : "前往我的教材", exact: true });
        await action.focus(); await page.keyboard.press("Tab"); await page.keyboard.press("Shift+Tab");
        await expect(action).toBeFocused();
        expect(await action.evaluate(element => getComputedStyle(element).outlineStyle)).toBe("solid");
        await page.keyboard.press("Enter");
        await expect(page).toHaveURL(state === "empty" ? /\/upload$/ : /\/materials$/);
      }
    });
  }
}

test("map, study and processing actions preserve distinct bindings; ordinary library/detail keep their hierarchy", async ({ page }) => {
  await session(page);
  await page.route("**/v1/materials", route => route.fulfill({ json: { schema: "material-library/v2", materials: [withStudy] } }));
  await page.route(`**/v1/materials/${materialId}`, route => route.fulfill({ json: withStudy }));
  for (const [name, path] of [["開啟知識地圖", mapPath], ["接續上次學習", `${mapPath}/study-sessions/${studyId}`], ["查看最新處理", `/materials/${materialId}/runs/${latestRun}`]]) {
    await page.goto("/knowledge-maps");
    const cards = page.locator(".library-item");
    await expect(cards.locator(".state-actions button").first()).toHaveText("開啟知識地圖");
    await cards.getByRole("button", { name, exact: true }).click();
    expect(new URL(page.url()).pathname).toBe(path);
  }
  for (const path of ["/materials", `/materials/${materialId}`]) {
    await page.goto(path);
    await expect(page.locator(".material-library")).not.toHaveClass(/is-maps-only/);
    await expect(page.locator(".sidebar-helper")).toHaveCount(path === "/materials" ? 0 : 1);
    expect(await page.locator(".material-library").evaluate(element => getComputedStyle(element).maxWidth)).toBe(path === "/materials" ? "1260px" : "1018px");
    const cards = page.locator(path === "/materials" ? ".library-item" : ".material-detail-card");
    await expect(cards.getByRole("button", { name: "接續上次學習", exact: true })).toHaveClass("primary-button");
    await expect(cards.getByRole("button", { name: "開啟知識地圖", exact: true })).toHaveClass("secondary-button");
    if (path !== "/materials") {
      await expect(cards.getByRole("region", { name: "已發布版本" })).toBeVisible();
      await expect(cards.getByRole("region", { name: "學習紀錄" })).toBeVisible();
      await expect(cards.getByRole("link", { name: "開啟原始 PDF", exact: true })).toHaveAttribute("href", `/v1/artifacts/${materialId}`);
    }
  }
});

test("unpublished processing states remain truthful and pending polling still discovers a published map", async ({ page }) => {
  await page.clock.install(); await page.clock.pauseAt(new Date());
  await session(page);
  let reads = 0;
  await page.route("**/v1/materials", route => {
    reads++;
    return route.fulfill({ json: { schema: "material-library/v2", materials: reads === 1 ? [
      unpublished,
      { ...unpublished, material_id: studyId, latest_attempt: { cancel_requested_at: null, ...published.latest_attempt!, status: "running", progress_stage: "evidence", error_code: null } },
      { ...unpublished, material_id: latestRun, latest_attempt: published.latest_attempt },
    ] : [published] } });
  });
  await page.goto("/knowledge-maps");
  await expect(page.getByRole("heading", { name: "尚無可開啟的知識地圖", exact: true })).toBeVisible();
  await expect(page.locator(".library-empty")).not.toContainText(/正在生成|尚未有學習教材/);
  await page.clock.runFor(3001);
  await expect(page.getByRole("article")).toHaveCount(1);
  await expect(page.getByRole("button", { name: "開啟知識地圖", exact: true })).toHaveClass("primary-button");
  expect(reads).toBe(2);
});

for (const status of ["succeeded", "partial"] as const) {
  for (const studyStatus of [null, "active", "no_safe", "completed"] as const) {
    test(`map collection ${status} with ${studyStatus ?? "no"} study hides completed processing action`, async ({ page }) => {
      await page.setViewportSize({ width: 1536, height: 1024 });
      await session(page);
      const item: MaterialLibraryItem = { ...published,
        latest_attempt: { ...published.latest_attempt!, run_id: publishedRun, status, error_code: null, progress_stage: "completed", completed_pages: 2 },
        available_structures: [{ ...published.available_structures[0], status }],
        study_sessions: studyStatus ? [{ ...active, status: studyStatus }] : [] };
      await page.route("**/v1/materials", route => route.fulfill({ json: { schema: "material-library/v2", materials: [item] } }));
      await page.goto("/knowledge-maps");
      const card = page.getByRole("article");
      await expect(card.locator(".state-actions button").first()).toHaveText("開啟知識地圖");
      await expect(card.getByRole("button", { name: "開啟知識地圖", exact: true })).toHaveClass("primary-button");
      await expect(card.getByRole("button", { name: "查看最新處理", exact: true })).toHaveCount(0);
      await expect(card).not.toContainText("最新處理：");
      if (studyStatus) await expect(card.getByRole("button", { name: studyStatus === "completed" ? "查看上次學習" : "接續上次學習", exact: true })).toHaveClass("secondary-button");
      await page.screenshot({ path: `/tmp/studydy-map-library/completed-${status}-${studyStatus ?? "none"}.png`, fullPage: true });
      await page.goto("/materials");
      await expect(card.getByRole("button", { name: "開啟知識地圖", exact: true })).toHaveClass(studyStatus ? "secondary-button" : "primary-button");
      await expect(card).not.toContainText("最新處理：");
      await expect(card.getByRole("button", { name: "查看最新處理", exact: true })).toHaveCount(0);
      if (studyStatus) await expect(card.locator(".primary-button")).toHaveText(studyStatus === "completed" ? "查看上次學習" : "接續上次學習");
    });
  }
}

for (const status of ["pending", "running", "failed", "cancelled", "succeeded", "partial"] as const) {
  test(`map collection retains latest ${status} action when only an older map exists`, async ({ page }) => {
    await session(page);
    const completed = status === "succeeded" || status === "partial";
    const item = { ...published, latest_attempt: { ...published.latest_attempt!, status,
      progress_stage: completed ? "completed" : status === "pending" ? "queued" : "semantics",
      completed_pages: completed ? 2 : 0, error_code: status === "failed" ? "STORAGE_UNAVAILABLE" : null,
      cancel_requested_at: status === "cancelled" ? published.created_at : null } };
    await page.route("**/v1/materials", route => route.fulfill({ json: { schema: "material-library/v2", materials: [item] } }));
    await page.goto("/knowledge-maps");
    const card = page.getByRole("article");
    await expect(card.locator(".library-state")).toContainText("最新處理：");
    if (status === "failed" || status === "cancelled") await expect(card.locator(".library-state")).toHaveText(status === "failed" ? "最新處理：處理失敗" : "最新處理：已取消處理");
    await expect(card.getByRole("button", { name: "開啟知識地圖", exact: true })).toHaveClass("primary-button");
    await expect(card.getByRole("button", { name: "查看最新處理", exact: true })).toHaveClass("secondary-button");
  });
}
