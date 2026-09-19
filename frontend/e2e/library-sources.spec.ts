import { expect, test } from "@playwright/test";
import type { SourceView } from "../src/api/contracts";

const id = "11111111-1111-4111-8111-111111111111";
const pdf = "22222222-2222-4222-8222-222222222222";
const office = "33333333-3333-4333-8333-333333333333";
for (const width of [1536, 390]) {
  test(`multi-source library previews, retry and conversion refresh at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await page.route("**/v1/session/refresh", route => route.fulfill({ status: 204 }));
    await page.route("**/v1/session", route => route.fulfill({ json: { schema: "learner-identity/v1", learner_id: id } }));
    await page.route("**/v1/materials", route => route.fulfill({ json: { schema: "material-library/v2", materials: [{
      schema: "material-library-item/v2", material_id: id, source_artifact_id: pdf,
      display_name: "資料結構", size_bytes: 1200, created_at: "2026-09-20T00:00:00Z",
      source_count: 3, latest_attempt: null, available_structures: [], study_sessions: [],
    }] } }));
    const sources: SourceView[] = [
      { source_id: id, normalization_id: id, original_artifact_id: id, original_name: "第一章.pdf", media_type: "application/pdf", status: "ready", normalized_artifact_id: pdf, page_count: 12, error_code: null, included: true },
      { source_id: office, normalization_id: office, original_artifact_id: office, original_name: "第二章_樹的走訪與練習題_".repeat(5) + ".ppt", media_type: "application/vnd.ms-powerpoint", status: "running", normalized_artifact_id: null, page_count: null, error_code: null, included: false },
      { source_id: pdf, normalization_id: pdf, original_artifact_id: pdf, original_name: "補充.docx", media_type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document", status: "failed", normalized_artifact_id: null, page_count: null, error_code: "NORMALIZATION_FAILED", included: false },
    ];
    let reads = 0;
    await page.route(`**/v2/materials/${id}/sources`, route => {
      expect(route.request().method()).toBe("GET");
      reads++;
      if (reads === 1) return route.fulfill({ status: 503, json: { schema: "api-error/v1", request_id: id, reason_code: "STORAGE_UNAVAILABLE", retryable: true, message: "Unavailable" } });
      if (reads >= 3) sources[1] = { ...sources[1], status: "ready", normalized_artifact_id: office, page_count: 20 };
      return route.fulfill({ json: { schema: "material-sources/v1", material_id: id, sources } });
    });
    await page.goto("/materials");
    const details = page.locator(".library-sources");
    await expect(details.locator("summary")).toHaveText("來源檔案（3 份）");
    expect(reads).toBe(0);
    await details.locator("summary").click();
    await details.getByRole("button", { name: "重新讀取來源" }).click();
    const rows = details.locator("li");
    await expect(rows).toHaveCount(3);
    await expect(rows.nth(0).getByRole("link", { name: "開啟 PDF" })).toHaveAttribute("href", `/v1/artifacts/${pdf}`);
    await expect(rows.nth(0).getByRole("link", { name: "下載原檔" })).toHaveAttribute("href", `/v2/artifacts/${id}`);
    await expect(rows.nth(1)).toContainText("等待或正在轉換");
    await expect(rows.nth(2)).toContainText("轉換失敗");
    await expect(rows.nth(2).getByRole("link")).toHaveCount(1);
    await expect(rows.nth(1).getByRole("link", { name: "轉換後 PDF" })).toHaveAttribute("href", `/v1/artifacts/${office}`, { timeout: 10000 });
    await expect(rows.nth(1)).toContainText("尚未納入目前地圖");
    for (const link of await details.getByRole("link").all()) {
      await expect(link).toHaveAttribute("target", "_blank");
      await expect(link).toHaveAttribute("rel", "noopener noreferrer");
    }
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: `/tmp/studydy-library-sources-${width}.png`, fullPage: true });
  });
}
