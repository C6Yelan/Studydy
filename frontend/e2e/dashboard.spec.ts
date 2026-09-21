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

for (const viewport of [{ width: 1920, height: 1080 }, { width: 1536, height: 1024 }, { width: 1366, height: 768 }, { width: 1024, height: 768 }, { width: 390, height: 844 }]) {
  for (const state of ["empty", "active", "no_safe", "completed", "map", "unpublished", "loading", "failure"] as const) {
    test(`full dashboard ${state} at ${viewport.width}px retains simplified navigation`, async ({ page }) => {
      await page.setViewportSize(viewport); await signedIn(page);
      let failed = state === "failure", waiting = state === "loading";
      let release!: () => void; const pending = new Promise<void>(resolve => { release = resolve; });
      const empty = ["empty", "loading", "failure"].includes(state);
      const hasState = ["active", "no_safe", "completed"].includes(state);
      const items = empty ? [] : [{ ...material, available_structures: state === "unpublished" ? [] : material.available_structures,
        study_sessions: hasState ? [{ ...active, status: state as "active" | "no_safe" | "completed" }] : [] }];
      await page.route("**/v1/materials", async route => {
        if (waiting) await pending;
        return failed ? route.fulfill({ status: 503, json: { schema: "api-error/v1", request_id: id, reason_code: "STORAGE_UNAVAILABLE", retryable: true, message: "Request could not be completed." } }) : route.fulfill({ json: { schema: "material-library/v2", materials: items } });
      });
      await page.goto("/");
      const home = page.locator(".dashboard");
      await expect(home.getByRole("heading", { level: 1 })).toHaveText("歡迎回來！");
      await expect(page.getByRole("navigation", { name: "主要導覽" }).getByRole("button")).toHaveText(["首頁", "我的教材"]);
      await expect(page.locator(".account-avatar, .sidebar-helper, .nav-unavailable")).toHaveCount(0);
      await expect(page.locator(".brand small")).toHaveText("AI 智慧學習平台");
      await expect(page.getByRole("button", { name: "登出", exact: true })).toBeVisible();
      await expect(home.locator(".dashboard-hero")).toBeVisible();
      await expect(home.getByRole("heading", { name: "建立你的知識地圖", level: 2, exact: true })).toBeVisible();
      const help = page.getByRole("complementary", { name: "Studydy 學習協助" });
      await expect(help).toBeVisible();
      await expect(help.locator("article")).toHaveCount(4);
      await expect(help.getByRole("button")).toHaveCount(0);
      const stats = home.locator(".dashboard-stats");
      await expect(stats.locator("article.dashboard-stat")).toHaveCount(4);
      await expect(stats.getByRole("button")).toHaveCount(0);
      await expect(stats.locator(".dashboard-stat > svg")).toHaveCount(0);
      await expect(stats.locator(".stat-icon svg")).toHaveCount(4);
      await expect(stats.locator(".stat-copy > span")).toHaveText(["教材", "知識地圖", "已開始學習", "已完成"]);
      await expect(home.locator(".dashboard-hero").getByRole("button")).toHaveText(["上傳教材"]);
      await expect(home.getByRole("button", { name: "前往我的教材", exact: true })).toHaveCount(0);
      if (state === "loading" || state === "failure") await expect(stats.locator("strong")).toHaveText(["—", "—", "—", "—"]);
      if (state === "loading") { await expect(stats).toHaveAttribute("aria-busy", "true"); waiting = false; release(); }
      if (state === "failure") {
        await expect(home.getByRole("alert")).toContainText("資料服務暫時無法使用");
        await expect(home.getByRole("button", { name: "上傳教材", exact: true })).toBeEnabled();
        failed = false; await home.getByRole("button", { name: "重新讀取", exact: true }).click();
      }
      await expect(stats.locator("strong")).toHaveText([empty ? "0" : "1", empty || state === "unpublished" ? "0" : "1", hasState ? "1" : "0", state === "completed" ? "1" : "0"]);
      await expect(stats).toHaveAttribute("aria-busy", "false");
      await expect(stats.locator("small")).toHaveText(["已保存的教材", "可開啟地圖的教材", "有學習紀錄的教材", "已完成學習的教材"]);
      await expect(home.locator(".dashboard-resume")).toHaveCount(hasState ? 1 : 0);
      if (hasState) await expect(home.locator(".dashboard-resume p")).toHaveText(longName);
      const main = (await home.locator(".dashboard-primary").boundingBox())!, rail = (await help.boundingBox())!;
      if (viewport.width >= 1440) { expect(rail.x).toBeGreaterThanOrEqual(main.x + main.width + 20); expect(rail.width).toBeGreaterThanOrEqual(280); expect(rail.width).toBeLessThanOrEqual(320); expect(Math.abs(main.y - rail.y)).toBeLessThan(2); }
      else expect(rail.y).toBeGreaterThanOrEqual(main.y + main.height);
      if (viewport.width > 600) { await expect(home.locator(".hero-illustration")).toBeVisible(); await expect.poll(() => home.locator(".hero-illustration img").evaluate((img: HTMLImageElement) => img.complete && img.naturalWidth > 0)).toBe(true); }
      else await expect(home.locator(".hero-illustration")).toBeHidden();
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      await page.screenshot({ path: `/tmp/studydy-restored-home-${viewport.width}-${state}.png`, fullPage: true });
      if (hasState) {
        const action = home.getByRole("button", { name: state === "completed" ? "查看學習成果" : "繼續學習", exact: true });
        await action.focus(); await page.keyboard.press("Enter");
        await expect.poll(() => new URL(page.url()).pathname).toBe(`/materials/${id}/runs/${id}/knowledge-structures/${encodeURIComponent(revision)}/study-sessions/${id}`);
      } else {
        await home.getByRole("button", { name: "上傳教材", exact: true }).click();
        await expect(page).toHaveURL(/\/upload$/);
      }
    });
  }
}

test("home chooses active exact-bound state before newer completion and ignores stale revision", async ({ page }) => {
  await signedIn(page);
  const secondId = "22222222-2222-4222-8222-222222222222";
  const staleId = "33333333-3333-4333-8333-333333333333";
  let items: MaterialLibraryItem[] = [
    { ...material, display_name: "Calculus.pdf", study_sessions: [{ ...active, status: "completed", started_at: "2026-09-14T00:00:00Z" }] },
    { ...material, material_id: secondId, display_name: "Linear Algebra.pdf", study_sessions: [{ ...active, status: "no_safe" }] },
    { ...material, material_id: staleId, display_name: "Probability.pdf", study_sessions: [{ ...active, knowledge_structure_revision: `knowledge-structure:sha256:${"b".repeat(64)}`, started_at: "2026-09-15T00:00:00Z" }] },
  ];
  await page.route("**/v1/materials", route => route.fulfill({ json: { schema: "material-library/v2", materials: items } }));
  await page.goto("/");
  await expect(page.locator(".dashboard-resume p")).toHaveText("Linear Algebra.pdf");
  await page.getByRole("button", { name: "繼續學習", exact: true }).click();
  expect(new URL(page.url()).pathname).toBe(`/materials/${secondId}/runs/${id}/knowledge-structures/${encodeURIComponent(revision)}/study-sessions/${id}`);
  items = items.filter(item => item.material_id !== secondId);
  await page.goto("/");
  await expect(page.locator(".dashboard-resume p")).toHaveText("Calculus.pdf");
  items = items.filter(item => item.display_name !== "Calculus.pdf");
  await page.reload();
  await expect(page.locator(".dashboard-resume")).toHaveCount(0);
  await expect(page.locator(".dashboard-stat strong")).toHaveText(["1", "1", "0", "0"]);
  await expect(page.getByRole("button", { name: "繼續學習", exact: true })).toHaveCount(0);
});


test("all four overview stats use Materials and count only exact canonical states", async ({ page }) => {
  await signedIn(page);
  const stale = { ...active, study_session_id: "33333333-3333-4333-8333-333333333333", knowledge_structure_revision: `knowledge-structure:sha256:${"b".repeat(64)}` };
  await page.route("**/v1/materials", route => route.fulfill({ json: { schema: "material-library/v2", materials: [{ ...material, study_sessions: [stale, active, { ...active, study_session_id: "22222222-2222-4222-8222-222222222222" }] }] } }));
  await page.goto("/");
  await expect(page.locator(".dashboard-stat strong")).toHaveText(["1", "1", "1", "0"]);
  for (const stat of await page.locator(".dashboard-stat").all()) {
    await expect(stat).toHaveJSProperty("tabIndex", -1);
    await expect(stat.locator("button, a, [role=button], [tabindex]")).toHaveCount(0);
    const border = await stat.evaluate(el => getComputedStyle(el).borderColor);
    await stat.hover();
    expect(await stat.evaluate(el => getComputedStyle(el).borderColor)).toBe(border);
    expect(await stat.evaluate(el => getComputedStyle(el).cursor)).not.toBe("pointer");
    await stat.click();
    expect(new URL(page.url()).pathname).toBe("/");
  }
  await page.getByRole("button", { name: "上傳教材", exact: true }).focus();
  await page.keyboard.press("Tab");
  await expect(page.getByRole("button", { name: "繼續學習", exact: true })).toBeFocused();
  await page.keyboard.press("Shift+Tab");
  await expect(page.getByRole("button", { name: "上傳教材", exact: true })).toBeFocused();
});
