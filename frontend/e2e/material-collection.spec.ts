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

for (const viewport of [{ width: 1536, height: 1024 }, { width: 1366, height: 768 }, { width: 1024, height: 768 }, { width: 390, height: 844 }]) {
  for (const mapsOnly of [false, true]) for (const state of states) {
    test(`${mapsOnly ? "maps" : "materials"} direct hub ${state} at ${viewport.width}px`, async ({ page }) => {
      await page.setViewportSize(viewport); await signedIn(page);
      const list = materials(state); let fail = state === "failure";
      let release!: () => void; const loading = new Promise<void>(resolve => { release = resolve; });
      await page.route("**/v1/materials", async route => {
        if (state === "loading") await loading;
        return fail ? route.fulfill({ status: 503, json: { schema: "api-error/v1", request_id: materialId, reason_code: "STORAGE_UNAVAILABLE", retryable: true, message: "Unavailable" } }) : route.fulfill({ json: { schema: "material-library/v2", materials: list } });
      });
      await page.goto(mapsOnly ? "/knowledge-maps" : "/materials");
      if (state === "loading") { await expect(page.getByRole("heading", { name: "正在讀取教材庫" })).toBeVisible(); release(); }
      if (fail) { await expect(page.getByRole("heading", { name: "無法讀取教材" })).toBeVisible(); fail = false; await page.getByRole("button", { name: "重新讀取", exact: true }).click(); }
      const visible = mapsOnly ? list.filter(item => item.available_structures.length) : list;
      const cards = page.locator(".library-item");
      await expect(cards).toHaveCount(visible.length);
      await expect(page.locator(".sidebar-helper")).toHaveCount(0);
      if (!visible.length) await expect(page.locator(".library-empty")).toBeVisible();
      for (const [index, item] of visible.entries()) {
        const card = cards.nth(index); const structure = item.available_structures[0];
        const learning = structure && item.study_sessions.find(s => s.run_id === structure.run_id && s.knowledge_structure_revision === structure.knowledge_structure_revision);
        const busy = item.latest_attempt && ["pending", "running"].includes(item.latest_attempt.status);
        const expected = busy ? "查看處理狀態" : mapsOnly ? "開啟知識地圖" : learning ? learning.status === "completed" ? "查看學習成果" : "繼續學習" : structure ? "開啟知識地圖" : !item.latest_attempt ? "開始整理教材" : item.latest_attempt.status === "failed" ? "重新處理教材" : null;
        await expect(card.getByRole("heading")).toHaveText(item.display_name);
        await expect(card.getByRole("button", { name: item.display_name, exact: true })).toHaveCount(0);
        await expect(card.locator(".primary-button")).toHaveCount(expected ? 1 : 0);
        if (expected) await expect(card.locator(".primary-button")).toHaveText(expected);
        await expect(card.getByRole("link", { name: "原始 PDF" })).toHaveAttribute("href", `/v1/artifacts/${item.source_artifact_id}`);
        await expect(card.getByRole("link", { name: "原始 PDF" })).toHaveAttribute("rel", "noopener noreferrer");
        const pdf = card.getByRole("link", { name: "原始 PDF" });
        const pdfBox = (await pdf.boundingBox())!, titleBox = (await card.getByRole("heading").boundingBox())!;
        expect(Math.abs(pdfBox.x - titleBox.x)).toBeLessThan(2);
        expect(pdfBox.width).toBeLessThan(150);
        await expect(pdf).not.toHaveClass(/primary-button|secondary-button/);
        expect(await pdf.evaluate(el => el.previousElementSibling?.tagName)).toBe("P");
        await expect(card.getByRole("region", { name: "已發布版本" })).toHaveCount(0);
        await expect(card.getByRole("region", { name: "學習紀錄" })).toHaveCount(0);
      }
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      await page.screenshot({ path: `/tmp/studydy-direct-hub-${viewport.width}-${mapsOnly}-${state}.png`, fullPage: true });
    });
  }
}

test("latest structure controls exact saved learning binding, never an older state", async ({ page }) => {
  await signedIn(page);
  const newerRun = "55555555-5555-4555-8555-555555555555";
  const newerRevision = `knowledge-structure:sha256:${"b".repeat(64)}`;
  const item = material("active");
  item.available_structures.unshift({ ...published, run_id: newerRun, knowledge_structure_revision: newerRevision });
  await page.route("**/v1/materials", route => route.fulfill({ json: { schema: "material-library/v2", materials: [item] } }));
  await page.goto("/materials");
  await expect(page.getByRole("article").locator(".primary-button")).toHaveText("開啟知識地圖");
  await expect(page.getByRole("button", { name: "繼續學習", exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "開啟知識地圖", exact: true }).click();
  expect(new URL(page.url()).pathname).toBe(`/materials/${materialId}/runs/${newerRun}/knowledge-structures/${encodeURIComponent(newerRevision)}`);
  item.study_sessions.push({ ...active, run_id: newerRun, knowledge_structure_revision: newerRevision });
  await page.goto("/materials");
  await page.getByRole("article").getByRole("button", { name: "繼續學習", exact: true }).click();
  expect(new URL(page.url()).pathname).toContain(`${encodeURIComponent(newerRevision)}/study-sessions/${studyId}`);
});

test("direct hub polls an active run until a usable map is published", async ({ page }) => {
  await signedIn(page); await page.clock.install();
  let reads = 0;
  await page.route("**/v1/materials", route => { reads++; return route.fulfill({ json: { schema: "material-library/v2", materials: [material(reads === 1 ? "pending" : "map")] } }); });
  await page.goto("/materials");
  await expect(page.getByRole("article").locator(".primary-button")).toHaveText("查看處理狀態");
  await page.clock.runFor(3000);
  await expect(page.getByRole("article").locator(".primary-button")).toHaveText("開啟知識地圖");
  const stopped = reads; await page.clock.runFor(6000); expect(reads).toBe(stopped);
});
