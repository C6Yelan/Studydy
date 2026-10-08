import { expect, test, type Page } from "@playwright/test";
import { artifactId, materialId, runId, sessionId, structureRevision, structureView, mockKnowledgeMapApi, json } from "../fixtures/knowledge-map";

const cardSetId = "55555555-5555-4555-8555-555555555555";
const mapPath = `/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}`;

async function mockCards(page: Page, { saved = false, long = false, many = false, withProgress = false, title = "資料結構 · 核心概念" } = {}) {
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
  const requests: { key: string | undefined; body: unknown }[] = [];
  const forbiddenMutations: string[] = [];
  page.on("request", (request) => {
    if (request.method() !== "GET" && request.url().includes("/study-sessions")) forbiddenMutations.push(request.url());
  });
  const summary = () => ({
    version, card_set_id: cardSetId, material_id: materialId, material_name: material.display_name,
    knowledge_structure_revision: structureRevision, name, card_count: selection.length,
    created_at: "2026-10-02T00:00:00Z", is_current_revision: !long,
  });
  await page.route(`**/v1/materials/${materialId}/card-sets`, (route) => {
    const body = route.request().postDataJSON();
    requests.push({ key: route.request().headers()["idempotency-key"], body });
    selection = body.concept_ids;
    name = body.name;
    exists = true;
    return json(route, summary(), 201);
  });
  await page.route(`**/v1/card-sets/${cardSetId}/update`, (route) => {
    const body = route.request().postDataJSON();
    if (body.expected_version !== version) return json(route, { schema: "api-error/v1", request_id: sessionId, reason_code: "CARD_SET_CONFLICT", retryable: false, message: "Request could not be completed." }, 409);
    name = body.name;
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
    return json(route, { ...summary(), schema: "card-set/v1", source_resolver: decodeURIComponent(view.source_resolver), status: view.status, excluded_pages: [], cards: selection.map((id) => view.concepts.find((item) => item.concept_id === id)!).map(({ concept_id, label, claims }) => ({ concept_id, label, claims })) });
  });
  return { requests, forbiddenMutations, view, summary };
}

async function openSavedCardManagement(page: Page) {
  await expect(page.getByRole("button", { name: "管理卡組", exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "返回卡組", exact: true }).click();
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
  await expect(page.getByRole("button", { name: "幫我選卡", exact: true })).toHaveCount(0);
  await expect(page.getByRole("region", { name: "推薦選卡", exact: true })).toHaveCount(0);
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
  await expect(manual).toContainText("已選 3 / 31");
  await manual.getByRole("button", { name: "預覽「堆疊（Stack）」" }).click();
  await page.getByRole("button", { name: "查看「堆疊（Stack）」的完整重點與來源" }).click();
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
  await expect(page.locator(".flashcard-title")).toHaveText("堆疊（Stack）");
  await page.screenshot({ path: "../.studydy-runtime/card-preview/study-front-desktop.png", fullPage: true });
  await page.keyboard.press("Space");
  await expect(page.locator(".flashcard-point")).toContainText("後進先出");
  await expect(page.locator(".flashcard")).not.toContainText("先備");
  await expect(page.getByRole("region", { name: "概念重點來源", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "查看第 1 頁來源" })).toBeVisible();
  await page.getByRole("button", { name: "查看第 1 頁來源" }).click();
  await expect(page.getByRole("dialog", { name: "教材來源" })).toBeVisible();
  await expect(page.getByRole("link", { name: "開啟 PDF 來源頁" })).toHaveAttribute("href", `/v1/artifacts/${artifactId}#page=1`);
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "下一張", exact: true }).click();
  await expect(page.locator(".flashcard-title")).toHaveText("陣列（Array）");
  await page.keyboard.press("ArrowLeft");
  await expect(page.locator(".flashcard-title")).toHaveText("堆疊（Stack）");
  await page.getByRole("button", { name: "翻面", exact: true }).click();
  await page.screenshot({ path: "../.studydy-runtime/card-preview/study-back-desktop.png", fullPage: true });
  await page.getByRole("button", { name: "洗牌重看" }).click();
  await expect(page.getByRole("status")).toHaveText("已洗牌，從第一張開始。");
  await page.getByRole("button", { name: "下一張", exact: true }).click();
  await page.getByRole("button", { name: "完成本輪" }).click();
  await expect(page.getByRole("heading", { name: "已瀏覽全部卡片" })).toBeVisible();
  await page.getByRole("button", { name: "再看一次" }).click();
  await page.reload();
  await expect(page.locator(".flashcard-title")).toHaveText("堆疊（Stack）");
  await page.getByRole("button", { name: "返回卡組", exact: true }).click();
  await expect(page.getByRole("heading", { name: "我的資料結構卡組" })).toBeVisible();
  await page.screenshot({ path: "../.studydy-runtime/card-preview/library-desktop.png", fullPage: true });
  await page.getByRole("button", { name: "管理卡組「我的資料結構卡組」" }).click();
  await page.getByRole("button", { name: "刪除卡組「我的資料結構卡組」" }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(page.getByRole("form", { name: "刪除卡組確認" }).getByRole("heading", { name: "確定刪除？", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "確認刪除卡組" }).click();
  await expect(page.getByRole("heading", { name: "收藏一組值得反覆看的重點" })).toBeVisible();
  expect(fixture.forbiddenMutations).toEqual([]);
});

test("card deletion confirms inline, cancels safely and retries once even when search hides a pending item", async ({ page }) => {
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
  const confirmation = card.getByRole("form", { name: "刪除卡組確認" });
  for (const viewport of [{ width: 1440, height: 900 }, { width: 390, height: 844 }]) {
    await page.setViewportSize(viewport);
    await opener.click();
    await card.getByRole("button", { name: `刪除卡組「${title}」`, exact: true }).click();
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await expect(confirmation.getByRole("heading", { name: "確定刪除？", exact: true })).toBeVisible();
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
  await confirmation.evaluate(form => {
    form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
  });
  await expect.poll(() => deletes).toBe(2);
  await expect(confirmation.getByRole("button", { name: "正在刪除…", exact: true })).toBeDisabled();
  await expect(confirmation.getByRole("button", { name: "取消", exact: true })).toBeDisabled();
  await page.keyboard.press("Escape");
  await expect(confirmation).toBeVisible();
  const search = page.getByRole("searchbox", { name: "搜尋卡組或教材", exact: true });
  await search.fill("不存在的卡組");
  await expect(card).toHaveCount(0);
  await search.fill("");
  await expect(confirmation.getByRole("button", { name: "正在刪除…", exact: true })).toBeDisabled();
  await expect(confirmation.getByRole("button", { name: "取消", exact: true })).toBeDisabled();
  await search.fill("不存在的卡組");
  release();
  await expect(page.locator(".collection-summary")).toContainText("已保存 0 個卡組");
  await expect(search).toBeFocused();
  await search.fill("");
  await expect(card).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "收藏一組值得反覆看的重點" })).toBeVisible();
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
  const search = page.getByRole("searchbox", { name: "搜尋卡組或教材", exact: true });
  await search.focus();
  releaseDelete();
  await expect(target).toHaveCount(0);
  await expect(page.getByRole("article", { name: "已重新命名的卡組", exact: true })).toBeVisible();
  await expect(search).toBeFocused();
  const lateRead = page.waitForResponse(response => response.url().endsWith("/v1/card-sets"));
  releaseRead();
  await lateRead;
  await expect(page.locator(".library-item")).toHaveCount(1);
  await expect(target).toHaveCount(0);
  await expect(page.locator(".collection-summary")).toContainText("已保存 1 個卡組");
});

test("mobile keeps review status, long content and sources usable without page overflow", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await mockCards(page, { saved: true, long: true });
  await page.goto(`/concept-cards/${cardSetId}`);
  await expect(page.getByText("教材已有新版。此卡組保留建立時的教材內容與來源。")).toBeVisible();
  await page.getByRole("button", { name: "翻面", exact: true }).click();
  await expect(page.locator(".flashcard-content")).toContainText("x != 0");
  await expect(page.getByText("待確認 · 原教材尚未通過檢查", { exact: true })).toBeVisible();
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
  await page.getByRole("button", { name: "翻面", exact: true }).click();
  await page.screenshot({ path: "../.studydy-runtime/card-preview/study-mobile.png", fullPage: true });
});

test("account collection can select a material; failed save retries the same intent", async ({ page }) => {
  const fixture = await mockCards(page);
  await page.goto("/concept-cards");
  await page.getByRole("button", { name: "建立卡組", exact: true }).click();
  await expect(page).toHaveURL(/\/concept-cards\/new$/);
  await page.getByLabel("選擇教材", { exact: true }).selectOption({ label: "資料結構講義.pdf" });
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
  await expect(page.locator(".flashcard-title")).toBeVisible();
  expect(keys).toHaveLength(2);
  expect(keys[0]).toBe(keys[1]);
  expect(fixture.requests).toHaveLength(1);
});

test("saved decks can be renamed and edited without replacing their identity", async ({ page }) => {
  await mockCards(page, { saved: true });
  await page.goto("/concept-cards");
  await page.getByRole("button", { name: "管理卡組「資料結構 · 核心概念」" }).click();
  await expect(page.getByRole("button", { name: "重新命名", exact: true })).toHaveCount(1);
  await page.locator(".material-management-menu[open]").getByRole("button", { name: "管理卡組", exact: true }).click();
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
  const materialSearch = await page.getByRole("searchbox").boundingBox();
  const materialCard = (await page.locator('.library-item').first().boundingBox())!;
  await page.getByRole('navigation', { name: '主要導覽' }).getByRole('button', { name: '概念卡', exact: true }).click();
  const cardSearch = await page.getByRole("searchbox").boundingBox();
  const cardTile = (await page.locator('.library-item').first().boundingBox())!;
  expect(cardSearch!.x).toBe(materialSearch!.x);
  expect(cardSearch!.width).toBeGreaterThanOrEqual(materialSearch!.width);
  expect(cardTile.width).toBeCloseTo(materialCard.width, 0);
  expect(cardTile.width).toBeLessThan(320);
  expect(cardTile.height).toBeLessThan(270);
  await expect(page.getByRole('heading', { name: '我的概念卡', exact: true })).toHaveCount(0);
  await expect(page.locator('.library-item').getByRole('button', { name: '管理卡組', exact: true })).toHaveCount(0);
  await page.screenshot({ path: "../.studydy-runtime/card-preview/library-aligned.png", fullPage: true });
  let release!: () => void;
  const held = new Promise<void>(resolve => { release = resolve; });
  await page.route(`**/v1/card-sets/${cardSetId}`, async route => { await held; await route.fallback(); });
  await page.getByRole("button", { name: "開始複習", exact: true }).click();
  const back = page.getByRole('button', {name:'返回卡組', exact:true});
  await expect(back).toBeVisible();
  const loadingBack = (await back.boundingBox())!;
  const tabs = (await page.getByRole('tablist', {name:'教材學習內容'}).boundingBox())!;
  expect(loadingBack.x).toBeCloseTo(tabs.x, 0);
  release();
  await expect(page.locator(".flashcard")).toBeVisible();
  expect((await back.boundingBox())!.x).toBeCloseTo(loadingBack.x, 0);
  expect((await back.boundingBox())!.y).toBeCloseTo(loadingBack.y, 0);
  const content = await page.locator(".cards-study").boundingBox();
  const card = await page.locator(".flashcard").boundingBox();
  expect(card!.width / content!.width).toBeLessThan(.85);
  await expect(page.getByRole("navigation", {name:"選擇概念卡"})).toBeVisible();
  expect(card!.width/card!.height).toBeCloseTo(4/3,1);
  expect(card!.width).toBeLessThanOrEqual(800);
  expect(card!.height).toBeLessThan(560);
  await expect(page.locator(".concept-visual")).toBeVisible();
  expect(card!.x).toBeGreaterThan((await page.locator(".cards-browse-list").boundingBox())!.x);
  const controls = page.locator('.cards-study-toolbar');
  await expect(controls).toContainText('1 / 2');
  await expect(controls.getByRole('button', {name:'洗牌重看'})).toBeVisible();
  for(const button of await controls.getByRole('button').all()) {
    const box = (await button.boundingBox())!;
    expect(box.width).toBeLessThan(150);
    expect(box.height).toBeGreaterThanOrEqual(40);
    expect(box.y + box.height).toBeLessThan(960);
  }
  await page.setViewportSize({width:390,height:844});
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(390);
  for(const button of await controls.getByRole('button').all()) {
    const box = (await button.boundingBox())!;
    expect(box.height).toBeGreaterThanOrEqual(40);
    expect(box.y + box.height).toBeLessThan(844);
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
  await page.getByRole("button", { name: "翻面", exact: true }).click();
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
  await page.getByRole("button", { name: "翻面", exact: true }).click();
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
  await page.getByRole("button", { name: "翻面", exact: true }).click();
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
  await page.getByRole("button", { name: "我的教材", exact: true }).click();
  const saved = page.waitForResponse((r) => r.url().endsWith("/card-sets") && r.request().method() === "POST");
  releaseSave();
  await saved;
  await expect(page).toHaveURL(/\/materials$/);
  await page.getByLabel("資料結構講義.pdf", { exact: true }).getByRole("button", { name: "概念卡", exact: true }).click();
  await page.getByRole("button", { name: "開始複習", exact: true }).click();
  await page.getByRole("button", { name: "翻面", exact: true }).click();
  let releaseSource!: () => void;
  const sourceGate = new Promise<void>((resolve) => { releaseSource = resolve; });
  await page.route("**/evidence/*/source", async (route) => { await sourceGate; await route.fallback(); });
  const reading = page.waitForRequest("**/evidence/*/source");
  await page.getByRole("button", { name: "查看第 1 頁來源" }).click();
  await reading;
  await page.getByRole("button", { name: "返回卡組", exact: true }).click();
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
  await page.route(`**/v1/card-sets/${cardSetId}`, async (route) => { await gate; await route.fallback(); });
  const pending = page.waitForRequest(`**/v1/card-sets/${cardSetId}`);
  await page.goto(`/concept-cards/${cardSetId}`);
  await pending;
  await page.getByRole("button", { name: "登出", exact: true }).click();
  await expect(page.getByRole("heading", { name: "登入您的帳戶" })).toBeVisible();
  release();
  // 第二帳號只得到空卡組列表，舊卡組仍由 API 拒絕。
  await page.route(`**/v1/card-sets/${cardSetId}`, (route) => json(route, { schema: "api-error/v1", request_id: sessionId, reason_code: "RESOURCE_NOT_FOUND", retryable: false, message: "Synthetic foreign card set" }, 404));
  const stranger = { schema: "learner-identity/v1", learner_id: "77777777-7777-4777-8777-777777777777" };
  await page.route("**/v1/session/login", (route) => json(route, stranger));
  await page.route("**/v1/session/refresh", (route) => json(route, stranger));
  await page.route("**/v1/card-sets", (route) => json(route, { schema: "card-set-list/v1", card_sets: [] }));
  await page.getByLabel("Email", { exact: true }).fill("second@example.test");
  await page.getByLabel("密碼", { exact: true }).fill("Synthetic password 42");
  await page.getByRole("button", { name: "登入", exact: true }).click();
  await page.getByRole("button", { name: "概念卡", exact: true }).click();
  await expect(page.getByRole("heading", { name: "收藏一組值得反覆看的重點" })).toBeVisible();
  await page.goto(`/concept-cards/${cardSetId}`);
  await expect(page.getByRole("heading", { name: "無法開啟卡組" })).toBeVisible();
  await expect(page.locator(".flashcard")).toHaveCount(0);
});

test("account change from another tab removes an open card source dialog", async ({ page }) => {
  await mockCards(page, { saved: true });
  await page.goto(`/concept-cards/${cardSetId}`);
  await page.getByRole("button", { name: "翻面", exact: true }).click();
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
  await expect(page.locator('.app-sidebar')).toBeVisible();
  await page.getByRole('button', { name: '開始複習', exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`/materials/${materialId}/concept-cards/${cardSetId}$`));
  await expect(page.locator('.app-sidebar')).toHaveCount(0);
  const tabs = page.getByRole('tablist', { name: '教材學習內容' });
  await expect(tabs).toBeVisible();
  await expect(tabs.getByRole('tab', { name: '概念卡', exact: true })).toHaveAttribute('aria-selected', 'true');
  await openSavedCardManagement(page);
  await expect(page).toHaveURL(new RegExp(`/materials/${materialId}/concept-cards/${cardSetId}/edit$`));
  await expect(tabs).toBeVisible();
  await expect(page.locator('.app-sidebar')).toHaveCount(0);
  await page.getByRole('button', { name: '取消變更', exact: true }).click();
  await expect(tabs).toBeVisible();
  await page.getByRole('button', { name: '返回卡組', exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`/materials/${materialId}/concept-cards$`));
  await expect(tabs).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(390);
});


test('saved material card review keeps the chat entry at the viewport corner',async({page})=>{
 await mockCards(page,{saved:true});
 for(const width of [1920,1440,390]){
  await page.setViewportSize({width,height:900});await page.goto(`/materials/${materialId}/concept-cards/${cardSetId}`);
  await expect(page.locator('.flashcard-title')).toBeVisible();
  const chat=page.getByRole('button',{name:'教材問答',exact:true});await expect(chat).toBeVisible();const box=(await chat.boundingBox())!;
  expect(width-box.x-box.width).toBeCloseTo(width===390?16:24,0);expect(900-box.y-box.height).toBeCloseTo(width===390?16:24,0);
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
      const svg = page.locator(".concept-visual");
      await expect(svg).toHaveAttribute("data-visual-kind", expected);
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(width);
      if (sample === "prerequisite") await expect(svg).toContainText("學習依賴 · 非時間流程");
      if (sample === "contrast") await expect(svg).toContainText("不推定優劣");
      if (sample === "process-fallback") await expect(page.locator(".flashcard-review")).toBeVisible();
      if (!["structure", "code", "formula"].includes(expected)) {
        await expect(svg).toHaveAttribute("data-relation-id", relation.relation_id);
        expect(await svg.locator("text").allTextContents()).toEqual(expect.arrayContaining([source.label, target.label]));
      }
      const portability = await svg.evaluate(async element => {
        const text = new XMLSerializer().serializeToString(element);
        const url = URL.createObjectURL(new Blob([text], { type: "image/svg+xml" }));
        const image = new Image(); image.src = url;
        try { await image.decode(); return [image.naturalWidth, image.naturalHeight, element.querySelectorAll("script, foreignObject, image, a").length]; }
        finally { URL.revokeObjectURL(url); }
      });
      expect(portability[0]).toBeGreaterThan(0); expect(portability[1]).toBeGreaterThan(0); expect(portability[2]).toBe(0);
      // 各圖型以 DOM／來源斷言驗證，不持續累積可重建全頁快照。
      await page.getByRole("button", { name: "翻面", exact: true }).click();
      await expect(page.locator(".flashcard-point p").first()).toHaveText(source.claims[0].text);
      if (!["structure", "code", "formula"].includes(expected)) {
        await expect(page.getByRole("region", { name: "圖卡關係與來源" })).toContainText(relation.learner_reason);
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
  await page.route("**/knowledge-structures/*", route => {
    reads.push(decodeURIComponent(new URL(route.request().url()).pathname));
    if (failed) return json(route, { schema: "api-error/v1", request_id: sessionId, reason_code: "STORAGE_UNAVAILABLE", retryable: true, message: "Synthetic unavailable structure" }, 503);
    return route.fallback();
  });
  await page.goto(`/materials/${materialId}/concept-cards/${cardSetId}`);
  await expect(page.getByRole("heading", { name: "無法開啟卡組" })).toBeVisible();
  await expect(page.locator(".flashcard")).toHaveCount(0);
  failed = false;
  await page.getByRole("button", { name: "重新讀取", exact: true }).click();
  await expect(page.locator(".concept-visual")).toBeVisible();
  expect(reads.every(url => url.endsWith(structureRevision))).toBe(true);
  let release!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  await page.route("**/knowledge-structures/*", async route => { await gate; await route.fallback(); });
  const pending = page.waitForRequest("**/knowledge-structures/*");
  await page.reload(); await pending;
  await page.getByRole("button", { name: "返回卡組", exact: true }).click();
  const response = page.waitForResponse("**/knowledge-structures/*");
  release(); await response;
  await expect(page.locator(".flashcard")).toHaveCount(0);
  await expect(page.locator(".library-item")).toHaveCount(1);
});

test("pending visual structure cannot restore a previous account's card", async ({ page }) => {
  await mockCards(page, { saved: true });
  let release!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  await page.route("**/knowledge-structures/*", async route => { await gate; await route.fallback(); });
  const pending = page.waitForRequest("**/knowledge-structures/*");
  await page.goto(`/materials/${materialId}/concept-cards/${cardSetId}`); await pending;
  const aborted = page.waitForEvent("requestfailed", request => request.url().includes("/knowledge-structures/"));
  await page.getByRole("button", { name: "登出", exact: true }).click();
  await expect(page.getByRole("heading", { name: "登入您的帳戶" })).toBeVisible();
  await aborted; release();
  await expect(page.locator(".flashcard")).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "登入您的帳戶" })).toBeVisible();
});

test("visual labels remain inert text and long excerpts keep full conditions on the back", async ({ page }) => {
  const { view } = await mockCards(page, { saved: true, long: true });
  view.concepts[0].label = '<script>alert("x")</script> & <image href="https://invalid.test" />';
  await page.goto(`/materials/${materialId}/concept-cards/${cardSetId}`);
  await expect(page.locator(".flashcard-definition")).toHaveText("保留所有必要條件與符號。");
  expect((await page.locator(".flashcard-bullets").textContent())!.length).toBeLessThan(400);
  await expect(page.locator(".flashcard script, .flashcard image, .flashcard foreignObject")).toHaveCount(0);
  await expect(page.locator(".flashcard-title")).toHaveText(view.concepts[0].label);
  await page.getByRole("button", { name: "翻面", exact: true }).click();
  await expect(page.locator(".flashcard-point p").first()).toContainText("最後一個條件不可遺漏：x != 0。");
});
