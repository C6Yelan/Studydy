import { expect, test, type Page } from "@playwright/test";
import { artifactId, materialId, runId, sessionId, structureRevision, structureView, mockKnowledgeMapApi, json } from "../fixtures/knowledge-map";

const cardSetId = "55555555-5555-4555-8555-555555555555";
const mapPath = `/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}`;

async function mockCards(page: Page, { saved = false, long = false } = {}) {
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
  await mockKnowledgeMapApi(page, view);
  const material = {
    schema: "material-library-item/v1", material_id: materialId, source_artifact_id: artifactId,
    display_name: "資料結構講義.pdf", size_bytes: 100, created_at: "2026-10-02T00:00:00Z",
    head_revision: structureRevision, latest_attempt: null, study_sessions: [],
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
    return json(route, { ...summary(), schema: "card-set/v1", source_resolver: decodeURIComponent(view.source_resolver), status: view.status, excluded_pages: [], cards: view.concepts.filter((item) => selection.includes(item.concept_id)).map(({ concept_id, label, claims }) => ({ concept_id, label, claims })) });
  });
  return { requests, forbiddenMutations };
}

test("map creates a saved multi-card deck; keyboard, sources, shuffle and delete work", async ({ page }) => {
  const fixture = await mockCards(page);
  await page.goto(mapPath);
  await page.getByRole("button", { name: "建立概念卡", exact: true }).click();
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

test("sidebar entry can select a material; failed save retries the same intent", async ({ page }) => {
  const fixture = await mockCards(page);
  await page.goto("/concept-cards");
  await page.getByRole("button", { name: "建立卡組", exact: true }).click();
  await page.getByRole("button", { name: "資料結構講義.pdf", exact: true }).click();
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
  await expect(page.getByRole("button", { name: "重新命名", exact: true })).toHaveCount(0);
  await page.locator(".material-management-menu[open]").getByRole("button", { name: "管理卡組", exact: true }).click();
  await page.getByLabel("卡組名稱", { exact: true }).fill("重新命名的卡組");
  await page.getByRole("button", { name: "保存變更", exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`/concept-cards/${cardSetId}$`));
  await expect(page.getByRole("heading", { name: "重新命名的卡組", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "管理卡組", exact: true }).click();
  await page.getByRole("checkbox").nth(1).uncheck();
  await page.getByRole("button", { name: "保存變更", exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`/concept-cards/${cardSetId}$`));
  await expect(page.locator(".deck-count")).toHaveText("1 張卡片");
  await page.getByRole("button", { name: "管理卡組", exact: true }).click();
  await expect(page.getByRole("checkbox").nth(1)).not.toBeChecked();
  await page.getByRole("button", { name: "全選", exact: true }).click();
  await page.getByRole("button", { name: "保存變更", exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`/concept-cards/${cardSetId}$`));
  await page.reload();
  await expect(page.locator(".deck-count")).toHaveText("2 張卡片");
  await page.getByRole("button", { name: "管理卡組", exact: true }).click();
  await page.getByLabel("卡組名稱", { exact: true }).fill("不應保存");
  await page.getByRole("button", { name: "取消變更", exact: true }).click();
  await expect(page.getByRole("heading", { name: "重新命名的卡組", exact: true })).toBeVisible();
});

test("library layout matches materials and review uses the available content width", async ({ page }) => {
  await page.setViewportSize({ width: 1536, height: 960 });
  await mockCards(page, { saved: true });
  await page.goto("/materials");
  const materialSearch = await page.getByRole("searchbox").boundingBox();
  const materialHeading = await page.getByRole("heading", { name: "我的教材", exact: true }).boundingBox();
  await page.getByRole("button", { name: "概念卡", exact: true }).click();
  const cardSearch = await page.getByRole("searchbox").boundingBox();
  const cardHeading = await page.getByRole("heading", { name: "我的概念卡", exact: true }).boundingBox();
  expect(cardSearch!.x).toBe(materialSearch!.x);
  expect(cardSearch!.width).toBe(materialSearch!.width);
  expect(cardSearch!.y - cardHeading!.y).toBeCloseTo(materialSearch!.y - materialHeading!.y, 0);
  await page.screenshot({ path: "../.studydy-runtime/card-preview/library-aligned.png", fullPage: true });
  await page.getByRole("button", { name: "開始複習", exact: true }).click();
  await expect(page.locator(".flashcard")).toBeVisible();
  const content = await page.locator(".cards-study").boundingBox();
  const card = await page.locator(".flashcard").boundingBox();
  expect(card!.width / content!.width).toBeGreaterThan(.95);
  await page.screenshot({ path: "../.studydy-runtime/card-preview/study-wide.png", fullPage: true });
});
