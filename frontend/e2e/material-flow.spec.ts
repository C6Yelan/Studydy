import { expect, test, type Page } from "@playwright/test";

const materialId = "9f9619ff-8b86-4e3a-a2f1-2bb9424d5c72";
const artifactId = "af9619ff-8b86-4e3a-a2f1-2bb9424d5c73";
const runId = "bf9619ff-8b86-4e3a-a2f1-2bb9424d5c74";

async function sessionReady(page: Page) {
  await page.route("**/v1/session/refresh", (route) => route.fulfill({ status: 204 }));
}

async function dropFiles(page: Page, files: { name: string; type: string; content: string }[]) {
  const dataTransfer = await page.evaluateHandle((items) => {
    const transfer = new DataTransfer();
    items.forEach((item) => transfer.items.add(new File(
      [item.content], item.name, { type: item.type }
    )));
    return transfer;
  }, files);
  await page.locator(".file-drop").dispatchEvent("drop", { dataTransfer });
  await dataTransfer.dispose();
}

function failedRun() {
  return {
    schema: "material-processing-run/v3",
    run_id: runId,
    material_id: materialId,
    source_artifact_id: artifactId,
    status: "failed",
    progress_stage: "concept_generation",
    completed_pages: 4,
    total_pages: 7,
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
    progress_stage: "completed",
    completed_pages: 7,
    total_pages: 7,
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

function activeRun(
  progressStage: "queued" | "page_evidence" | "concept_generation" | "knowledge_map_generation" | "publishing",
  completedPages: number,
  totalPages: number | null,
) {
  return {
    ...failedRun(),
    status: progressStage === "queued" ? "pending" : "running",
    progress_stage: progressStage,
    completed_pages: completedPages,
    total_pages: totalPages,
    error_code: null,
    completed_at: null,
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
  await expect(page.getByRole("alert")).toContainText("這不是可用的 PDF");
  await page.getByRole("button", { name: "上傳並分析完整教材" }).click();
  await expect(page.getByRole("alert")).toContainText("這不是可用的 PDF");
  expect(materialCalls).toBe(0);
});

test("drop 與 file input 共用 PDF、multiple 與 type validation", async ({ page }) => {
  await sessionReady(page);
  await page.goto("/");
  const input = page.getByLabel("選擇 PDF 教材");
  const lesson = {
    name: "lesson.pdf",
    mimeType: "application/pdf",
    buffer: Buffer.from("%PDF-1.4"),
  };

  await input.setInputFiles(lesson);
  await expect(page.getByLabel("已選擇的檔案")).toContainText("lesson.pdf");
  await expect(page.getByLabel("已選擇的檔案")).toContainText("準備上傳");

  await dropFiles(page, [
    { name: "one.pdf", type: "application/pdf", content: "%PDF-1.4" },
    { name: "two.pdf", type: "application/pdf", content: "%PDF-1.4" },
  ]);
  await expect(page.getByRole("alert")).toContainText("一次只能處理一份 PDF");
  await expect(page.getByLabel("已選擇的檔案")).toHaveCount(0);
  await expect(input).toHaveValue("");

  await input.setInputFiles(lesson);
  await expect(page.getByLabel("已選擇的檔案")).toContainText("lesson.pdf");

  await input.setInputFiles({
    name: "notes.txt",
    mimeType: "text/plain",
    buffer: Buffer.from("notes"),
  });
  await expect(page.getByRole("alert")).toContainText("這不是可用的 PDF");
  await expect(page.getByLabel("已選擇的檔案")).toHaveCount(0);
  await expect(input).toHaveValue("");

  await input.setInputFiles(lesson);
  await expect(page.getByLabel("已選擇的檔案")).toContainText("lesson.pdf");
  await expect(page.getByLabel("已選擇的檔案")).toContainText("準備上傳");
});

test("內容無法建立 Evidence 時顯示 truthful terminal failure", async ({ page }) => {
  await sessionReady(page);
  await page.route(`**/v1/material-processing-runs/${runId}`, (route) => {
    return route.fulfill({ status: 200, json: failedRun() });
  });

  await page.goto(`/materials/${materialId}/runs/${runId}`);
  await expect(page.getByRole("heading", { name: "教材處理失敗" })).toBeVisible();
  await expect(page.getByText("教材沒有產生可安全回查的概念與依據。")).toBeVisible();
  await expect(page.getByText("最後安全進度：建立並檢查教材概念，4 / 7 頁")).toBeVisible();
  await expect(page.getByText("NO_USABLE_EVIDENCE")).toBeVisible();
  await expect(page.getByRole("button", { name: "返回上傳" })).toBeVisible();
});

test("處理頁顯示 current-stage 真實頁數、排隊與估算中", async ({ page }) => {
  await sessionReady(page);
  const snapshots = [
    activeRun("queued", 0, null),
    activeRun("page_evidence", 2, 7),
    activeRun("knowledge_map_generation", 7, 7),
  ];
  let calls = 0;
  await page.route(`**/v1/material-processing-runs/${runId}`, (route) => {
    const snapshot = snapshots[Math.min(Math.max(calls - 1, 0), snapshots.length - 1)];
    calls += 1;
    return route.fulfill({ status: 200, json: snapshot });
  });

  await page.goto(`/materials/${materialId}/runs/${runId}`);
  await expect(page.getByText(/一個本機處理工作依序執行/)).toBeVisible();
  await expect(page.getByText("估算中")).toBeVisible();
  await expect(page.getByText(/可以離開此頁，稍後返回同一處理作業/)).toBeVisible();
  const pageProgress = page.getByRole("progressbar", { name: /整理頁面與教材來源 2 \/ 7 頁/ });
  await expect(pageProgress).toHaveAttribute("max", "7", { timeout: 6_000 });
  await expect(pageProgress).toHaveAttribute("value", "2");
  const statusRegion = page.locator(".processing-status");
  await expect(statusRegion).toHaveAttribute("aria-live", "polite");
  await expect(statusRegion).toContainText("目前階段已完成 2 / 7 頁");
  await expect(statusRegion.getByText("已經過")).toHaveCount(0);
  await expect(page.locator(".processing-times").getByText("已經過")).toBeVisible();
  await expect(
    page.locator(".processing-times").locator("xpath=ancestor::*[@aria-live]")
  ).toHaveCount(0);
  await expect(page.locator("section.processing-page")).not.toHaveAttribute("aria-live", "polite");
  await expect(page.getByText("建立知識地圖", { exact: true }).first()).toBeVisible({ timeout: 6_000 });
  await expect(page.locator('.indeterminate-progress[aria-label*="建立知識地圖"]')).toBeVisible();
  await expect(page.getByText(/%|預計.*分鐘|剩餘.*分鐘/)).toHaveCount(0);
});

test("validated latest pointer can return, while stale owner-scoped run fails safe", async ({ page }) => {
  await page.addInitScript(({ materialId, runId }) => {
    localStorage.setItem("studydy.latest-material-run/v1", JSON.stringify({ materialId, runId }));
  }, { materialId, runId });
  await sessionReady(page);
  await page.route(`**/v1/material-processing-runs/${runId}`, (route) => route.fulfill({
    status: 404,
    json: {
      schema: "api-error/v1",
      request_id: "6f9619ff-8b86-4e3a-a2f1-2bb9424d5c71",
      reason_code: "RESOURCE_NOT_FOUND",
      retryable: false,
      message: "Request could not be completed.",
    },
  }));

  await page.goto("/");
  await page.getByRole("button", { name: "返回最近處理作業" }).click();
  await expect(page).toHaveURL(`/materials/${materialId}/runs/${runId}`);
  await expect(page.getByRole("heading", { name: "無法讀取處理狀態" })).toBeVisible();
  await expect(page.getByText(/找不到這筆資料，或你沒有權限讀取/)).toBeVisible();
  await expect.poll(() => page.evaluate(() => localStorage.getItem("studydy.latest-material-run/v1"))).toBeNull();
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
