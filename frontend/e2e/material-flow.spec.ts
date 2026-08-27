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
    error_code: "NO_USABLE_EVIDENCE",
    created_at: "2026-08-19T00:00:00Z",
    updated_at: "2026-08-19T00:01:00Z",
    completed_at: "2026-08-19T00:01:00Z",
  };
}

function noConceptRun() {
  return {
    ...failedRun(),
    status: "partial",
    output_binding: {
      schema: "material-run-output-binding/v3",
      producer_bundle_id: `text-first-producer-bundle:sha256:${"1".repeat(64)}`,
      producer_run_id: "text-first-run:00000000-0000-4000-8000-000000000001",
      concept_evidence_output_id: `concept-evidence-output:sha256:${"2".repeat(64)}`,
      study_material_output_revision: `study-material-output:sha256:${"3".repeat(64)}`,
      knowledge_map_revision: `knowledge-map:sha256:${"4".repeat(64)}`,
      runtime_binding_sha256: "5".repeat(64),
      page_count: 7,
      processing: "partial",
      quality: "needs_review",
      decision: "review",
      reason_codes: ["KNOWLEDGE_MAP_REVIEW_REQUIRED", "NO_FORMAL_CONCEPT"],
      ocr_calls: 0,
      concept_calls: 7,
    },
    error_code: null,
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

test("內容無法建立 Evidence 時顯示 truthful terminal failure", async ({ page }) => {
  await sessionReady(page);
  await page.route(`**/v1/material-processing-runs/${runId}`, (route) => {
    return route.fulfill({ status: 200, json: failedRun() });
  });

  await page.goto(`/materials/${materialId}/runs/${runId}`);
  await expect(page.getByRole("heading", { name: "教材處理失敗" })).toBeVisible();
  await expect(page.getByText("教材沒有產生可安全回查的概念與依據。")).toBeVisible();
  await expect(page.getByText("NO_USABLE_EVIDENCE")).toBeVisible();
  await expect(page.getByRole("button", { name: "返回上傳" })).toBeVisible();
});

test("0 Concept partial run 不顯示完成或開啟地圖", async ({ page }) => {
  await sessionReady(page);
  await page.route(`**/v1/material-processing-runs/${runId}`, (route) => {
    return route.fulfill({ status: 200, json: noConceptRun() });
  });

  await page.goto(`/materials/${materialId}/runs/${runId}`);
  await expect(page.getByRole("heading", { name: "目前沒有可開啟的知識地圖" })).toBeVisible();
  await expect(page.getByRole("button", { name: "改用其他教材" })).toBeVisible();
  await expect(page.getByText("一切準備完成")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "開啟複核地圖" })).toHaveCount(0);
});


test("選擇不同 PDF 會更換 upload 與 run idempotency keys", async ({ page }) => {
  await sessionReady(page);
  const uploadKeys: string[] = [];
  const runKeys: string[] = [];
  let uploadCount = 0;
  await page.route("**/v1/materials", async (route) => {
    uploadKeys.push(route.request().headers()["idempotency-key"]);
    uploadCount += 1;
    await route.fulfill({
      status: 201,
      json: {
        schema: "material/v1",
        material_id: uploadCount === 1 ? materialId : "9f9619ff-8b86-4e3a-a2f1-2bb9424d5c75",
        source_artifact_id: uploadCount === 1 ? artifactId : "af9619ff-8b86-4e3a-a2f1-2bb9424d5c76",
        source_sha256: "a".repeat(64),
        size_bytes: 20,
      },
    });
  });
  await page.route("**/v1/material-processing-runs", async (route) => {
    runKeys.push(route.request().headers()["idempotency-key"]);
    await route.fulfill({
      status: 503,
      json: {
        schema: "api-error/v1",
        request_id: "6f9619ff-8b86-4e3a-a2f1-2bb9424d5c71",
        reason_code: "STORAGE_UNAVAILABLE",
        retryable: true,
        message: "Request could not be completed.",
      },
    });
  });

  await page.goto("/");
  const input = page.locator('input[type="file"]');
  for (const name of ["first.pdf", "second.pdf"]) {
    await input.setInputFiles({
      name,
      mimeType: "application/pdf",
      buffer: Buffer.from("%PDF-1.4\n%%EOF"),
    });
    await page.getByRole("button", { name: "上傳並分析完整教材" }).click();
    await expect(page.getByRole("alert")).toBeVisible();
  }

  expect(uploadKeys).toHaveLength(2);
  expect(runKeys).toHaveLength(2);
  expect(uploadKeys[0]).not.toBe(uploadKeys[1]);
  expect(runKeys[0]).not.toBe(runKeys[1]);
});
