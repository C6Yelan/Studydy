import { expect, test } from "@playwright/test";
import type { SourceView } from "../src/api/contracts";

const id = "11111111-1111-4111-8111-111111111111";
const pdf = "22222222-2222-4222-8222-222222222222";
const office = "33333333-3333-4333-8333-333333333333";
for (const width of [1536, 390]) {
  test(`multi-source bookshelf opens existing source management at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await page.route("**/v1/session/refresh", route => route.fulfill({ json: { schema: "learner-identity/v1", learner_id: "33333333-3333-4333-8333-333333333333" } }));
    await page.route("**/v1/session", route => route.fulfill({ json: { schema: "learner-identity/v1", learner_id: id } }));
    const item = {
      schema: "material-library-item/v1", material_id: id, source_artifact_id: pdf,
      display_name: "資料結構", size_bytes: 1200, created_at: "2026-09-20T00:00:00Z",
      source_count: 3, latest_attempt: null, available_structures: [{
        run_id: pdf, knowledge_structure_revision: `knowledge-structure:sha256:${"a".repeat(64)}`,
        status: "partial", created_at: "2026-09-20T00:00:00Z",
      }], study_sessions: [],
    };
    await page.route("**/v1/materials", route => route.fulfill({ json: { schema: "material-library/v1", materials: [item] } }));
    await page.route(`**/v1/materials/${id}`, route => route.fulfill({ json: item }));
    await page.route("**/v1/source-capabilities", route => route.fulfill({ json: { formats: [] } }));
    const sources: SourceView[] = [
      { source_id: id, normalization_id: id, original_artifact_id: id, original_name: "第一章.pdf", media_type: "application/pdf", status: "ready", normalized_artifact_id: pdf, page_count: 12, error_code: null, included: true },
      { source_id: office, normalization_id: office, original_artifact_id: office, original_name: "第二章_樹的走訪與練習題_".repeat(5) + ".ppt", media_type: "application/vnd.ms-powerpoint", status: "running", normalized_artifact_id: null, page_count: null, error_code: null, included: false },
      { source_id: pdf, normalization_id: pdf, original_artifact_id: pdf, original_name: "補充.docx", media_type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document", status: "failed", normalized_artifact_id: null, page_count: null, error_code: "NORMALIZATION_FAILED", included: false },
    ];
    let reads = 0;
    await page.route(`**/v1/materials/${id}/sources`, route => {
      expect(route.request().method()).toBe("GET");
      reads++;
      if (reads >= 3) sources[1] = { ...sources[1], status: "ready", normalized_artifact_id: office, page_count: 20 };
      return route.fulfill({ json: { schema: "material-sources/v1", material_id: id, sources } });
    });
    await page.goto("/materials");
    const card = page.getByRole("article");
    await expect(card).toContainText("3 個檔案");
    await expect(card).not.toContainText(/來源檔案|第一章.pdf|轉換後 PDF|下載原檔|最新處理|教材轉換|先前題目與作答|partial|needs_review/);
    await expect(card.getByRole("link")).toHaveCount(0);
    await expect(card.locator("details:not(.material-management-menu)")).toHaveCount(0);
    await expect(card.locator(".library-state")).toHaveCount(0);
    await expect(card.locator(".primary-button")).toHaveText("開啟知識地圖");
    await expect(card.locator(".state-actions > button")).toHaveCount(1);
    expect(reads).toBe(0);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: `/tmp/studydy-library-sources-${width}.png`, fullPage: true });
    await card.getByRole("button", { name: "管理「資料結構」", exact: true }).click();
    await card.getByRole("button", { name: "管理教材", exact: true }).click();
    await expect(page).toHaveURL(new RegExp(`/materials/${id}/sources$`));
    await expect(page.getByText("第一章.pdf", { exact: true })).toBeVisible();
    await expect(page.getByRole("link", { name: "下載原檔" }).first()).toHaveAttribute("href", `/v1/artifacts/${id}/download`);
    await expect(page.getByRole("link", { name: /預覽 PDF/ }).first()).toHaveAttribute("href", `/v1/artifacts/${pdf}`);
    await expect(page.getByRole("link", { name: /預覽 PDF/ })).toHaveCount(2, { timeout: 10000 });
    expect(reads).toBeGreaterThanOrEqual(3);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  });
}
