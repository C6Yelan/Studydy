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

for (const viewport of [{ width: 1920, height: 1080 }, { width: 1536, height: 1024 }, { width: 1366, height: 768 }, { width: 390, height: 844 }]) {
  for (const state of ["empty", "materials", "recent", "loading", "failure"] as const) {
    test(`dashboard ${state} at ${viewport.width}px keeps content and navigation usable`, async ({ page }) => {
      await page.setViewportSize(viewport);
      await signedIn(page);
      let responseState: string = state;
      let release!: () => void;
      const pending = new Promise<void>(resolve => { release = resolve; });
      await page.route("**/v1/materials", async route => {
        if (responseState === "loading") await pending;
        if (responseState === "failure") return route.fulfill({ status: 503, json: {
          schema: "api-error/v1", request_id: id, reason_code: "STORAGE_UNAVAILABLE", retryable: true, message: "Request could not be completed.",
        } });
        return route.fulfill({ json: { schema: "material-library/v2", materials: responseState === "materials" ? [material]
          : responseState === "recent" ? [{ ...material, study_sessions: [active] }] : [] } });
      });
      await page.goto("/");
      const dashboard = page.locator(".dashboard");
      await expect(dashboard.getByRole("heading", { level: 1 })).toHaveText("歡迎回來！");
      const stats = page.locator(".dashboard-stats");
      const expected = state === "empty" ? ["0", "0", "0", "0"] : state === "materials" ? ["1", "1", "0", "0"]
        : state === "recent" ? ["1", "1", "1", "0"] : ["—", "—", "—", "—"];
      await expect(stats.locator("strong")).toHaveText(expected);
      await expect(stats).toHaveAttribute("aria-busy", state === "loading" ? "true" : "false");
      await expect(page.locator(".sidebar-helper")).toHaveCount(0);
      await expect(page.getByRole("button", { name: "首頁", exact: true })).toHaveAttribute("aria-current", "page");
      await expect(dashboard.getByRole("button", { name: "上傳教材", exact: true })).toHaveCount(1);
      const help = page.getByRole("complementary", { name: "Studydy 學習協助" });
      await expect(help.getByRole("heading", { level: 3 })).toHaveText(["建立知識地圖", "依循學習路徑", "理解概念", "練習與複習"]);
      await expect(help.getByRole("button")).toHaveCount(0);
      const header = await page.locator(".app-header").boundingBox();
      expect(header!.height).toBe(viewport.width > 900 ? 74 : 72);
      if (viewport.width > 900) expect((await page.locator(".app-sidebar").boundingBox())!.width).toBe(252);
      const primary = await page.locator(".dashboard-primary").boundingBox();
      const rail = await help.boundingBox();
      if (viewport.width >= 1440) {
        expect(rail!.x).toBeGreaterThanOrEqual(primary!.x + primary!.width + 20);
        expect(rail!.width).toBeGreaterThanOrEqual(280);
        expect(rail!.width).toBeLessThanOrEqual(320);
        expect(Math.abs(rail!.y - primary!.y)).toBeLessThan(1);
        expect((await dashboard.boundingBox())!.width).toBeGreaterThan(1018);
        expect(rail!.y + rail!.height).toBeLessThan(viewport.height);
      } else expect(rail!.y).toBeGreaterThanOrEqual(primary!.y + primary!.height + 20);
      const cards = await stats.locator("button").evaluateAll(elements => elements.map(element => {
        const rect = element.getBoundingClientRect(); return { height: rect.height, top: rect.top };
      }));
      expect(new Set(cards.map(card => card.height)).size).toBe(1);
      expect(cards[0].top === cards[2].top).toBe(viewport.width > 1200);
      if (state === "recent") {
        await expect(page.locator(".dashboard-primary .dashboard-resume")).toContainText(longName);
        await expect(page.getByRole("button", { name: "繼續學習", exact: true })).toBeVisible();
        if (viewport.width >= 1440) {
          const resume = await page.locator(".dashboard-resume").boundingBox();
          expect(resume!.y + resume!.height).toBeLessThan(viewport.height);
        }
      } else await expect(page.locator(".dashboard-resume")).toHaveCount(0);
      if (state === "failure") await expect(page.locator(".dashboard-primary [role=alert]")).toContainText("資料服務暫時無法使用");
      if (viewport.width > 600) await expect.poll(() => page.locator(".hero-illustration img").evaluate((img: HTMLImageElement) => img.complete && img.naturalWidth > 0)).toBe(true);
      else await expect(page.locator(".hero-illustration")).toBeHidden();
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(viewport.width);
      const clipped = await dashboard.locator("button, h1, h2, h3, small, p").evaluateAll(elements => elements.filter(element => element.scrollWidth > element.clientWidth + 1).map(element => element.tagName));
      expect(clipped).toEqual([]);
      await page.screenshot({ path: `/tmp/studydy-dashboard/${viewport.width}-${state}.png`, fullPage: true });
      if (state === "loading") {
        const height = (await stats.boundingBox())!.height;
        responseState = "empty"; release();
        await expect(stats.locator("strong")).toHaveText(["0", "0", "0", "0"]);
        expect((await stats.boundingBox())!.height).toBe(height);
      }
      if (state === "failure") {
        responseState = "empty";
        await page.getByRole("button", { name: "重新讀取", exact: true }).click();
        await expect(stats.locator("strong")).toHaveText(["0", "0", "0", "0"]);
        await expect(page.getByRole("alert")).toHaveCount(0);
      }
      const upload = dashboard.getByRole("button", { name: "上傳教材", exact: true });
      await upload.focus();
      await page.keyboard.press("Tab");
      await page.keyboard.press("Shift+Tab");
      await expect(upload).toBeFocused();
      expect(await upload.evaluate(element => getComputedStyle(element).outlineStyle)).toBe("solid");
      await page.keyboard.press("Enter");
      await expect(page).toHaveURL(/\/upload$/);
      await page.getByRole("button", { name: "首頁", exact: true }).click();
      await expect(dashboard).toBeVisible();
      await dashboard.getByRole("button", { name: "前往我的教材", exact: true }).click();
      await expect(page).toHaveURL(/\/materials$/);
      await expect(page.getByRole("button", { name: "教材庫", exact: true })).toHaveAttribute("aria-current", "page");
    });
  }
}

test("dashboard resume retains active/completed routes and overview destinations", async ({ page }) => {
  await signedIn(page);
  let status: "active" | "completed" = "active";
  await page.route("**/v1/materials", route => route.fulfill({ json: { schema: "material-library/v2",
    materials: [{ ...material, study_sessions: [{ ...active, status }] }] } }));
  for (const value of ["active", "completed"] as const) {
    status = value;
    await page.goto("/");
    await expect(page.locator(".dashboard-stat strong")).toHaveText(["1", "1", "1", value === "completed" ? "1" : "0"]);
    await page.getByRole("button", { name: value === "active" ? "繼續學習" : "查看學習成果", exact: true }).click();
    expect(new URL(page.url()).pathname).toBe(`/materials/${id}/runs/${id}/knowledge-structures/${encodeURIComponent(revision)}/study-sessions/${id}`);
  }
  for (const [index, path] of ["materials", "knowledge-maps", "materials", "materials"].entries()) {
    await page.goto("/");
    await page.locator(".dashboard-stat").nth(index).click();
    await expect(page).toHaveURL(new RegExp(`/${path}$`));
  }
});
