import { expect, test, type Page, type Route } from "@playwright/test";

const materialId = "11111111-1111-4111-8111-111111111111";
const runId = "22222222-2222-4222-8222-222222222222";
const artifactId = "33333333-3333-4333-8333-333333333333";
const pdf = { name: "課程講義.pdf", mimeType: "application/pdf", buffer: Buffer.from("%PDF-1.7\nSynthetic upload fixture\n%%EOF") };
const material = { schema: "material/v1", material_id: materialId, source_artifact_id: artifactId, source_sha256: "a".repeat(64), size_bytes: pdf.buffer.length };
const run = { schema: "material-processing-run/v5", cancel_requested_at: null, run_id: runId, material_id: materialId, source_artifact_id: artifactId,
  status: "pending", progress_stage: "queued", completed_pages: 0, total_pages: null, output_binding: null, error_code: null,
  created_at: "2026-09-12T00:00:00Z", updated_at: "2026-09-12T00:00:00Z", completed_at: null };

async function signedIn(page: Page) {
  await page.route("**/v1/session/refresh", route => route.fulfill({ status: 204 }));
  await page.route("**/v1/session", route => route.fulfill({ json: { schema: "learner-identity/v1", learner_id: materialId } }));
  await page.route(`**/v1/material-processing-runs/${runId}`, route => route.fulfill({ json: run }));
}
async function failure(route: Route) {
  await route.fulfill({ status: 503, json: { schema: "api-error/v1", request_id: materialId, reason_code: "STORAGE_UNAVAILABLE", retryable: true, message: "Request could not be completed." } });
}
async function transfer(page: Page, files: { name: string; type: string; size?: number }[]) {
  return page.evaluateHandle(files => {
    const data = new DataTransfer();
    for (const file of files) data.items.add(new File([new Uint8Array(file.size ?? 20)], file.name, { type: file.type }));
    return data;
  }, files);
}

for (const viewport of [{ width: 1920, height: 1080 }, { width: 1536, height: 1024 }, { width: 1366, height: 768 }, { width: 390, height: 844 }]) {
  for (const state of ["initial", "selected", "long-name", "invalid", "oversized", "drag-over", "submitting", "api-failure"] as const) {
    test(`upload ${state} at ${viewport.width}px keeps the task usable`, async ({ page }) => {
      await page.setViewportSize(viewport); await signedIn(page);
      let calls = 0;
      await page.route("**/v1/materials", route => { calls++; return state === "submitting" ? undefined : failure(route); });
      await page.goto("/upload");
      const upload = page.locator(".upload-page");
      const input = page.getByLabel("選擇 PDF 教材", { exact: true });
      const submit = page.locator(".upload-card .full-button");
      await expect(upload.getByRole("heading", { name: "上傳教材", level: 1, exact: true })).toBeVisible();
      await expect(submit).toBeDisabled();
      const drop = upload.locator(".file-drop");
      const initialHeight = (await drop.boundingBox())!.height;
      expect(initialHeight).toBeGreaterThanOrEqual(210);
      await expect(drop).toContainText("將 PDF 拖放到此處");
      await expect(drop).toContainText("或點擊選擇 PDF · 最大 100 MiB");
      expect((await drop.locator(".file-drop__icon").boundingBox())!.width).toBe(58);
      await expect(input).toHaveAttribute("accept", "application/pdf");
      await expect(upload).toHaveAttribute("aria-labelledby", "upload-title");
      await expect(page.locator(".sidebar-helper")).toHaveCount(0);
      await expect(upload.locator(".step-number")).toHaveCount(0);
      await expect(upload.locator("img")).toHaveCount(1);
      await expect(upload.locator(".upload-aside > .surface")).toHaveCount(1);
      await expect(upload.getByRole("region", { name: "檔案需求" })).toContainText("PDF（.pdf）· 最大 100 MiB");
      await expect(upload.locator(".privacy-note")).toContainText("教材由 Studydy 處理，語意分析使用配置的 AI 服務");
      if (["selected", "long-name", "submitting", "api-failure"].includes(state)) {
        const file = state === "long-name" ? { ...pdf, name: "資料結構_" + "LongUnbrokenFilename".repeat(7) + ".pdf" } : pdf;
        await input.setInputFiles(file);
        await expect(upload.locator(".chosen-file strong")).toHaveText(file.name);
        await expect(upload.getByText(file.name, { exact: true })).toHaveCount(1);
        await expect(submit).toBeEnabled();
      }
      if (state === "invalid") await input.setInputFiles({ name: "notes.txt", mimeType: "text/plain", buffer: Buffer.from("notes") });
      if (state === "oversized" || state === "drag-over") {
        const data = await transfer(page, [{ name: "lecture.pdf", type: "application/pdf", size: state === "oversized" ? 100 * 1024 * 1024 + 1 : 20 }]);
        await page.locator(".file-drop").dispatchEvent(state === "oversized" ? "drop" : "dragenter", { dataTransfer: data });
        await data.dispose();
      }
      if (state === "submitting" || state === "api-failure") {
        await submit.click();
        await expect.poll(() => calls).toBe(1);
        if (state === "submitting") {
          await expect(submit).toBeDisabled(); await expect(input).toBeDisabled();
          await expect(upload.getByRole("button", { name: "移除", exact: true })).toBeDisabled();
          await expect(submit).toHaveText("正在上傳…");
          await expect(page.locator(".file-drop")).toHaveClass(/is-disabled/);
        } else {
          await expect(upload.getByRole("alert")).toContainText("資料服務暫時無法使用"); await expect(submit).toBeEnabled();
          await expect(upload.locator(".chosen-file strong")).toHaveText(pdf.name);
          await expect(upload.locator(".chosen-file")).toContainText("準備上傳");
          await expect(upload.locator(".chosen-file")).not.toContainText("需要修正");
          await expect(upload.locator("#upload-submit-error")).toHaveAttribute("role", "alert");
          await expect(upload.locator("#upload-file-error")).toHaveCount(0);
          expect(await input.getAttribute("aria-invalid")).toBeNull();
          expect(await input.getAttribute("aria-describedby")).toBeNull();
          await expect(page).toHaveURL(/\/upload$/);
        }
      }
      if (state === "invalid" || state === "oversized") {
        await expect(upload.getByRole("alert")).toContainText(state === "invalid" ? "不是可用的 PDF" : "不可超過 100 MiB");
        await expect(upload.locator("#upload-file-error")).toHaveAttribute("role", "alert");
        await expect(upload.locator("#upload-submit-error")).toHaveCount(0);
        await expect(input).toHaveAttribute("aria-invalid", "true");
        await expect(input).toHaveAttribute("aria-describedby", "upload-file-error");
        await expect(upload.locator(".chosen-file")).toHaveCount(0); await expect(submit).toBeDisabled();
        expect(await input.inputValue()).toBe("");
      }
      if (state === "drag-over") await expect(page.locator(".file-drop")).toHaveClass(/is-dragging/);
      const selected = ["selected", "long-name", "submitting", "api-failure"].includes(state);
      const dropHeight = (await drop.boundingBox())!.height;
      if (selected) {
        expect(dropHeight).toBeGreaterThanOrEqual(110); expect(dropHeight).toBeLessThanOrEqual(130);
        expect(dropHeight).toBeLessThan(initialHeight);
        await expect(drop).toContainText("拖放或點擊以更換 PDF");
        await expect(drop).not.toContainText("100 MiB");
        const filename = await upload.locator(".chosen-file strong").textContent();
        await expect(drop).not.toContainText(filename!);
        expect((await drop.locator(".file-drop__icon").boundingBox())!.width).toBe(42);
      } else expect(dropHeight).toBe(initialHeight);
      expect(await upload.evaluate(element => getComputedStyle(element).maxWidth)).toBe("1280px");
      const card = await upload.locator(".upload-card").boundingBox();
      const rail = await upload.locator(".upload-aside").boundingBox();
      if (viewport.width > 1200) {
        expect(rail!.width).toBe(320); expect(card!.width).toBeGreaterThan(rail!.width * 1.5);
        expect(rail!.x).toBeGreaterThanOrEqual(card!.x + card!.width + 20);
      } else expect(rail!.y).toBeGreaterThanOrEqual(card!.y + card!.height + 20);
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(viewport.width);
      expect(await upload.locator("button, h1, h2, h3, p, strong").evaluateAll(elements => elements.filter(element => element.scrollWidth > element.clientWidth + 1).map(element => element.tagName))).toEqual([]);
      await expect.poll(() => upload.locator(".upload-hero img").evaluate((img: HTMLImageElement) => img.complete && img.naturalWidth > 0)).toBe(true);
      await page.screenshot({ path: `/tmp/studydy-upload/${viewport.width}-${state}.png`, fullPage: true });
    });
  }
}

test("selection, invalid replacements, accessible picker, drag/drop and removal keep one valid file", async ({ page }) => {
  await signedIn(page); await page.goto("/upload");
  const input = page.getByLabel("選擇 PDF 教材", { exact: true });
  const drop = page.locator(".file-drop");
  const chosen = page.locator(".chosen-file");
  const submit = page.locator(".upload-card .full-button");
  await input.focus(); await page.keyboard.press("Tab"); await page.keyboard.press("Shift+Tab"); await input.focus();
  expect(await drop.evaluate(element => getComputedStyle(element).outlineStyle)).toBe("solid");
  expect(await input.evaluate(element => getComputedStyle(element).display)).not.toBe("none");
  const picker = page.waitForEvent("filechooser"); await drop.click(); await (await picker).setFiles(pdf);
  await expect(chosen).toContainText("1 KiB · 準備上傳"); await expect(submit).toBeEnabled();
  const compactHeight = (await drop.boundingBox())!.height;
  expect(compactHeight).toBeLessThan(130);
  const replacementPicker = page.waitForEvent("filechooser");
  await drop.click(); await (await replacementPicker).setFiles({ ...pdf, name: "replacement.pdf" });
  await expect(chosen.locator("strong")).toHaveText("replacement.pdf");
  expect((await drop.boundingBox())!.height).toBe(compactHeight);
  await input.focus(); expect(await drop.evaluate(element => getComputedStyle(element).outlineStyle)).toBe("solid");
  const replacementDrop = await transfer(page, [{ name: "drag-replaced.pdf", type: "application/pdf" }]);
  await drop.dispatchEvent("dragenter", { dataTransfer: replacementDrop });
  await expect(drop).toHaveClass(/has-file/); await expect(drop).toHaveClass(/is-dragging/);
  expect(await drop.evaluate(element => getComputedStyle(element).boxShadow)).not.toBe("none");
  await drop.dispatchEvent("drop", { dataTransfer: replacementDrop }); await replacementDrop.dispose();
  await expect(chosen.locator("strong")).toHaveText("drag-replaced.pdf");
  expect((await drop.boundingBox())!.height).toBe(compactHeight);
  for (const invalid of [
    { name: "empty.pdf", mimeType: "application/pdf", buffer: Buffer.alloc(0) },
    { name: "not-pdf.txt", mimeType: "text/plain", buffer: Buffer.from("notes") },
  ]) {
    await input.setInputFiles(invalid); await expect(page.getByRole("alert")).toBeVisible();
    await expect(chosen).toHaveCount(0); await expect(submit).toBeDisabled();
    await expect(drop).not.toHaveClass(/has-file/);
    expect((await drop.boundingBox())!.height).toBeGreaterThanOrEqual(210);
    await expect(input).toHaveAttribute("aria-invalid", "true");
    await expect(input).toHaveAttribute("aria-describedby", "upload-file-error");
    await expect(page.locator("#upload-file-error")).toHaveAttribute("role", "alert");
    await expect(page.locator("#upload-submit-error")).toHaveCount(0);
  }
  const valid = await transfer(page, [{ name: "dropped.pdf", type: "application/pdf" }]);
  await drop.dispatchEvent("dragenter", { dataTransfer: valid }); await expect(drop).toHaveClass(/is-dragging/);
  await drop.dispatchEvent("dragover", { dataTransfer: valid });
  await drop.dispatchEvent("dragleave", { dataTransfer: valid }); await expect(drop).not.toHaveClass(/is-dragging/);
  await drop.dispatchEvent("drop", { dataTransfer: valid }); await expect(chosen.locator("strong")).toHaveText("dropped.pdf");
  await expect(drop).not.toHaveClass(/is-dragging/); await expect(submit).toBeEnabled(); await valid.dispose();
  for (const files of [[{ name: "not-pdf.txt", type: "text/plain" }], [{ name: "a.pdf", type: "application/pdf" }, { name: "b.pdf", type: "application/pdf" }]]) {
    const invalid = await transfer(page, files); await drop.dispatchEvent("drop", { dataTransfer: invalid }); await invalid.dispose();
    await expect(page.getByRole("alert")).toContainText(files.length === 2 ? "一次只能處理一份 PDF" : "不是可用的 PDF");
    await expect(chosen).toHaveCount(0); await expect(submit).toBeDisabled();
    await expect(drop).not.toHaveClass(/has-file/);
    expect((await drop.boundingBox())!.height).toBeGreaterThanOrEqual(210);
    await expect(input).toHaveAttribute("aria-invalid", "true");
    await expect(input).toHaveAttribute("aria-describedby", "upload-file-error");
    await expect(page.locator("#upload-file-error")).toHaveAttribute("role", "alert");
    await expect(page.locator("#upload-submit-error")).toHaveCount(0);
  }
  await input.setInputFiles(pdf); await expect(page.getByRole("alert")).toHaveCount(0);
  await page.getByRole("button", { name: "移除", exact: true }).click();
  await expect(chosen).toHaveCount(0); await expect(submit).toBeDisabled(); expect(await input.inputValue()).toBe("");
  await expect(drop).not.toHaveClass(/has-file/);
  expect((await drop.boundingBox())!.height).toBeGreaterThanOrEqual(210);
  await expect(drop).toContainText("將 PDF 拖放到此處");
  await expect(page.getByRole("alert")).toHaveCount(0);
});

test("rapid submit creates one material then one bound run and navigates only after both succeed", async ({ page }) => {
  await signedIn(page);
  const requests: { path: string; key: string }[] = [];
  let release!: () => void;
  const pending = new Promise<void>(resolve => { release = resolve; });
  await page.route("**/v1/materials", async route => {
    requests.push({ path: "material", key: route.request().headers()["idempotency-key"] });
    expect(route.request().headers()["content-type"]).toBe("application/pdf");
    expect(decodeURIComponent(route.request().headers()["x-material-name"])).toBe(pdf.name);
    expect(route.request().postDataBuffer()).toEqual(pdf.buffer);
    await pending; await route.fulfill({ status: 201, json: material });
  });
  await page.route("**/v1/material-processing-runs", route => {
    requests.push({ path: "run", key: route.request().headers()["idempotency-key"] });
    expect(route.request().postDataJSON()).toEqual({ schema: "material-processing-create/v1", material_id: materialId, source_artifact_id: artifactId });
    return route.fulfill({ status: 201, json: run });
  });
  await page.goto("/upload"); await page.getByLabel("選擇 PDF 教材", { exact: true }).setInputFiles(pdf);
  await page.locator(".upload-card .full-button").evaluate(button => { (button as HTMLButtonElement).click(); (button as HTMLButtonElement).click(); });
  await expect.poll(() => requests.length).toBe(1);
  await expect(page.locator(".upload-card .full-button")).toBeDisabled();
  const ignored = await transfer(page, [{ name: "ignored.txt", type: "text/plain" }]);
  await page.locator(".file-drop").dispatchEvent("drop", { dataTransfer: ignored }); await ignored.dispose();
  await expect(page.locator(".chosen-file strong")).toHaveText(pdf.name);
  release();
  await expect(page).toHaveURL(new RegExp(`/materials/${materialId}/runs/${runId}$`));
  expect(requests.map(request => request.path)).toEqual(["material", "run"]);
  expect(requests[0].key).toMatch(/^[0-9a-f-]{36}$/); expect(requests[1].key).not.toBe(requests[0].key);
});

for (const stage of ["material", "run"] as const) {
  test(`${stage} failure stays on Upload and retry reuses each original idempotency key`, async ({ page }) => {
    await signedIn(page);
    let fail = true;
    const uploads: string[] = []; const runs: string[] = [];
    await page.route("**/v1/materials", route => { uploads.push(route.request().headers()["idempotency-key"]); return fail && stage === "material" ? failure(route) : route.fulfill({ status: 201, json: material }); });
    await page.route("**/v1/material-processing-runs", route => { runs.push(route.request().headers()["idempotency-key"]); return fail && stage === "run" ? failure(route) : route.fulfill({ status: 201, json: run }); });
    await page.goto("/upload"); await page.getByLabel("選擇 PDF 教材", { exact: true }).setInputFiles(pdf);
    const submit = page.locator(".upload-card .full-button");
    await submit.click(); await expect(page.getByRole("alert")).toContainText("資料服務暫時無法使用");
    await expect(page).toHaveURL(/\/upload$/); await expect(submit).toBeEnabled();
    await expect(page.locator(".chosen-file strong")).toHaveText(pdf.name);
    await expect(page.locator(".chosen-file")).toContainText(stage === "run" ? "已上傳，可重試建立處理任務" : "準備上傳");
    await expect(page.locator(".chosen-file")).not.toContainText("需要修正");
    await expect(page.locator("#upload-submit-error")).toHaveAttribute("role", "alert");
    await expect(page.locator("#upload-file-error")).toHaveCount(0);
    const input = page.getByLabel("選擇 PDF 教材", { exact: true });
    expect(await input.getAttribute("aria-invalid")).toBeNull();
    expect(await input.getAttribute("aria-describedby")).toBeNull();
    if (stage === "material") expect(runs).toHaveLength(0);
    fail = false; await submit.click();
    await expect(page).toHaveURL(new RegExp(`/materials/${materialId}/runs/${runId}$`));
    expect(uploads).toHaveLength(stage === "run" ? 1 : 2);
    if (stage === "material") expect(uploads[1]).toBe(uploads[0]);
    expect(runs).toHaveLength(stage === "run" ? 2 : 1);
    if (stage === "run") expect(runs[1]).toBe(runs[0]);
  });
}

test("remove and replacement reset both keys without retaining old selection errors", async ({ page }) => {
  await signedIn(page);
  const uploads: string[] = []; const runs: string[] = [];
  await page.route("**/v1/materials", route => { uploads.push(route.request().headers()["idempotency-key"]); return route.fulfill({ status: 201, json: material }); });
  await page.route("**/v1/material-processing-runs", route => { runs.push(route.request().headers()["idempotency-key"]); return failure(route); });
  await page.goto("/upload"); const input = page.getByLabel("選擇 PDF 教材", { exact: true });
  for (let index = 0; index < 3; index++) {
    if (index === 1) {
      await page.getByRole("button", { name: "移除", exact: true }).click();
      await expect(page.locator(".chosen-file")).toHaveCount(0);
      await expect(page.getByRole("alert")).toHaveCount(0);
      await expect(page.locator(".upload-card .full-button")).toBeDisabled();
      expect(await input.inputValue()).toBe("");
      expect(await input.getAttribute("aria-invalid")).toBeNull();
      expect(await input.getAttribute("aria-describedby")).toBeNull();
    }
    await input.setInputFiles({ ...pdf, name: index === 2 ? "replacement.pdf" : pdf.name });
    await expect(page.getByRole("alert")).toHaveCount(0);
    await expect(page.locator(".chosen-file strong")).toHaveText(index === 2 ? "replacement.pdf" : pdf.name);
    expect(await input.getAttribute("aria-invalid")).toBeNull();
    expect(await input.getAttribute("aria-describedby")).toBeNull();
    await expect(page.locator(".upload-card .full-button")).toBeEnabled();
    await page.locator(".upload-card .full-button").click(); await expect(page.getByRole("alert")).toBeVisible();
  }
  expect(new Set(uploads).size).toBe(3); expect(new Set(runs).size).toBe(3);
  await input.setInputFiles({ name: "invalid.txt", mimeType: "text/plain", buffer: Buffer.from("notes") });
  await expect(page.locator("#upload-submit-error")).toHaveCount(0);
  await expect(page.locator("#upload-file-error")).toBeVisible();
  await expect(input).toHaveAttribute("aria-invalid", "true");
  await expect(input).toHaveAttribute("aria-describedby", "upload-file-error");
  await expect(page.locator(".chosen-file")).toHaveCount(0);
  await expect(page.locator(".upload-card .full-button")).toBeDisabled();
  await input.setInputFiles(pdf);
  await expect(page.getByRole("alert")).toHaveCount(0);
  await expect(page.locator(".chosen-file")).toContainText("準備上傳");
  expect(await input.getAttribute("aria-invalid")).toBeNull();
  expect(await input.getAttribute("aria-describedby")).toBeNull();
  await expect(page.locator(".upload-card .full-button")).toBeEnabled();
});

test("task supporting rail stacks before the main upload area becomes cramped", async ({ page }) => {
  await signedIn(page); await page.setViewportSize({ width: 1100, height: 800 }); await page.goto("/upload");
  const card = await page.locator(".upload-card").boundingBox(); const rail = await page.locator(".upload-aside").boundingBox();
  expect(rail!.y).toBeGreaterThan(card!.y + card!.height);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(1100);
});

test("nested drag, Escape and picker cancel preserve a stable single-file selection", async ({ page }) => {
  await signedIn(page); await page.goto("/upload");
  const drop = page.locator(".file-drop"), child = drop.locator("strong");
  const input = page.getByLabel("選擇 PDF 教材", { exact: true });
  await input.setInputFiles(pdf);
  const data = await transfer(page, [{ name: "replacement.pdf", type: "application/pdf" }]);
  await drop.dispatchEvent("dragenter", { dataTransfer: data });
  await child.dispatchEvent("dragenter", { dataTransfer: data });
  await child.dispatchEvent("dragleave", { dataTransfer: data });
  await expect(drop).toHaveClass(/is-dragging/);
  await page.keyboard.press("Escape"); await expect(drop).not.toHaveClass(/is-dragging/);
  await input.dispatchEvent("cancel");
  await expect(page.locator(".chosen-file strong")).toHaveText(pdf.name);
  await input.setInputFiles(pdf);
  await expect(page.locator(".chosen-file strong")).toHaveText(pdf.name);
  await drop.dispatchEvent("dragenter", { dataTransfer: data });
  await drop.dispatchEvent("dragleave", { dataTransfer: data });
  await expect(drop).not.toHaveClass(/is-dragging/);
  await data.dispose();
});

for (const kind of ["text/plain", "text/uri-list", "folder"] as const) {
  test(`${kind} drag is not accepted as a PDF or submitted as the previous file`, async ({ page }) => {
    await signedIn(page); await page.goto("/upload");
    await page.getByLabel("選擇 PDF 教材", { exact: true }).setInputFiles(pdf);
    const drop = page.locator(".file-drop");
    const data = await page.evaluateHandle(kind => {
      const transfer = new DataTransfer();
      if (kind === "folder") {
        transfer.items.add(new File([], "folder.pdf"));
        // Chromium 每次索引可能回傳不同 wrapper，於測試 context 的原型模擬資料夾 entry。
        Object.defineProperty(DataTransferItem.prototype, "webkitGetAsEntry", { configurable: true, value: () => ({ isDirectory: true }) });
      } else transfer.setData(kind, "https://example.invalid/notes.pdf");
      return transfer;
    }, kind);
    await drop.dispatchEvent("dragenter", { dataTransfer: data });
    if (kind !== "folder") await expect(drop).not.toHaveClass(/is-dragging/);
    await drop.dispatchEvent("drop", { dataTransfer: data });
    await expect(page.getByRole("alert")).toContainText(kind === "folder" ? "不接受資料夾" : "不接受文字或網址");
    await expect(page.locator(".chosen-file")).toHaveCount(0);
    await expect(page.locator(".upload-card .full-button")).toBeDisabled();
    await expect(drop).not.toHaveClass(/is-dragging/);
    await data.dispose();
  });
}

test("lost run response retries only the same run intent after a confirmed upload", async ({ page }) => {
  await signedIn(page);
  let uploads = 0; const keys: string[] = [];
  let release!: () => void; const pending = new Promise<void>(resolve => { release = resolve; });
  await page.route("**/v1/materials", route => { uploads++; return route.fulfill({ status: 201, json: material }); });
  await page.route("**/v1/material-processing-runs", async route => {
    keys.push(route.request().headers()["idempotency-key"]);
    if (keys.length === 1) { await pending; await route.abort("connectionreset"); }
    else await route.fulfill({ status: 201, json: run });
  });
  await page.goto("/upload"); await page.getByLabel("選擇 PDF 教材", { exact: true }).setInputFiles(pdf);
  await page.locator(".upload-card .full-button").click();
  await expect(page.getByRole("button", { name: "正在建立處理任務…" })).toBeDisabled();
  await expect(page.locator(".chosen-file")).toContainText("已上傳，正在建立處理任務");
  await expect(page.getByRole("button", { name: "移除", exact: true })).toBeDisabled();
  release(); await expect(page.getByRole("alert")).toBeVisible();
  await page.getByRole("button", { name: "重試建立處理任務" }).click();
  await expect(page).toHaveURL(new RegExp(`/materials/${materialId}/runs/${runId}$`));
  expect(uploads).toBe(1); expect(keys).toHaveLength(2); expect(keys[1]).toBe(keys[0]);
});

test("lost upload response replays the original key and creates one bound run", async ({ page }) => {
  await signedIn(page); const keys: string[] = []; let runs = 0;
  await page.route("**/v1/materials", route => {
    keys.push(route.request().headers()["idempotency-key"]);
    return keys.length === 1 ? route.abort("connectionreset") : route.fulfill({ status: 201, json: material });
  });
  await page.route("**/v1/material-processing-runs", route => {
    runs++; expect(route.request().postDataJSON().material_id).toBe(materialId);
    return route.fulfill({ status: 201, json: run });
  });
  await page.goto("/upload"); await page.getByLabel("選擇 PDF 教材", { exact: true }).setInputFiles(pdf);
  await page.locator(".upload-card .full-button").click(); await expect(page.getByRole("alert")).toBeVisible();
  expect(runs).toBe(0); await page.locator(".upload-card .full-button").click();
  await expect(page).toHaveURL(new RegExp(`/materials/${materialId}/runs/${runId}$`));
  expect(keys).toHaveLength(2); expect(keys[1]).toBe(keys[0]); expect(runs).toBe(1);
});
