import { expect, test, type Page } from "@playwright/test";
import { artifactId, materialId, runId, sessionId, structureRevision, structureView, mockKnowledgeMapApi, json, progress as baseProgress } from "../fixtures/knowledge-map";

const cardSetId = "55555555-5555-4555-8555-555555555555";
const mapPath = `/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}`;

async function mockCards(page: Page, { saved = false, long = false, withProgress = false, reversedPath = false } = {}) {
  const view = structureView();
  view.concepts[0].label = "堆疊（Stack）";
  view.concepts[0].claims[0].text = "堆疊遵循後進先出（LIFO）的原則：最後放入的元素會最先被取出。";
  view.concepts[1].label = "陣列（Array）";
  view.concepts[1].claims[0].text = "陣列將元素保存在連續的記憶體位置，可使用索引存取特定元素。";
  if (long) {
    view.status.quality = "needs_review";
    view.status.decision = "review";
    view.concepts[0].claims[0].text = "保留所有必要條件與符號。".repeat(100) + "\n最後一個條件不可遺漏：x != 0。";
  }
  if (reversedPath) view.initial_learning_path = [...view.initial_learning_path].reverse().map((step, index) => ({ ...step, position: index + 1 }));
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
  let name = "資料結構 · 核心概念";
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
  return { requests, forbiddenMutations, view };
}

async function openSavedCardManagement(page: Page) {
  await expect(page.getByRole("button", { name: "管理卡組", exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "返回卡組", exact: true }).click();
  await page.locator(".library-item .material-management-menu summary").click();
  await page.locator(".material-management-menu[open]").getByRole("button", { name: "管理卡組", exact: true }).click();
}

test("map creates a saved multi-card deck; keyboard, sources, shuffle and delete work", async ({ page }) => {
  const fixture = await mockCards(page);
  await page.goto(mapPath);
  await page.getByRole("navigation", { name: "教材學習內容" }).getByRole("button", { name: "概念卡", exact: true }).click();
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
  await page.getByText("查看來源", { exact: true }).click();
  await page.getByRole("button", { name: "查看第 1 頁來源" }).click();
  await expect(page.getByRole("dialog", { name: "教材來源" })).toBeVisible();
  await expect(page.getByRole("link", { name: "開啟 PDF 來源頁" })).toHaveAttribute("href", `/v1/artifacts/${artifactId}#page=1`);
  await page.keyboard.press("Escape");
  await page.getByText("查看來源", { exact: true }).click();
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
  await expect(page.getByRole("dialog")).toContainText("原教材與學習紀錄會保留");
  await page.getByRole("button", { name: "確認刪除卡組" }).click();
  await expect(page.getByRole("heading", { name: "收藏一組值得反覆看的重點" })).toBeVisible();
  expect(fixture.forbiddenMutations).toEqual([]);
});

test("mobile supports long content, quality and old-version notices without page overflow", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await mockCards(page, { saved: true, long: true });
  await page.goto(`/concept-cards/${cardSetId}`);
  await expect(page.getByText("教材已有新版。此卡組保留建立時的教材內容與來源。")).toBeVisible();
  await page.getByRole("button", { name: "翻面", exact: true }).click();
  await expect(page.locator(".flashcard-content")).toContainText("x != 0");
  await expect(page.getByText(/原教材有待確認/)).toHaveCount(0);
  await expect(page.getByText(/來源狀態/)).not.toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  const content = page.locator(".flashcard-content");
  expect(await content.evaluate((element) => element.scrollHeight > element.clientHeight)).toBe(true);
  await content.evaluate((element) => { element.scrollTop = element.scrollHeight; });
  await page.getByText("查看來源", { exact: true }).click();
  await expect(page.getByText(/來源狀態/)).toBeVisible();
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
  await expect(page.locator(".deck-count")).toHaveText("1 張卡片");
  await openSavedCardManagement(page);
  await expect(page.getByRole("checkbox").nth(1)).not.toBeChecked();
  await page.getByRole("button", { name: "全選", exact: true }).click();
  await page.getByRole("button", { name: "保存變更", exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`/concept-cards/${cardSetId}$`));
  await page.reload();
  await expect(page.locator(".deck-count")).toHaveText("2 張卡片");
  await openSavedCardManagement(page);
  await page.getByLabel("卡組名稱", { exact: true }).fill("不應保存");
  await page.getByRole("button", { name: "取消變更", exact: true }).click();
  await expect(page.getByRole("heading", { name: "重新命名的卡組", exact: true })).toBeVisible();
});

test("library layout matches materials and review uses the available content width", async ({ page }) => {
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
  await page.getByRole("button", { name: "開始複習", exact: true }).click();
  await expect(page.locator(".flashcard")).toBeVisible();
  const content = await page.locator(".cards-study").boundingBox();
  const card = await page.locator(".flashcard").boundingBox();
  expect(card!.width / content!.width).toBeGreaterThan(.95);
  await page.screenshot({ path: "../.studydy-runtime/card-preview/study-wide.png", fullPage: true });
});

test("no-session recommendations explain the fallback, preserve manual control and save path order", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const fixture = await mockCards(page, { reversedPath: true });
  let progressReads = 0;
  page.on("request", (request) => { if (request.url().endsWith("/progress")) progressReads++; });
  await page.goto(mapPath + "/create-cards");
  await page.getByRole("button", { name: "幫我選卡", exact: true }).click();
  const panel = page.getByRole("region", { name: "幫我選卡", exact: true });
  await expect(panel).toContainText("尚無學習紀錄");
  await page.getByLabel("最多張數", { exact: true }).fill("1");
  await expect(panel.locator("li")).toHaveCount(1);
  await expect(page.getByRole("checkbox").first()).toBeChecked();
  await page.getByRole("button", { name: "套用推薦", exact: true }).click();
  await expect(page.getByRole("checkbox").first()).not.toBeChecked();
  await expect(page.getByRole("checkbox").nth(1)).toBeChecked();
  await expect(page.locator(".cards-selection-reason")).toHaveText("依教材學習路徑推薦");
  await page.getByRole("checkbox").first().check();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: "../.studydy-runtime/card-preview/recommendation-mobile.png", fullPage: true });
  await page.getByRole("button", { name: "保存並開始複習" }).click();
  await expect(page).toHaveURL(new RegExp(`/concept-cards/${cardSetId}$`));
  await expect(page.locator(".flashcard-title")).toHaveText("陣列（Array）");
  await page.reload();
  await expect(page.locator(".flashcard-title")).toHaveText("陣列（Array）");
  expect(progressReads).toBe(0);
  expect(fixture.forbiddenMutations).toEqual([]);
});

test("weakness recommendations use matching progress and a late read never changes manual selection", async ({ page }) => {
  const fixture = await mockCards(page, { withProgress: true });
  const progress = structuredClone(baseProgress);
  const weak = progress.concept_states[1];
  weak.status = "needs_review";
  weak.weak_claim_ids = [structureView().concepts[1].claims[0].claim_id];
  progress.weaknesses = [{ concept_id: weak.concept_id, claim_ids: weak.weak_claim_ids, reason: "latest_answer_incorrect" }];
  let finishRead!: () => void;
  const ready = new Promise<void>((resolve) => { finishRead = resolve; });
  await page.route(`**/v1/study-sessions/${sessionId}/progress`, async (route) => { await ready; return json(route, progress); });
  await page.goto(mapPath + "/create-cards");
  await page.getByRole("button", { name: "幫我選卡", exact: true }).click();
  await expect(page.getByText("正在讀取學習進度，尚未改變你的勾選。")).toBeVisible();
  await page.getByRole("button", { name: "取消全選", exact: true }).click();
  await page.getByRole("checkbox").first().check();
  finishRead();
  const panel = page.getByRole("region", { name: "幫我選卡" });
  await expect(panel.locator("li")).toContainText("陣列（Array）");
  await expect(panel.locator("li")).toContainText("有待複習重點");
  await expect(page.getByRole("checkbox").first()).toBeChecked();
  await expect(page.getByRole("checkbox").nth(1)).not.toBeChecked();
  await page.getByRole("button", { name: "套用推薦", exact: true }).click();
  await expect(page.getByRole("checkbox").first()).not.toBeChecked();
  await expect(page.getByRole("checkbox").nth(1)).toBeChecked();
  await page.getByRole("combobox", { name: "推薦方式", exact: true }).selectOption("path");
  await expect(panel.locator("li").first()).toContainText("目前正在學習");
  expect(fixture.forbiddenMutations).toEqual([]);
});

test("no weaknesses, all mastered and incompatible progress never invent recommendations", async ({ page }) => {
  await mockCards(page, { withProgress: true });
  const progress = structuredClone(baseProgress);
  await page.route(`**/v1/study-sessions/${sessionId}/progress`, (route) => json(route, progress));
  await page.goto(mapPath + "/create-cards");
  await page.getByRole("button", { name: "幫我選卡", exact: true }).click();
  await expect(page.getByText(/目前沒有待複習的弱點/)).toBeVisible();
  await expect(page.getByRole("button", { name: "套用推薦", exact: true })).toBeDisabled();
  await expect(page.getByRole("checkbox").first()).toBeChecked();
  await page.getByRole("button", { name: "幫我選卡", exact: true }).click();
  progress.concept_states.forEach((state) => { state.status = "mastered"; });
  await page.getByRole("button", { name: "幫我選卡", exact: true }).click();
  await page.getByRole("combobox", { name: "推薦方式", exact: true }).selectOption("path");
  await expect(page.getByText(/概念都已掌握/)).toBeVisible();
  await expect(page.getByRole("button", { name: "套用推薦", exact: true })).toBeDisabled();
  await page.getByRole("button", { name: "幫我選卡", exact: true }).click();
  progress.knowledge_structure_revision = `knowledge-structure:sha256:${"f".repeat(64)}`;
  await page.getByRole("button", { name: "幫我選卡", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("無法讀取推薦所需的學習進度");
  await expect(page.getByRole("button", { name: "套用推薦", exact: true })).toBeDisabled();
  await expect(page.getByRole("checkbox").first()).toBeChecked();
  progress.knowledge_structure_revision = structureRevision;
  await page.getByRole("button", { name: "重新讀取進度", exact: true }).click();
  await expect(page.getByRole("alert")).toHaveCount(0);
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
  await page.getByText("查看來源", { exact: true }).click();
  await page.getByRole("button", { name: "A.pdf · PDF 第 1 頁", exact: true }).click();
  await expect(page.getByRole("link", { name: "開啟 PDF 來源頁" })).toHaveAttribute("href", `/v1/artifacts/${artifactId}#page=1`);
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "B.pdf · PDF 第 1 頁", exact: true }).click();
  await expect(page.getByRole("alert")).toBeVisible();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  failed = false;
  await page.getByRole("button", { name: "B.pdf · PDF 第 1 頁", exact: true }).click();
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
  await page.getByRole("button", { name: "教材庫", exact: true }).click();
  const saved = page.waitForResponse((r) => r.url().endsWith("/card-sets") && r.request().method() === "POST");
  releaseSave();
  await saved;
  await expect(page).toHaveURL(/\/materials$/);
  await page.getByRole("button", { name: "概念卡", exact: true }).click();
  await page.getByRole("button", { name: "開始複習", exact: true }).click();
  await page.getByRole("button", { name: "翻面", exact: true }).click();
  await page.getByText("查看來源", { exact: true }).click();
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
  await expect(page.getByRole("heading", { name: "我的概念卡", exact: true })).toBeVisible();
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
  await page.getByText("查看來源", { exact: true }).click();
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
  const tabs = page.getByRole('navigation', { name: '教材學習內容' });
  await expect(tabs).toBeVisible();
  await expect(tabs.getByRole('button', { name: '概念卡', exact: true })).toHaveAttribute('aria-current', 'page');
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
