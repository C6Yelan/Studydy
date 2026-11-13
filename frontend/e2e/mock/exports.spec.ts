import { test, expect } from "@playwright/test";
import { mockKnowledgeMapApi, materialId, structureRevision, runId, json } from "../fixtures/knowledge-map";

test("outline download retains errors and allows retry from the export button", async ({ page }) => {
  await mockKnowledgeMapApi(page);
  let release!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  let fail = true, requests = 0;
  await page.route("**/map/export/md", async route => {
    requests++;
    await gate;
    return fail ? json(route, { schema: "api-error/v1", request_id: materialId, reason_code: "STORAGE_UNAVAILABLE", retryable: true, message: "Unavailable" }, 503)
      : route.fulfill({ status: 200, contentType: "text/markdown", headers: { "Content-Disposition": "attachment; filename*=UTF-8''map.md" }, body: "# Map" });
  });
  await page.goto(`/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}`);
  const trigger = page.getByRole("button", { name: "下載地圖大綱", exact: true });
  await trigger.click();
  // 圓鈕只有圖示，下載中以 disabled 與 aria-busy 表示。
  await expect(trigger).toBeDisabled();
  await expect(trigger).toHaveAttribute("aria-busy", "true");
  release();
  await expect(page.getByRole("alert")).toBeVisible();
  await expect(trigger).toBeEnabled();
  fail = false;
  const download = page.waitForEvent("download");
  await trigger.click();
  expect((await download).suggestedFilename()).toBe("map.md");
  await expect(trigger).toBeEnabled();
  await expect(page.getByRole("alert")).toHaveCount(0);
  expect(requests).toBe(2);
});
