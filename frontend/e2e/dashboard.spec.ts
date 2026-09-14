import { expect, test, type Page } from "@playwright/test";
import type { MaterialLibraryItem } from "../src/api/contracts";

const id = "11111111-1111-4111-8111-111111111111";
const revision = `knowledge-structure:sha256:${"a".repeat(64)}`;
const longName = "資料結構與演算法：堆疊、佇列、遞迴與樹狀結構的概念整理及練習講義_" + "LongMaterialFilename".repeat(5) + ".pdf";
const material: MaterialLibraryItem = {
  schema: "material-library-item/v2", material_id: id, source_artifact_id: id, display_name: longName,
  size_bytes: 100, created_at: "2026-09-12T00:00:00Z", latest_attempt: null,
  available_structures: [{ run_id: id, knowledge_structure_revision: revision, created_at: "2026-09-12T00:00:00Z", status: "succeeded" }],
  study_sessions: [],
};
const active = { study_session_id: id, run_id: id, knowledge_structure_revision: revision,
  status: "active" as const, started_at: "2026-09-12T01:00:00Z", current_concept_id: null };

async function signedIn(page: Page) {
  await page.route("**/v1/session/refresh", route => route.fulfill({ status: 204 }));
  await page.route("**/v1/session", route => route.fulfill({ json: { schema: "learner-identity/v1", learner_id: id } }));
}

for (const viewport of [{ width: 1536, height: 1024 }, { width: 1366, height: 768 }, { width: 1024, height: 768 }, { width: 390, height: 844 }]) {
  for (const state of ["empty", "active", "no_safe", "completed", "map", "unpublished", "loading", "failure"] as const) {
    test(`home ${state} at ${viewport.width}px has one next action and two navigation items`, async ({ page }) => {
      await page.setViewportSize(viewport); await signedIn(page);
      let failed = state === "failure", waiting = state === "loading";
      let release!: () => void; const pending = new Promise<void>(resolve => { release = resolve; });
      const items = state === "empty" || state === "loading" || state === "failure" ? [] : [{ ...material,
        available_structures: state === "unpublished" ? [] : material.available_structures,
        study_sessions: ["active", "no_safe", "completed"].includes(state) ? [{ ...active, status: state as "active" | "no_safe" | "completed" }] : [] }];
      await page.route("**/v1/materials", async route => {
        if (waiting) await pending;
        return failed ? route.fulfill({ status: 503, json: { schema: "api-error/v1", request_id: id, reason_code: "STORAGE_UNAVAILABLE", retryable: true, message: "Unavailable" } }) : route.fulfill({ json: { schema: "material-library/v2", materials: items } });
      });
      await page.goto("/");
      const home = page.locator(".dashboard");
      await expect(home.getByRole("heading", { level: 1 })).toHaveText("首頁");
      const nav = page.getByRole("navigation", { name: "主要導覽" });
      await expect(nav.getByRole("button")).toHaveText(["首頁", "我的教材"]);
      await expect(nav.getByRole("button").first()).toHaveAttribute("aria-current", "page");
      await expect(page.locator(".account-avatar, .sidebar-helper, .nav-unavailable, .brand small")).toHaveCount(0);
      await expect(page.getByRole("button", { name: "登出", exact: true })).toBeVisible();
      await expect(home.locator(".dashboard-stats, .dashboard-stat, .dashboard-help, .dashboard-features, .library-grid")).toHaveCount(0);
      if (state === "loading") {
        await expect(home.getByRole("status")).toContainText("正在讀取學習進度"); waiting = false; release();
      }
      if (state === "failure") {
        await expect(home.getByRole("alert")).toContainText("無法讀取學習進度");
        await expect(home.getByRole("button", { name: "上傳教材", exact: true })).toBeEnabled();
        failed = false; await home.getByRole("button", { name: "重新讀取", exact: true }).click();
      }
      const empty = ["empty", "loading", "failure"].includes(state);
      await expect(home.locator(".dashboard-onboarding")).toHaveCount(empty ? 1 : 0);
      await expect(home.locator(".dashboard-next")).toHaveCount(empty ? 0 : 1);
      await expect(home.locator(".primary-button")).toHaveCount(1);
      const label = empty ? "上傳第一份教材" : state === "map" ? "開啟知識地圖" : state === "unpublished" ? "前往我的教材" : state === "completed" ? "查看學習成果" : "繼續學習";
      await expect(home.locator(".primary-button")).toHaveText(label);
      if (!empty && state !== "unpublished") await expect(home.locator(".dashboard-next")).toContainText(longName);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      await page.screenshot({ path: `/tmp/studydy-simple-home-${viewport.width}-${state}.png`, fullPage: true });
      const action = home.getByRole("button", { name: label, exact: true });
      await action.focus(); await page.keyboard.press("Enter");
      const map = `/materials/${id}/runs/${id}/knowledge-structures/${encodeURIComponent(revision)}`;
      await expect.poll(() => new URL(page.url()).pathname).toBe(empty ? "/upload" : state === "map" ? map : state === "unpublished" ? "/materials" : `${map}/study-sessions/${id}`);
      if (empty || state === "unpublished") await expect(page.getByRole("navigation", { name: "主要導覽" }).getByRole("button").last()).toHaveAttribute("aria-current", "page");
    });
  }
}

test("home chooses active exact-bound state before newer completion and ignores stale revision", async ({ page }) => {
  await signedIn(page);
  const secondId = "22222222-2222-4222-8222-222222222222";
  const staleId = "33333333-3333-4333-8333-333333333333";
  let items: MaterialLibraryItem[] = [
    { ...material, display_name: "Completed.pdf", study_sessions: [{ ...active, status: "completed", started_at: "2026-09-14T00:00:00Z" }] },
    { ...material, material_id: secondId, display_name: "Active.pdf", study_sessions: [{ ...active, status: "no_safe" }] },
    { ...material, material_id: staleId, display_name: "Stale.pdf", study_sessions: [{ ...active, knowledge_structure_revision: `knowledge-structure:sha256:${"b".repeat(64)}`, started_at: "2026-09-15T00:00:00Z" }] },
  ];
  await page.route("**/v1/materials", route => route.fulfill({ json: { schema: "material-library/v2", materials: items } }));
  await page.goto("/");
  await expect(page.locator(".dashboard-next h2")).toHaveText("Active.pdf");
  await page.getByRole("button", { name: "繼續學習", exact: true }).click();
  expect(new URL(page.url()).pathname).toBe(`/materials/${secondId}/runs/${id}/knowledge-structures/${encodeURIComponent(revision)}/study-sessions/${id}`);
  items = items.filter(item => item.material_id !== secondId);
  await page.goto("/");
  await expect(page.locator(".dashboard-next h2")).toHaveText("Completed.pdf");
  items = items.filter(item => item.display_name !== "Completed.pdf");
  await page.reload();
  await expect(page.getByRole("button", { name: "開啟知識地圖", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "繼續學習", exact: true })).toHaveCount(0);
});

test("standard sidebar has only Home and Materials on all management routes", async ({ page }) => {
  await signedIn(page);
  await page.route("**/v1/material-processing-runs/*", route => route.fulfill({ status: 503, json: { schema: "api-error/v1", request_id: id, reason_code: "STORAGE_UNAVAILABLE", retryable: true, message: "Unavailable" } }));
  await page.route("**/v1/materials", route => route.fulfill({ json: { schema: "material-library/v2", materials: [] } }));
  for (const path of ["/", "/materials", "/upload", `/materials/${id}/runs/${id}`]) {
    await page.goto(path);
    const nav = page.getByRole("navigation", { name: "主要導覽" });
    await expect(nav.getByRole("button")).toHaveText(["首頁", "我的教材"]);
    await expect(nav.getByRole("button").nth(path === "/" ? 0 : 1)).toHaveAttribute("aria-current", "page");
  }
});
