import { expect, test } from "@playwright/test";
import { json, mockKnowledgeMapApi, materialId, runId, structureRevision, structureView } from "../fixtures/knowledge-map";
import type { MaterialNotesView, NoteKind } from "../../src/api/contracts";

const formula = String.raw`\text{大小} = \text{推入次數} - \text{取出次數}`;

function notes(kind: NoteKind = "summary"): MaterialNotesView {
  const c = structureView().concepts[0];
  const source = { claim_ids: [c.claims[0].claim_id], evidence_ids: [c.claims[0].evidence[0].evidence_id], concepts: [{ concept_id: c.concept_id, label: c.label }], evidence: c.claims[0].evidence };
  return { schema: "material-notes/v2", material_id: materialId, run_id: runId, knowledge_structure_revision: structureRevision,
    kind, status: "ready", generation: 1, source_status: { processing: "succeeded", quality: "accepted", decision: "retain", reason_codes: [] }, source_resolver: `/v1/materials/${materialId}/knowledge-structures/${structureRevision}/evidence`, error_code: null,
    progress: null, annotations: [],
    outline: [{ paragraph_id: "0", level: 1, number: "一、", heading: "堆疊的順序" }, { paragraph_id: "1", level: 2, number: "1.1", heading: "堆疊大小" }],
    paragraphs: [
      { id: "0", anchor: "stable-anchor", heading: "堆疊的順序", level: 1, ...source, blocks: [
        { type: "bullets", items: [{ text: "堆疊使用後進先出", children: [{ text: "最後放入的項目先取出", children: [{ text: "只從頂端操作" }] }] }] },
        { type: "table", caption: "操作與順序", headers: ["操作", "順序"], rows: [["推入", "加入頂端"], ["取出", "從頂端取出"]] },
        { type: "callout", style: "case_study", title: "疊盤子", items: [{ text: "最上面的盤子最先拿走", children: [] }] }] },
      { id: "1", anchor: "size-anchor", heading: "堆疊大小", level: 2, ...source, blocks: [
        { type: "formula", latex: formula },
        { type: "table", caption: null, headers: ["推入", "取出", "大小"], rows: [["3", "1", "2"]] },
        { type: "callout", style: "reminder", title: null, items: [{ text: "空堆疊不能再取出", children: [] }] }] }] };
}

function legacyParagraph(view: MaterialNotesView) {
  const { blocks: _blocks, level: _level, ...rest } = view.paragraphs[0] as Extract<MaterialNotesView["paragraphs"][number], { blocks: unknown }>;
  return { ...rest, heading: "先前的段落", text: "舊版段落正文。", formula: "大小 = 推入次數 − 取出次數",
    table: { headers: ["操作", "順序"], rows: [["推入", "加入頂端"]] } };
}

async function setup(page: Parameters<typeof mockKnowledgeMapApi>[0], initial = notes()) {
  await mockKnowledgeMapApi(page);
  let summary = initial;
  let full: MaterialNotesView = { ...notes("full"), status: "not_generated", generation: 0, paragraphs: [] };
  const writes: string[] = [];
  await page.route("**/notes/**", route => {
    const request = route.request();
    if (!request.url().includes("/v1/")) return route.fallback();
    const kind = request.url().includes("/notes/full") ? "full" : "summary";
    let view = kind === "full" ? full : summary;
    if (request.method() === "POST") { writes.push("generate"); view = { ...view, status: "pending" }; }
    if (request.method() === "PUT") {
      writes.push("annotation");
      const body = request.postDataJSON();
      const id = request.url().split("/").pop()!;
      view = { ...view, annotations: body.body ? [{ annotation_id: id, anchor: view.paragraphs[0].anchor, body: body.body,
        original_revision: structureRevision, original_paragraph: view.paragraphs[0], paragraph_id: "0", updated_at: "2026-10-25T01:00:00Z" }] : [] };
    }
    if (kind === "full") full = view; else summary = view;
    return json(route, view);
  });
  return { writes, setSummary: (v: MaterialNotesView) => { summary = v; }, setFull: (v: MaterialNotesView) => { full = v; } };
}

test("notes read without generating and preserve annotations after reload", async ({ page }) => {
  const state = await setup(page);
  await page.goto(`/materials/${materialId}/notes`);
  await expect(page.getByRole("tab", { name: "筆記", exact: true })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("heading", { name: "一、堆疊的順序" })).toBeVisible();
  expect(state.writes).toEqual([]);
  await page.getByRole("button", { name: "新增註記", exact: true }).first().click();
  await page.getByLabel("個人註記", { exact: true }).fill("記住最後進入、最先取出。");
  await page.getByRole("button", { name: "儲存註記", exact: true }).click();
  await expect(page.getByRole("dialog")).toBeHidden();
  await page.reload();
  await expect(page.getByText("記住最後進入、最先取出。", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "查看第 1 頁來源" }).first().click();
  await expect(page.getByRole("dialog", { name: "教材來源" })).toBeVisible();
  await page.getByRole("button", { name: "關閉", exact: true }).click();
  await page.getByRole("button", { name: "Stack · 查看地圖" }).first().click();
  await expect(page.getByRole("dialog", { name: "概念詳情" })).toBeVisible();
  expect(state.writes).toEqual(["annotation"]);
});

test("full notes require a click and failed regeneration keeps content and previous annotations", async ({ page }) => {
  const initial = notes();
  initial.status = "failed"; initial.error_code = "NOTE_REFERENCE_INVALID";
  initial.source_status.quality = "needs_review";
  initial.annotations = [{ annotation_id: "55555555-5555-4555-8555-555555555555", anchor: "previous-anchor", body: "保留當時想法",
    original_revision: structureRevision, original_paragraph: legacyParagraph(initial), paragraph_id: null, updated_at: "2026-10-25T01:00:00Z" }];
  const state = await setup(page, initial);
  await page.goto(`/materials/${materialId}/notes`);
  await expect(page.getByText(/來源引用未通過驗證/)).toBeVisible();
  await expect(page.getByText(/教材仍有待核對/)).toBeVisible();
  await expect(page.getByRole("heading", { name: "一、堆疊的順序" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "先前段落的註記" })).toBeVisible();
  await page.getByText("當時段落：先前的段落").click();
  await expect(page.getByRole("heading", { name: "先前的段落", exact: true })).toBeVisible();
  await expect(page.getByText("舊版段落正文。", { exact: true })).toBeVisible();
  await expect(page.getByText("大小 = 推入次數 − 取出次數", { exact: true })).toBeVisible();
  await expect(page.getByText("保留當時想法", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "完整筆記", exact: true }).click();
  await expect(page.getByRole("button", { name: "生成完整筆記", exact: true })).toBeVisible();
  expect(state.writes).toEqual([]);
  await page.getByRole("button", { name: "生成完整筆記", exact: true }).click();
  await expect(page.getByRole("button", { name: "生成中", exact: true })).toBeDisabled();
  expect(state.writes).toEqual(["generate"]);
});

test("notes and annotation editor remain usable on narrow screens and keyboard", async ({ page }, testInfo) => {
  await setup(page);
  for (const width of [1280, 390, 320]) {
    await page.setViewportSize({ width, height: 800 });
    await page.goto(`/materials/${materialId}/notes`);
    await expect(page.getByRole("heading", { name: "一、堆疊的順序" })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(width);
    await page.screenshot({ path: testInfo.outputPath(`notes-${width}.png`), fullPage: true });
    await page.getByRole("button", { name: "新增註記", exact: true }).first().focus();
    await page.keyboard.press("Enter");
    await expect(page.getByLabel("個人註記", { exact: true })).toBeFocused();
    await page.keyboard.press("Escape");
    await expect(page.getByRole("button", { name: "新增註記", exact: true }).first()).toBeFocused();
  }
});


test("full notes show restored section progress and retain completed work on retry", async ({ page }) => {
  const state = await setup(page);
  state.setFull({ ...notes("full"), status: "failed", generation: 0, paragraphs: [], error_code: "NOTE_GENERATION_FAILED",
    progress: { stage: "generating", completed_sections: 1, total_sections: 3, completed_parts: 2, total_parts: 6, current_section: "排序方法" } });
  await page.goto(`/materials/${materialId}/notes`);
  await page.getByRole("button", { name: "完整筆記", exact: true }).click();
  await expect(page.getByRole("progressbar", { name: "已完成分區" })).toHaveAttribute("value", "1");
  await expect(page.getByRole("progressbar", { name: "已完成分區" })).toHaveAttribute("max", "3");
  await expect(page.getByText("已完成 1 / 3 區 · 2 / 6 批", { exact: true })).toBeVisible();
  await expect(page.getByText(/重試時會從未完成部分接續/)).toBeVisible();
  await page.getByRole("button", { name: "重試生成", exact: true }).click();
  await expect(page.getByRole("button", { name: "生成中", exact: true })).toBeDisabled();
  await expect(page.getByRole("progressbar", { name: "已完成分區" })).toHaveAttribute("value", "1");
});


test("content verification failure retains the last published notes", async ({ page }) => {
  const initial = notes();
  initial.status = "failed";
  initial.error_code = "NOTE_CONTENT_UNSUPPORTED";
  await setup(page, initial);
  await page.goto(`/materials/${materialId}/notes`);
  await expect(page.getByText(/筆記內容與引用原文不符/)).toBeVisible();
  await expect(page.getByText(/以下保留上一份筆記/)).toBeVisible();
  await expect(page.getByRole("heading", { name: "一、堆疊的順序" })).toBeVisible();
  await expect(page.getByRole("button", { name: "重新生成", exact: true })).toBeEnabled();
});

test("notes export each format and report download failure without regenerating", async ({ page }) => {
  const state = await setup(page);
  let fail = false;
  const formats: string[] = [];
  await page.route("**/notes/summary/export/*", route => {
    const format = route.request().url().split("/").pop()!;
    formats.push(format);
    if (fail) return json(route, { schema: "api-error/v1", request_id: "55555555-5555-4555-8555-555555555555", reason_code: "RESOURCE_NOT_FOUND", retryable: false, message: "Request could not be completed." }, 404);
    return route.fulfill({ status: 200, contentType: "application/octet-stream", headers: { "Content-Disposition": `attachment; filename*=UTF-8''notes.${format}` }, body: "export" });
  });
  await page.goto(`/materials/${materialId}/notes`);
  const controls = page.getByRole("group", { name: "匯出筆記" });
  for (const format of ["md", "pdf", "docx"]) {
    await controls.getByRole("combobox").selectOption(format);
    const downloaded = page.waitForEvent("download");
    await controls.getByRole("button", { name: "匯出", exact: true }).click();
    expect((await downloaded).suggestedFilename()).toBe(`notes.${format}`);
  }
  fail = true;
  await controls.getByRole("button", { name: "匯出", exact: true }).click();
  await expect(controls.getByRole("alert")).toContainText("找不到");
  expect(formats).toEqual(["md", "pdf", "docx", "docx"]);
  expect(state.writes).toEqual([]);
});


test("structured notes show outline, numbered levels, nested bullets, tables, formula and callouts", async ({ page }) => {
  await setup(page);
  const errors: string[] = [];
  page.on("pageerror", e => errors.push(e.message));
  // KaTeX 的排版警告會出現在 console warning；其他 mock 未攔截的 API 404 與本案無關。
  page.on("console", m => { if (m.type() === "warning") errors.push(m.text()); });
  await page.goto(`/materials/${materialId}/notes`);
  const outline = page.getByRole("navigation", { name: "章節大綱" });
  await expect(outline.getByRole("button")).toHaveText(["一、堆疊的順序", "1.1 堆疊大小"]);
  await expect(page.getByRole("heading", { level: 2, name: "一、堆疊的順序" })).toBeVisible();
  await expect(page.getByRole("heading", { level: 3, name: "1.1 堆疊大小" })).toBeVisible();
  await expect(page.locator(".note-bullets .note-bullets .note-bullets li")).toHaveText("只從頂端操作");
  await expect(page.getByRole("region", { name: "操作與順序" })).toBeVisible();
  await expect(page.getByRole("region", { name: "堆疊大小表格" })).toBeVisible();
  const math = page.locator(".note-formula .katex-display");
  await expect(math).toHaveCount(1);
  await expect(page.locator(".note-formula annotation")).toHaveText(formula);
  await expect(page.locator(".note-formula .katex-error")).toHaveCount(0);
  await expect(page.getByRole("complementary", { name: "案例研究" })).toContainText("案例研究：疊盤子");
  await expect(page.getByRole("complementary", { name: "重要提醒" })).toContainText("空堆疊不能再取出");
  await outline.getByRole("button", { name: "1.1 堆疊大小" }).click();
  await expect(page.locator("#note-section-1")).toBeFocused();
  expect(errors).toEqual([]);
});

test("readable math remains visible in nested bullets and table cells alongside rendered formulas", async ({ page }) => {
  const view = notes();
  const first = view.paragraphs[0];
  const second = view.paragraphs[1];
  if (!("blocks" in first) || !("blocks" in second)) throw new Error("structured fixture required");
  first.blocks.push({ type: "bullets", items: [{ text: "平方根", children: [{ text: "√4 = 2" }] }] });
  second.blocks.push({ type: "table", caption: "近似值", headers: ["項目", "機率"], rows: [["甲", "≈ 0.5"]] });
  await setup(page, view);
  await page.goto(`/materials/${materialId}/notes`);
  await expect(page.getByText("√4 = 2", { exact: true })).toBeVisible();
  await expect(page.getByRole("cell", { name: "≈ 0.5", exact: true })).toBeVisible();
  await expect(page.locator(".note-formula .katex-display")).toBeVisible();
  await expect(page.locator(".note-formula .katex-error")).toHaveCount(0);
});


for (const kind of ["summary", "full"] as const) test(`Luna call limit keeps published ${kind} notes until explicit retry`, async ({ page }) => {
  const saved = { ...notes(kind), status: "failed" as const, error_code: "LUNA_CALL_LIMIT_EXCEEDED" };
  const state = await setup(page, kind === "summary" ? saved : notes());
  if (kind === "full") state.setFull(saved);
  await page.goto(`/materials/${materialId}/notes`);
  if (kind === "full") await page.getByRole("button", { name: "完整筆記", exact: true }).click();
  await expect(page.getByText(/這次生成用量超過上限，已停止。可以稍後重試。/)).toBeVisible();
  await expect(page.getByRole("heading", { name: "一、堆疊的順序" })).toBeVisible();
  expect(state.writes).toEqual([]);
  await page.getByRole("button", { name: "重新生成", exact: true }).click();
  expect(state.writes).toEqual(["generate"]);
});
