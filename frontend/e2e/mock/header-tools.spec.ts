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
  await expect(dialog.getByRole("region", { name: "概念" }).getByRole("button")).toHaveText(["Array1 個重點 · Data structures.pdf"]);
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
  // 欄位有文字時 Escape 也直接關閉，並把焦點還給觸發按鈕。
  await page.getByRole("button", { name: "搜尋概念或教材" }).click();
  await dialog.getByRole("searchbox").fill("array");
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

function apiError(reason: string, status: number, retryable = false) {
  return { status, json: { schema: "api-error/v1", request_id: otherMaterial, reason_code: reason, retryable, message: "Request could not be completed." } };
}

async function mockAccount(page: Page, initialSessions = 3) {
  let active = initialSessions;
  const writes: { path: string; method: string; origin: string | undefined; body: unknown }[] = [];
  await page.route("**/v1/profile", (route) => route.fulfill({ json: { schema: "learner-profile/v1", display_name: "以安" } }));
  await page.route("**/v1/account/sessions", (route) => route.fulfill({ json: { schema: "account-sessions/v1", active_sessions: active } }));
  await page.route("**/v1/account/sessions/others", (route) => {
    writes.push({ path: "others", method: route.request().method(), origin: route.request().headers().origin, body: route.request().postData() });
    active = 1;
    return route.fulfill({ json: { schema: "account-sessions/v1", active_sessions: 1 } });
  });
  const passwordReplies: (() => Parameters<import("@playwright/test").Route["fulfill"]>[0])[] = [];
  await page.route("**/v1/account/password", (route) => {
    writes.push({ path: "password", method: route.request().method(), origin: route.request().headers().origin, body: route.request().postDataJSON() });
    const reply = passwordReplies.shift();
    if (reply) return route.fulfill(reply());
    active = 1;
    return route.fulfill({ json: { schema: "account-sessions/v1", active_sessions: 1 } });
  });
  return { writes, passwordReplies };
}

test("password change validates input, reports a wrong current password and then succeeds", async ({ page }) => {
  await mockKnowledgeMapApi(page);
  const { writes, passwordReplies } = await mockAccount(page);
  passwordReplies.push(() => apiError("CURRENT_PASSWORD_INVALID", 400));
  passwordReplies.push(() => apiError("TOO_MANY_ATTEMPTS", 429, true));
  await page.goto("/");
  await page.getByRole("button", { name: "帳號設定：以安" }).click();
  const dialog = page.getByRole("dialog", { name: "帳號設定" });
  const section = dialog.getByRole("region", { name: "修改密碼" });
  await expect(dialog.getByRole("region", { name: "個人名稱" })).toBeVisible();
  await expect(dialog.getByRole("region", { name: "登入裝置" })).toContainText("目前有 3 個有效登入（含這個裝置）。");
  const submit = section.getByRole("button", { name: "更新密碼" });
  await expect(submit).toBeDisabled();
  await section.getByLabel("目前密碼").fill("old synthetic secret");
  await section.getByLabel("新密碼", { exact: true }).fill("too short");
  await expect(section.getByText("新密碼需為 15–128 個字元，目前 9 個字元。")).toBeVisible();
  await expect(submit).toBeDisabled();
  await section.getByLabel("新密碼", { exact: true }).fill("new synthetic secret 1");
  await section.getByLabel("確認新密碼").fill("new synthetic secret 2");
  await expect(section.getByText("兩次輸入的新密碼不一致。")).toBeVisible();
  await expect(submit).toBeDisabled();
  await section.getByLabel("確認新密碼").fill("new synthetic secret 1");
  await submit.click();
  // 目前密碼錯誤：清空該欄並聚焦，新密碼保留。
  await expect(section.getByRole("alert")).toHaveText("目前密碼不正確。");
  await expect(section.getByLabel("目前密碼")).toHaveValue("");
  await expect(section.getByLabel("目前密碼")).toBeFocused();
  await expect(section.getByLabel("確認新密碼")).toHaveValue("new synthetic secret 1");
  await section.getByLabel("目前密碼").fill("old synthetic secret");
  await submit.click();
  await expect(section.getByRole("alert")).toHaveText("嘗試次數過多，請 15 分鐘後再試。");
  await submit.click();
  await expect(section.getByRole("status")).toHaveText("密碼已更新，其他裝置已登出；這個裝置維持登入。");
  for (const label of ["目前密碼", "確認新密碼"]) await expect(section.getByLabel(label)).toHaveValue("");
  await expect(dialog.getByRole("region", { name: "登入裝置" })).toContainText("目前只有這個裝置登入中。");
  await expect(dialog.getByRole("button", { name: "登出其他裝置" })).toBeDisabled();
  expect(writes).toHaveLength(3);
  for (const write of writes) {
    expect(write).toMatchObject({ path: "password", method: "POST", origin: new URL(page.url()).origin });
    expect(write.body).toEqual({ schema: "password-change/v1", current_password: "old synthetic secret", new_password: "new synthetic secret 1" });
  }
  // 重新開啟時不保留上次輸入與訊息。
  await dialog.getByRole("button", { name: "關閉帳號設定" }).click();
  await page.getByRole("button", { name: "帳號設定：以安" }).click();
  await expect(section.getByRole("status")).toHaveCount(0);
  await expect(section.getByLabel("新密碼", { exact: true })).toHaveValue("");
});

test("sign out other devices keeps this device and hides the count when it cannot be read", async ({ page }) => {
  await mockKnowledgeMapApi(page);
  const { writes } = await mockAccount(page, 2);
  await page.goto(mapPath);
  await page.getByRole("button", { name: "帳號設定：以安" }).click();
  const dialog = page.getByRole("dialog", { name: "帳號設定" });
  const devices = dialog.getByRole("region", { name: "登入裝置" });
  await expect(devices).toContainText("目前有 2 個有效登入（含這個裝置）。");
  await devices.getByRole("button", { name: "登出其他裝置" }).click();
  await expect(devices.getByRole("status")).toHaveText("已登出其他裝置，目前只有這個裝置登入中。");
  await expect(devices).toContainText("目前只有這個裝置登入中。");
  expect(writes).toEqual([{ path: "others", method: "DELETE", origin: new URL(page.url()).origin, body: null }]);
  await expect(page).toHaveURL(new RegExp(`${mapPath.replaceAll("/", "\\/")}$`));

  await dialog.getByRole("button", { name: "關閉帳號設定" }).click();
  await page.unroute("**/v1/account/sessions");
  await page.route("**/v1/account/sessions", (route) => route.fulfill(apiError("STORAGE_UNAVAILABLE", 503, true)));
  await page.unroute("**/v1/account/sessions/others");
  await page.route("**/v1/account/sessions/others", (route) => route.fulfill(apiError("STORAGE_UNAVAILABLE", 503, true)));
  await page.getByRole("button", { name: "帳號設定：以安" }).click();
  await expect(devices).toContainText("可讓其他裝置上的登入全部失效，這個裝置會維持登入。");
  await expect(devices).not.toContainText(/\d+ 個有效登入/);
  await devices.getByRole("button", { name: "登出其他裝置" }).click();
  await expect(devices.getByRole("alert")).toHaveText("資料服務暫時無法使用，請稍後再試。");
  await expect(dialog).toBeVisible();
});

test("account settings fit a 390px phone without horizontal scroll", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await mockKnowledgeMapApi(page);
  await mockAccount(page);
  await page.goto("/");
  await page.getByRole("button", { name: "帳號設定：以安" }).click();
  const dialog = page.getByRole("dialog", { name: "帳號設定" });
  await expect(dialog.getByRole("region", { name: "登入裝置" })).toContainText("3 個有效登入");
  const box = (await dialog.boundingBox())!;
  expect(box.x).toBeGreaterThanOrEqual(0);
  expect(box.x + box.width).toBeLessThanOrEqual(390);
  expect(await dialog.evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true);
  const devicesButton = dialog.getByRole("button", { name: "登出其他裝置" });
  await devicesButton.scrollIntoViewIfNeeded();
  await expect(devicesButton).toBeInViewport();
  await page.screenshot({ path: test.info().outputPath("account-settings-390.png") });
});
