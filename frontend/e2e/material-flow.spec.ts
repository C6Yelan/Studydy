import { expect, test } from "@playwright/test";

const materialId = "9f9619ff-8b86-4e3a-a2f1-2bb9424d5c72";
const artifactId = "af9619ff-8b86-4e3a-a2f1-2bb9424d5c73";
const runId = "bf9619ff-8b86-4e3a-a2f1-2bb9424d5c74";

function failedRun() {
  return {
    schema: "material-processing-run/v1",
    run_id: runId,
    material_id: materialId,
    source_artifact_id: artifactId,
    status: "failed",
    catalog_revision: null,
    output_binding: null,
    error_code: "MATERIAL_ANALYSIS_FAILED",
    created_at: "2026-08-15T00:00:00Z",
    updated_at: "2026-08-15T00:01:00Z",
    completed_at: "2026-08-15T00:01:00Z",
  };
}

test("非 PDF 在 client 端明確拒絕且不呼叫 material API", async ({ page }) => {
  let materialCalls = 0;
  page.on("request", (request) => {
    if (new URL(request.url()).pathname === "/v1/materials") materialCalls += 1;
  });

  await page.goto("/");
  await page.getByLabel("資料結構").check();
  await page.getByLabel("選擇 PDF 教材").setInputFiles({
    name: "notes.txt",
    mimeType: "text/plain",
    buffer: Buffer.from("not a pdf"),
  });
  await expect(page.getByRole("alert")).toContainText("只接受 application/pdf");
  await page.getByRole("button", { name: "上傳並開始處理" }).click();
  await expect(page.getByRole("alert")).toContainText("請先選擇 PDF 教材");
  expect(materialCalls).toBe(0);
});

test("terminal failure 缺少 sessionStorage 時 fail closed 並提供返回出口", async ({ page }) => {
  await page.route(`**/v1/material-processing-runs/${runId}`, async (route) => {
    await route.fulfill({ status: 200, json: failedRun() });
  });

  await page.goto(`/materials/${materialId}/runs/${runId}`);
  await expect(page.getByRole("heading", { name: "教材處理失敗" })).toBeVisible();
  await expect(page.getByText("此分頁缺少原科目資訊，無法安全建立新的處理作業。請返回上傳頁重新選擇教材。")).toBeVisible();
  await expect(page.getByRole("button", { name: "使用原教材重新處理" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "返回上傳頁" })).toBeVisible();
});
