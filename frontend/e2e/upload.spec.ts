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
  await page.route("**/v1/session/refresh", r => r.fulfill({ json: { schema: "learner-identity/v1", learner_id: "33333333-3333-4333-8333-333333333333" } }));
  await page.route("**/v1/source-capabilities", r => r.fulfill({ json: { schema: "source-capabilities/v1", quality_notice: "PDF 優先", formats: [
    { extension: ".pdf", media_type: "application/pdf", max_bytes: 104857600 }, { extension: ".txt", media_type: "text/plain", max_bytes: 104857600 },
  ] } }));
  await page.route("**/v1/materials", r => {
    state.drafts.push(r.request().headers()["idempotency-key"]);
    if (state.draftLost) { state.draftLost = false; return r.abort("connectionreset"); }
    return r.fulfill({ status: 201, json: { schema: "material-draft/v1", material_id: material } });
  });
  const item = () => ({ schema: "material-library-item/v1", material_id: material,
    source_artifact_id: null, display_name: "A.pdf", size_bytes: 128, created_at: stamp, latest_attempt: state.run, available_structures: [], study_sessions: [], source: state.sources[0] });
  await page.route(`**/v1/materials/${material}`, r => r.fulfill({ json: item() }));
  await page.route("**/v1/materials", r => r.fulfill({ json: { schema: "material-library/v1", materials: [item()] } }));
  const listing = () => ({ schema: "material-sources/v1", material_id: material, sources: state.sources });
  await page.route(`**/v1/materials/${material}/sources`, r => {
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
  await page.route(`**/v1/materials/${material}/sources/*`, r => {
    expect(r.request().method()).toBe("DELETE"); state.deleted++;
    state.sources = state.sources.filter(source => !r.request().url().endsWith(source.source_id));
    return r.fulfill({ json: listing() });
  });
  await page.route(`**/v1/materials/${material}/revisions`, r => {
    state.starts.push({ key: r.request().headers()["idempotency-key"], body: r.request().postDataJSON() });
    if (state.runLost) { state.runLost = false; return r.fulfill({ status: 503, json: failure }); }
    state.run = { schema: "material-processing-run/v1", run_id: runId, material_id: material, source_artifact_id: uuid(13),
      source_names: state.sources.map(source => source.original_name), status: "pending", progress_stage: "queued", completed_pages: 0, total_pages: null,
      output_binding: null, error_code: null, cancel_requested_at: null, created_at: stamp, updated_at: stamp, completed_at: null };
    return r.fulfill({ status: 202, json: state.run });
  });
  await page.route(`**/v1/material-processing-runs/${runId}`, r => r.fulfill({ json: state.run }));
  return state;
}

for (const width of [1536, 390]) {
  test(`rejected selections stay outside the queue and valid mixed files still upload at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 844 });
    const state = await setup(page);
    await page.goto("/upload");
    const input = page.getByLabel("選擇教材檔案", { exact: true });
    await expect(input).toBeEnabled();
    const dropJpg = async () => {
      const data = await page.evaluateHandle(() => {
        const transfer = new DataTransfer();
        transfer.items.add(new File([new Uint8Array(4096)], "screenshot.jpg", { type: "image/jpeg" }));
        return transfer;
      });
      await page.locator(".file-drop").dispatchEvent("drop", { dataTransfer: data });
      await data.dispose();
    };
    const rejection = page.locator("#upload-selection-error");
    const cta = page.getByRole("button", { name: "上傳並確認來源", exact: true });
    await dropJpg();
    await expect(rejection).toContainText("screenshot.jpg");
    await expect(rejection).toContainText("不支援");
    await expect(page.locator(".chosen-file")).toHaveCount(0);
    await expect(cta).toBeDisabled();
    await input.setInputFiles(pdf);
    await expect(rejection).toHaveCount(0);
    await dropJpg();
    await expect(page.locator(".chosen-file strong")).toHaveText(["A.pdf"]);
    await expect(page.getByText("共 1 份 · 1 KiB", { exact: true })).toBeVisible();
    await expect(cta).toBeEnabled();
    const drop = (await page.locator(".file-drop").boundingBox())!, error = (await rejection.boundingBox())!, card = (await page.locator(".chosen-file").boundingBox())!;
    expect(error.y).toBeGreaterThanOrEqual(drop.y + drop.height);
    expect(error.y + error.height).toBeLessThanOrEqual(card.y);
    await page.getByRole("button", { name: "移除 A.pdf", exact: true }).click();
    await expect(page.locator(".chosen-file")).toHaveCount(0);
    await input.setInputFiles([pdf, txt, { name: "screenshot.jpg", mimeType: "image/jpeg", buffer: Buffer.alloc(4096) }]);
    await expect(page.locator(".chosen-file strong")).toHaveText(["A.pdf", "B.txt"]);
    await expect(rejection).toContainText("screenshot.jpg");
    await expect(page.getByText("共 2 份 · 1 KiB", { exact: true })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await cta.click();
    await expect(page).toHaveURL(new RegExp(`/materials/${material}/sources$`));
    expect(state.uploads.map(upload => upload.name)).toEqual(["A.pdf", "B.txt"]);
    expect(state.starts).toHaveLength(0);
  });

  test(`upload copy separates file selection from processing explanation at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 844 });
    const state = await setup(page);
    await page.goto("/upload");
    await expect(page.getByRole("heading", { name: "上傳教材", exact: true })).toBeVisible();
    await expect(page.locator(".upload-hero p:last-child")).toHaveText("選擇一份或多份教材，確認來源後再開始分析。");
    await expect(page.locator(".upload-card").getByRole("heading")).toHaveCount(0);
    await expect(page.locator(".upload-page")).not.toContainText(/接下來會發生什麼|檔案需求|建議優先上傳 PDF|配置的 AI/);
    const guide = page.getByRole("complementary", { name: "教材處理說明" });
    await expect(guide.getByRole("heading")).toHaveText("Studydy 如何處理教材");
    await expect(guide.locator("li strong")).toHaveText(["準備教材", "整理內容與來源", "建立知識結構", "建立知識地圖"]);
    await expect(guide.locator("li p")).toHaveText([
      "各份來源分開保留；非 PDF 教材會先轉換為可分析的 PDF。",
      "讀取教材內容，需要時辨識圖片中的文字，並保留可回查的原始來源。",
      "從教材整理重要概念、概念關係與建議學習順序。",
      "將整理結果呈現在知識地圖中，之後可探索概念並回查教材來源。",
    ]);
    await expect(page.locator(".file-drop strong")).toHaveText("將教材拖放到此處，或點擊選取");
    await expect(page.locator(".file-drop > span:last-child")).toHaveText("PDF、TXT · 可選多份 · 每份最大 100 MiB");
    await expect(page.getByText("PDF、TXT · 可選多份 · 每份最大 100 MiB", { exact: true })).toHaveCount(1);
    await expect(page.getByText("確認來源後才會開始 AI 分析", { exact: true })).toHaveCount(0);
    await expect(page.locator(".privacy-note")).toHaveCount(0);
    await expect(page.locator(".upload-card > :last-child")).toHaveText("上傳並確認來源");
    const conversion = page.getByText("非 PDF 教材會先轉換為 PDF，請在下一步確認轉換內容。", { exact: true });
    const input = page.getByLabel("選擇教材檔案", { exact: true });
    await expect(conversion).toHaveCount(0);
    await input.setInputFiles({ ...pdf, name: "A.PDF" });
    await expect(conversion).toHaveCount(0);
    await input.setInputFiles(txt);
    await expect(page.locator(".chosen-file")).toHaveCount(2);
    await expect(conversion).toBeVisible();
    await expect(page.getByRole("button", { name: "上傳並確認來源", exact: true })).toBeEnabled();
    await page.getByRole("button", { name: "移除 B.txt", exact: true }).click();
    await expect(conversion).toHaveCount(0);
    await page.getByRole("button", { name: "移除 A.PDF", exact: true }).click();
    await input.setInputFiles(txt);
    await expect(conversion).toBeVisible();
    await expect(page.locator(".chosen-file")).toHaveCount(1);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    expect(state.drafts).toHaveLength(0); expect(state.uploads).toHaveLength(0); expect(state.starts).toHaveLength(0);
  });

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
    await page.getByLabel("選擇教材檔案", { exact: true }).setInputFiles({ name: "image.jpg", mimeType: "image/jpeg", buffer: Buffer.from("image") });
    await expect(page.locator(".chosen-file")).toHaveCount(2);
    await expect(page.locator(".chosen-file").filter({ hasText: "B.txt" }).getByRole("alert")).toBeVisible();
    await expect(page.locator("#upload-selection-error")).toContainText("image.jpg");
    await page.getByRole("button", { name: "重試未完成的上傳" }).click();
    await expect(page).toHaveURL(new RegExp(`/materials/${material}/sources$`));
    expect(state.uploads.map(item => item.name)).toEqual(["A.pdf", "B.txt", "B.txt"]);
    expect(state.uploads[1].key).toBe(state.uploads[2].key);
    await expect(page.locator(".source-row")).toHaveCount(2);
    await expect(page.getByRole("link", { name: /預覽 PDF/ })).toHaveCount(2);
    await page.reload();
    await page.getByRole("button", { name: "上移 B.txt" }).click();
    await expect(page.locator(".source-row").first()).toContainText("B.txt");
    await expect(page.locator(".source-list-footer")).toContainText("2 份教材 · 共 2 頁");
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
    await expect(page.getByRole("listitem", { name: "B.txt", exact: true }).getByRole("status")).toHaveText("轉換失敗");
    await expect(start).toHaveCount(0); await page.reload();
    await expect(page.getByRole("listitem", { name: "B.txt", exact: true }).getByRole("status")).toHaveText("轉換失敗");
    await expect(start).toHaveCount(0);
    expect(state.starts).toHaveLength(0);
    await page.getByRole("listitem", { name: "B.txt", exact: true }).getByRole("button", { name: "移除", exact: true }).click();
    await expect(start).toBeEnabled();
    await start.click();
    await expect(page).toHaveURL(new RegExp(`/runs/${runId}$`));
    expect(state.deleted).toBe(1); expect(state.starts[0].body.normalization_ids).toEqual([uuid(11)]);
  });
}

test("capability read failure keeps PDF selection and its actionable notice", async ({ page }) => {
  await setup(page);
  await page.route("**/v1/source-capabilities", route => route.fulfill({ status: 503, json: failure }));
  await page.goto("/upload");
  await expect(page.getByRole("status")).toHaveText("其他格式目前無法載入，仍可上傳 PDF。");
  await expect(page.locator(".file-drop > span:last-child")).toHaveText("PDF · 可選多份 · 每份最大 100 MiB");
  await page.getByLabel("選擇教材檔案", { exact: true }).setInputFiles(pdf);
  await expect(page.getByRole("button", { name: "上傳並確認來源", exact: true })).toBeEnabled();
});

test("selection waits for capabilities instead of rejecting a supported non-PDF format", async ({ page }) => {
  await setup(page);
  let release!: () => void;
  const pending = new Promise<void>(resolve => { release = resolve; });
  await page.route("**/v1/source-capabilities", async route => { await pending; await route.fallback(); });
  await page.goto("/upload");
  const input = page.getByLabel("選擇教材檔案", { exact: true });
  await expect(input).toBeDisabled();
  const data = await page.evaluateHandle(() => {
    const transfer = new DataTransfer(); transfer.items.add(new File(["text"], "B.txt", { type: "text/plain" })); return transfer;
  });
  await page.locator(".file-drop").dispatchEvent("drop", { dataTransfer: data }); await data.dispose();
  await expect(page.getByRole("alert")).toContainText("支援格式載入");
  await expect(page.locator(".chosen-file")).toHaveCount(0);
  release(); await expect(input).toBeEnabled();
  await input.setInputFiles(txt);
  await expect(page.locator(".chosen-file strong")).toHaveText(["B.txt"]);
  await expect(page.getByRole("alert")).toHaveCount(0);
});

test("client-known invalid files are rejected before enqueue without blocking valid files", async ({ page }) => {
  const state = await setup(page);
  await page.goto("/upload");
  await page.getByLabel("選擇教材檔案", { exact: true }).setInputFiles([pdf, { name: "empty.txt", mimeType: "text/plain", buffer: Buffer.alloc(0) }]);
  await expect(page.getByRole("alert")).toContainText("不可為空白");
  await expect(page.locator(".chosen-file")).toHaveCount(1);
  await expect(page.locator(".chosen-file")).not.toContainText("empty.txt");
  await expect(page.getByRole("button", { name: "上傳並確認來源" })).toBeEnabled();
  const data = await page.evaluateHandle(() => {
    const transfer = new DataTransfer();
    transfer.items.add(new File([new Uint8Array(104857601)], "too-large.pdf", { type: "application/pdf" }));
    return transfer;
  });
  await page.locator(".file-drop").dispatchEvent("drop", { dataTransfer: data }); await data.dispose();
  await expect(page.getByRole("alert")).toContainText("100 MiB");
  await expect(page.locator(".chosen-file")).toHaveCount(1);
  await expect(page.locator(".chosen-file")).not.toContainText("too-large.pdf");
  await page.getByLabel("選擇教材檔案", { exact: true }).setInputFiles({ name: "mismatch.pdf", mimeType: "text/plain", buffer: Buffer.from("text") });
  await expect(page.getByRole("alert")).toContainText("副檔名與檔案類型不一致");
  await expect(page.locator(".chosen-file")).toHaveCount(1);
  expect(state.drafts).toHaveLength(0);
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
  await expect(input).toBeEnabled();
  await input.focus(); expect(await drop.evaluate(element => getComputedStyle(element).outlineStyle)).toBe("solid");
  const picker = page.waitForEvent("filechooser"); await drop.click(); await (await picker).setFiles([pdf, txt]);
  const data = await page.evaluateHandle(() => { const value = new DataTransfer(); value.items.add(new File(["text"], "C.txt", { type: "text/plain" })); return value; });
  await drop.dispatchEvent("dragenter", { dataTransfer: data }); await expect(drop).toHaveClass(/is-dragging/);
  await page.keyboard.press("Escape"); await expect(drop).not.toHaveClass(/is-dragging/);
  await drop.dispatchEvent("drop", { dataTransfer: data }); await data.dispose();
  await expect(page.locator(".chosen-file")).toHaveCount(3);
  for (const kind of ["text/plain", "text/uri-list", "folder"]) {
    const invalidDrop = await page.evaluateHandle(kind => {
      const transfer = new DataTransfer();
      if (kind === "folder") {
        transfer.items.add(new File(["file"], "notes"));
        Object.defineProperty(transfer, "items", { value: [{ webkitGetAsEntry: () => ({ isDirectory: true }) }] });
      } else transfer.setData(kind, kind === "text/plain" ? "text" : "https://example.com/notes");
      return transfer;
    }, kind);
    await drop.dispatchEvent("drop", { dataTransfer: invalidDrop }); await invalidDrop.dispose();
    await expect(page.locator("#upload-selection-error")).toContainText(kind === "folder" ? "不接受資料夾" : "不接受文字或網址");
    await expect(page.locator(".chosen-file")).toHaveCount(3);
  }
  expect(state.uploads).toHaveLength(0);
});
