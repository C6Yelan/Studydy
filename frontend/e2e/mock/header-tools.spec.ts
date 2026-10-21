import { expect, test, type Page } from "@playwright/test";
import {
  materialId,
  runId,
  structureRevision,
  firstConcept,
  secondConcept,
  mockKnowledgeMapApi,
} from "../fixtures/knowledge-map";

const mapPath = `/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}`;
const otherMaterial = "66666666-6666-4666-8666-666666666666";

function searchResult(query: string, concepts = [{ id: secondConcept, label: "Array" }]) {
  return {
    schema: "library-search/v1", query, concepts_truncated: concepts.length > 1,
    materials: [{ material_id: otherMaterial, display_name: "Array 講義草稿.pdf", run_id: null, knowledge_structure_revision: null }],
    concepts: concepts.map(({ id, label }) => ({
      material_id: materialId, material_name: "Data structures.pdf", run_id: runId,
      knowledge_structure_revision: structureRevision, concept_id: id, label, claim_count: 1,
    })),
  };
}

async function mockSearch(page: Page, respond = (query: string) => searchResult(query)) {
  const queries: string[] = [];
  await page.route("**/v1/search?*", (route) => {
    const query = new URL(route.request().url()).searchParams.get("q")!;
    queries.push(query);
    return route.fulfill({ json: respond(query.trim()) });
  });
  return queries;
}

test("slash opens site search and a concept on the current map opens its detail", async ({ page }) => {
  await mockKnowledgeMapApi(page);
  const queries = await mockSearch(page);
  await page.goto(mapPath);
  await expect(page.getByRole("dialog", { name: "概念詳情" })).toHaveCount(0);
  await page.locator("body").press("/");
  const dialog = page.getByRole("dialog", { name: "全站搜尋" });
  await expect(dialog).toBeVisible();
  const input = dialog.getByRole("searchbox", { name: "搜尋概念或教材" });
  await expect(input).toBeFocused();
  await input.fill("arr");
  await expect(dialog.getByRole("region", { name: "概念" }).getByRole("button")).toHaveText(["ArrayData structures.pdf · 1 個重點"]);
  // 停止輸入後才送出一次查詢。
  expect(queries).toEqual(["arr"]);
  await dialog.getByRole("region", { name: "概念" }).getByRole("button", { name: /^Array/ }).click();
  await expect(dialog).toBeHidden();
  await expect(page.getByRole("dialog", { name: "概念詳情" }).getByRole("heading", { name: "Array" })).toBeVisible();
  await expect.poll(() => new URL(page.url()).pathname).toBe(mapPath);
});

test("site search from home opens the map at the chosen concept and Enter picks the first hit", async ({ page }) => {
  await mockKnowledgeMapApi(page);
  await mockSearch(page, (query) => searchResult(query, [{ id: firstConcept, label: "Stack" }, { id: secondConcept, label: "Array" }]));
  await page.goto("/");
  await page.getByRole("button", { name: "搜尋概念或教材" }).click();
  const dialog = page.getByRole("dialog", { name: "全站搜尋" });
  await dialog.getByRole("searchbox").fill("a");
  await expect(dialog.getByText("只顯示前 2 個概念，輸入更多關鍵字可縮小範圍。")).toBeVisible();
  await dialog.getByRole("searchbox").press("Enter");
  await expect.poll(() => new URL(page.url()).pathname).toBe(mapPath);
  await expect(page.getByRole("dialog", { name: "概念詳情" }).getByRole("heading", { name: "Stack" })).toBeVisible();
});

test("site search shows empty and failed results, retries, and opens unbuilt materials in the library", async ({ page }) => {
  await mockKnowledgeMapApi(page);
  let fail = true;
  await page.route("**/v1/search?*", (route) => {
    const query = new URL(route.request().url()).searchParams.get("q")!.trim();
    if (query === "zzz") return route.fulfill({ json: { ...searchResult(query, []), materials: [] } });
    if (fail) {
      fail = false;
      return route.fulfill({ status: 503, json: { schema: "api-error/v1", request_id: otherMaterial, reason_code: "STORAGE_UNAVAILABLE", retryable: true, message: "x" } });
    }
    return route.fulfill({ json: searchResult(query) });
  });
  await page.goto(mapPath);
  await page.getByRole("button", { name: "搜尋概念或教材" }).click();
  const dialog = page.getByRole("dialog", { name: "全站搜尋" });
  await dialog.getByRole("searchbox").fill("zzz");
  await expect(dialog.getByRole("status")).toHaveText("找不到「zzz」，試試其他關鍵字。");
  await dialog.getByRole("searchbox").fill("array");
  await expect(dialog.getByRole("alert")).toBeVisible();
  await dialog.getByRole("button", { name: "重新搜尋" }).click();
  await dialog.getByRole("region", { name: "教材" }).getByRole("button", { name: /Array 講義草稿/ }).click();
  await expect.poll(() => new URL(page.url()).pathname).toBe("/materials");
  // Escape 關閉並把焦點還給觸發按鈕。
  await page.getByRole("button", { name: "搜尋概念或教材" }).click();
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
  await expect(page.getByRole("button", { name: "搜尋概念或教材" })).toBeFocused();
});

test("display name is saved with Origin, validated, and greets on the home page", async ({ page }) => {
  await mockKnowledgeMapApi(page);
  let stored: string | null = null;
  const posts: { origin: string | undefined; body: unknown }[] = [];
  await page.route("**/v1/profile", (route) => {
    if (route.request().method() === "POST") {
      const body = route.request().postDataJSON();
      posts.push({ origin: route.request().headers().origin, body });
      stored = body.display_name;
    }
    return route.fulfill({ json: { schema: "learner-profile/v1", display_name: stored } });
  });
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("歡迎回來！今天想探索哪座島？");
  const trigger = page.getByRole("button", { name: "帳號設定：尚未設定名稱" });
  await trigger.click();
  const dialog = page.getByRole("dialog", { name: "帳號設定" });
  const field = dialog.getByRole("textbox", { name: "顯示名稱" });
  await field.fill("一".repeat(21));
  await expect(dialog.getByText("名稱最多 20 個字，目前 21 個字。")).toBeVisible();
  await expect(dialog.getByRole("button", { name: "儲存" })).toBeDisabled();
  await field.fill("  以安 ");
  await dialog.getByRole("button", { name: "儲存" }).click();
  await expect(dialog).toBeHidden();
  expect(posts).toEqual([{ origin: new URL(page.url()).origin, body: { schema: "learner-profile-update/v1", display_name: "以安" } }]);
  await expect(page.getByRole("button", { name: "帳號設定：以安" })).toBeFocused();
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(/^(早安|午安|晚安)，以安！今天想探索哪座島？$/);
  // 清空即移除名稱，回到原本的問候。
  await page.getByRole("button", { name: "帳號設定：以安" }).click();
  await dialog.getByRole("textbox", { name: "顯示名稱" }).fill("");
  await dialog.getByRole("button", { name: "儲存" }).click();
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("歡迎回來！今天想探索哪座島？");
  expect(posts.at(-1)!.body).toEqual({ schema: "learner-profile-update/v1", display_name: null });
});

test("profile save failure keeps the dialog open with the entered name", async ({ page }) => {
  await mockKnowledgeMapApi(page);
  await page.route("**/v1/profile", (route) => route.request().method() === "POST"
    ? route.fulfill({ status: 400, json: { schema: "api-error/v1", request_id: otherMaterial, reason_code: "REQUEST_INVALID", retryable: false, message: "x" } })
    : route.fulfill({ json: { schema: "learner-profile/v1", display_name: null } }));
  await page.goto(mapPath);
  await page.getByRole("button", { name: "帳號設定：尚未設定名稱" }).click();
  const dialog = page.getByRole("dialog", { name: "帳號設定" });
  await dialog.getByRole("textbox", { name: "顯示名稱" }).fill("以安");
  await dialog.getByRole("button", { name: "儲存" }).click();
  await expect(dialog.getByRole("alert")).toBeVisible();
  await expect(dialog.getByRole("textbox", { name: "顯示名稱" })).toHaveValue("以安");
});
