import { expect, test } from "@playwright/test";
import { json, mockKnowledgeMapApi, openMapConcept, artifactId, materialId, runId, structureRevision, structureView } from "../fixtures/knowledge-map";
import { outlineView } from "../fixtures/knowledge-read-views.mjs";
import type { MaterialNotesView, NoteHighlight, NoteKind } from "../../src/api/contracts";

const formula = String.raw`\text{大小} = \text{推入次數} - \text{取出次數}`;

function notes(kind: NoteKind = "summary"): MaterialNotesView {
  const c = structureView().concepts[0];
  const source = { claim_ids: [c.claims[0].claim_id], evidence_ids: [c.claims[0].evidence[0].evidence_id], concepts: [{ concept_id: c.concept_id, label: c.label }], evidence: c.claims[0].evidence };
  return { schema: "material-notes/v3", material_id: materialId, run_id: runId, knowledge_structure_revision: structureRevision,
    kind, status: "ready", generation: 1, source_status: { processing: "succeeded", quality: "accepted", decision: "retain", reason_codes: [] }, source_resolver: `/v1/materials/${materialId}/knowledge-structures/${structureRevision}/evidence`, error_code: null,
    progress: null, sections: [], unorganized_concepts: [], annotations: [], content_revision: structureRevision, content_generation: 1,
    update_available: false, updating: false, started_at: null, updated_at: null,
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

test("an empty update keeps the published notes and explains the retained content", async ({ page }) => {
  await setup(page, { ...notes(), status: "failed", error_code: "NOTE_NO_SUPPORTED_CONTENT" });
  await page.goto(`/materials/${materialId}/notes`);
  await expect(page.locator(".note-status p")).toHaveText("本次沒有可保留的筆記內容，已保留先前筆記。");
  await expect(page.getByRole("heading", { name: "一、堆疊的順序", exact: true })).toBeVisible();
});

test("source chapters preserve three levels, chapter outlines and missing concept map links", async ({ page }, testInfo) => {
  const view = notes();
  const missing = structureView().concepts[1];
  view.sections = [
    { section_id: "chapter", heading: "第一章 資料結構", level: 1, parent_id: null, paragraph_ids: [], outline: [
      { section_id: "section", heading: "1.1 堆疊", level: 2 },
      { section_id: "subsection", heading: "1.1.1 大小", level: 3 },
    ] },
    { section_id: "section", heading: "1.1 堆疊", level: 2, parent_id: "chapter", paragraph_ids: ["0"], outline: [] },
    { section_id: "subsection", heading: "1.1.1 大小", level: 3, parent_id: "section", paragraph_ids: ["1"], outline: [] },
  ];
  view.unorganized_concepts = [{ concept_id: missing.concept_id, label: missing.label }];
  await setup(page, view);
  for (const width of [1536, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    await page.goto(`/materials/${materialId}/notes`);
    await expect(page.getByRole("button", { name: "複習速覽", exact: true })).toHaveAttribute("aria-pressed", "true");
    await expect(page.getByRole("heading", { name: "第一章 資料結構", level: 2 })).toBeVisible();
    const chapter = page.getByRole("navigation", { name: "第一章 資料結構章節大綱", exact: true });
    await chapter.getByRole("button", { name: "1.1.1 大小", exact: true }).click();
    await expect(page.locator("#note-section-subsection")).toBeFocused();
    await expect(page.getByRole("heading", { name: "1.1.1 大小", level: 4 })).toBeInViewport();
    await expect(page.getByRole("heading", { name: "堆疊大小", level: 5 })).toBeVisible();
    await expect(page.getByRole("navigation", { name: "章節大綱", exact: true }).getByRole("button")).toHaveCount(3);
    await expect(page.locator("#note-section-0 .source-reference-button")).toHaveCount(1);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(width);
    await page.screenshot({ path: testInfo.outputPath(`source-chapters-${width}.png`) });
  }
  await page.getByRole("button", { name: `${missing.label} · 在地圖查看`, exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`/runs/${runId}/knowledge-structures/`));
  expect(decodeURIComponent(page.url())).toContain(structureRevision);
  await expect(page.getByRole("dialog", { name: "概念詳情", exact: true })).toContainText(missing.label);
});

async function setup(page: Parameters<typeof mockKnowledgeMapApi>[0], initial = notes()) {
  await mockKnowledgeMapApi(page);
  let summary = initial;
  let full: MaterialNotesView = { ...notes("full"), status: "not_generated", generation: 0, paragraphs: [], outline: [], content_revision: null, content_generation: null };
  const writes: string[] = [];
  let highlights: NoteHighlight[] = [];
  await page.route("**/notes/**", route => {
    const request = route.request();
    if (!request.url().includes("/v1/")) return route.fallback();
    const kind = request.url().includes("/notes/full") ? "full" : "summary";
    let view = kind === "full" ? full : summary;
    if (request.url().includes("/highlights")) {
      if (request.method() === "POST") {
        const body = request.postDataJSON();
        const highlight = { ...body, highlight_id: crypto.randomUUID(), created_at: new Date().toISOString(), original_revision: structureRevision, original_generation: 1, original_paragraph: view.paragraphs.find(p => p.id === body.paragraph_id)! };
        highlights.push(highlight);
        return json(route, highlight, 201);
      }
      if (request.method() === "DELETE") { highlights = highlights.filter(h => h.highlight_id !== request.url().split("/").pop()); return route.fulfill({ status: 204 }); }
      return json(route, { highlights });
    }
    if (request.method() === "POST") { writes.push("generate"); view = { ...view, status: "pending" }; }
    if (request.method() === "PUT") {
      writes.push("annotation");
      const body = request.postDataJSON();
      const id = request.url().split("/").pop()!;
      view = { ...view, annotations: [...view.annotations.filter(a => a.annotation_id !== id), ...(body.body ? [{ annotation_id: id, anchor: view.paragraphs[0].anchor, body: body.body,
        original_revision: structureRevision, original_paragraph: view.paragraphs[0], paragraph_id: "0", block_path: body.block_path, start: body.start, end: body.end, quote: body.quote, updated_at: "2026-10-25T01:00:00Z" }] : [])] };
    }
    if (kind === "full") full = view; else summary = view;
    return json(route, view);
  });
  return { writes, setSummary: (v: MaterialNotesView) => { summary = v; }, setFull: (v: MaterialNotesView) => { full = v; } };
}

async function selectText(page: Parameters<typeof mockKnowledgeMapApi>[0], selector = "#note-section-0 [data-note-text]") {
  const text = page.locator(selector).first();
  await expect(text).toBeVisible();
  await text.evaluate(element => {
    const range = document.createRange(); range.selectNodeContents(element);
    const selection = window.getSelection()!; selection.removeAllRanges(); selection.addRange(range);
    element.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
  });
  await expect(page.getByRole("toolbar", { name: "選取文字工具列" })).toBeVisible();
}

test("notes read without generating and preserve annotations after reload", async ({ page }) => {
  const state = await setup(page);
  await page.goto(`/materials/${materialId}/notes`);
  await expect(page.getByRole("tab", { name: "筆記", exact: true })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("heading", { name: "一、堆疊的順序" })).toBeVisible();
  expect(state.writes).toEqual([]);
  await selectText(page);
  await page.getByRole("button", { name: "新增註記", exact: true }).first().click();
  await page.getByRole("textbox", { name: "個人註記", exact: true }).fill("記住最後進入、最先取出。");
  await page.getByRole("button", { name: "儲存註記", exact: true }).click();
  await expect(page.getByRole("dialog")).toBeHidden();
  await page.reload();
  await expect(page.getByText("記住最後進入、最先取出。", { exact: true })).toBeVisible();
  await page.locator(".note-sources .source-reference-button").first().click();
  await expect(page.getByRole("dialog", { name: "教材來源" })).toBeVisible();
  await page.getByRole("button", { name: "關閉", exact: true }).click();
  await selectText(page);
  await page.getByRole("button", { name: "在地圖查看", exact: true }).click();
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
  await expect(page.getByTitle("由 AI 整理，每個重點都可點開原文對照。",{exact:true})).toBeVisible();
  await expect(page.getByRole("heading", { name: "一、堆疊的順序" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "先前的註記" })).toBeVisible();
  await page.getByText("當時段落：先前的段落").click();
  await expect(page.getByRole("heading", { name: "先前的段落", exact: true })).toBeVisible();
  await expect(page.getByText("舊版段落正文。", { exact: true })).toBeVisible();
  await expect(page.getByText("大小 = 推入次數 − 取出次數", { exact: true })).toBeVisible();
  await expect(page.getByText("保留當時想法", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "完整筆記", exact: true }).click();
  await expect(page.getByRole("button", { name: "生成完整筆記", exact: true })).toBeVisible();
  expect(state.writes).toEqual([]);
  await page.getByRole("button", { name: "生成完整筆記", exact: true }).click();
  await expect(page.getByRole("progressbar", { name: "已完成批次" })).toBeVisible();
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
    await selectText(page);
    await page.keyboard.press("Tab");
    await expect(page.getByRole("button", { name: "標記黃色", exact: true })).toBeFocused();
    await page.getByRole("button", { name: "新增註記", exact: true }).first().focus();
    await page.keyboard.press("Enter");
    await expect(page.getByRole("textbox", { name: "個人註記", exact: true })).toBeFocused();
    await page.getByRole("button", { name: "取消", exact: true }).click();
  }
});


test("full notes show restored section progress and retain completed work on retry", async ({ page }) => {
  const state = await setup(page);
  state.setFull({ ...notes("full"), status: "failed", generation: 0, paragraphs: [], outline: [], content_revision: null, content_generation: null, error_code: "NOTE_GENERATION_FAILED",
    progress: { stage: "generating", completed_sections: 1, total_sections: 3, completed_parts: 2, total_parts: 6, current_section: "排序方法" } });
  await page.goto(`/materials/${materialId}/notes`);
  await page.getByRole("button", { name: "完整筆記", exact: true }).click();
  await expect(page.getByText(/重試時會從未完成部分接續/)).toBeVisible();
  await page.getByRole("button", { name: "重試生成", exact: true }).click();
  await expect(page.getByRole("progressbar", { name: "已完成批次" })).toBeVisible();
  await expect(page.getByRole("progressbar", { name: "已完成批次" })).toHaveAttribute("aria-valuenow", "2");
  await expect(page.getByRole("progressbar", { name: "已完成批次" })).toHaveAttribute("aria-valuemax", "6");
  await expect(page.getByText("已完成 2 / 6 批", { exact: true })).toBeVisible();
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
  await expect(page.getByRole("button", { name: "重試生成", exact: true })).toBeEnabled();
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
  const controls = page.getByRole("menu", { name: "匯出筆記" });
  for (const format of ["md", "pdf", "docx"]) {
    await page.getByRole("button", { name: "匯出筆記", exact: true }).click();
    const downloaded = page.waitForEvent("download");
    await controls.getByRole("menuitem", { name: ({ md: "Markdown", pdf: "PDF", docx: "Word（.docx）" })[format], exact: true }).click();
    expect((await downloaded).suggestedFilename()).toBe(`notes.${format}`);
  }
  fail = true;
  await page.getByRole("button", { name: "匯出筆記", exact: true }).click();
  await controls.getByRole("menuitem", { name: "Word（.docx）", exact: true }).click();
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
  second.blocks.push({ type: "formula", latex: Array.from({ length: 24 }, (_, index) => `a_{${index}}`).join(" + ") });
  await setup(page, view);
  await page.goto(`/materials/${materialId}/notes`);
  await expect(page.getByText(/^√4 = 2/ )).toBeVisible();
  await expect(page.getByRole("cell", { name: /^≈ 0.5/ })).toBeVisible();
  await expect(page.locator(".note-formula .katex-display").last()).toBeVisible();
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 900 });
    expect(await page.locator(".note-formula").evaluateAll(elements => elements.every(element => element.scrollWidth <= element.clientWidth))).toBe(true);
  }
  await expect(page.locator(".note-formula .katex-error")).toHaveCount(0);
});

for (const format of ['audio','images'] as const) test(`note references use verified ${format} locations and previews`,async({page},testInfo)=>{
 await setup(page);
 await page.route('**/evidence/*/source',route=>json(route,{schema:'evidence-source/v1',format,display_name:format==='audio'?'課堂錄音':'投影片圖片',original_name:'original',original_url:`/v1/artifacts/${artifactId}/download`,preview_url:`/v1/artifacts/${artifactId}#page=1`,normalized_page:1,accuracy:'exact',origin_locators:[],label:format==='audio'?'音訊片段':'第 3 張圖片',...(format==='audio'?{playback:{kind:'audio',start_seconds:242,end_seconds:265,url:`/v1/artifacts/${artifactId}/media`}}:{})}));
 await page.route(`**/v1/artifacts/${artifactId}`,route=>route.fulfill({contentType:'text/html',body:'<!doctype html><html lang="zh-Hant"><body style="font-family:sans-serif;background:#e8efff;padding:24px"><h1>堆疊示意圖</h1><p>最後放入，最先取出。</p></body></html>'}));
 await page.goto(`/materials/${materialId}/notes`);
 const marker=page.locator('.note-sources .source-reference-button').first();await expect(marker).toHaveText(format==='audio'?'[▶ 04:02]':'[第 3 張圖片]');
 await marker.click();await expect(page.getByRole('dialog',{name:'教材來源'})).toContainText(format==='audio'?'課堂錄音':'投影片圖片');
 await page.getByRole('dialog',{name:'教材來源'}).getByRole('button',{name:'關閉'}).click();
 for(const [width,height] of [[1440,900],[1024,768],[390,844]]){
  await page.setViewportSize({width,height});await marker.scrollIntoViewIfNeeded();await marker.hover();
  await page.screenshot({path:testInfo.outputPath(`note-${format}-${width}.png`)});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBe(true);
 }
});


test("older visible notes keep annotation and export requests on their content revision",async({page})=>{
 const initial=notes();const oldRevision=`knowledge-structure:sha256:${"e".repeat(64)}`;
 initial.content_revision=oldRevision;initial.content_generation=3;initial.generation=0;initial.update_available=true;
 initial.source_resolver=`/v1/materials/${materialId}/knowledge-structures/${oldRevision}/evidence`;
 const state=await setup(page,initial);let annotationPath="";let exportPath="";
 await page.route(`**/knowledge-structures/${oldRevision}/evidence/*/source`,route=>json(route,{schema:'evidence-source/v1',format:'pdf',display_name:'舊教材',original_name:'old.pdf',original_url:`/v1/artifacts/${artifactId}/download`,preview_url:`/v1/artifacts/${artifactId}#page=1`,normalized_page:1,accuracy:'exact',origin_locators:[],label:'PDF 第 1 頁'}));
 await page.route('**/notes/summary/annotations/*',route=>{
  annotationPath=decodeURIComponent(new URL(route.request().url()).pathname);
  expect(route.request().postDataJSON().expected_generation).toBe(3);
  return json(route,{...initial,knowledge_structure_revision:oldRevision,generation:3,update_available:false});
 });
 await page.route('**/notes/summary/export/pdf',route=>{
  exportPath=decodeURIComponent(new URL(route.request().url()).pathname);
  return route.fulfill({contentType:'application/pdf',headers:{'Content-Disposition':"attachment; filename*=UTF-8''notes.pdf"},body:'%PDF'});
 });
 await page.goto(`/materials/${materialId}/notes`);
 await selectText(page);
 await page.getByRole('button',{name:'新增註記',exact:true}).first().click();
 await page.getByRole('textbox',{name:'個人註記',exact:true}).fill('舊稿的個人註記');
 await page.getByRole('button',{name:'儲存註記',exact:true}).click();await expect(page.getByRole('dialog')).toBeHidden();
 expect(annotationPath).toContain(`/knowledge-structures/${oldRevision}/notes/summary/annotations/`);
 await page.getByRole('button',{name:'匯出筆記',exact:true}).click();
 const exportMenu=page.getByRole('menu',{name:'匯出筆記',exact:true});
 const downloading=page.waitForEvent('download');await exportMenu.getByRole('menuitem',{name:'PDF',exact:true}).click();await downloading;
 expect(exportPath).toBe(`/v1/materials/${materialId}/knowledge-structures/${oldRevision}/notes/summary/export/pdf`);
 expect(state.writes).toEqual([]);
});


test("updated materials offer a full notes update", async ({ page }) => {
  const state = await setup(page);
  state.setFull({ ...notes("full"), update_available: true });
  await page.goto(`/materials/${materialId}/notes`);
  await page.getByRole("button", { name: "完整筆記", exact: true }).click();
  await expect(page.getByText("教材已更新，要更新完整筆記嗎？", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "更新完整筆記", exact: true }).click();
  expect(state.writes).toEqual(["generate"]);
});

test("updating notes retain readable content, real elapsed time and section progress until replacement", async ({ page }) => {
  const initial = { ...notes(), status: "running" as const, updating: true, started_at: new Date(Date.now() - 65000).toISOString(),
    progress: { stage: "generating" as const, completed_sections: 0, total_sections: 4, completed_parts: 0, total_parts: 10, current_section: "堆疊" } };
  const state = await setup(page, initial);
  await page.goto(`/materials/${materialId}/notes`);
  await expect(page.getByText("正在更新筆記，完成後自動替換", { exact: true })).toBeVisible();
  await expect(page.getByText(/已耗時 1 分/)).toBeVisible();
  await expect(page.getByRole("progressbar", { name: "已完成批次" })).not.toHaveAttribute("aria-valuenow");
  await expect(page.getByRole("heading", { name: "一、堆疊的順序" })).toBeVisible();
  state.setSummary({ ...initial, progress: { ...initial.progress, completed_sections: 1, completed_parts: 2 } });
  await expect(page.getByRole("progressbar", { name: "已完成批次" })).toHaveAttribute("aria-valuenow", "2");
  await expect(page.getByText("已完成 2 / 10 批", { exact: true })).toBeVisible();
  const completed = notes();
  completed.paragraphs[0].heading = "更新後的堆疊"; completed.outline[0].heading = "更新後的堆疊";
  state.setSummary(completed);
  await expect(page.getByRole("heading", { name: "一、更新後的堆疊" })).toBeVisible();
});

test("notes toolbar and current chapter remain reachable while reading, mobile drawer restores chapter focus", async ({ page }, testInfo) => {
  const initial = notes();
  const first = initial.paragraphs[0];
  if (!("blocks" in first)) throw new Error("structured fixture required");
  first.blocks.push({ type: "text", text: Array(35).fill("由頂端放入與取出，依序追蹤堆疊中的資料。").join("\n") });
  await setup(page, initial);
  for (const [width, height] of [[1440,900],[1300,900],[1200,900],[1024,768],[390,844]]) {
    await page.setViewportSize({ width, height });
    await page.goto(`/materials/${materialId}/notes`);
    await expect(page.getByRole("heading", { name: "一、堆疊的順序" })).toBeVisible();
    await page.screenshot({ path: testInfo.outputPath(`notes-top-${width}.png`) });
    if (width <= 720) {
      await page.getByRole("button", { name: "章節目錄", exact: true }).click();
      const drawer = page.getByRole("dialog", { name: "章節目錄" });
      await page.screenshot({ path: testInfo.outputPath(`notes-drawer-${width}.png`) });
      await drawer.getByRole("button", { name: "1.1 堆疊大小", exact: true }).click();
      await expect(drawer).toBeHidden();
      await expect(page.locator("#note-section-1")).toBeFocused();
      await page.getByRole("button", { name: "章節目錄", exact: true }).click();
      await page.keyboard.press("Escape");
      await expect(page.getByRole("button", { name: "章節目錄", exact: true })).toBeFocused();
    } else {
      const outline = page.getByRole("navigation", { name: "章節大綱" });
      await outline.getByRole("button", { name: "1.1 堆疊大小", exact: true }).click();
      await expect(page.locator("#note-section-1")).toBeFocused();
      await expect(outline.getByRole("button", { name: "1.1 堆疊大小", exact: true })).toHaveAttribute("aria-current", "location");
      await page.getByRole("button", { name: "章節目錄", exact: true }).click();
      await expect(page.getByRole("button", { name: "章節目錄", exact: true })).toHaveAttribute("aria-expanded", "false");
      await page.getByRole("button", { name: "章節目錄", exact: true }).click();
    }
    await expect(page.getByRole("button", { name: "完整筆記", exact: true })).toBeInViewport();
    await expect(page.getByRole("button", { name: "匯出筆記", exact: true })).toBeInViewport();
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(width);
    await page.screenshot({ path: testInfo.outputPath(`notes-reading-${width}.png`) });
  }
});


test("older notes open the map bound to the visible content and its original run", async ({ page }) => {
  const oldRevision = `knowledge-structure:sha256:${"e".repeat(64)}`;
  const oldRun = "77777777-7777-4777-8777-777777777777";
  const initial = { ...notes(), content_revision: oldRevision, update_available: true, source_resolver: `/v1/materials/${materialId}/knowledge-structures/${oldRevision}/evidence` };
  await setup(page, initial);
  const oldView = { ...structureView(), knowledge_structure_revision: oldRevision, source_resolver: initial.source_resolver };
  await page.route(`**/knowledge-structures/${encodeURIComponent(oldRevision)}/outline`, route => route.fulfill({ json: outlineView(oldView, materialId, oldRun) }));
  await page.goto(`/materials/${materialId}/notes`);
  await selectText(page);
  await page.getByRole("button", { name: "在地圖查看", exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`/runs/${oldRun}/knowledge-structures/`));
  expect(decodeURIComponent(page.url())).toContain(oldRevision);
});


test("long notes keep the current outline entry visible and restore the local reading position", async ({ page }) => {
  const initial = notes();
  initial.paragraphs = Array.from({ length: 36 }, (_,index) => ({ ...initial.paragraphs[0], id: String(index), anchor: `anchor-${index}`, heading: `閱讀段落 ${index + 1}` }));
  initial.outline = initial.paragraphs.map(p => ({ paragraph_id: p.id, level: 1, number: "", heading: p.heading }));
  await setup(page, initial);
  await page.goto(`/materials/${materialId}/notes`);
  const body = page.locator(".note-reading-body");
  await body.evaluate(element => { element.scrollTop = element.scrollHeight; });
  const last = page.getByRole("navigation", { name: "章節大綱" }).getByRole("button", { name: "閱讀段落 36", exact: true });
  await expect(last).toHaveAttribute("aria-current", "location");
  await expect(last).toBeInViewport();
  const storedPosition = await body.evaluate(element => element.scrollTop);
  await page.reload();
  await page.getByRole("button", { name: "回到上次閱讀位置", exact: true }).click();
  await expect.poll(() => body.evaluate(element => element.scrollTop)).toBe(storedPosition);
  await expect(last).toHaveAttribute("aria-current", "location");
  await expect(last).toBeInViewport();
  await page.setViewportSize({ width: 390, height: 844 });
  const trigger = page.getByRole("button", { name: "章節目錄", exact: true });
  await expect(trigger).toHaveAttribute("aria-expanded", "false");
  await trigger.click();
  const drawer = page.getByRole("dialog", { name: "章節目錄" });
  await expect(trigger).toHaveAttribute("aria-expanded", "true");
  await expect(drawer.getByRole("button", { name: "閱讀段落 36", exact: true })).toBeInViewport();
  await page.keyboard.press("Escape");
  await expect(trigger).toHaveAttribute("aria-expanded", "false");
});

test("existing full content remains readable while an update is available", async ({ page }) => {
  const state = await setup(page);
  state.setFull({ ...notes("full"), status: "not_generated", update_available: true });
  await page.goto(`/materials/${materialId}/notes`);
  await page.getByRole("button", { name: "完整筆記", exact: true }).click();
  await expect(page.getByRole("heading", { name: "一、堆疊的順序" })).toBeVisible();
  expect(state.writes).toEqual([]);
});

test("progress page follows the upload layout, keeps the old draft readable and returns when finished", async ({ page }) => {
  const initial = { ...notes(), status: "running" as const, updating: true, started_at: new Date(Date.now() - 65000).toISOString(), updated_at: new Date().toISOString(),
    progress: { stage: "generating" as const, completed_sections: 0, total_sections: 4, completed_parts: 3, total_parts: 10, current_section: "堆疊" } };
  const state = await setup(page, initial);
  await page.goto(`/materials/${materialId}/notes`);
  await page.getByRole("button", { name: "查看進度", exact: true }).click();
  const progress = page.getByRole("region", { name: "筆記生成進度" });
  await expect(progress.getByRole("heading", { name: "正在更新重點整理", level: 1 })).toBeVisible();
  await expect(progress.getByRole("heading", { name: "整體進度" })).toBeVisible();
  await expect(progress.getByText("已完成 3 / 10 批。完成後自動開啟筆記。")).toBeVisible();
  for (const step of ["規劃分批", "逐批撰寫", "逐段核對", "整理章節", "發布"]) await expect(progress.getByText(step, { exact: true })).toBeVisible();
  await expect(progress.getByText("已完成 3 / 10 批・堆疊", { exact: true })).toBeVisible();
  await expect(progress.getByText(/1 分/)).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await expect(page).toHaveURL(/\/notes\?view=progress$/);
  await page.reload();
  await expect(progress).toBeVisible();
  await page.goBack();
  await expect(page.getByRole("heading", { name: "一、堆疊的順序" })).toBeVisible();
  await page.goForward();
  await expect(progress).toBeVisible();
  await progress.getByRole("button", { name: "返回筆記" }).click();
  await expect(page).toHaveURL(/\/notes$/);
  await expect(page.getByRole("heading", { name: "一、堆疊的順序" })).toBeVisible();
  await page.getByRole("button", { name: "查看進度", exact: true }).click();
  const completed = notes();
  completed.paragraphs[0].heading = "更新後的堆疊"; completed.outline[0].heading = "更新後的堆疊";
  state.setSummary(completed);
  await expect(page.getByRole("region", { name: "筆記生成進度" })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "一、更新後的堆疊" })).toBeVisible();
  await expect(page).toHaveURL(/\/notes$/);
});

test("knowledge map concept opens the first note section that cites it", async ({ page }) => {
  await setup(page);
  await page.goto(`/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}`);
  await page.getByRole("tab", { name: "知識地圖" }).click();
  await openMapConcept(page);
  await page.getByRole("dialog", { name: "概念詳情", exact: true }).getByRole("button", { name: "在筆記中查看" }).click();
  await expect(page).toHaveURL(new RegExp(`/materials/${materialId}/notes$`));
  await expect(page.locator("#note-section-0")).toHaveClass(/note-paragraph-located/);
  await expect(page.locator("#note-section-0")).toBeFocused();
});

test("knowledge map hides the note link when notes do not cite the concept", async ({ page }) => {
  await setup(page, { ...notes(), paragraphs: [], outline: [] });
  await page.goto(`/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}`);
  await page.getByRole("tab", { name: "知識地圖" }).click();
  await openMapConcept(page);
  await expect(page.getByRole("dialog", { name: "概念詳情", exact: true }).getByRole("button", { name: "在筆記中查看" })).toHaveCount(0);
});

test("selected text retains highlight styles after reload and removes overlapping marks", async ({ page }) => {
  await setup(page);
  await page.goto(`/materials/${materialId}/notes`);
  await selectText(page);
  await page.getByRole("button", { name: "標記黃色", exact: true }).click();
  await expect(page.locator(".note-mark-yellow")).toHaveText("堆疊使用後進先出");
  await page.getByRole("button", { name: "標記粗體", exact: true }).click();
  await expect(page.locator(".note-mark-yellow")).toHaveCSS("font-weight", "700");
  await page.reload();
  await expect(page.locator(".note-mark-yellow")).toHaveText("堆疊使用後進先出");
  await selectText(page);
  await page.getByRole("button", { name: "移除標記", exact: true }).click();
  await expect(page.locator("#note-section-0 [data-note-text]").first()).toHaveCSS("font-weight", "400");
  await page.reload();
  await expect(page.locator("#note-section-0 [data-note-text]").first()).toHaveCSS("background-color", "rgba(0, 0, 0, 0)");
});

test("selection opens a BOT draft without submitting a question", async ({ page }) => {
  await setup(page);
  const posts: string[] = [];
  await page.route("**/voice-conversations", route => {
    if (route.request().method() === "POST") posts.push(route.request().url());
    return json(route, { conversations: [] });
  });
  await page.goto(`/materials/${materialId}/notes`);
  await selectText(page);
  await page.getByRole("button", { name: "問 AI BOT", exact: true }).click();
  await expect(page.getByLabel("問題／辨識文字")).toHaveValue("堆疊使用後進先出");
  expect(posts).toEqual([]);
});

test("cross-block selection saves separate marks and explains the annotation limit", async ({ page }) => {
  await setup(page);
  await page.goto(`/materials/${materialId}/notes`);
  await expect(page.locator("#note-section-0 [data-note-text]").first()).toBeVisible();
  await page.locator("#note-section-0").evaluate(element => {
    const spans = element.querySelectorAll("[data-note-text]");
    const range = document.createRange(); range.setStart(spans[0].firstChild!, 0); range.setEnd(spans[1].firstChild!, spans[1].textContent!.length);
    const selection = window.getSelection()!; selection.removeAllRanges(); selection.addRange(range);
    spans[1].dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
  });
  await expect(page.getByRole("button", { name: "新增註記", exact: true })).toBeDisabled();
  await expect(page.getByText("註記請選取同一段內的文字", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "標記綠色", exact: true }).click();
  await expect(page.locator(".note-mark-green")).toHaveCount(2);
  await page.reload();
  await expect(page.locator(".note-mark-green")).toHaveCount(2);
});

test("touch selection changes reveal the mobile toolbar below the selected text", async ({ page }) => {
  await setup(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`/materials/${materialId}/notes`);
  const text = page.locator("#note-section-0 [data-note-text]").first();
  await expect(text).toBeVisible();
  await text.evaluate(element => {
    const range = document.createRange(); range.selectNodeContents(element);
    const selection = window.getSelection()!; selection.removeAllRanges(); selection.addRange(range);
    document.dispatchEvent(new Event("selectionchange"));
  });
  const toolbar = page.getByRole("toolbar", { name: "選取文字工具列" });
  await expect(toolbar).toBeVisible();
  expect((await toolbar.boundingBox())!.y).toBeGreaterThanOrEqual((await text.boundingBox())!.y);
  await toolbar.getByRole("button", { name: "新增註記", exact: true }).click();
  await expect(page.getByRole("dialog", { name: "註記邊欄" })).toBeVisible();
  await expect(page.getByRole("textbox", { name: "個人註記", exact: true })).toBeFocused();
});

test("overlapping comments align to the same quote and cycle their active cards", async ({ page }, testInfo) => {
  const initial = notes();
  initial.paragraphs = [{ ...initial.paragraphs[0], blocks: [{ type: "bullets", items: [{ text: "最上面的盤子最先拿走" }] }] }];
  initial.outline = initial.outline.slice(0, 1);
  initial.annotations = ["第一則想法", "第二則想法"].map((body, i) => ({
    annotation_id: `55555555-5555-4555-8555-${String(i + 1).padStart(12, "0")}`, anchor: "stable-anchor", body,
    paragraph_id: "0", block_path: ["blocks", 0, "items", 0, "text"], start: 0, end: 11, quote: "最上面的盤子最先拿走",
    original_revision: structureRevision, original_paragraph: initial.paragraphs[0], updated_at: "2026-10-25T01:00:00Z",
  }));
  await setup(page, initial);
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto(`/materials/${materialId}/notes`);
  const quote = page.locator(".note-mark-comment").first();
  await quote.click();
  await expect(page.locator(".note-comment-card.is-active")).toContainText("第一則想法");
  await quote.click();
  await expect(page.locator(".note-comment-card.is-active")).toContainText("第二則想法");
  const cards = page.locator(".note-comment-lane .note-comment-card");
  const first = (await cards.nth(0).boundingBox())!, second = (await cards.nth(1).boundingBox())!;
  expect(first.y).toBeGreaterThanOrEqual((await quote.boundingBox())!.y - 2);
  expect(second.y).toBeGreaterThanOrEqual(first.y + first.height);
  const widths = await page.locator(".note-markup").evaluate(element => ({
    column: parseFloat(getComputedStyle(element).gridTemplateColumns),
    prose: element.querySelector(".note-markup-prose")!.getBoundingClientRect().width,
  }));
  expect(widths.prose).toBeCloseTo(widths.column, 1);
  await page.screenshot({ path: testInfo.outputPath("notes-comments-1440.png"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  const pins = page.getByRole("button", { name: "開啟註記", exact: true });
  await expect(pins).toHaveCount(2);
  await expect.poll(async () => {
    const first = (await pins.nth(0).boundingBox())!, second = (await pins.nth(1).boundingBox())!;
    return (second.y - first.y) / first.height;
  }).toBeLessThan(2);
});
