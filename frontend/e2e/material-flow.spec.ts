import { expect, test, type Page } from "@playwright/test";

const materialId = "9f9619ff-8b86-4e3a-a2f1-2bb9424d5c72";
const artifactId = "af9619ff-8b86-4e3a-a2f1-2bb9424d5c73";
const runId = "bf9619ff-8b86-4e3a-a2f1-2bb9424d5c74";

async function sessionReady(page: Page) {
  await page.route("**/v1/session/refresh", (route) => route.fulfill({ status: 204 }));
}

function failedRun() {
  return {
    schema: "material-processing-run/v2",
    run_id: runId,
    material_id: materialId,
    source_artifact_id: artifactId,
    status: "failed",
    output_binding: null,
    error_code: "MATERIAL_PAGE_LIMIT_EXCEEDED",
    created_at: "2026-08-19T00:00:00Z",
    updated_at: "2026-08-19T00:01:00Z",
    completed_at: "2026-08-19T00:01:00Z",
  };
}

test("非 PDF 在 client 端拒絕且不呼叫 material API", async ({ page }) => {
  await sessionReady(page);
  let materialCalls = 0;
  page.on("request", (request) => {
    if (new URL(request.url()).pathname === "/v1/materials") materialCalls += 1;
  });

  await page.goto("/");
  await page.getByLabel("選擇 PDF 教材").setInputFiles({
    name: "notes.txt",
    mimeType: "text/plain",
    buffer: Buffer.from("not a pdf"),
  });
  await expect(page.getByRole("alert")).toContainText("只接受 application/pdf");
  await page.getByRole("button", { name: "上傳並分析完整教材" }).click();
  await expect(page.getByRole("alert")).toContainText("只接受 application/pdf");
  expect(materialCalls).toBe(0);
});

test("33 頁 terminal failure 顯示固定原因並提供返回出口", async ({ page }) => {
  await sessionReady(page);
  await page.route(`**/v1/material-processing-runs/${runId}`, (route) => {
    return route.fulfill({ status: 200, json: failedRun() });
  });

  await page.goto(`/materials/${materialId}/runs/${runId}`);
  await expect(page.getByRole("heading", { name: "教材處理失敗" })).toBeVisible();
  await expect(page.getByText("目前一次最多處理 32 頁 PDF。")).toBeVisible();
  await expect(page.getByText("MATERIAL_PAGE_LIMIT_EXCEEDED")).toBeVisible();
  await expect(page.getByRole("button", { name: "返回上傳" })).toBeVisible();
});
