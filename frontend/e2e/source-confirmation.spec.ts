import { expect, test } from "@playwright/test";
import type { MaterialLibraryItem, MaterialProcessingRunView, SourceView } from "../src/api/contracts";

const uuid = (n: number) => `22222222-2222-4222-8222-${String(n).padStart(12, "0")}`;
const material = uuid(1), runId = uuid(2), revision = `knowledge-structure:sha256:${"a".repeat(64)}`;
const stamp = "2026-09-21T00:00:00Z";
for (const width of [1536, 1366, 390]) test.describe(`compact source confirmation at ${width}px`, () => {
  test.use({ viewport: { width, height: width === 390 ? 844 : 1024 }, hasTouch: width === 390 });
  for (const append of [false, true]) test(`${append ? "append selection" : "initial order"} preserves actions and source authority`, async ({ page, context }, info) => {
    let sources: SourceView[] = ["Networks.pdf", "Transport.pptx", "Applications.txt"].map((original_name, i) => ({
      source_id: uuid(10 + i), normalization_id: uuid(20 + i), original_artifact_id: uuid(30 + i), normalized_artifact_id: uuid(40 + i),
      original_name, media_type: i === 0 ? "application/pdf" : i === 1 ? "application/vnd.openxmlformats-officedocument.presentationml.presentation" : "text/plain",
      status: "ready", page_count: 10, error_code: null, included: append && i === 0,
    }));
    let run: MaterialProcessingRunView | null = null, retries = 0, uploads = 0;
    const removed: string[] = [], starts: unknown[] = [], uploadKeys: string[] = [];
    let loseUpload = true;
    let releaseUpload!: () => void, releaseRefresh!: () => void, holdRefresh = false;
    const uploadPending = new Promise<void>(resolve => { releaseUpload = resolve; });
    const refreshPending = new Promise<void>(resolve => { releaseRefresh = resolve; });
    const item = (): MaterialLibraryItem => ({ schema: "material-library-item/v3", ingestion_kind: "sources-v2", material_id: material,
      display_name: "網路概論", source_artifact_id: uuid(40), source_count: sources.length, size_bytes: 1024, created_at: stamp,
      latest_attempt: run, study_sessions: [], available_structures: append ? [{ run_id: runId, knowledge_structure_revision: revision, status: "succeeded", created_at: stamp }] : [] });
    const listing = () => ({ schema: "material-sources/v1", material_id: material, sources });
    await page.route("**/v1/session", r => r.fulfill({ json: { schema: "learner-identity/v1", learner_id: uuid(99) } }));
    await page.route("**/v1/session/refresh", r => r.fulfill({ json: { schema: "learner-identity/v1", learner_id: "33333333-3333-4333-8333-333333333333" } }));
    await page.route("**/v2/source-capabilities", r => r.fulfill({ json: { schema: "source-capabilities/v1", quality_notice: "PDF", formats: [
      { extension: ".pdf", media_type: "application/pdf", max_bytes: 104857600 }, { extension: ".txt", media_type: "text/plain", max_bytes: 104857600 },
    ] } }));
    await page.route(`**/v1/materials/${material}`, r => r.fulfill({ json: item() }));
    await page.route(`**/v2/materials/${material}/sources`, async r => {
      if (r.request().method() === "POST") {
        uploads++; uploadKeys.push(r.request().headers()["idempotency-key"]);
        if (loseUpload) { await uploadPending; loseUpload = false; return r.abort("connectionreset"); }
        sources.push({ ...sources[0], source_id: uuid(13), normalization_id: uuid(23), original_name: "Extra.txt", included: false,
          status: "running", normalized_artifact_id: null, page_count: null });
        holdRefresh = true;
      } else if (holdRefresh) {
        await refreshPending;
      }
      return r.fulfill({ json: listing() });
    });
    await page.route(`**/v2/materials/${material}/sources/*`, r => {
      expect(r.request().method()).toBe("DELETE");
      const id = r.request().url().split("/").at(-1)!; removed.push(id);
      expect(sources.find(s => s.source_id === id)?.included).toBe(false);
      sources = sources.filter(s => s.source_id !== id); return r.fulfill({ json: listing() });
    });
    await page.route(`**/v2/materials/${material}/sources/*/retry`, r => {
      retries++; sources[2] = { ...sources[2], status: "ready", normalized_artifact_id: uuid(42), page_count: 10, error_code: null };
      return r.fulfill({ json: listing() });
    });
    await page.route(`**/v2/materials/${material}/revisions`, r => {
      starts.push(r.request().postDataJSON());
      run = { schema: "material-processing-run/v6", run_id: uuid(3), material_id: material, source_artifact_id: uuid(40),
        status: "pending", progress_stage: "queued", completed_pages: 0, total_pages: null, output_binding: null,
        error_code: null, cancel_requested_at: null, created_at: stamp, updated_at: stamp, completed_at: null };
      return r.fulfill({ status: 202, json: run });
    });
    await page.route(`**/v1/material-processing-runs/${uuid(3)}`, r => r.fulfill({ json: run }));
    await context.route("**/v1/artifacts/*", r => r.fulfill({ contentType: "text/plain", body: "Synthetic preview destination" }));
    const activate = async (locator: ReturnType<typeof page.locator>) => width === 390 ? locator.tap() : locator.click();
    await page.goto(`/materials/${material}/sources`);
    await expect(page.getByRole("heading", { name: append ? "新增教材" : "確認教材", exact: true })).toBeVisible();
    const rows = page.locator(".source-row"), start = page.getByRole("button", { name: append ? "確認新增並更新地圖" : "開始分析教材", exact: true });
    await expect(rows).toHaveCount(3); await expect(start).toBeEnabled();
    await expect(page.locator(".source-list-card")).toHaveCount(1);
    await expect(page.locator(".source-page")).not.toContainText(/原始教材已保留|轉換完成|選擇新增檔案|確認分析清單|轉換提醒/);
    await expect(page.locator(".source-row").getByRole("status")).toHaveCount(0);
    await expect(page.getByRole("link", { name: "預覽 PDF", exact: true })).toHaveCount(3);
    const heights = await rows.evaluateAll(es => es.map(e => e.getBoundingClientRect().height));
    expect(Math.max(...heights)).toBeLessThan(width === 390 ? 180 : 145);
    expect((await start.boundingBox())!.y).toBeLessThan(width === 390 ? 1050 : 900);
    await page.screenshot({ path: info.outputPath(`${append ? "append" : "initial"}-ready.png`), fullPage: true });
    await expect(page.locator(".source-menu, .source-management")).toHaveCount(0);
    for (const [index, row] of (await rows.all()).entries()) {
      await expect(row.getByRole("link", { name: "預覽 PDF", exact: true })).toBeVisible();
      await expect(row.getByRole("link", { name: "下載原檔", exact: true })).toBeVisible();
      if (append && index === 0) await expect(row.getByRole("button", { name: "移除", exact: true })).toHaveCount(0);
      else await expect(row.getByRole("button", { name: "移除", exact: true })).toBeVisible();
    }
    await rows.first().getByRole("link", { name: "預覽 PDF", exact: true }).focus();
    await page.keyboard.press("Tab");
    await expect(rows.first().getByRole("link", { name: "下載原檔", exact: true })).toBeFocused();
    const deleteMaterial = page.getByRole("button", { name: "刪除教材", exact: true });
    await activate(deleteMaterial);
    await expect(page.getByRole("heading", { name: "確定要刪除這份教材嗎？" })).toBeVisible();
    await expect(page.locator(".cancel-confirmation")).toContainText(append ? "這份教材及已建立的知識地圖" : "目前已上傳的 3 份教材，以及已產生的轉換內容");
    await expect(page.locator(".cancel-confirmation")).not.toContainText(/學習紀錄|題目|作答|處理紀錄/);
    await expect(page.getByRole("button", { name: "取消", exact: true })).toBeFocused();
    await page.keyboard.press("Escape"); await expect(deleteMaterial).toBeFocused();
    await activate(deleteMaterial); await activate(page.getByRole("button", { name: "取消", exact: true }));
    await expect(deleteMaterial).toBeFocused();
    const popup = page.waitForEvent("popup"); await activate(rows.first().getByRole("link", { name: "預覽 PDF", exact: true }));
    const preview = await popup; await expect(preview).toHaveURL(new RegExp(`/v1/artifacts/${uuid(40)}$`)); await preview.close();
    if (append) {
      await expect(page.getByRole("button", { name: /^上移 / })).toHaveCount(0);
      await rows.nth(2).getByRole("checkbox").uncheck();
      await expect(page.locator(".source-summary")).toHaveText("本次新增 1 份教材 · 共 10 頁");
      await rows.nth(2).getByRole("checkbox").check();
    } else {
      await expect(page.getByRole("button", { name: "上移 Networks.pdf", exact: true })).toBeDisabled();
      await expect(page.getByRole("button", { name: "下移 Applications.txt", exact: true })).toBeDisabled();
      await page.getByRole("button", { name: "上移 Transport.pptx", exact: true }).focus(); await page.keyboard.press("Enter");
      await expect(rows.first()).toContainText("Transport.pptx");
    }
    sources[2] = { ...sources[2], status: "pending", normalized_artifact_id: null, page_count: null };
    await page.reload(); await expect(page.locator(".source-list-footer")).toContainText("等待教材轉換完成"); await expect(start).toHaveCount(0);
    sources[2].status = "running";
    const failed = page.getByRole("listitem", { name: "Applications.txt", exact: true });
    await expect(failed.getByRole("status")).toHaveText("正在轉換…");
    await expect(failed.getByRole("button", { name: "移除", exact: true })).toBeDisabled();
    sources[2] = { ...sources[2], status: "failed", error_code: "NORMALIZATION_FAILED" };
    await expect(failed.getByRole("status")).toHaveText("轉換失敗");
    if (append) {
      await failed.getByRole("checkbox").uncheck(); await expect(start).toBeEnabled();
      await failed.getByRole("checkbox").check(); await expect(start).toHaveCount(0);
    }
    await failed.locator(".source-technical summary").click(); await expect(failed.locator("code")).toHaveText("NORMALIZATION_FAILED");
    await failed.getByRole("button", { name: "重試轉換", exact: true }).click(); await expect(start).toBeEnabled(); expect(retries).toBe(1);
    await failed.getByRole("button", { name: "移除", exact: true }).click();
    await expect(rows).toHaveCount(2); expect(removed).toEqual([uuid(12)]);
    const input = page.getByLabel("選擇新增教材", { exact: true });
    await expect(input).toBeEnabled();
    const chooser = page.waitForEvent("filechooser");
    await activate(page.locator(".source-add-control"));
    await (await chooser).setFiles([{ name: "Extra.txt", mimeType: "text/plain", buffer: Buffer.from("extra") }, { name: "image.jpg", mimeType: "image/jpeg", buffer: Buffer.from("image") }]);
    await expect(page.locator(".source-upload-row")).toHaveCount(1); await expect(page.locator("#source-selection-error")).toContainText("image.jpg");
    await expect(start).toHaveCount(0);
    const local = page.locator(".source-list > .source-upload-row");
    await expect(rows).toHaveCount(3);
    await expect(page.locator(".source-list-card .primary-button")).toHaveText("上傳新增教材");
    await expect(page.locator(".source-list-card .chosen-file")).toHaveCount(0);
    await expect(local.getByRole("status")).toHaveText("待上傳");
    await expect(local.getByRole("link")).toHaveCount(0);
    await expect(local.getByRole("button", { name: /^上移 |^下移 / })).toHaveCount(0);
    expect(await local.getByRole("status").evaluate(el => {
      const sample = document.createElement("span"); sample.style.color = "var(--text-secondary)"; el.append(sample);
      const same = getComputedStyle(el).color === getComputedStyle(sample).color; sample.remove(); return same;
    })).toBe(true);
    await expect(page.locator(".source-summary")).toHaveText(`${append ? "本次新增 1" : "2"} 份已準備 · 1 份待上傳`);
    await page.getByRole("button", { name: "上傳新增教材", exact: true }).click();
    await expect(local.getByRole("status")).toHaveText("上傳中…");
    await expect(page.locator(".source-list-card .primary-button")).toHaveText("正在上傳…");
    await expect(page.locator(".source-list-card .primary-button")).toBeDisabled();
    releaseUpload();
    await expect(local.getByRole("status")).toHaveText("上傳失敗");
    await expect(local.getByRole("alert")).toBeVisible();
    await expect(local.getByRole("button", { name: "移除", exact: true })).toBeEnabled();
    await expect(page.locator(".source-list-card .primary-button")).toHaveText("重試上傳");
    await page.getByRole("button", { name: "重試上傳", exact: true }).click();
    await expect(local.getByRole("status")).toHaveText("已上傳");
    await expect(start).toHaveCount(0);
    await expect(rows).toHaveCount(3);
    releaseRefresh();
    await expect(local).toHaveCount(0);
    await expect(rows).toHaveCount(3);
    await expect(page.getByRole("listitem", { name: "Extra.txt", exact: true }).getByRole("status")).toHaveText("正在轉換…");
    await expect(page.locator(".source-list-card .primary-button")).toHaveCount(0);
    await expect(page.locator(".source-summary")).toHaveText(`${append ? "本次新增 1" : "2"} 份已準備 · 1 份正在轉換`);
    sources[2] = { ...sources[2], status: "ready", normalized_artifact_id: uuid(40), page_count: 10 };
    await expect(start).toBeEnabled();
    await expect(page.locator(".source-summary")).toHaveText(append ? "本次新增 2 份教材 · 共 20 頁" : "3 份教材 · 共 30 頁");
    expect(uploads).toBe(2); expect(uploadKeys[0]).toBe(uploadKeys[1]);
    if (append) await page.getByRole("listitem", { name: "Extra.txt", exact: true }).getByRole("checkbox").uncheck();
    else await page.getByRole("button", { name: "上移 Transport.pptx", exact: true }).click();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await start.click(); await expect(page).toHaveURL(new RegExp(`/runs/${uuid(3)}$`));
    expect(starts).toEqual([{ schema: "material-revision-create/v1", base_revision: append ? revision : null,
      normalization_ids: append ? [uuid(21)] : [uuid(21), uuid(20), uuid(23)] }]);
    await page.goto(`/materials/${material}/sources`);
    await expect(page.getByRole("button", { name: "查看處理狀態", exact: true })).toBeEnabled();
    const staged = page.getByRole("listitem", { name: "Transport.pptx", exact: true });
    await expect(staged.getByRole("button", { name: "移除", exact: true })).toBeDisabled();
  });
});
