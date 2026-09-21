import { expect, test, type Page } from "@playwright/test";
import type { MaterialProcessingRunView, SourceView } from "../src/api/contracts";

const uuid = (n: number) => `11111111-1111-4111-8111-${String(n).padStart(12, "0")}`;
const material = uuid(1), runId = uuid(2);
const stamp = "2026-09-19T00:00:00Z";
const pdf = { name: "A.pdf", mimeType: "application/pdf", buffer: Buffer.from("%PDF-synthetic") };
const txt = { name: "B.txt", mimeType: "text/plain", buffer: Buffer.from("Queue uses FIFO.") };
const failure = { schema: "api-error/v1", request_id: material, reason_code: "STORAGE_UNAVAILABLE", retryable: true, message: "Request could not be completed." };

async function setup(page: Page) {
  const state = { drafts: [] as string[], uploads: [] as { name: string; key: string }[], sources: [] as SourceView[],
    starts: [] as { key: string; body: { normalization_ids: string[]; base_revision: string | null } }[],
    uploadLost: false, runLost: false, draftLost: false, conversionFailed: false, deleted: 0, run: null as MaterialProcessingRunView | null };
  await page.route("**/v1/session", r => r.fulfill({ json: { schema: "learner-identity/v1", learner_id: uuid(99) } }));
  await page.route("**/v1/session/refresh", r => r.fulfill({ status: 204 }));
  await page.route("**/v2/source-capabilities", r => r.fulfill({ json: { schema: "source-capabilities/v1", quality_notice: "PDF 優先", formats: [
    { extension: ".pdf", media_type: "application/pdf", max_bytes: 104857600 }, { extension: ".txt", media_type: "text/plain", max_bytes: 104857600 },
  ] } }));
  await page.route("**/v2/materials", r => {
    state.drafts.push(r.request().headers()["idempotency-key"]);
    if (state.draftLost) { state.draftLost = false; return r.abort("connectionreset"); }
    return r.fulfill({ status: 201, json: { schema: "material-draft/v1", material_id: material } });
  });
  const item = () => ({ schema: "material-library-item/v3", ingestion_kind: "sources-v2", material_id: material,
    source_artifact_id: null, display_name: "A.pdf", size_bytes: 128, created_at: stamp, latest_attempt: state.run, available_structures: [], study_sessions: [], source: state.sources[0] });
  await page.route(`**/v1/materials/${material}`, r => r.fulfill({ json: item() }));
  await page.route("**/v1/materials", r => r.fulfill({ json: { schema: "material-library/v2", materials: [item()] } }));
  const listing = () => ({ schema: "material-sources/v1", material_id: material, sources: state.sources });
  await page.route(`**/v2/materials/${material}/sources`, r => {
    if (r.request().method() === "POST") {
      const name = decodeURIComponent(r.request().headers()["x-material-name"]), key = r.request().headers()["idempotency-key"];
      if (!state.uploads.some(upload => upload.key === key)) {
        const n = 10 + state.sources.length * 10;
        const failed = state.conversionFailed && name === "B.txt";
        state.sources.push({ source_id: uuid(n), normalization_id: uuid(n + 1), original_artifact_id: uuid(n + 2), original_name: name,
          media_type: r.request().headers()["content-type"], status: failed ? "failed" : "ready", normalized_artifact_id: failed ? null : uuid(n + 3), page_count: failed ? null : 1, error_code: failed ? "UTF8_REQUIRED" : null, included: false });
      }
      state.uploads.push({ name, key });
      if (state.uploadLost && name === "B.txt") { state.uploadLost = false; return r.abort("connectionreset"); }
    }
    return r.fulfill({ json: listing() });
  });
  await page.route(`**/v2/materials/${material}/sources/*`, r => {
    expect(r.request().method()).toBe("DELETE"); state.deleted++;
    state.sources = state.sources.filter(source => !r.request().url().endsWith(source.source_id));
    return r.fulfill({ json: listing() });
  });
  await page.route(`**/v2/materials/${material}/revisions`, r => {
    state.starts.push({ key: r.request().headers()["idempotency-key"], body: r.request().postDataJSON() });
    if (state.runLost) { state.runLost = false; return r.fulfill({ status: 503, json: failure }); }
    state.run = { schema: "material-processing-run/v6", run_id: runId, material_id: material, source_artifact_id: uuid(13),
      source_names: state.sources.map(source => source.original_name), status: "pending", progress_stage: "queued", completed_pages: 0, total_pages: null,
      output_binding: null, error_code: null, cancel_requested_at: null, created_at: stamp, updated_at: stamp, completed_at: null };
    return r.fulfill({ status: 202, json: state.run });
  });
  await page.route(`**/v1/material-processing-runs/${runId}`, r => r.fulfill({ json: state.run }));
  return state;
}

for (const width of [1536, 390]) {
  test(`initial multi-source upload confirms exact order and replays analysis at ${width}px`, async ({ page }, info) => {
    await page.setViewportSize({ width, height: 844 });
    const state = await setup(page); state.uploadLost = true; state.runLost = true;
    await page.goto("/upload");
    await page.getByLabel("選擇教材檔案", { exact: true }).setInputFiles([pdf, txt]);
    await expect(page.locator(".chosen-file")).toHaveCount(2);
    const upload = page.getByRole("button", { name: "上傳並確認來源", exact: true });
    await upload.evaluate(element => { (element as HTMLButtonElement).click(); (element as HTMLButtonElement).click(); });
    await expect(page.getByText("上傳失敗，可重試", { exact: false })).toBeVisible();
    expect(state.starts).toHaveLength(0); expect(state.drafts).toHaveLength(1);
    await page.getByRole("button", { name: "重試未完成的上傳" }).click();
    await expect(page).toHaveURL(new RegExp(`/materials/${material}/sources$`));
    expect(state.uploads.map(item => item.name)).toEqual(["A.pdf", "B.txt", "B.txt"]);
    expect(state.uploads[1].key).toBe(state.uploads[2].key);
    await expect(page.locator(".source-card")).toHaveCount(2);
    await expect(page.getByRole("link", { name: /預覽轉換後 PDF/ })).toHaveCount(2);
    await page.reload();
    await page.getByRole("button", { name: "上移 B.txt" }).click();
    await expect(page.locator(".source-card").first()).toContainText("B.txt");
    await expect(page.locator(".source-confirmation")).toContainText("已選 2 份來源，共 2 頁");
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: info.outputPath("initial-multiple.png"), fullPage: true });
    await page.getByRole("button", { name: "開始分析教材", exact: true }).click();
    await expect(page.locator(".source-request-error")).toBeVisible();
    await page.getByRole("button", { name: "開始分析教材", exact: true }).click();
    await expect(page).toHaveURL(new RegExp(`/materials/${material}/runs/${runId}$`));
    expect(state.starts).toHaveLength(2); expect(state.starts[0].key).toBe(state.starts[1].key);
    expect(state.starts[1].body.base_revision).toBeNull();
    expect(state.starts[1].body.normalization_ids).toEqual([uuid(21), uuid(11)]);
    expect(state.deleted).toBe(0);
  });

  test(`failed initial source requires explicit removal before analysis at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 844 });
    const state = await setup(page); state.conversionFailed = true;
    await page.goto("/upload");
    await page.getByLabel("選擇教材檔案", { exact: true }).setInputFiles([pdf, txt]);
    await page.getByRole("button", { name: "上傳並確認來源" }).click();
    const start = page.getByRole("button", { name: "開始分析教材", exact: true });
    await expect(start).toBeDisabled(); await page.reload(); await expect(start).toBeDisabled();
    expect(state.starts).toHaveLength(0);
    await page.getByRole("region", { name: "B.txt", exact: true }).getByRole("button", { name: "移除這份教材", exact: true }).click();
    await expect(start).toBeEnabled();
    await start.click();
    await expect(page).toHaveURL(new RegExp(`/runs/${runId}$`));
    expect(state.deleted).toBe(1); expect(state.starts[0].body.normalization_ids).toEqual([uuid(11)]);
  });
}

test("invalid and oversized files stay visible until explicitly removed", async ({ page }) => {
  const state = await setup(page);
  await page.goto("/upload");
  await page.getByLabel("選擇教材檔案", { exact: true }).setInputFiles([pdf, { name: "empty.txt", mimeType: "text/plain", buffer: Buffer.alloc(0) }]);
  await expect(page.getByRole("alert")).toContainText("不可為空白");
  await expect(page.getByRole("button", { name: "上傳並確認來源" })).toBeDisabled();
  await page.getByRole("button", { name: "移除 empty.txt", exact: true }).click();
  const data = await page.evaluateHandle(() => {
    const transfer = new DataTransfer();
    transfer.items.add(new File([new Uint8Array(104857601)], "too-large.pdf", { type: "application/pdf" }));
    return transfer;
  });
  await page.locator(".file-drop").dispatchEvent("drop", { dataTransfer: data }); await data.dispose();
  await expect(page.getByRole("alert")).toContainText("100 MiB");
  await expect(page.locator(".chosen-file")).toHaveCount(2);
  expect(state.drafts).toHaveLength(0);
  await page.getByRole("button", { name: "移除 too-large.pdf", exact: true }).click();
  await expect(page.getByRole("button", { name: "上傳並確認來源" })).toBeEnabled();
});

test("draft response loss keeps the same intent and never starts analysis from upload", async ({ page }) => {
  const state = await setup(page); state.draftLost = true;
  await page.goto("/upload");
  await page.getByLabel("選擇教材檔案", { exact: true }).setInputFiles(pdf);
  await page.getByRole("button", { name: "上傳並確認來源" }).click();
  await expect(page.getByRole("alert")).toBeVisible();
  await page.getByRole("button", { name: "上傳並確認來源" }).click();
  await expect(page).toHaveURL(new RegExp(`/materials/${material}/sources$`));
  expect(state.drafts).toHaveLength(2); expect(state.drafts[0]).toBe(state.drafts[1]);
  expect(state.uploads).toHaveLength(1); expect(state.starts).toHaveLength(0);
});

test("drag state and accessible picker support multiple files without accidental submission", async ({ page }) => {
  const state = await setup(page); await page.goto("/upload");
  const drop = page.locator(".file-drop"), input = page.getByLabel("選擇教材檔案", { exact: true });
  await input.focus(); expect(await drop.evaluate(element => getComputedStyle(element).outlineStyle)).toBe("solid");
  const picker = page.waitForEvent("filechooser"); await drop.click(); await (await picker).setFiles([pdf, txt]);
  const data = await page.evaluateHandle(() => { const value = new DataTransfer(); value.items.add(new File(["text"], "C.txt", { type: "text/plain" })); return value; });
  await drop.dispatchEvent("dragenter", { dataTransfer: data }); await expect(drop).toHaveClass(/is-dragging/);
  await page.keyboard.press("Escape"); await expect(drop).not.toHaveClass(/is-dragging/);
  await drop.dispatchEvent("drop", { dataTransfer: data }); await data.dispose();
  await expect(page.locator(".chosen-file")).toHaveCount(3);
  expect(state.uploads).toHaveLength(0);
});
