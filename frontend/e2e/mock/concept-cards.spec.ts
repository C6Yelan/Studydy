import { recordCardDrawing, cardDrawing, drawnText, expectCardTextFits } from "../fixtures/canvas-cards";
import { visualCases, visualCase } from "../fixtures/visual-concept-cards";
import { difficultyView } from "../fixtures/learning-diagnosis";
import type { CardGeneration, CardTextEdit, CardDesign } from "../../src/api/contracts";
import { expect, test, type Page } from "@playwright/test";
import { artifactId, materialId, runId, sessionId, structureRevision, structureView, mockKnowledgeMapApi, json } from "../fixtures/knowledge-map";

const cardSetId = "55555555-5555-4555-8555-555555555555";
const mapPath = `/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}`;

test("card difficulty stays bound to the saved card revision", async ({ page }) => {
  const fixture = await mockCards(page, { saved: true });
  fixture.material.head_revision = `knowledge-structure:sha256:${"9".repeat(64)}`;
  fixture.material.available_structures.push({ ...fixture.material.available_structures[0],
    run_id: "66666666-6666-4666-8666-666666666666", knowledge_structure_revision: fixture.material.head_revision });
  const requests: string[] = [];
  let assessed = true;
  await page.route("**/concept-difficulty", route => {
    requests.push(decodeURIComponent(new URL(route.request().url()).pathname));
    return json(route, { ...difficultyView(materialId, structureRevision), status: assessed ? "ready" : "failed", concepts: [{
      concept_id: structureView().concepts[0].concept_id, label: "堆疊（Stack）", level: assessed ? "hard" : null, reason: assessed ? "先理解後進先出的順序。" : null,
      personal_difficulty: true, incorrect_count: 2, answer_count: 3,
    }] });
  });
  await page.goto(`/materials/${materialId}/concept-cards/${cardSetId}`);
  await expect(page.locator(".concept-card > .concept-difficulty")).toContainText("偏難");
  await expect(page.locator(".concept-card > .concept-difficulty")).toContainText("先理解後進先出的順序。");
  await expect(page.locator(".concept-card > .concept-difficulty")).toContainText("你的難點");
  await expect(page.locator(".concept-card > .concept-difficulty")).toContainText("答錯率 67%（2 / 3 題）");
  expect(requests).toEqual([`/v1/materials/${materialId}/knowledge-structures/${structureRevision}/concept-difficulty`]);
  assessed = false;
  await page.reload();
  await expect(page.locator(".concept-card > .concept-difficulty")).toContainText("你的難點");
  await expect(page.locator(".concept-card > .concept-difficulty")).toContainText("答錯率 67%（2 / 3 題）");
  await expect(page.locator(".concept-card > .concept-difficulty")).not.toContainText("一般");
});

async function mockCards(page: Page, { saved = false, long = false, many = false, withProgress = false, title = "資料結構 · 核心概念", savedCount = null as number | null } = {}) {
  await recordCardDrawing(page);
  const view = structureView();
  view.concepts[0].label = "堆疊（Stack）";
  view.concepts[0].claims[0].text = "堆疊遵循後進先出（LIFO）的原則：最後放入的元素會最先被取出。";
  view.concepts[1].label = "陣列（Array）";
  view.concepts[1].claims[0].text = "陣列將元素保存在連續的記憶體位置，可使用索引存取特定元素。";
  if (many) {
    for (let index = 2; index < 31; index++) {
      const concept = structuredClone(view.concepts[0]);
      concept.concept_id = `concept:sha256:${(index + 100).toString(16).padStart(64, "0")}`;
      concept.claims[0].claim_id = `claim:sha256:${(index + 100).toString(16).padStart(64, "0")}`;
      concept.label = `概念 ${index + 1}：資料結構與記憶體管理`;
      view.concepts.push(concept);
      view.document_tree.sections[0].concept_ids.push(concept.concept_id);
      view.initial_learning_path.push({ position: index + 1, concept_id: concept.concept_id, reason: "document_order" });
    }
  }
  if (long) {
    view.status.quality = "needs_review";
    view.status.decision = "review";
    view.concepts[0].claims[0].text = "保留所有必要條件與符號。".repeat(100) + "\n最後一個條件不可遺漏：x != 0。";
  }
  await mockKnowledgeMapApi(page, view);
  const material = {
    schema: "material-library-item/v1", material_id: materialId, source_artifact_id: artifactId,
    display_name: "資料結構講義.pdf", size_bytes: 100, created_at: "2026-10-02T00:00:00Z",
    head_revision: structureRevision, latest_attempt: null, study_sessions: withProgress ? [{ study_session_id: sessionId, run_id: runId, knowledge_structure_revision: structureRevision, current_concept_id: view.concepts[0].concept_id, status: "active", started_at: "2026-10-02T00:00:00Z" }] : [],
    available_structures: [{ run_id: runId, knowledge_structure_revision: structureRevision, created_at: "2026-10-02T00:00:00Z", status: "succeeded" }],
  };
  await page.route(`**/v1/materials/${materialId}`, (route) => json(route, material));
  await page.route("**/v1/materials", (route) => json(route, { schema: "material-library/v1", materials: [material] }));
  let selection = view.concepts.map((item) => item.concept_id);
  let name = title;
  let exists = saved;
  let version = 1;
  let cardCount = savedCount;
  let pageCounts: Record<string, number> = {};
  let generation: CardGeneration | null = null;
  let displayEdits: Record<string, CardTextEdit[]> = {};
  let designs: Record<string, CardDesign> = {};
  const readyGeneration = (): CardGeneration => ({ status: "ready", stage: "complete", completed: selection.length, total: selection.length, error_code: null, retained_original: 0, cached: 0 });
  await page.route(`**/v1/card-sets/${cardSetId}/generation`, route => json(route, generation ?? readyGeneration()));
  await page.route(`**/v1/card-sets/${cardSetId}/generation/retry`, route => {
    generation = { ...(generation ?? readyGeneration()), status: "pending", error_code: null };
    return json(route, generation);
  });
  const requests: { key: string | undefined; body: unknown }[] = [];
  const forbiddenMutations: string[] = [];
  page.on("request", (request) => {
    if (request.method() !== "GET" && request.url().includes("/study-sessions")) forbiddenMutations.push(request.url());
  });
  const summary = () => ({
    version, card_set_id: cardSetId, material_id: materialId, material_name: material.display_name,
    knowledge_structure_revision: structureRevision, name, card_count: cardCount,
    generation: generation ?? readyGeneration(),
    created_at: "2026-10-02T00:00:00Z", is_current_revision: !long,
  });
  const batch = (offset = 0) => {
    const selected = selection.slice(offset, offset + 8);
    const relations = view.relations.filter(r => selected.includes(r.source_concept_id) || selected.includes(r.target_concept_id));
    const ids = new Set([...selected, ...relations.flatMap(r => [r.source_concept_id, r.target_concept_id])]);
    const allEvidence = new Map(view.concepts.flatMap(c => c.claims.flatMap(claim => claim.evidence)).map(e => [e.evidence_id, e]));
    return { schema: 'card-batch/v1', card_set_id: cardSetId, version, offset, total: selection.length,
      cards: selected.map(id => { const { concept_id, label, claims } = view.concepts.find(c => c.concept_id === id)!; return { concept_id, label, claims, display_edits: displayEdits[id] ?? [], design: designs[id] ?? null }; }),
      context: { source_resolver: decodeURIComponent(view.source_resolver), status: view.status,
        concepts: view.concepts.filter(c => ids.has(c.concept_id)), relations,
        relation_evidence: [...new Set(relations.flatMap(r => r.evidence_refs))].flatMap(id => allEvidence.has(id) ? [allEvidence.get(id)!] : []) } };
  };
  const study = () => ({ schema: 'card-study/v1', ...summary(), source_resolver: decodeURIComponent(view.source_resolver), status: view.status,
    navigation: selection.map(id => ({ concept_id: id, label: view.concepts.find(c => c.concept_id === id)!.label, page_count: pageCounts[id] ?? null })),
    batch: (generation ?? readyGeneration()).status === 'ready' ? batch() : null });
  await page.route(`**/v1/card-sets/${cardSetId}/study`, route => exists ? json(route, study())
    : json(route, { schema: 'api-error/v1', request_id: sessionId, reason_code: 'RESOURCE_NOT_FOUND', retryable: false, message: 'Request could not be completed.' }, 404));
  await page.route(`**/v1/card-sets/${cardSetId}/cards?*`, route => {
    const query = new URL(route.request().url()).searchParams;
    if (Number(query.get('version')) !== version) return json(route, { schema: 'api-error/v1', request_id: sessionId, reason_code: 'CARD_SET_CONFLICT', retryable: false, message: 'Request could not be completed.' }, 409);
    return json(route, batch(Number(query.get('offset'))));
  });
  await page.route(`**/v1/card-sets/${cardSetId}/count`, route => {
    const body = route.request().postDataJSON();
    if (body.expected_version !== version) return json(route, { schema: "api-error/v1", request_id: sessionId, reason_code: "CARD_SET_CONFLICT", retryable: false, message: "Request could not be completed." }, 409);
    if (body.concept_counts) {
      pageCounts = { ...body.concept_counts, ...pageCounts };
      if (selection.every(id => pageCounts[id] !== undefined)) cardCount = selection.reduce((sum, id) => sum + pageCounts[id], 0);
    } else cardCount ??= body.card_count;
    return json(route, summary());
  });
  await page.route(`**/v1/materials/${materialId}/card-sets`, (route) => {
    const body = route.request().postDataJSON();
    requests.push({ key: route.request().headers()["idempotency-key"], body });
    selection = body.concept_ids;
    name = body.name;
    exists = true;
    cardCount = null; pageCounts = {};
    return json(route, summary(), 201);
  });
  await page.route(`**/v1/card-sets/${cardSetId}/update`, (route) => {
    const body = route.request().postDataJSON();
    if (body.expected_version !== version) return json(route, { schema: "api-error/v1", request_id: sessionId, reason_code: "CARD_SET_CONFLICT", retryable: false, message: "Request could not be completed." }, 409);
    name = body.name;
    if (body.concept_ids && JSON.stringify(selection) !== JSON.stringify(body.concept_ids)) { cardCount = null; pageCounts = {}; }
    selection = body.concept_ids ?? selection;
    version++;
    return json(route, summary());
  });
  await page.route("**/v1/card-sets", (route) => json(route, { schema: "card-set-list/v1", card_sets: exists ? [summary()] : [] }));
  await page.route(`**/v1/card-sets/${cardSetId}`, (route) => {
    if (route.request().method() === "DELETE") {
      exists = false;
      return json(route, { schema: "card-set-deleted/v1", card_set_id: cardSetId });
    }
    if (!exists) return json(route, { schema: "api-error/v1", request_id: sessionId, reason_code: "RESOURCE_NOT_FOUND", retryable: false, message: "Request could not be completed." }, 404);
    return json(route, { ...summary(), schema: "card-set/v1", source_resolver: decodeURIComponent(view.source_resolver), status: view.status, excluded_pages: [], display_edits: displayEdits, generation: generation ?? readyGeneration(), cards: selection.map((id) => view.concepts.find((item) => item.concept_id === id)!).map(({ concept_id, label, claims }) => ({ concept_id, label, claims })) });
  });
  return { requests, forbiddenMutations, material, view, summary, study, batch, setDesigns: (value: Record<string, CardDesign>) => { designs = value; }, setGeneration: (value: CardGeneration | null) => { generation = value; }, setEdits: (value: Record<string, CardTextEdit[]>) => { displayEdits = value; } };
}

async function openSavedCardManagement(page: Page) {
  await expect(page.getByRole("button", { name: "管理卡組", exact: true })).toHaveCount(0);
  await page.getByRole('tablist', { name: '教材學習內容' }).getByRole('tab', { name: '概念卡', exact: true }).click();
  await page.locator(".library-item .material-management-menu summary").click();
  await page.locator(".material-management-menu[open]").getByRole("button", { name: "管理卡組", exact: true }).click();
}

test("card creation fits a desktop viewport and preserves selections across pages and search", async ({ page }) => {
  const fixture = await mockCards(page, { many: true, long: true, withProgress: true });
  let progressReads = 0;
  page.on("request", request => { if (request.url().endsWith("/progress")) progressReads++; });
  await page.setViewportSize({ width: 1366, height: 768 });
  await page.goto(mapPath + "/create-cards");
  const manual = page.getByRole("region", { name: "選擇概念", exact: true });
  const pagination = page.getByRole("navigation", { name: "概念清單分頁" });
  for (const viewport of [{ width: 1440, height: 900 }, { width: 1366, height: 768 }, { width: 1280, height: 720 }, { width: 1024, height: 768 }]) {
    await page.setViewportSize(viewport);
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollHeight <= innerHeight && document.documentElement.scrollWidth === innerWidth)).toBe(true);
    await expect.poll(() => manual.getByRole("checkbox").count()).toBeGreaterThanOrEqual(viewport.height > 850 ? 12 : 9);
    const firstRow = await manual.locator(".cards-concept-row").evaluateAll(elements => elements.slice(0, 3).map(element => {
      const { x, y } = element.getBoundingClientRect();
      return { x, y };
    }));
    expect(firstRow[0].y).toBe(firstRow[1].y);
    expect(firstRow[1].y).toBe(firstRow[2].y);
    expect(firstRow[0].x).toBeLessThan(firstRow[1].x);
    expect(firstRow[1].x).toBeLessThan(firstRow[2].x);
  }
  await page.setViewportSize({ width: 1366, height: 768 });
  await expect.poll(() => manual.getByRole("checkbox").count()).toBeGreaterThanOrEqual(9);
  await manual.getByRole("button", { name: "取消全選", exact: true }).click();
  const nextPageConcept = fixture.view.concepts[await manual.getByRole("checkbox").count()].concept_id;
  await manual.getByRole("checkbox").first().check();
  await pagination.getByRole("button", { name: "下一頁", exact: true }).click();
  await manual.getByRole("checkbox").first().check();
  await pagination.getByRole("button", { name: "上一頁", exact: true }).click();
  await expect(manual.getByRole("checkbox").first()).toBeChecked();
  await manual.getByRole("searchbox", { name: "搜尋概念" }).fill("概念 31");
  await expect(manual.getByRole("checkbox")).toHaveCount(1);
  await manual.getByRole("checkbox").check();
  await manual.getByRole("searchbox", { name: "搜尋概念" }).fill("");
  await manual.getByRole("button", { name: "預覽「堆疊（Stack）」" }).click();
  await expect(page.locator('.cards-preview canvas.visual-concept-card').first()).toBeVisible();
  await expect(manual).toContainText('已選 3 / 31 個觀念');
  await page.locator(".concept-card-details > summary").click();
  await expect(page.locator(".flashcard-point")).toContainText("最後一個條件不可遺漏");
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollHeight <= innerHeight)).toBe(true);
  await pagination.getByRole("button", { name: "下一頁", exact: true }).click();
  await page.getByRole("button", { name: "保存並開始複習" }).click();
  await expect(page).toHaveURL(new RegExp(`/concept-cards/${cardSetId}$`));
  expect(fixture.requests).toHaveLength(1);
  expect((fixture.requests[0].body as { concept_ids: string[] }).concept_ids).toEqual([fixture.view.concepts[0].concept_id, nextPageConcept, fixture.view.concepts[30].concept_id]);
  expect(progressReads).toBe(0);
  expect(fixture.forbiddenMutations).toEqual([]);
});

test("map creates a saved multi-card deck; keyboard, sources, shuffle and delete work", async ({ page }) => {
  const fixture = await mockCards(page);
  await page.goto(mapPath);
  await page.getByRole("tablist", { name: "教材學習內容" }).getByRole("tab", { name: "概念卡", exact: true }).click();
  await page.locator(".library-header").getByRole("button", { name: "建立卡組", exact: true }).click();
  await expect(page.getByRole("heading", { name: "建立概念卡組" })).toBeVisible();
  await page.getByLabel("卡組名稱", { exact: true }).fill("我的資料結構卡組");
  await page.getByRole("button", { name: "取消全選", exact: true }).click();
  await expect(page.getByRole("button", { name: "保存並開始複習" })).toBeDisabled();
  await page.getByRole("button", { name: "全選", exact: true }).click();
  await page.screenshot({ path: "../.studydy-runtime/card-preview/create-desktop.png", fullPage: true });
  await page.getByRole("button", { name: "保存並開始複習" }).click();
  await expect(page).toHaveURL(new RegExp(`/concept-cards/${cardSetId}$`));
  expect(fixture.requests).toHaveLength(1);
  await expect(page.locator(".visual-concept-card")).toHaveAttribute("title", "堆疊（Stack）｜概念卡");
  await page.screenshot({ path: "../.studydy-runtime/card-preview/study-front-desktop.png", fullPage: true });
  await page.keyboard.press("Space");
  await expect(page.locator(".visual-concept-card")).toBeVisible();
  await expect(page.locator(".concept-card-details")).not.toHaveAttribute("open", "");
  await page.locator(".concept-card-details > summary").click();
  await expect(page.locator(".flashcard-point")).toContainText("後進先出");
  await expect(page.locator(".flashcard")).not.toContainText("先備");
  await expect(page.getByRole("region", { name: "概念重點來源", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "查看第 1 頁來源" })).toBeVisible();
  await page.getByRole("button", { name: "查看第 1 頁來源" }).click();
  await expect(page.getByRole("dialog", { name: "教材來源" })).toBeVisible();
  await expect(page.getByRole("link", { name: "開啟 PDF 來源頁" })).toHaveAttribute("href", `/v1/artifacts/${artifactId}#page=1`);
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "下一張", exact: true }).click();
  await expect(page.locator(".visual-concept-card")).toHaveAttribute("title", "陣列（Array）｜概念卡");
  await page.keyboard.press("ArrowLeft");
  await expect(page.locator(".visual-concept-card")).toHaveAttribute("title", "堆疊（Stack）｜概念卡");
  await page.locator(".concept-card-details > summary").click();
  await page.screenshot({ path: "../.studydy-runtime/card-preview/study-details-desktop.png", fullPage: true });
  await page.getByRole("button", { name: "洗牌重看" }).click();
  await expect(page.locator(".cards-study-notice")).toHaveText("已洗牌，從第一張開始。");
  await page.getByRole("button", { name: "下一張", exact: true }).click();
  await page.getByRole("button", { name: "完成本輪" }).click();
  await expect(page.getByRole("heading", { name: "已瀏覽全部卡片" })).toBeVisible();
  await page.getByRole("button", { name: "再看一次" }).click();
  await page.reload();
  await expect(page.locator(".visual-concept-card")).toHaveAttribute("title", "堆疊（Stack）｜概念卡");
  await page.getByRole('tablist', { name: '教材學習內容' }).getByRole('tab', { name: '概念卡', exact: true }).click();
  await expect(page.getByRole("heading", { name: "我的資料結構卡組" })).toBeVisible();
  await page.screenshot({ path: "../.studydy-runtime/card-preview/library-desktop.png", fullPage: true });
  await page.getByRole("button", { name: "管理卡組「我的資料結構卡組」" }).click();
  await page.getByRole("button", { name: "刪除卡組「我的資料結構卡組」" }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(page.getByRole("group", { name: "刪除卡組確認" }).getByText("確定刪除？", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "確認刪除卡組" }).click();
  await expect(page.getByRole("region", { name: "空卡組引導" })).toBeVisible();
  expect(fixture.forbiddenMutations).toEqual([]);
});

test("card deletion confirms inline, cancels safely and retries once", async ({ page }) => {
  const title = "資料結構考前複習｜堆疊、陣列與佇列的核心概念";
  const fixture = await mockCards(page, { saved: true, title });
  let deletes = 0;
  let release!: () => void;
  const pending = new Promise<void>(resolve => { release = resolve; });
  await page.route(`**/v1/card-sets/${cardSetId}`, async route => {
    if (route.request().method() !== "DELETE") return route.fallback();
    deletes++;
    if (deletes === 1) return json(route, { schema: "api-error/v1", request_id: sessionId, reason_code: "STORAGE_UNAVAILABLE", retryable: true, message: "Request could not be completed." }, 503);
    await pending;
    return route.fallback();
  });
  await page.goto("/concept-cards");
  const card = page.getByRole("article", { name: title, exact: true });
  const opener = card.getByRole("button", { name: `管理卡組「${title}」`, exact: true });
  const confirmation = card.getByRole("group", { name: "刪除卡組確認" });
  for (const viewport of [{ width: 1440, height: 900 }, { width: 390, height: 844 }]) {
    await page.setViewportSize(viewport);
    await opener.click();
    await card.getByRole("button", { name: `刪除卡組「${title}」`, exact: true }).click();
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await expect(confirmation.getByText("確定刪除？", { exact: true })).toBeVisible();
    await expect(confirmation.getByText(title, { exact: true })).toHaveCount(0);
    await expect(confirmation.getByRole("button", { name: "取消", exact: true })).toBeFocused();
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(viewport.width);
    await page.keyboard.press("Escape");
    await expect(confirmation).toHaveCount(0);
    await expect(opener).toBeFocused();
    await opener.click();
    await card.getByRole("button", { name: `刪除卡組「${title}」`, exact: true }).click();
    await confirmation.getByRole("button", { name: "取消", exact: true }).click();
    await expect(opener).toBeFocused();
    expect(deletes).toBe(0);
  }
  await opener.click();
  await card.getByRole("button", { name: `刪除卡組「${title}」`, exact: true }).click();
  await confirmation.getByRole("button", { name: "確認刪除卡組", exact: true }).click();
  await expect(confirmation.getByRole("alert")).toContainText("無法刪除卡組。資料服務暫時無法使用");
  await expect(confirmation.getByRole("button", { name: "取消", exact: true })).toBeFocused();
  await expect(card).toBeVisible();
  expect(deletes).toBe(1);
  // 連按兩次確認（同一個事件迴圈內）只送出一次刪除。
  await confirmation.getByRole("button", { name: "確認刪除卡組", exact: true }).evaluate(button => {
    (button as HTMLButtonElement).click();
    (button as HTMLButtonElement).click();
  });
  await expect.poll(() => deletes).toBe(2);
  await expect(confirmation.getByRole("button", { name: "正在刪除…", exact: true })).toBeDisabled();
  await expect(confirmation.getByRole("button", { name: "取消", exact: true })).toBeDisabled();
  await page.keyboard.press("Escape");
  await expect(confirmation).toBeVisible();
  release();
  // 數量在標題下方的說明行；刪除後焦點回到頁面標題。
  await expect(page.locator(".library-subtitle b")).toHaveText("已保存 0 個卡組");
  await expect(page.getByRole("heading", { level: 1, name: "我的概念卡" })).toBeFocused();
  await expect(card).toHaveCount(0);
  await expect(page.getByRole("region", { name: "空卡組引導" })).toBeVisible();
  expect(deletes).toBe(2);
  expect(fixture.forbiddenMutations).toEqual([]);
});

test("a late list read cannot restore a deleted card while another card is renamed", async ({ page }) => {
  const fixture = await mockCards(page, { saved: true });
  const otherId = "88888888-8888-4888-8888-888888888888";
  let items = [fixture.summary(), { ...fixture.summary(), card_set_id: otherId, name: "另一個卡組" }];
  let reads = 0;
  let releaseRead!: () => void;
  let releaseDelete!: () => void;
  const pendingRead = new Promise<void>(resolve => { releaseRead = resolve; });
  const pendingDelete = new Promise<void>(resolve => { releaseDelete = resolve; });
  await page.route("**/v1/card-sets", async route => {
    reads++;
    const snapshot = structuredClone(items);
    if (reads === 2) await pendingRead;
    return json(route, { schema: "card-set-list/v1", card_sets: snapshot });
  });
  await page.route(`**/v1/card-sets/${cardSetId}`, async route => {
    if (route.request().method() !== "DELETE") return route.fallback();
    await pendingDelete;
    items = items.filter(item => item.card_set_id !== cardSetId);
    return route.fallback();
  });
  await page.route(`**/v1/card-sets/${otherId}/update`, route => {
    const body = route.request().postDataJSON();
    items = items.map(item => item.card_set_id === otherId ? { ...item, name: body.name, version: item.version + 1 } : item);
    return json(route, items.find(item => item.card_set_id === otherId));
  });
  await page.goto("/concept-cards");
  const target = page.getByRole("article", { name: "資料結構 · 核心概念", exact: true });
  await target.getByRole("button", { name: "管理卡組「資料結構 · 核心概念」" }).click();
  await target.getByRole("button", { name: "刪除卡組「資料結構 · 核心概念」" }).click();
  await target.getByRole("button", { name: "確認刪除卡組" }).click();
  const other = page.getByRole("article", { name: "另一個卡組", exact: true });
  await other.getByRole("button", { name: "管理卡組「另一個卡組」" }).click();
  await other.getByRole("button", { name: "重新命名", exact: true }).click();
  await other.getByLabel("卡組名稱").fill("已重新命名的卡組");
  await other.getByRole("button", { name: "儲存", exact: true }).click();
  await expect.poll(() => reads).toBe(2);
  const heading = page.getByRole("heading", { level: 1, name: "我的概念卡" });
  await heading.focus();
  releaseDelete();
  await expect(target).toHaveCount(0);
  await expect(page.getByRole("article", { name: "已重新命名的卡組", exact: true })).toBeVisible();
  await expect(heading).toBeFocused();
  const lateRead = page.waitForResponse(response => response.url().endsWith("/v1/card-sets"));
  releaseRead();
  await lateRead;
  await expect(page.locator(".library-item")).toHaveCount(1);
  await expect(target).toHaveCount(0);
  await expect(page.locator(".library-subtitle b")).toHaveText("已保存 1 個卡組");
});

test("mobile keeps review status, long content and sources usable without page overflow", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await mockCards(page, { saved: true, long: true });
  await page.goto(`/concept-cards/${cardSetId}`);
  await expect(page.getByText("教材已有新版。此卡組保留建立時的教材內容與來源。")).toBeVisible();
  await page.locator(".concept-card-details > summary").click();
  await expect(page.locator(".flashcard-content")).toContainText("x != 0");
  await expect(page.getByText("教材版本待複核", { exact: true })).toBeVisible();
  await expect(page.getByRole("region", { name: "概念重點來源", exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  const content = page.locator(".flashcard-content");
  expect(await content.evaluate((element) => element.scrollHeight > element.clientHeight)).toBe(true);
  await content.evaluate((element) => { element.scrollTop = element.scrollHeight; });
  await expect(page.getByText(/來源狀態/)).toHaveCount(0);
  await page.getByRole("button", { name: "查看第 1 頁來源" }).click();
  await expect(page.getByRole("dialog", { name: "教材來源" })).toBeInViewport();
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "下一張", exact: true }).click();
  await page.locator(".concept-card-details > summary").click();
  await page.screenshot({ path: "../.studydy-runtime/card-preview/study-mobile.png", fullPage: true });
});

test("account collection can select a material; failed save retries the same intent", async ({ page }) => {
  const fixture = await mockCards(page);
  await page.goto("/concept-cards");
  await page.getByRole("button", { name: "建立卡組", exact: true }).click();
  await expect(page).toHaveURL(/\/concept-cards\/new$/);
  await page.getByRole("group", { name: "選擇教材" }).getByRole("button", { name: /資料結構講義\.pdf/ }).click();
  await expect(page.getByRole("heading", { name: "建立概念卡組" })).toBeVisible();
  const keys: string[] = [];
  let fail = true;
  await page.route(`**/v1/materials/${materialId}/card-sets`, async (route) => {
    keys.push(route.request().headers()["idempotency-key"]);
    if (fail) {
      fail = false;
      return json(route, { schema: "api-error/v1", request_id: sessionId, reason_code: "STORAGE_UNAVAILABLE", retryable: true, message: "Request could not be completed." }, 503);
    }
    return route.fallback();
  });
  await page.getByRole("button", { name: "保存並開始複習" }).click();
  await expect(page.getByRole("alert")).toContainText("資料服務暫時無法使用");
  await page.getByRole("button", { name: "保存並開始複習" }).click();
  await expect(page.locator(".visual-concept-card")).toBeVisible();
  expect(keys).toHaveLength(2);
  expect(keys[0]).toBe(keys[1]);
  expect(fixture.requests).toHaveLength(1);
});

test("saved decks can be renamed and edited without replacing their identity", async ({ page }) => {
  await mockCards(page, { saved: true });
  const fullReads: string[] = [];
  page.on("request", request => {
    if (request.method() === "GET" && new URL(request.url()).pathname === `/v1/card-sets/${cardSetId}`)
      fullReads.push(request.url());
  });
  await page.goto("/concept-cards");
  await page.getByRole("button", { name: "管理卡組「資料結構 · 核心概念」" }).click();
  await expect(page.getByRole("button", { name: "重新命名", exact: true })).toHaveCount(1);
  await page.locator(".material-management-menu[open]").getByRole("button", { name: "管理卡組", exact: true }).click();
  expect(fullReads).toEqual([]);
  await page.getByLabel("卡組名稱", { exact: true }).fill("重新命名的卡組");
  await page.getByRole("button", { name: "保存變更", exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`/concept-cards/${cardSetId}$`));
  await expect(page.getByRole("heading", { name: "重新命名的卡組", exact: true })).toBeVisible();
  await openSavedCardManagement(page);
  await page.getByRole("checkbox").nth(1).uncheck();
  await page.getByRole("button", { name: "保存變更", exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`/concept-cards/${cardSetId}$`));
  await expect(page.locator(".cards-study-counter")).toContainText("1 / 1");
  await openSavedCardManagement(page);
  await expect(page.getByRole("checkbox").nth(1)).not.toBeChecked();
  await page.getByRole("button", { name: "全選", exact: true }).click();
  await page.getByRole("button", { name: "保存變更", exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`/concept-cards/${cardSetId}$`));
  await page.reload();
  await expect(page.locator(".cards-study-counter")).toContainText("1 / 2");
  await openSavedCardManagement(page);
  await page.getByLabel("卡組名稱", { exact: true }).fill("不應保存");
  await page.getByRole("button", { name: "取消變更", exact: true }).click();
  await expect(page.getByRole("heading", { name: "重新命名的卡組", exact: true })).toBeVisible();
});

test("library stays aligned and card review keeps compact content and controls", async ({ page }) => {
  await page.setViewportSize({ width: 1536, height: 960 });
  await mockCards(page, { saved: true });
  await page.goto("/materials");
  const materialCard = (await page.locator('.library-item').first().boundingBox())!;
  await page.getByRole('navigation', { name: '主要導覽' }).getByRole('link', { name: '概念卡', exact: true }).click();
  const cardTile = (await page.locator('.library-item').first().boundingBox())!;
  // 三個清單頁共用同一套欄數：卡組卡片與教材卡片左緣與寬度一致。
  expect(cardTile.x).toBeCloseTo(materialCard.x, 0);
  expect(Math.abs(cardTile.width - materialCard.width)).toBeLessThanOrEqual(1);
  // 卡組加上封面插畫後仍維持單張卡片高度上限。
  expect(cardTile.height).toBeLessThan(420);
  await expect(page.getByRole('heading', { name: '我的概念卡', exact: true, level: 1 })).toHaveCount(1);
  await expect(page.locator('.library-item').getByRole('button', { name: '管理卡組', exact: true })).toHaveCount(0);
  await page.screenshot({ path: "../.studydy-runtime/card-preview/library-aligned.png", fullPage: true });
  let release!: () => void;
  const held = new Promise<void>(resolve => { release = resolve; });
  await page.route(`**/v1/card-sets/${cardSetId}`, async route => { await held; await route.fallback(); });
  await page.getByRole("button", { name: "開始複習", exact: true }).click();
  const back = page.getByRole('button', {name:'返回卡組', exact:true});
  await expect(back).toBeVisible();
  const loadingBack = (await back.boundingBox())!;
  const tabs = (await page.locator('.material-learning-toolbar').boundingBox())!;
  expect(loadingBack.x).toBeCloseTo(tabs.x, 0);
  release();
  await expect(page.locator(".visual-concept-card")).toBeVisible();
  expect((await back.boundingBox())!.x).toBeCloseTo(loadingBack.x, 0);
  expect((await back.boundingBox())!.y).toBeCloseTo(loadingBack.y, 0);
  const content = await page.locator(".cards-study").boundingBox();
  const card = await page.locator(".visual-concept-card").boundingBox();
  expect(card!.width / content!.width).toBeLessThan(.85);
  await expect(page.getByRole("navigation", {name:"選擇概念卡"})).toBeVisible();
  expect(card!.width/card!.height).toBeCloseTo(1200/Number(await page.locator(".visual-concept-card").getAttribute("height")),1);
  expect(card!.width).toBeGreaterThan(600);
  expect(card!.width).toBeLessThanOrEqual(760);
  await expect(page.locator(".visual-concept-card")).toBeVisible();
  expect(card!.x).toBeGreaterThan((await page.locator(".cards-browse-list").boundingBox())!.x);
  const controls = page.locator('.cards-study-toolbar');
  await expect(controls).toContainText('1 / 2');
  await expect(controls.getByRole('button', {name:'洗牌重看'})).toBeVisible();
  for(const button of await controls.getByRole('button').all()) {
    const box = (await button.boundingBox())!;
    expect(box.width).toBeLessThan(150);
    expect(box.height).toBeGreaterThanOrEqual(40);
    await button.scrollIntoViewIfNeeded();
    await expect(button).toBeVisible();
  }
  await page.setViewportSize({width:390,height:844});
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(390);
  for(const button of await controls.getByRole('button').all()) {
    const box = (await button.boundingBox())!;
    expect(box.height).toBeGreaterThanOrEqual(40);
    await button.scrollIntoViewIfNeeded();
    await expect(button).toBeVisible();
  }
  await page.setViewportSize({width:1536,height:960});
  await page.screenshot({ path: "../.studydy-runtime/card-preview/study-wide.png", fullPage: true });
});

test("desktop code scroll preserves keyboard control and formula literals", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  const { view } = await mockCards(page, { saved: true });
  const claim = view.concepts[0].claims[0];
  claim.text = "def compare(values):\n    return " + "values[index] != 0 and ".repeat(30) + "values[-1] <= 100\n# 最後一行不可遺漏";
  claim.evidence[0].kind = "code";
  claim.evidence[0].quote = claim.text;
  const formula = "f(x) = (x² − 1)/(x − 1), x ≠ 1\nΣᵢ₌₁ⁿ i = n(n + 1)/2；n ∈ ℕ，n ≥ 1\nP(A | B) = P(A ∩ B)/P(B)，P(B) > 0";
  view.concepts[1].claims[0].text = formula;
  await page.goto(`/concept-cards/${cardSetId}`);
  await page.locator(".concept-card-details > summary").click();
  const code = page.locator(".flashcard-point .is-code");
  expect(await code.textContent()).toBe(claim.text);
  expect(await code.evaluate((e) => e.scrollWidth > e.clientWidth)).toBe(true);
  await code.focus();
  await page.keyboard.press("ArrowRight");
  await expect.poll(() => code.evaluate((e) => e.scrollLeft)).toBeGreaterThan(0);
  await expect(page.locator(".cards-study-counter")).toContainText("1 / 2");
  await code.evaluate((e) => { e.scrollLeft = e.scrollWidth; });
  expect(await code.evaluate((e) => Math.abs(e.scrollWidth - e.clientWidth - e.scrollLeft) < 2)).toBe(true);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.getByRole("button", { name: "下一張", exact: true }).click();
  await page.locator(".concept-card-details > summary").click();
  expect(await page.locator(".flashcard-point p").textContent()).toBe(formula);
});

test("card sources keep distinct files on the same page and recover after source failure", async ({ page }) => {
  const { view } = await mockCards(page, { saved: true });
  const claim = view.concepts[0].claims[0];
  const first = claim.evidence[0];
  Object.assign(first, { source_id: materialId, source_name: "A.pdf", normalized_page: 1 });
  const second = { ...first, evidence_id: `evidence:sha256:${"9".repeat(64)}`, source_id: runId, source_name: "B.pdf", page: 2, normalized_page: 1 };
  claim.evidence.push(second);
  view.concepts[0].claims.push({ ...claim, claim_id: `claim:sha256:${"8".repeat(64)}`, evidence: [first] });
  const secondArtifact = "66666666-6666-4666-8666-666666666666";
  const reads: string[] = [];
  let failed = true;
  await page.route("**/evidence/*/source", (route) => {
    reads.push(decodeURIComponent(new URL(route.request().url()).pathname));
    const evidence = reads.at(-1)!.includes(second.evidence_id) ? second : first;
    if (evidence === second && failed) return json(route, { schema: "api-error/v1", request_id: sessionId, reason_code: "RESOURCE_NOT_FOUND", retryable: false, message: "Synthetic missing source" }, 404);
    const artifact = evidence === first ? artifactId : secondArtifact;
    return json(route, { schema: "evidence-source/v1", format: "pdf", original_name: evidence === first ? "A.pdf" : "B.pdf", original_url: `/v1/artifacts/${artifact}/download`, preview_url: `/v1/artifacts/${artifact}#page=1`, normalized_page: 1, accuracy: "exact", origin_locators: [], label: "PDF 第 1 頁" });
  });
  await page.goto(`/concept-cards/${cardSetId}`);
  await page.locator(".concept-card-details > summary").click();
  const links = page.getByRole("region", { name: "概念重點來源", exact: true }).getByRole("button");
  await expect(links).toHaveCount(2);
  const positions = await links.evaluateAll(elements => elements.map(element => element.getBoundingClientRect().y));
  expect(Math.abs(positions[0] - positions[1])).toBeLessThanOrEqual(1);
  await page.getByRole("button", { name: "A.pdf · 第 1 頁", exact: true }).click();
  await expect(page.getByRole("link", { name: "開啟 PDF 來源頁" })).toHaveAttribute("href", `/v1/artifacts/${artifactId}#page=1`);
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "B.pdf · 第 1 頁", exact: true }).click();
  await expect(page.getByRole("alert")).toBeVisible();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  failed = false;
  await page.getByRole("button", { name: "B.pdf · 第 1 頁", exact: true }).click();
  await expect(page.getByRole("link", { name: "開啟 PDF 來源頁" })).toHaveAttribute("href", `/v1/artifacts/${secondArtifact}#page=1`);
  expect(reads).toEqual([first, second, second].map((e) => `${decodeURIComponent(view.source_resolver)}/${e.evidence_id}/source`));
});

test("late card save and source read do not reopen a page after navigation", async ({ page }) => {
  await mockCards(page);
  let releaseSave!: () => void;
  const saveGate = new Promise<void>((resolve) => { releaseSave = resolve; });
  await page.route(`**/v1/materials/${materialId}/card-sets`, async (route) => { await saveGate; await route.fallback(); });
  await page.goto(mapPath + "/create-cards");
  const saving = page.waitForRequest((r) => r.url().endsWith("/card-sets") && r.method() === "POST");
  await page.getByRole("button", { name: "保存並開始複習" }).click();
  await saving;
  // 教材內沒有全站導覽；用教材頁首的返回鍵離開。
  await page.getByRole("button", { name: "回到我的教材", exact: true }).click();
  const saved = page.waitForResponse((r) => r.url().endsWith("/card-sets") && r.request().method() === "POST");
  releaseSave();
  await saved;
  await expect(page).toHaveURL(/\/materials$/);
  await page.getByLabel("資料結構講義.pdf", { exact: true }).getByRole("link", { name: "概念卡", exact: true }).click();
  await page.getByRole("button", { name: "開始複習", exact: true }).click();
  await page.locator(".concept-card-details > summary").click();
  let releaseSource!: () => void;
  const sourceGate = new Promise<void>((resolve) => { releaseSource = resolve; });
  await page.route("**/evidence/*/source", async (route) => { await sourceGate; await route.fallback(); });
  const reading = page.waitForRequest("**/evidence/*/source");
  await page.getByRole("button", { name: "查看第 1 頁來源" }).click();
  await reading;
  await page.getByRole('tablist', { name: '教材學習內容' }).getByRole('tab', { name: '概念卡', exact: true }).click();
  const sourceRead = page.waitForResponse("**/evidence/*/source");
  releaseSource();
  await sourceRead;
  await expect(page).toHaveURL(new RegExp(`/materials/${materialId}/concept-cards$`));
  await expect(page.locator('.library-grid')).toBeVisible();
  await expect(page.getByRole("dialog")).toHaveCount(0);
});

test("switching account while a card read is pending hides old content and rejects its URL", async ({ page }) => {
  await mockCards(page, { saved: true });
  let release!: () => void;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  await page.route(`**/v1/card-sets/${cardSetId}/study`, async (route) => { await gate; await route.fallback(); });
  const pending = page.waitForRequest(`**/v1/card-sets/${cardSetId}/study`);
  await page.goto(`/concept-cards/${cardSetId}`);
  await pending;
  await page.getByRole("button", { name: "登出", exact: true }).click();
  await expect(page.getByRole("heading", { name: "登入您的帳戶" })).toBeVisible();
  release();
  // 第二帳號只得到空卡組列表，舊卡組仍由 API 拒絕。
  await page.route(`**/v1/card-sets/${cardSetId}/study`, (route) => json(route, { schema: "api-error/v1", request_id: sessionId, reason_code: "RESOURCE_NOT_FOUND", retryable: false, message: "Request could not be completed." }, 404));
  const stranger = { schema: "learner-identity/v1", learner_id: "77777777-7777-4777-8777-777777777777" };
  await page.route("**/v1/session/login", (route) => json(route, stranger));
  await page.route("**/v1/session/refresh", (route) => json(route, stranger));
  await page.route("**/v1/card-sets", (route) => json(route, { schema: "card-set-list/v1", card_sets: [] }));
  await page.getByLabel("Email", { exact: true }).fill("second@example.test");
  await page.getByLabel("密碼", { exact: true }).fill("Synthetic password 42");
  await page.getByRole("button", { name: "登入", exact: true }).click();
  await page.getByRole("navigation", { name: "主要導覽" }).getByRole("link", { name: "概念卡", exact: true }).click();
  await expect(page.getByRole("region", { name: "空卡組引導" })).toBeVisible();
  await page.goto(`/concept-cards/${cardSetId}`);
  // 別人的卡組一律顯示「找不到」，不提供永遠不會成功的重新讀取。
  await expect(page.getByRole("heading", { name: "找不到這個卡組" })).toBeVisible();
  await expect(page.locator(".flashcard")).toHaveCount(0);
});

test("account change from another tab removes an open card source dialog", async ({ page }) => {
  await mockCards(page, { saved: true });
  await page.goto(`/concept-cards/${cardSetId}`);
  await page.locator(".concept-card-details > summary").click();
  await page.getByRole("button", { name: "查看第 1 頁來源" }).click();
  await expect(page.getByRole("dialog", { name: "教材來源" })).toBeVisible();
  // 另一個 channel instance 模擬其他分頁送出的正式帳號變更事件。
  await page.evaluate(() => {
    const channel = new BroadcastChannel("studydy-account");
    channel.postMessage("identity-changed");
    channel.close();
  });
  await expect(page.getByRole("heading", { name: "登入您的帳戶" })).toBeVisible();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(page.locator(".flashcard")).toHaveCount(0);
});


test("card library renames inline like materials without changing selected cards", async ({ page }) => {
  await mockCards(page, { saved: true });
  await page.goto('/concept-cards');
  await page.getByRole('button', { name: '管理卡組「資料結構 · 核心概念」' }).click();
  await page.getByRole('button', { name: '重新命名', exact: true }).click();
  const form = page.getByRole('form', { name: '重新命名卡組' });
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(form.getByLabel('卡組名稱')).toBeFocused();
  await form.getByLabel('卡組名稱').fill('暫不保存');
  await page.keyboard.press('Escape');
  await expect(page.getByRole('heading', { name: '資料結構 · 核心概念' })).toBeVisible();
  await page.getByRole('button', { name: '管理卡組「資料結構 · 核心概念」' }).click();
  await page.getByRole('button', { name: '重新命名', exact: true }).click();
  await form.getByLabel('卡組名稱').fill('我的新卡組名稱');
  const update = page.waitForRequest(r => r.url().endsWith(`/card-sets/${cardSetId}/update`));
  await form.getByRole('button', { name: '儲存', exact: true }).click();
  expect((await update).postDataJSON()).not.toHaveProperty('concept_ids');
  await expect(page.getByRole('heading', { name: '我的新卡組名稱' })).toBeVisible();
  await expect(page.getByText(/2 張卡片/)).toBeVisible();
});


test("saved card views and editing stay inside the material workspace from either entry", async ({ page }) => {
  await mockCards(page, { saved: true });
  await page.goto('/concept-cards');
  await page.getByRole('button', { name: '開始複習', exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`/materials/${materialId}/concept-cards/${cardSetId}$`));
  const tabs = page.getByRole('tablist', { name: '教材學習內容' });
  await expect(tabs).toBeVisible();
  await expect(tabs.getByRole('tab', { name: '概念卡', exact: true })).toHaveAttribute('aria-selected', 'true');
  await openSavedCardManagement(page);
  await expect(page).toHaveURL(new RegExp(`/materials/${materialId}/concept-cards/${cardSetId}/edit$`));
  await expect(tabs).toBeVisible();
  await page.getByRole('button', { name: '取消變更', exact: true }).click();
  await expect(tabs).toBeVisible();
  await page.getByRole('tablist', { name: '教材學習內容' }).getByRole('tab', { name: '概念卡', exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`/materials/${materialId}/concept-cards$`));
  await expect(tabs).toBeVisible();
  // 從全站概念卡列表開啟管理卡組時保留返回該列表。
  await page.goto('/concept-cards');
  await page.getByRole('button', { name: '管理卡組「資料結構 · 核心概念」' }).click();
  await page.locator('.material-management-menu[open]').getByRole('button', { name: '管理卡組', exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`/materials/${materialId}/concept-cards/${cardSetId}/edit$`));
  await page.getByRole('button', { name: '返回我的概念卡', exact: true }).click();
  await expect(page).toHaveURL(/\/concept-cards$/);
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(390);
});


test('saved material card review keeps the chat entry in the material tab row',async({page})=>{
 await mockCards(page,{saved:true});
 for(const width of [1920,1440,390]){
  await page.setViewportSize({width,height:900});await page.goto(`/materials/${materialId}/concept-cards/${cardSetId}`);
  await expect(page.locator('.visual-concept-card')).toBeVisible();
  const chat=page.getByRole('button',{name:'教材問答',exact:true});await expect(chat).toBeVisible();const box=(await chat.boundingBox())!;
  // 問答入口在教材頁首右端，緊鄰頭像選單。
  const toolbar=(await page.locator('.material-learning-toolbar').boundingBox())!;expect(box.y).toBeGreaterThanOrEqual(toolbar.y);expect(box.y+box.height).toBeLessThanOrEqual(toolbar.y+toolbar.height);
  const account=(await page.locator('.material-learning-toolbar .account-menu').boundingBox())!;expect(account.x).toBeGreaterThanOrEqual(box.x+box.width);expect(account.x-box.x-box.width).toBeLessThanOrEqual(12);expect(toolbar.x+toolbar.width-account.x-account.width).toBeLessThanOrEqual(12);
 }
});

for (const sample of ["general", "prerequisite", "contrast", "part_of", "application", "example", "process-fallback", "code", "formula", "unresolved-evidence"] as const) {
  test(`visual concept card: ${sample} has grounded, portable desktop/mobile artwork`, async ({ page }) => {
    const { view } = await mockCards(page, { saved: true });
    const [source, target] = view.concepts;
    const relation = view.relations[0];
    source.label = "堆疊（Stack）";
    source.claims[0].text = "堆疊遵循後進先出（LIFO）：最後放入的元素最先被取出。";
    if (sample === "prerequisite") {
      source.label = "索引"; target.label = "陣列存取";
      source.claims[0].text = "索引用來指定陣列中的元素位置。";
      relation.learner_reason = "使用索引存取陣列元素前，需要理解索引所代表的位置。";
    } else if (sample === "contrast") {
      relation.type = "contrast"; target.label = "佇列（Queue）";
      target.claims[0].text = "佇列遵循先進先出（FIFO）。";
      relation.learner_reason = "堆疊後進先出；佇列先進先出。教材對照的是取出順序。";
    } else if (sample === "part_of") {
      relation.type = "part_of"; source.label = "節點"; target.label = "鏈結串列";
      source.claims[0].text = "鏈結串列由節點組成；各節點保存資料與指向下一節點的連結。";
      relation.learner_reason = "節點是鏈結串列的組成部分。";
    } else if (sample === "application" || sample === "example") {
      relation.type = sample; target.label = sample === "application" ? "復原操作" : "書本堆疊";
      relation.learner_reason = sample === "application" ? "復原操作以堆疊保存動作，先復原最近的動作。" : "書本堆疊是後進先出的具體實例。";
    } else if (sample === "code" || sample === "formula") {
      view.relations = [];
      source.label = sample === "code" ? "陣列索引" : "等差級數";
      source.claims[0].evidence[0].kind = sample;
      source.claims[0].text = sample === "code" ? "const values = [2, 4, 6];\nvalues[0] === 2;\nvalues[2] === 6;" : "Σᵢ₌₁ⁿ i = n(n + 1)/2\nn ∈ ℕ，n ≥ 1";
    } else if (sample === "unresolved-evidence") {
      relation.evidence_refs = [`evidence:sha256:${"0".repeat(64)}`];
    } else {
      view.relations = [];
      if (sample === "process-fallback") {
        source.label = "資料處理流程";
        source.claims[0].text = "教材提及資料處理流程，但沒有提供各步驟的先後依據。";
        view.status.quality = "needs_review"; view.status.decision = "review";
      }
    }
    source.claims[0].evidence[0].quote = source.claims[0].text;
    const expected = ["general", "process-fallback", "unresolved-evidence"].includes(sample) ? "structure" : sample;
    await page.emulateMedia({ reducedMotion: "reduce" });
    for (const width of [1280, 390]) {
      await page.setViewportSize({ width, height: 900 });
      await page.goto(`/materials/${materialId}/concept-cards/${cardSetId}`);
      const image = page.locator("canvas.visual-concept-card");
      await expect(image).toBeVisible();
      await expect(page.locator(".visual-card svg")).toHaveCount(0);
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(width);
      const text = await drawnText(page);
      if (sample === "prerequisite") expect(text).toContain("學習先備");
      if (sample === "contrast") expect(text).toContain("對照：");
      if (!["structure", "code", "formula"].includes(expected)) expect(text).toContain(target.label);
      expect(await image.evaluate((canvas: HTMLCanvasElement) => [canvas.width, canvas.height])).toEqual([1200, 760]);
      expect(await page.evaluate(() => window.cardEncodingCount)).toBe(0);
      await expectCardTextFits(page);
      // 各圖型以 DOM／來源斷言驗證，不持續累積可重建全頁快照。
      await page.locator(".concept-card-details > summary").click();
      await expect(page.locator(".flashcard-point p").first()).toHaveText(source.claims[0].text);
      if (!["structure", "code", "formula"].includes(expected)) {
        await expect(page.getByRole("region", { name: "圖卡關係與來源" }).first()).toContainText(relation.learner_reason);
        const request = page.waitForRequest("**/evidence/*/source");
        await page.getByRole("button", { name: "關係來源 · 第 1 頁", exact: true }).click();
        expect(decodeURIComponent((await request).url())).toContain(relation.evidence_refs[0]);
        await expect(page.getByRole("dialog")).toBeVisible();
        await page.keyboard.press("Escape");
      }
    }
  });
}

test("visual structure read retries the retained revision and ignores completion after navigation", async ({ page }) => {
  await mockCards(page, { saved: true, long: true });
  let failed = true;
  const reads: string[] = [];
  await page.route(`**/v1/card-sets/${cardSetId}/study`, route => {
    reads.push(decodeURIComponent(new URL(route.request().url()).pathname));
    if (failed) return json(route, { schema: "api-error/v1", request_id: sessionId, reason_code: "STORAGE_UNAVAILABLE", retryable: true, message: "Synthetic unavailable structure" }, 503);
    return route.fallback();
  });
  await page.goto(`/materials/${materialId}/concept-cards/${cardSetId}`);
  await expect(page.getByRole("heading", { name: "無法開啟卡組" })).toBeVisible();
  await expect(page.locator(".flashcard")).toHaveCount(0);
  failed = false;
  await page.getByRole("button", { name: "重新讀取", exact: true }).click();
  await expect(page.locator(".visual-concept-card")).toBeVisible();
  expect(reads.every(url => url.endsWith("/study"))).toBe(true);
  let release!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  await page.route(`**/v1/card-sets/${cardSetId}/study`, async route => { await gate; await route.fallback(); });
  const pending = page.waitForRequest(`**/v1/card-sets/${cardSetId}/study`);
  await page.reload(); await pending;
  await page.getByRole('tablist', { name: '教材學習內容' }).getByRole('tab', { name: '概念卡', exact: true }).click();
  const response = page.waitForResponse(`**/v1/card-sets/${cardSetId}/study`);
  release(); await response;
  await expect(page.locator(".flashcard")).toHaveCount(0);
  await expect(page.locator(".library-item")).toHaveCount(1);
});

test("pending visual structure cannot restore a previous account's card", async ({ page }) => {
  const { view } = await mockCards(page, { saved: true, many: true });
  let release!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  await page.route(`**/v1/card-sets/${cardSetId}/cards?offset=24&*`, async route => { await gate; await route.fallback(); });
  await page.goto(`/materials/${materialId}/concept-cards/${cardSetId}`);
  await expect(page.locator('canvas.visual-concept-card')).toBeVisible();
  const pending = page.waitForRequest(`**/v1/card-sets/${cardSetId}/cards?offset=24&*`);
  await page.getByRole('navigation', { name: '選擇概念卡' }).getByRole('button', { name: view.concepts[30].label, exact: true }).click();
  await pending;
  const aborted = page.waitForEvent("requestfailed", request => request.url().includes("/cards?offset=24"));
  // 教材內從頭像選單登出。
  await page.getByRole("button", { name: /^帳號選單/ }).click();
  await page.getByRole("button", { name: "登出", exact: true }).click();
  await expect(page.getByRole("heading", { name: "登入您的帳戶" })).toBeVisible();
  await aborted; release();
  await expect(page.locator(".flashcard")).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "登入您的帳戶" })).toBeVisible();
});

test("visual labels remain inert text and long excerpts keep full conditions in details", async ({ page }) => {
  const { view } = await mockCards(page, { saved: true, long: true });
  view.concepts[0].label = '<script>alert("x")</script> & <image href="https://invalid.test" />';
  await page.goto(`/materials/${materialId}/concept-cards/${cardSetId}`);
  expect(await drawnText(page)).toContain("保留所有必要條件與符號。");
  await expect(page.locator(".visual-card script, .visual-card svg")).toHaveCount(0);
  await expect(page.locator(".visual-concept-card")).toHaveAttribute("title", view.concepts[0].label + `｜概念卡 · 1/${await page.locator(".visual-concept-card").getAttribute("data-image-count")}`);
  await page.locator(".concept-card-details > summary").click();
  await expect(page.locator(".flashcard-point p").first()).toContainText("最後一個條件不可遺漏：x != 0。");
});

test("whole PNG card exports grounded content and preserves reading navigation", async ({ page }) => {
  const { view } = await mockCards(page, { saved: true, long: true });
  const first = view.concepts[0];
  first.claims.push(...Array.from({ length: 4 }, (_, i) => ({ ...structuredClone(first.claims[0]), claim_id: `claim:sha256:${(i + 7).toString(16).repeat(64)}`, text: `重點 ${i + 1}：` + "長英文 ABCD 與中文條件。".repeat(20) })));
  first.claims[0].evidence[0].source_name = "資料結構講義.pdf";
  first.claims[0].evidence[0].normalized_page = 7;
  await page.goto(`/concept-cards/${cardSetId}`);
  const image = page.locator("canvas.visual-concept-card");
  await expect(image).toBeVisible();
  expect(await drawnText(page)).not.toMatch(/STUDYDY|教材版本待複核|概念關係/);
  await expectCardTextFits(page);
  const downloaded = page.waitForEvent("download");
  await page.getByRole("button", { name: "下載 PNG" }).click();
  const download = await downloaded;
  expect(download.suggestedFilename()).toBe("堆疊（Stack）-1.png");
  const stream = (await download.createReadStream())!;
  const chunks = [];
  for await (const chunk of stream) chunks.push(chunk);
  const png = Buffer.concat(chunks);
  expect([...png.subarray(0, 8)]).toEqual([137, 80, 78, 71, 13, 10, 26, 10]);
  expect(await page.evaluate(async base64 => {
    const image = new Image();
    image.src = `data:image/png;base64,${base64}`;
    await image.decode();
    return [image.naturalWidth, image.naturalHeight];
  }, png.toString("base64"))).toEqual([1200, 760]);
  await page.locator(".concept-card-details > summary").click();
  await expect(page.locator(".flashcard-content")).toContainText("最後一個條件不可遺漏：x != 0。");
  await page.getByRole("button", { name: "資料結構講義.pdf · 第 7 頁", exact: true }).click();
  await expect(page.getByRole("dialog", { name: "教材來源" })).toBeVisible();
  await page.keyboard.press("Escape");
  const imageCount = Number(await image.getAttribute("data-image-count"));
  for (let i = 0; i < imageCount; i++) await page.getByRole("button", { name: "下一張", exact: true }).click();
  await expect(image).toHaveAttribute("aria-label", "陣列（Array）視覺化概念卡");
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(390);
  await page.reload();
  await expect(page.locator("canvas.visual-concept-card")).toBeVisible();
  await expect(page.locator(".visual-concept-card")).toHaveAttribute("title", `堆疊（Stack）｜概念卡 · 1/${await image.getAttribute("data-image-count")}`);
});

test("sparse PNG card keeps review status without inventing points", async ({ page }) => {
  const { view } = await mockCards(page, { saved: true });
  view.status.quality = "needs_review";
  view.relations = [];
  await page.goto(`/concept-cards/${cardSetId}`);
  expect(await drawnText(page)).toContain("堆疊遵循後進先出");
  expect(await drawnText(page)).not.toContain("教材版本待複核");
  await page.locator(".concept-card-details > summary").click();
  await expect(page.locator(".flashcard-review")).toHaveText("教材版本待複核");
});

for (const sample of visualCases) {
  test(`v2 layout ${sample}: local text regions, semantic fidelity and mobile display`, async ({ page }) => {
    const { view } = await mockCards(page, { saved: true });
    Object.assign(view, visualCase(sample));
    await page.setViewportSize({ width: 1536, height: 960 });
    await page.goto(`/materials/${materialId}/concept-cards/${cardSetId}`);
    const image = page.locator("canvas.visual-concept-card");
    const family = ['code', 'formula'].includes(sample) ? 'technical' : sample === 'long' ? 'text' : ['three', 'four', 'many'].includes(sample) ? 'points' : 'sparse';
    await expect(image).toHaveAttribute('data-layout', family);
    await expectCardTextFits(page);
    if (sample === 'two') expect((await cardDrawing(page)).iconStrokes).toBeGreaterThan(0);
    if (sample === 'many') {
      await expect(image).toHaveAttribute('data-image-count', '2');
      await page.getByRole('button', { name: '下一張', exact: true }).click();
      await expect(image).toHaveAttribute('data-image-index', '2');
      for (const claim of view.concepts[0].claims.slice(4)) expect(await drawnText(page)).toContain(claim.text);
    }
    if (sample === 'code' || sample === 'formula') {
      const positions = (await cardDrawing(page)).texts.filter(line => line.font.includes('monospace'));
      expect(positions.map(line => line.text)).toEqual(view.concepts[0].claims[0].text.split('\n'));
      const step = positions[1].y - positions[0].y;
      positions.slice(1).forEach((line, i) => expect(line.y - positions[i].y).toBeCloseTo(step, 2));
    }
    const counterBeforeResize = await page.locator('.cards-study-counter').innerText();
    await page.setViewportSize({ width: 390, height: 844 });
    await expect(page.locator('.cards-study-counter')).toHaveText(counterBeforeResize);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(390);
    await page.locator(".concept-card-details > summary").click();
    expect(await page.locator('.flashcard-point p').allTextContents()).toEqual(view.concepts[0].claims.map(c => c.text));
    if (sample === 'missing-relation') await expect(page.getByRole('region', { name: '待複核的教材關係' })).toContainText(view.relations[0].learner_reason);
  });
}

test('v2 relation ranking keeps all original reasons and sources in details', async ({ page }) => {
  const { view } = await mockCards(page, { saved: true });
  const original = view.relations[0];
  view.relations.push({ ...original, relation_id: `relation:sha256:${'9'.repeat(64)}`, type: 'part_of', learner_reason: '合成案例：A 是 B 的組成部分。' });
  await page.goto(`/materials/${materialId}/concept-cards/${cardSetId}`);
  const text = await drawnText(page);
  expect(text).toContain("整體");
  expect(text).toContain("組成部分");
  expect(text).toContain(view.concepts[0].label);
  expect(text).toContain(view.concepts[1].label);
  await page.locator(".concept-card-details > summary").click();
  const relations = page.getByRole('region', { name: '圖卡關係與來源' });
  await expect(relations).toHaveCount(2);
  await expect(relations.nth(0)).toContainText(view.relations[1].learner_reason);
  await expect(relations.nth(1)).toContainText(original.learner_reason);
  const request = page.waitForRequest('**/evidence/*/source');
  await relations.nth(1).getByRole('button').first().click();
  expect(decodeURIComponent((await request).url())).toContain(original.evidence_refs[0]);
});

test("PNG encoding preserves the pixels drawn on Canvas", async ({ page }) => {
  const { view } = await mockCards(page, { saved: true });
  Object.assign(view, visualCase("code"));
  await page.addInitScript(() => {
    const encode = HTMLCanvasElement.prototype.toBlob;
    HTMLCanvasElement.prototype.toBlob = function (callback, type, quality) {
      const context = this.getContext("2d")!;
      const expected = context.getImageData(0, 0, this.width, this.height).data;
      encode.call(this, async blob => {
        const image = new Image(), url = URL.createObjectURL(blob!);
        image.src = url;
        await image.decode();
        context.clearRect(0, 0, this.width, this.height);
        context.drawImage(image, 0, 0);
        const actual = context.getImageData(0, 0, this.width, this.height).data;
        (window as Window & { pngPixelsMatch?: boolean }).pngPixelsMatch = expected.every((value, i) => value === actual[i]);
        URL.revokeObjectURL(url);
        callback(blob);
      }, type, quality);
    };
  });
  await page.goto(`/concept-cards/${cardSetId}`);
  await expect(page.locator("canvas.visual-concept-card")).toBeVisible();
  expect(await page.evaluate(() => window.cardEncodingCount)).toBe(0);
  const downloading = page.waitForEvent("download");
  await page.getByRole("button", { name: "下載 PNG", exact: true }).click();
  await downloading;
  expect(await page.evaluate(() => (window as Window & { pngPixelsMatch?: boolean }).pngPixelsMatch)).toBe(true);
});

test('v2 prose preserves paragraph gaps and avoids orphan Chinese punctuation', async ({ page }) => {
  const { view } = await mockCards(page, { saved: true });
  view.relations = [];
  view.concepts[0].label = '段落閱讀';
  view.concepts[0].claims[0].text = '這是第一段。\n\n第二段保留空白分隔。';
  view.concepts[0].claims[0].evidence[0].quote = view.concepts[0].claims[0].text;
  await page.goto(`/materials/${materialId}/concept-cards/${cardSetId}`);
  const lines = (await cardDrawing(page)).texts.filter(line => line.y > 175 && line.text !== "原文節錄");
  expect(lines.map(l => l.text)).toEqual(['這是第一段。', '', '第二段保留空白分隔。']);
  expect(lines[1].y - lines[0].y).toBeGreaterThan(0);
  expect(lines[2].y - lines[0].y).toBeCloseTo(2 * (lines[1].y - lines[0].y), 2);
});


test("catalog icons are compact, embedded in downloads and keep sources in details", async ({ page }) => {
  const { view } = await mockCards(page, { saved: true });
  view.relations = [];
  view.concepts[0].label = "印表機";
  view.concepts[0].claims[0].text = "印表機把文件輸出到紙張。";
  view.concepts[0].claims[0].evidence[0].quote = view.concepts[0].claims[0].text;
  await page.setViewportSize({ width: 1536, height: 960 });
  await page.goto(`/materials/${materialId}/concept-cards/${cardSetId}`);
  const image = page.locator("canvas.visual-concept-card");
  await expect(image).toBeVisible();
  expect((await image.boundingBox())!.width).toBeLessThanOrEqual(760);
  expect((await image.boundingBox())!.height).toBeLessThanOrEqual(482);
  expect((await cardDrawing(page)).iconStrokes).toBeGreaterThan(0);
  expect(await drawnText(page)).not.toMatch(/STUDYDY|圖解筆記|教材版本待複核/);
  await expect(page.locator(".visual-card svg")).toHaveCount(0);
  await page.locator(".concept-card-details > summary").click();
  await expect(page.locator('.flashcard-point p').first()).toHaveText(view.concepts[0].claims[0].text);
  await expect(page.getByRole('region', { name: '概念重點來源' })).toBeVisible();
});


test("an unavailable icon bundle leaves the original card content accessible", async ({ page }) => {
  await mockCards(page, { saved: true });
  await page.route('**/assets/ConceptCardArtwork-*.js', route => route.abort());
  await page.goto(`/materials/${materialId}/concept-cards/${cardSetId}`);
  await expect(page.getByRole('status')).toContainText('圖卡載入失敗');
  await expect(page.getByRole('button', { name: '下載 PNG' })).toBeDisabled();
  await page.locator(".concept-card-details > summary").click();
  await expect(page.locator('.flashcard-point p').first()).toContainText('堆疊');
});


test("relation artwork shows three source claims beside the diagram without an omitted notice", async ({ page }) => {
  const { view } = await mockCards(page, { saved: true });
  const concept = view.concepts[0];
  concept.claims.push(...[7,8].map(n => ({ ...structuredClone(concept.claims[0]), claim_id: `claim:sha256:${String(n).repeat(64)}`, text: `教材重點 ${n}：保留原文的條件與來源。` })));
  await page.goto(`/materials/${materialId}/concept-cards/${cardSetId}`);
  await expect(page.locator("canvas.visual-concept-card")).toHaveAttribute('data-layout', 'relation');
  const text = await drawnText(page);
  for (const claim of concept.claims) expect(text).toContain(claim.text);
  expect(text).not.toContain("另有");
  await expectCardTextFits(page);
});


test("short and detailed cards keep a fixed canvas and navigation position", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1050 });
  const { view } = await mockCards(page, { saved: true });
  const relations = view.relations;
  const concept = view.concepts[0]; concept.label = "概念";
  concept.claims = [concept.claims[0]];
  concept.claims[0].text = "這是一則簡短的教材重點。";
  concept.claims[0].evidence[0].quote = concept.claims[0].text;
  view.relations = [];
  await page.goto(`/materials/${materialId}/concept-cards/${cardSetId}`);
  const image = page.locator("canvas.visual-concept-card");
  await expect(page.locator("canvas.visual-concept-card")).toBeVisible();
  expect(Number(await image.getAttribute('height'))).toBe(760);
  const before = await page.getByRole("button", { name: "下一張", exact: true }).boundingBox();
  expect(await drawnText(page)).not.toContain("原文節錄");
  expect(await drawnText(page)).toContain(concept.claims[0].text);
  view.relations = relations;
  await page.reload();
  await expect(image).toHaveAttribute('data-layout', 'relation');
  expect(Number(await image.getAttribute('height'))).toBe(760);
  const after = await page.getByRole("button", { name: "下一張", exact: true }).boundingBox();
  expect(after).toEqual(before);
  await page.getByRole("button", { name: "下一張", exact: true }).click();
  await expect(page.locator(".cards-study-counter")).toContainText("2 / 2");
  expect(await page.getByRole("button", { name: "完成本輪", exact: true }).boundingBox()).toEqual(before);
  expect(await drawnText(page)).not.toContain("原文節錄");
  expect(Number(await image.getAttribute('height'))).toBe(760);
});


for (const scenario of [
  { label: "格式示例", kind: "table", lines: ["Input: abc", "Count: 3", "Result: xyz"], check: "xyz" },
  { label: "回應分類", kind: "table", lines: ["來源中的分類", "2xx", "完成", "5xx", "需要確認原因"], check: "5xx" },
  { label: "訊息分區", kind: "fields", lines: ["訊息分區", "控制資訊  |  內容資料"], check: "內容資料" },
  { label: "交換步驟", kind: "sequence", lines: ["依來源順序交换。", "1 甲端 → 乙端", "START", "17", "2 乙端 → 甲端", "READY", "18", "不增加其他步驟。"], check: "READY" },
]) test(`source structure ${scenario.label} is preserved as a complete ${scenario.kind} graphic`, async ({ page }) => {
  const { view } = await mockCards(page, { saved: true });
  view.relations = [];
  const card = view.concepts[0], claim = card.claims[0], template = structuredClone(claim.evidence[0]);
  card.label = scenario.label;
  claim.text = scenario.lines.join(" ");
  claim.evidence = scenario.lines.map((quote, index) => ({ ...structuredClone(template), evidence_id: `evidence:sha256:${(index + 100).toString(16).padStart(64, "0")}`, quote, kind: "paragraph" }));
  await page.goto(`/materials/${materialId}/concept-cards/${cardSetId}`);
  const image = page.locator("canvas.visual-concept-card");
  await expect(image).toHaveAttribute('data-layout', scenario.kind);
  expect(await drawnText(page)).toContain(scenario.check);
  expect(await drawnText(page)).not.toContain("原文節錄");
  await expect(image).toHaveAttribute('height', '760');
  if (scenario.kind === 'sequence') {
    const text = await drawnText(page);
    expect(text.indexOf('START')).toBeLessThan(text.indexOf('READY'));
    expect(text).toContain("甲端"); expect(text).toContain("乙端");
  }
  await expectCardTextFits(page);
  await page.locator('.concept-card-details > summary').click();
  await expect(page.locator('.flashcard-point p').first()).toContainText(scenario.lines[0]);
});

test("concept canvases share the deck navigation, counter and export", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1050 });
  const { view } = await mockCards(page, { saved: true });
  view.relations = [];
  const concept = view.concepts[0];
  concept.claims = Array.from({ length: 7 }, (_, i) => ({ ...structuredClone(concept.claims[0]), claim_id: `claim:sha256:${(i + 100).toString(16).padStart(64, "0")}`, text: `這是教材的第 ${i + 1} 則重點。` }));
  await page.goto(`/materials/${materialId}/concept-cards/${cardSetId}`);
  const image = page.locator("canvas.visual-concept-card");
  await expect(image).toHaveAttribute('data-image-count', '2');
  expect(await drawnText(page)).not.toContain("原文節錄");
  await expect(page.locator('.cards-study-counter')).toContainText('1 / 3');
  const bounds = await page.locator("canvas.visual-concept-card").boundingBox();
  await page.getByRole('button', { name: '下一張', exact: true }).click();
  await expect(image).toHaveAttribute('data-image-index', '2');
  expect(await page.locator("canvas.visual-concept-card").boundingBox()).toEqual(bounds);
  expect(await drawnText(page)).toContain(concept.claims[6].text);
  expect(await drawnText(page)).not.toContain("另有");
  const downloading = page.waitForEvent('download');
  await page.getByRole('button', { name: '下載 PNG', exact: true }).click();
  expect((await downloading).suggestedFilename()).toMatch(/-2\.png$/);
  await page.getByRole('button', { name: '下一張', exact: true }).click();
  await expect(image).toHaveAttribute('data-image-index', '1');
  await expect(page.locator('.cards-study-counter')).toContainText('3 / 3');
  await page.keyboard.press('ArrowLeft');
  await expect(image).toHaveAttribute('data-image-index', '2');
  await expect(page.locator('.cards-study-counter')).toContainText('2 / 3');
  await page.keyboard.press('ArrowRight');
  await page.getByRole('button', { name: '完成本輪', exact: true }).click();
  await expect(page.getByRole('heading', { name: '已瀏覽全部卡片' })).toBeVisible();
  await page.getByRole('button', { name: '再看一次', exact: true }).click();
  await expect(image).toHaveAttribute('data-image-index', '1');
});

test("a long paragraph continues until its final condition is visible", async ({ page }) => {
  const { view } = await mockCards(page, { saved: true, long: true });
  view.relations = [];
  const claim = view.concepts[0].claims[0];
  claim.evidence[0].quote = claim.text;
  await page.goto(`/materials/${materialId}/concept-cards/${cardSetId}`);
  const image = page.locator("canvas.visual-concept-card");
  await expect(image).toHaveAttribute('data-image-index', '1');
  const count = Number(await image.getAttribute('data-image-count'));
  expect(count).toBeGreaterThan(1);
  let text = '';
  for (let i = 0; i < count; i++) {
    expect(await drawnText(page)).not.toContain("原文節錄");
    text += (await cardDrawing(page)).texts.filter(line => line.y > 175 && !/^第 .* 張圖$/.test(line.text)).map(line => line.text).join('');
    if (i < count - 1) await page.getByRole('button', { name: '下一張', exact: true }).click();
  }
  expect(text.replace(/\s/g, '')).toBe(claim.text.replace(/\s/g, ''));
});


test("selection counts concepts while saved library and study count generated pages", async ({ page }) => {
  const { view } = await mockCards(page, { saved: true });
  view.relations = [];
  const concept = view.concepts[0];
  concept.claims = Array.from({ length: 7 }, (_, i) => ({ ...structuredClone(concept.claims[0]), claim_id: `claim:sha256:${(i + 100).toString(16).padStart(64, "0")}`, text: `第 ${i + 1} 則教材重點。` }));
  let failed = true;
  await page.route(`**/v1/card-sets/${cardSetId}/study`, route => failed
    ? json(route, { schema: "api-error/v1", request_id: sessionId, reason_code: "STORAGE_UNAVAILABLE", retryable: true, message: "Request could not be completed." }, 503)
    : route.fallback());
  await page.goto('/concept-cards');
  const metadata = page.locator('.library-item .library-metadata');
  await expect(metadata).toContainText('張數計算或保存失敗');
  await expect(metadata).not.toContainText('2 張卡片');
  failed = false;
  await metadata.getByRole('button', { name: '重試', exact: true }).click();
  await expect(metadata).toContainText('3 張卡片');
  await page.getByRole('button', { name: '開始複習', exact: true }).click();
  await expect(page.locator('.cards-study-counter')).toContainText('1 / 3');
  await openSavedCardManagement(page);
  await expect(page.locator('.cards-create-summary strong')).toHaveText('2 個觀念');
  await expect(page.locator('.cards-selection-heading')).toContainText('已選 2 / 2 個觀念');
  await page.getByRole('checkbox').first().uncheck();
  await expect(page.locator('.cards-create-summary strong')).toHaveText('1 個觀念');
  await page.getByRole('button', { name: '保存變更', exact: true }).click();
  await expect(page.locator('.cards-study-counter')).toContainText('1 / 1');
  await page.getByRole('tablist', { name: '教材學習內容' }).getByRole('tab', { name: '概念卡', exact: true }).click();
  await expect(metadata).toContainText('1 張卡片');
});

test("saved card counts load without card bodies, knowledge structures or artwork", async ({ page }) => {
  await mockCards(page, { saved: true, savedCount: 1267 });
  const expensive: string[] = [];
  page.on('request', request => {
    if (request.url().endsWith(`/v1/card-sets/${cardSetId}`) || request.url().includes('/knowledge-structures/') || request.url().includes('/assets/ConceptCardArtwork-')) expensive.push(request.url());
  });
  await page.goto('/concept-cards');
  await expect(page.locator('.library-metadata')).toContainText('1267 張卡片');
  await page.reload();
  await expect(page.locator('.library-metadata')).toContainText('1267 張卡片');
  expect(expensive).toEqual([]);
});

test("first page count survives reload and a failed save remains retryable", async ({ page }) => {
  const fixture = await mockCards(page, { saved: true, long: true });
  let failSave = true;
  await page.route(`**/v1/card-sets/${cardSetId}/count`, route => failSave
    ? json(route, { schema: 'api-error/v1', request_id: sessionId, reason_code: 'STORAGE_UNAVAILABLE', retryable: true, message: 'Request could not be completed.' }, 503)
    : route.fallback());
  await page.goto('/concept-cards');
  await expect(page.locator('.library-metadata')).toContainText('張數計算或保存失敗');
  expect(fixture.summary().card_count).toBeNull();
  failSave = false;
  await page.getByRole('button', { name: '重試', exact: true }).click();
  await expect(page.locator('.library-metadata')).toContainText(/\d+ 張卡片/);
  const count = fixture.summary().card_count;
  expect(count).toBeGreaterThan(2);
  await page.route(`**/v1/card-sets/${cardSetId}`, route => route.abort());
  await page.route('**/knowledge-structures/**', route => route.abort());
  await page.reload();
  await expect(page.locator('.library-metadata')).toContainText(`${count} 張卡片`);
});

test("PNG download failure does not hide the canvas and can be retried", async ({ page }) => {
  await mockCards(page, { saved: true });
  await page.addInitScript(() => {
    const encode = HTMLCanvasElement.prototype.toBlob;
    HTMLCanvasElement.prototype.toBlob = function (callback) {
      HTMLCanvasElement.prototype.toBlob = encode;
      callback(null);
    };
  });
  await page.goto(`/concept-cards/${cardSetId}`);
  const canvas = page.locator("canvas.visual-concept-card");
  await expect(canvas).toBeVisible();
  await expect(page.getByRole("alert")).toHaveCount(0);
  await page.getByRole("button", { name: "下載 PNG", exact: true }).click();
  await expect(page.getByRole("alert")).toHaveText("PNG 下載失敗，請再試一次。");
  await expect(canvas).toBeVisible();
  const downloaded = page.waitForEvent("download");
  await page.getByRole("button", { name: "下載 PNG", exact: true }).click();
  expect((await downloaded).suggestedFilename()).toMatch(/\.png$/);
  await expect(page.getByRole("alert")).toHaveCount(0);
});

test("canvas preview encodes only on download and has no zoom", async ({ page }) => {
  await mockCards(page, { saved: true });
  await page.goto(`/concept-cards/${cardSetId}`);
  const preview = page.locator(".visual-card > canvas.visual-concept-card");
  await expect(preview).toBeVisible();
  expect(await page.evaluate(() => window.cardEncodingCount)).toBe(0);
  const downloaded = page.waitForEvent("download");
  await page.getByRole("button", { name: "下載 PNG", exact: true }).click();
  const stream = (await (await downloaded).createReadStream())!;
  const chunks = [];
  for await (const chunk of stream) chunks.push(chunk);
  const png = Buffer.concat(chunks);
  expect([...png.subarray(0, 8)]).toEqual([137, 80, 78, 71, 13, 10, 26, 10]);
  expect(await page.evaluate(() => window.cardEncodingCount)).toBe(1);
  await page.getByRole("button", { name: "下一張", exact: true }).click();
  await expect(preview).toHaveAttribute("aria-label", "陣列（Array）視覺化概念卡");
  expect(await page.evaluate(() => window.cardEncodingCount)).toBe(1);
});

test("a late download cannot replace the next card or trigger an obsolete download", async ({ page }) => {
  await mockCards(page, { saved: true });
  await page.addInitScript(() => {
    const encode = HTMLCanvasElement.prototype.toBlob;
    HTMLCanvasElement.prototype.toBlob = function (callback, type, quality) {
      HTMLCanvasElement.prototype.toBlob = encode;
      (window as Window & { finishFirstPng?: () => void }).finishFirstPng = () => encode.call(this, callback, type, quality);
    };
  });
  await page.goto(`/concept-cards/${cardSetId}`);
  await expect(page.locator('canvas.visual-concept-card')).toBeVisible();
  const downloads: string[] = [];
  page.on('download', item => downloads.push(item.suggestedFilename()));
  await page.getByRole('button', { name: '下載 PNG', exact: true }).click();
  await expect.poll(() => page.evaluate(() => typeof (window as Window & { finishFirstPng?: () => void }).finishFirstPng)).toBe("function");
  await page.getByRole("button", { name: "下一張", exact: true }).click();
  const image = page.locator("canvas.visual-concept-card");
  await expect(image).toHaveAttribute("aria-label", "陣列（Array）視覺化概念卡");
  await page.evaluate(async () => {
    (window as Window & { finishFirstPng?: () => void }).finishFirstPng!();
    await new Promise(resolve => setTimeout(resolve, 100));
  });
  await expect(image).toHaveAttribute("aria-label", "陣列（Array）視覺化概念卡");
  expect(downloads).toEqual([]);
});

test("returning to a card and reopening the deck reuse bounded canvas memory", async ({ page }) => {
  await mockCards(page, { saved: true });
  await page.goto(`/materials/${materialId}/concept-cards/${cardSetId}`);
  const image = page.locator("canvas.visual-concept-card");
  await expect(image).toBeVisible();
  await page.getByRole("button", { name: "下一張", exact: true }).click();
  await expect(image).toHaveAttribute("aria-label", "陣列（Array）視覺化概念卡");
  await expect(image).toBeVisible();
  expect(await page.evaluate(() => window.cardEncodingCount)).toBe(0);
  await page.evaluate(() => {
    HTMLCanvasElement.prototype.toBlob = () => { throw new Error("UNEXPECTED_REENCODE"); };
    CanvasRenderingContext2D.prototype.fillText = () => { throw new Error("UNEXPECTED_REDRAW"); };
  });
  await page.getByRole("button", { name: "上一張", exact: true }).click();
  await expect(image).toHaveAttribute("aria-label", "堆疊（Stack）視覺化概念卡");
  await expect(image).toBeVisible();
  await page.getByRole('tablist', { name: '教材學習內容' }).getByRole('tab', { name: '概念卡', exact: true }).click();
  await page.getByRole('button', { name: '開始複習', exact: true }).click();
  await expect(image).toBeVisible();
  await expect(image).toHaveAttribute("aria-label", "堆疊（Stack）視覺化概念卡");
  expect(await page.evaluate(() => window.cardEncodingCount)).toBe(0);
  await expect(page.getByRole("alert")).toHaveCount(0);
});


test("large decks read only the first batches and jump directly to a distant concept", async ({ page }) => {
  const { view } = await mockCards(page, { saved: true, many: true, savedCount: 31 });
  view.relations = [];
  const reads: number[] = [];
  await page.route(`**/v1/card-sets/${cardSetId}/cards?*`, route => { reads.push(Number(new URL(route.request().url()).searchParams.get('offset'))); return route.fallback(); });
  await page.route(`**/v1/card-sets/${cardSetId}`, route => route.abort());
  await page.route('**/knowledge-structures/*', route => route.abort());
  await page.goto(`/materials/${materialId}/concept-cards/${cardSetId}`);
  await expect(page.locator('canvas.visual-concept-card')).toBeVisible();
  await expect.poll(() => reads.includes(8)).toBe(true);
  expect(reads).not.toContain(16); expect(reads).not.toContain(24);
  await page.getByRole('navigation', { name: '選擇概念卡' }).getByRole('button', { name: view.concepts[30].label, exact: true }).click();
  await expect(page.locator('canvas.visual-concept-card')).toHaveAttribute('aria-label', `${view.concepts[30].label}視覺化概念卡`);
  await expect(page.locator('canvas.visual-concept-card')).toBeVisible();
  expect(reads).toContain(24); expect(reads).not.toContain(16);
  expect(await page.evaluate(() => window.cardEncodingCount)).toBe(0);
});

test("canvas cache evicts old pages instead of keeping every image", async ({ page }) => {
  const { view } = await mockCards(page, { saved: true, many: true });
  await page.goto(`/materials/${materialId}/concept-cards/${cardSetId}`);
  const canvas = page.locator('canvas.visual-concept-card');
  await expect(canvas).toBeVisible();
  for (let index = 1; index < 11; index++) {
    await page.getByRole('button', { name: '下一張', exact: true }).click();
    await expect(canvas).toHaveAttribute('aria-label', `${view.concepts[index].label}視覺化概念卡`);
    await expect(canvas).toBeVisible();
  }
  const before = await page.evaluate(() => window.cardRenderCount);
  await page.getByRole('navigation', { name: '選擇概念卡' }).getByRole('button', { name: '堆疊（Stack）', exact: true }).click();
  await expect(canvas).toHaveAttribute('aria-label', '堆疊（Stack）視覺化概念卡');
  await expect(canvas).toBeVisible();
  expect(await page.evaluate(() => window.cardRenderCount)).toBeGreaterThan(before);
  expect(await page.evaluate(() => window.cardEncodingCount)).toBe(0);
});

test("next and previous preserve continuation pages across batch boundaries", async ({ page }) => {
  const { view } = await mockCards(page, { saved: true, many: true });
  view.relations = [];
  for (const index of [7, 23]) {
    const concept = view.concepts[index];
    concept.claims = Array.from({ length: 7 }, (_, i) => ({ ...structuredClone(concept.claims[0]),
      claim_id: `claim:sha256:${(index * 100 + i).toString(16).padStart(64, '0')}`, text: `教材第 ${i + 1} 則重點。` }));
  }
  await page.goto(`/materials/${materialId}/concept-cards/${cardSetId}`);
  const canvas = page.locator('canvas.visual-concept-card');
  const concepts = page.getByRole('navigation', { name: '選擇概念卡' });
  await concepts.getByRole('button', { name: view.concepts[7].label, exact: true }).click();
  await expect(canvas).toHaveAttribute('data-image-count', '2');
  await page.getByRole('button', { name: '下一張', exact: true }).click();
  await expect(canvas).toHaveAttribute('data-image-index', '2');
  await page.getByRole('button', { name: '下一張', exact: true }).click();
  await expect(canvas).toHaveAttribute('aria-label', `${view.concepts[8].label}視覺化概念卡`);
  await page.getByRole('button', { name: '上一張', exact: true }).click();
  await expect(canvas).toHaveAttribute('aria-label', `${view.concepts[7].label}視覺化概念卡`);
  await expect(canvas).toHaveAttribute('data-image-index', '2');
  await concepts.getByRole('button', { name: view.concepts[24].label, exact: true }).click();
  await expect(canvas).toHaveAttribute('aria-label', `${view.concepts[24].label}視覺化概念卡`);
  await page.getByRole('button', { name: '上一張', exact: true }).click();
  await expect(canvas).toHaveAttribute('aria-label', `${view.concepts[23].label}視覺化概念卡`);
  await expect(canvas).toHaveAttribute('data-image-index', '2');
});

test("a failed canvas drawing leaves sources available and can be retried", async ({ page }) => {
  await mockCards(page, { saved: true });
  await page.addInitScript(() => {
    const fillText = CanvasRenderingContext2D.prototype.fillText;
    CanvasRenderingContext2D.prototype.fillText = function (text, ...args) {
      if (this.canvas.width === 1200 && text === '堆疊（Stack）') {
        CanvasRenderingContext2D.prototype.fillText = fillText;
        throw new Error('synthetic drawing failure');
      }
      Reflect.apply(fillText, this, [text, ...args]);
    };
  });
  await page.goto(`/materials/${materialId}/concept-cards/${cardSetId}`);
  await expect(page.getByRole('alert')).toHaveText('圖卡繪製失敗，請再試一次。');
  await page.locator('.concept-card-details > summary').click();
  await expect(page.locator('.flashcard-point p').first()).toContainText('堆疊');
  await page.getByRole('button', { name: '重試圖卡', exact: true }).click();
  await expect(page.locator('canvas.visual-concept-card')).toBeVisible();
  await expect(page.getByRole('alert')).toHaveCount(0);
});

test("card generation restores stages after reload, retries failure and keeps full sources", async ({ page }) => {
  const controls = await mockCards(page, { saved: true });
  const state: CardGeneration = { status: "running", stage: "designing", completed: 1, total: 2, error_code: null, retained_original: 0, cached: 0 };
  controls.setGeneration(state);
  await page.goto(`/materials/${materialId}/concept-cards/${cardSetId}`);
  await expect(page.getByRole("progressbar", { name: "概念卡生成進度" })).toHaveAttribute("value", "1");
  await expect(page.getByRole("progressbar", { name: "概念卡生成進度" })).toHaveClass("processing-progress");
  await expect(page.locator(".overall-heading")).toContainText("50%");
  await expect(page.locator(".status-timeline [aria-current=step]")).toContainText("整理與設計卡面");
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.reload();
  await expect(page.getByRole("heading", { name: "正在生成概念卡" })).toBeVisible();
  controls.setGeneration({ ...state, status: "failed", stage: "checking", error_code: "CARD_GENERATION_FAILED" });
  await expect(page.getByRole("button", { name: "重試未完成部分" })).toBeVisible();
  await expect(page.locator(".status-timeline [aria-current=step]")).toContainText("已中斷");
  await page.getByRole("button", { name: "重試未完成部分" }).click();
  await expect(page.getByRole("heading", { name: "正在生成概念卡" })).toBeVisible();
  const card = controls.view.concepts[0];
  controls.setEdits({ [card.concept_id]: [{ source: card.claims[0].text, text: "堆疊：後進先出（LIFO）。" }] });
  controls.setGeneration({ ...state, status: "ready", stage: "complete", completed: 2, retained_original: 1 });
  await expect(page.locator(".visual-concept-card").first()).toBeVisible();
  const drawing = await cardDrawing(page);
  expect(drawing.texts.some(line => line.text.includes("後進先出（LIFO）"))).toBeTruthy();
  await page.getByText("完整重點與來源", { exact: true }).click();
  await expect(page.locator(".flashcard-point").first()).toContainText(card.claims[0].text);
});

function designedCard(view: ReturnType<typeof structureView>, layout: 'focus' | 'compare' | 'flow' | 'grid', count: number): CardDesign {
  const card = view.concepts[0];
  const sources = { claim_ids: card.claims.map(c => c.claim_id), relation_ids: [], evidence_ids: [...new Set(card.claims.flatMap(c => c.evidence.map(e => e.evidence_id)))] };
  return { schema: 'concept-card-design/v1', revision: 'card-design:sha256:' + 'a'.repeat(64), provider: { model_id: 'synthetic', revision: 'test', api_protocol: 'test' }, details_claim_ids: [],
    pages: [{ title: '記住核心機制', layout, takeaway: { text: '先確認必要條件，再判斷操作結果。', ...sources },
      blocks: Array.from({ length: count }, (_, i) => ({ heading: ['必要前提', '處理方式', '回覆結果', '下一步'][i], text: layout === 'grid' && count === 4 ? '確認條件與對應結果。' : '保留必要的條件，省去重複鋪陳。', icon: ['list-check', 'cpu', 'database', 'clock'][i], ...sources })),
      links: layout === 'flow' ? Array.from({ length: count - 1 }, (_, i) => ({ source: i, target: i + 1, label: '成立後', ...sources })) : [] }] };
}

for (const [layout, count] of [['focus', 1], ['focus', 2], ['compare', 2], ['flow', 2], ['flow', 3], ['flow', 4], ['grid', 3], ['grid', 4]] as const) {
  test(`model-designed ${layout}/${count} keeps readable blocks and exact source access`, async ({ page }) => {
    const fixture = await mockCards(page, { saved: true, long: true });
    const design = designedCard(fixture.view, layout, count);
    fixture.setDesigns({ [fixture.view.concepts[0].concept_id]: design });
    await page.goto(`/materials/${materialId}/concept-cards/${cardSetId}`);
    await expect(page.locator('canvas.visual-concept-card')).toHaveAttribute('data-layout', 'designed');
    await expectCardTextFits(page);
    expect(await drawnText(page)).toContain(design.pages[0].takeaway.text);
    expect(await drawnText(page)).not.toContain('最後一個條件不可遺漏');
    await expect(page.getByRole('button', { name: '本張引用 · 第 1 頁', exact: true })).toBeVisible();
    await page.locator('.concept-card-details > summary').click();
    await expect(page.locator('.flashcard-point p').first()).toContainText('最後一個條件不可遺漏');
    expect(await page.evaluate(() => window.cardEncodingCount)).toBe(0);
    const downloading = page.waitForEvent('download');
    await page.getByRole('button', { name: '下載 PNG', exact: true }).click();
    expect((await downloading).suggestedFilename()).toMatch(/\.png$/);
  });
}


for (const [layout, count] of [['focus', 1], ['compare', 2], ['grid', 4], ['flow', 4]] as const) {
  test(`model-designed ${layout} wraps long text without truncation or fixed-height rejection`, async ({ page }) => {
    const fixture = await mockCards(page, { saved: true });
    const design = designedCard(fixture.view, layout, count);
    design.pages[0].title = '長標題依照實際字型量測換行並保留完整內容'.repeat(3);
    design.pages[0].takeaway.text = '結論需要完整保留必要條件，卡面應隨文字內容增高。'.repeat(5);
    for (const block of design.pages[0].blocks) {
      block.heading = '較長的區塊標題也必須完整換行顯示';
      block.text = '這段內容超過原本估算的字寬限制，但只需要正常換行即可完整閱讀。'.repeat(5);
    }
    fixture.setDesigns({ [fixture.view.concepts[0].concept_id]: design });
    await page.goto(`/materials/${materialId}/concept-cards/${cardSetId}`);
    await expectCardTextFits(page);
    const text = await drawnText(page);
    expect(text).toContain(design.pages[0].title);
    expect(text).toContain(design.pages[0].takeaway.text);
    expect(text).toContain(design.pages[0].blocks[0].text);
    expect(await page.locator('canvas.visual-concept-card').evaluate((canvas: HTMLCanvasElement) => canvas.height)).toBeGreaterThan(760);
    const downloading = page.waitForEvent('download');
    await page.getByRole('button', { name: '下載 PNG', exact: true }).click();
    expect(await (await downloading).failure()).toBeNull();
  });
}

// 學習標記 mock：marked 為一開始已標記的概念索引與標記；需要加強是標成不熟的卡（依概念順序）。
async function mockMarks(page: Page, view: ReturnType<typeof structureView>, { marked = {} as Record<number, "forgot" | "remembered">, failFirstPost = false, conflictOnce = false, listed = true } = {}) {
  const marks = new Map(Object.entries(marked).map(([index, rating]) => [view.concepts[Number(index)].concept_id, { rating, count: 1 }]));
  const posts: { key: string | undefined; body: { concept_id: string; rating: string; expected_review_count: number } }[] = [];
  let failed = false, conflicted = false;
  const summary = (setFilter: string | null) => {
    const ids = view.concepts.map(c => c.concept_id);
    const count = (rating: string) => ids.filter(id => marks.get(id)?.rating === rating).length;
    const weak = ids.filter(id => marks.get(id)?.rating === "forgot");
    return {
      schema: "card-reviews/v2", forgot_count: count("forgot"),
      // 已移到新版的舊卡組不列入標記統計（listed = false）。
      sets: listed ? [{ card_set_id: cardSetId, card_count: ids.length, forgot_count: count("forgot"), remembered_count: count("remembered") }] : [],
      marks: setFilter && listed ? ids.filter(id => marks.has(id)).map(id => ({ card_set_id: cardSetId, concept_id: id, rating: marks.get(id)!.rating, review_count: marks.get(id)!.count })) : [],
      cards: !listed ? [] : weak.map(id => {
        const { concept_id, label, claims } = view.concepts.find(c => c.concept_id === id)!;
        return { card_set_id: cardSetId, card_set_name: "資料結構 · 核心概念", material_id: materialId, material_name: "資料結構講義.pdf",
          knowledge_structure_revision: structureRevision, source_resolver: decodeURIComponent(view.source_resolver),
          review_count: marks.get(id)!.count, card: { concept_id, label, claims } };
      }),
    };
  };
  await page.route("**/v1/card-reviews*", route => json(route, summary(new URL(route.request().url()).searchParams.get("card_set_id"))));
  await page.route(`**/v1/card-sets/${cardSetId}/reviews`, route => {
    const body = route.request().postDataJSON();
    posts.push({ key: route.request().headers()["idempotency-key"], body });
    if (failFirstPost && !failed) { failed = true; return route.abort("connectionreset"); }
    const before = marks.get(body.concept_id)?.count ?? 0;
    if (conflictOnce && !conflicted) {
      conflicted = true;
      marks.set(body.concept_id, { rating: "remembered", count: before + 1 });
      return json(route, { schema: "api-error/v1", request_id: sessionId, reason_code: "CARD_REVIEW_CONFLICT", retryable: false, message: "Request could not be completed." }, 409);
    }
    expect(body.expected_review_count).toBe(before);
    marks.set(body.concept_id, { rating: body.rating, count: before + 1 });
    return json(route, { schema: "card-review/v2", card_set_id: cardSetId, concept_id: body.concept_id, rating: body.rating,
      review_count: before + 1, reviewed_at: "2026-10-21T04:00:00Z" });
  });
  return { posts };
}

test("needs-work row opens its own page that shows the original cards one at a time and finishes the round", async ({ page }) => {
  const { view } = await mockCards(page, { saved: true });
  const { posts } = await mockMarks(page, view, { marked: { 0: "forgot", 1: "forgot" } });
  await page.goto("/concept-cards");
  await expect(page.getByRole("article", { name: "資料結構 · 核心概念" }).locator(".card-review-due")).toHaveText("需要加強 2 張");
  const strip = page.getByRole("region", { name: "需要加強" });
  await expect(strip).toContainText("2 張卡片標成不熟");
  // 概念卡頁只有一列入口；加強在獨立頁進行（全部卡組）。
  await strip.getByRole("button", { name: "開始加強" }).click();
  await expect(page).toHaveURL(/\/concept-cards\/review$/);
  await expect(page.getByRole("heading", { name: "需要加強", level: 1 })).toBeVisible();
  await expect(page.locator(".card-review-progress")).toContainText("第 1 / 2 張");
  // 卡片是卡組瀏覽時的同一張概念卡：圖卡、完整重點與來源。
  const stage = page.getByRole("article", { name: "需要加強：堆疊（Stack）" });
  await expect(stage.locator(".visual-concept-card, .concept-card").first()).toBeVisible();
  await expect(stage.getByText("完整重點與來源")).toBeVisible();
  await expect(stage.getByRole("group", { name: "標記「堆疊（Stack）」熟不熟" }).getByRole("button")).toHaveText(["不熟", "熟悉"]);
  await stage.getByRole("button", { name: "熟悉" }).click();
  await expect(page.locator(".card-review-notice")).toHaveText("已標成熟悉，離開需要加強。");
  await expect(page.locator(".card-review-progress")).toContainText("第 2 / 2 張");
  await page.getByRole("article", { name: "需要加強：陣列（Array）" }).getByRole("button", { name: "不熟" }).click();
  await expect(page.getByText("這一輪加強完成！")).toBeVisible();
  await expect(page.locator(".card-review-done")).toContainText("標成熟悉 1 張，仍不熟 1 張");
  await expect(page.getByRole("button", { name: "再加強一輪" })).toBeVisible();
  expect(posts.map(p => [p.body.concept_id === view.concepts[0].concept_id, p.body.rating, p.body.expected_review_count])).toEqual([[true, "remembered", 1], [false, "forgot", 1]]);
  expect(new Set(posts.map(p => p.key)).size).toBe(2);
  await page.getByRole("button", { name: "返回概念卡" }).first().click();
  await expect(page).toHaveURL(/\/concept-cards$/);
});

test("a failed mark keeps the card and retries with the same intent key", async ({ page }) => {
  const { view } = await mockCards(page, { saved: true });
  const { posts } = await mockMarks(page, view, { marked: { 0: "forgot", 1: "forgot" }, failFirstPost: true });
  await page.goto("/concept-cards/review");
  const stage = page.getByRole("article", { name: "需要加強：堆疊（Stack）" });
  await stage.getByRole("button", { name: "不熟" }).click();
  await expect(stage.getByRole("alert")).toContainText("標記尚未保存");
  await expect(page.locator(".card-review-progress")).toContainText("第 1 / 2 張");
  await stage.getByRole("button", { name: "重試" }).click();
  await expect(page.locator(".card-review-notice")).toHaveText("已標成不熟，下一輪還會出現。");
  expect(posts.length).toBe(2);
  expect(posts[0].key).toBe(posts[1].key);
});

test("a mark conflict from another tab moves on without counting twice", async ({ page }) => {
  const { view } = await mockCards(page, { saved: true });
  const { posts } = await mockMarks(page, view, { marked: { 0: "forgot", 1: "forgot" }, conflictOnce: true });
  await page.goto("/concept-cards/review");
  await page.getByRole("article", { name: "需要加強：堆疊（Stack）" }).getByRole("button", { name: "熟悉" }).click();
  await expect(page.locator(".card-review-notice")).toHaveText("上一張卡已在其他頁面標記過，這裡不重複標記。");
  await expect(page.getByRole("article", { name: "需要加強：陣列（Array）" })).toBeVisible();
  expect(posts.length).toBe(1);
});

test("material needs-work entry opens a review of only this material's card sets and returns to the material", async ({ page }) => {
  const { view } = await mockCards(page, { saved: true });
  await mockMarks(page, view, { marked: { 0: "forgot", 1: "forgot" } });
  await page.goto(`/materials/${materialId}/concept-cards`);
  const strip = page.getByRole("region", { name: "需要加強" });
  await expect(strip).toContainText("這份教材 2 張卡片標成不熟");
  const read = page.waitForRequest(request => new URL(request.url()).pathname === "/v1/card-reviews" && new URL(request.url()).searchParams.has("material_id"));
  await strip.getByRole("button", { name: "開始加強" }).click();
  await expect(page).toHaveURL(new RegExp(`/materials/${materialId}/concept-cards/review$`));
  // 只讀這份教材的需要加強；頁首仍是教材頁首（概念卡分頁）。
  const params = new URL((await read).url()).searchParams;
  expect(params.get("material_id")).toBe(materialId);
  expect(params.has("card_set_id")).toBe(false);
  const tabs = page.getByRole("tablist", { name: "教材學習內容" });
  await expect(tabs.getByRole("tab", { name: "概念卡", exact: true })).toHaveAttribute("aria-selected", "true");
  await expect(page.locator(".card-review-heading")).toContainText("這份教材各卡組中標成不熟的卡片");
  // 混合多個卡組時標示卡組名稱；同一份教材內不再重複教材名稱。
  await expect(page.locator(".card-review-source")).toHaveText("資料結構 · 核心概念");
  // 卡組名稱過長時以省略號截斷（省略號要設在文字元素上，彈性容器本身不會顯示）。
  await expect(page.locator(".card-review-source > span")).toHaveCSS("text-overflow", "ellipsis");
  await page.getByRole("article", { name: "需要加強：堆疊（Stack）" }).getByRole("button", { name: "熟悉" }).click();
  await page.getByRole("article", { name: "需要加強：陣列（Array）" }).getByRole("button", { name: "熟悉" }).click();
  await expect(page.getByText("這一輪加強完成！")).toBeVisible();
  await page.getByRole("button", { name: "返回概念卡", exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`/materials/${materialId}/concept-cards$`));
});

test("card set page links to strengthening only that set and returns to the set", async ({ page }) => {
  const { view } = await mockCards(page, { saved: true });
  await mockMarks(page, view, { marked: { 0: "forgot", 1: "forgot" } });
  await page.goto("/concept-cards");
  await page.getByRole("button", { name: "開始複習" }).click();
  const link = page.getByRole("button", { name: /需要加強 2 張/ });
  await expect(link).toContainText("開始加強");
  await link.click();
  await expect(page).toHaveURL(new RegExp(`/materials/${materialId}/concept-cards/${cardSetId}/review$`));
  await expect(page.locator(".card-review-heading")).toContainText("資料結構 · 核心概念");
  await page.getByRole("article", { name: "需要加強：堆疊（Stack）" }).getByRole("button", { name: "熟悉" }).click();
  await expect(page.locator(".card-review-progress")).toContainText("第 2 / 2 張");
  await page.getByRole("button", { name: "返回卡組" }).click();
  await expect(page).toHaveURL(new RegExp(`/materials/${materialId}/concept-cards/${cardSetId}$`));
  await expect(page.getByRole("button", { name: /需要加強 1 張/ })).toBeVisible();
});

test("browsing marks the current card and an unmarked set needs no strengthening", async ({ page }) => {
  const { view } = await mockCards(page, { saved: true });
  const { posts } = await mockMarks(page, view);
  await page.goto("/concept-cards");
  await expect(page.locator(".card-review-due")).toHaveCount(0);
  const libraryStrip = page.getByRole("region", { name: "需要加強" });
  await expect(libraryStrip).toContainText("目前沒有，瀏覽卡組時把不熟的卡片標起來");
  await expect(libraryStrip.getByRole("button")).toHaveCount(0);
  await page.getByRole("button", { name: "開始複習" }).click();
  // 沒有不熟的卡片時不顯示需要加強入口。
  await expect(page.locator(".cards-weak-link")).toHaveCount(0);
  const marks = page.getByRole("group", { name: "標記「堆疊（Stack）」熟不熟" });
  await expect(marks.getByRole("button", { name: "不熟" })).toHaveAttribute("aria-pressed", "false");
  await marks.getByRole("button", { name: "不熟" }).click();
  await expect(marks.getByRole("button", { name: "不熟" })).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator(".cards-study-mark")).toContainText("目前：不熟");
  await expect(page.getByRole("button", { name: /需要加強 1 張/ })).toBeVisible();
  // 已標記的卡再改標：送出的次數接續目前的標記次數。
  await marks.getByRole("button", { name: "熟悉" }).click();
  await expect(page.locator(".cards-study-mark")).toContainText("目前：熟悉");
  expect(posts.map(p => [p.body.rating, p.body.expected_review_count])).toEqual([["forgot", 0], ["remembered", 1]]);
});

test("a card set left out of the marks summary (moved to a new version) offers no marking", async ({ page }) => {
  const { view } = await mockCards(page, { saved: true });
  const { posts } = await mockMarks(page, view, { marked: { 0: "forgot" }, listed: false });
  await page.goto(`/concept-cards/${cardSetId}`);
  await expect(page.locator(".visual-concept-card, .concept-card").first()).toBeVisible();
  await expect(page.locator(".cards-study-mark")).toHaveCount(0);
  await expect(page.locator(".cards-weak-link")).toHaveCount(0);
  expect(posts).toEqual([]);
});
