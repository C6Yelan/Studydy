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

test("slash opens site search and a concept opens its detail on the map", async ({ page }) => {
  await mockKnowledgeMapApi(page);
  const queries = await mockSearch(page);
  // 全站搜尋屬於教材外的頁首；教材內由地圖搜尋負責。
  await page.goto("/materials");
  await expect(page.getByRole("button", { name: "搜尋概念、教材、卡組或 Podcast" })).toBeVisible();
  await page.locator("body").press("/");
  const dialog = page.getByRole("dialog", { name: "全站搜尋" });
  await expect(dialog).toBeVisible();
  const input = dialog.getByRole("searchbox", { name: "搜尋概念、教材、卡組或 Podcast" });
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
  await page.getByRole("button", { name: "搜尋概念、教材、卡組或 Podcast" }).click();
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
  await page.goto("/");
  await page.getByRole("button", { name: "搜尋概念、教材、卡組或 Podcast" }).click();
  const dialog = page.getByRole("dialog", { name: "全站搜尋" });
  await dialog.getByRole("searchbox").fill("zzz");
  await expect(dialog.getByRole("status")).toHaveText("找不到「zzz」，試試其他關鍵字。");
  await dialog.getByRole("searchbox").fill("array");
  await expect(dialog.getByRole("alert")).toBeVisible();
  await dialog.getByRole("button", { name: "重新搜尋" }).click();
  await dialog.getByRole("region", { name: "教材" }).getByRole("button", { name: /Array 講義草稿/ }).click();
  await expect.poll(() => new URL(page.url()).pathname).toBe("/materials");
  // 欄位有文字時 Escape 也直接關閉，並把焦點還給觸發按鈕。
  await page.getByRole("button", { name: "搜尋概念、教材、卡組或 Podcast" }).click();
  await dialog.getByRole("searchbox").fill("array");
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
  await expect(page.getByRole("button", { name: "搜尋概念、教材、卡組或 Podcast" })).toBeFocused();
});

test("site search also lists matching card sets and podcasts and opens them", async ({ page }) => {
  await mockKnowledgeMapApi(page);
  await mockSearch(page, (query) => ({ ...searchResult(query, []), materials: [] }));
  const cardSetId = "77777777-7777-4777-8777-777777777777";
  const podcastId = "88888888-8888-4888-8888-888888888888";
  const concept = `concept:sha256:${"c".repeat(64)}`;
  const common = { material_id: materialId, material_name: "Data structures.pdf", knowledge_structure_revision: structureRevision, version: 1, created_at: "2026-10-20T00:00:00Z", is_current_revision: true };
  await page.route("**/v1/card-sets", (route) => route.fulfill({ json: { schema: "card-set-list/v1", card_sets: [
    { ...common, card_set_id: cardSetId, name: "陣列複習", card_count: 3, concept_ids: [concept],
      generation: { status: "ready", stage: "complete", completed: 1, total: 1, retained_original: 0, cached: 0, error_code: null } },
  ] } }));
  await page.route("**/v1/podcasts", (route) => route.fulfill({ json: { schema: "podcast-list/v1", podcasts: [
    { ...common, podcast_id: podcastId, name: "陣列 Podcast", delivery: "solo", video_enabled: false, concept_ids: [concept], status: "ready", error_code: null, episode_count: 2, completed_episodes: 2, duration_seconds: 900 },
  ] } }));
  await page.goto("/");
  await page.getByRole("button", { name: "搜尋概念、教材、卡組或 Podcast" }).click();
  const dialog = page.getByRole("dialog", { name: "全站搜尋" });
  await dialog.getByRole("searchbox").fill("陣列");
  await expect(dialog.getByRole("region", { name: "卡組" }).getByRole("button")).toHaveText(["陣列複習3 張卡片 · Data structures.pdf"]);
  await expect(dialog.getByRole("region", { name: "Podcast" }).getByRole("button")).toHaveText(["陣列 Podcast2 集 · Data structures.pdf"]);
  // 所屬教材名稱也能找到。
  await dialog.getByRole("searchbox").fill("data struct");
  await expect(dialog.getByRole("region", { name: "卡組" }).getByRole("button")).toHaveCount(1);
  await dialog.getByRole("region", { name: "卡組" }).getByRole("button").click();
  await expect(dialog).toBeHidden();
  await expect.poll(() => new URL(page.url()).pathname).toBe(`/materials/${materialId}/concept-cards/${cardSetId}`);
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
  await page.getByRole("button", { name: "個人檔案：尚未設定名稱" }).click();
  await expect(page).toHaveURL(/\/profile$/);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("尚未設定名稱");
  await expect(page.getByRole("button", { name: "個人檔案：尚未設定名稱" })).toHaveAttribute("aria-current", "page");
  const settings = page.getByRole("region", { name: "帳號設定" });
  const field = settings.getByRole("textbox", { name: "顯示名稱" });
  await field.fill("一".repeat(21));
  await expect(settings.getByText("名稱最多 20 個字，目前 21 個字。")).toBeVisible();
  await expect(settings.getByRole("button", { name: "儲存名稱" })).toBeDisabled();
  await field.fill("  以安 ");
  await settings.getByRole("button", { name: "儲存名稱" }).click();
  await expect(settings.getByRole("region", { name: "個人名稱" }).getByRole("status")).toHaveText("名稱已更新。");
  expect(posts).toEqual([{ origin: new URL(page.url()).origin, body: { schema: "learner-profile-update/v1", display_name: "以安" } }]);
  // 頁首與個人檔案標題立即更新。
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("以安");
  await expect(page.getByRole("button", { name: "個人檔案：以安" })).toBeVisible();
  await page.getByRole("navigation", { name: "主要導覽" }).getByRole("link", { name: "首頁" }).click();
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(/^(早安|午安|晚安)，以安！今天想探索哪座島？$/);
  // 清空即移除名稱，回到原本的問候。
  await page.getByRole("button", { name: "個人檔案：以安" }).click();
  await settings.getByRole("textbox", { name: "顯示名稱" }).fill("");
  await settings.getByRole("button", { name: "儲存名稱" }).click();
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("尚未設定名稱");
  expect(posts.at(-1)!.body).toEqual({ schema: "learner-profile-update/v1", display_name: null });
  await page.getByRole("navigation", { name: "主要導覽" }).getByRole("link", { name: "首頁" }).click();
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("歡迎回來！今天想探索哪座島？");
});

test("profile save failure keeps the entered name and shows the error", async ({ page }) => {
  await mockKnowledgeMapApi(page);
  await page.route("**/v1/profile", (route) => route.request().method() === "POST"
    ? route.fulfill({ status: 400, json: { schema: "api-error/v1", request_id: otherMaterial, reason_code: "REQUEST_INVALID", retryable: false, message: "x" } })
    : route.fulfill({ json: { schema: "learner-profile/v1", display_name: null } }));
  await page.goto(mapPath);
  await page.getByRole("button", { name: "個人檔案：尚未設定名稱" }).click();
  const settings = page.getByRole("region", { name: "帳號設定" });
  await settings.getByRole("textbox", { name: "顯示名稱" }).fill("以安");
  await settings.getByRole("button", { name: "儲存名稱" }).click();
  await expect(settings.getByRole("alert")).toBeVisible();
  await expect(settings.getByRole("textbox", { name: "顯示名稱" })).toHaveValue("以安");
  // 學習活動讀不到時只有統計區顯示錯誤，帳號設定照常可用。
  await expect(page.getByText("無法讀取學習統計")).toBeVisible();
});

function apiError(reason: string, status: number, retryable = false) {
  return { status, json: { schema: "api-error/v1", request_id: otherMaterial, reason_code: reason, retryable, message: "Request could not be completed." } };
}

// 第一筆是這個裝置；其餘依最後使用時間由新到舊，最後一筆是沒有標籤的舊登入。
function deviceSessions(count: number) {
  const now = Date.now();
  const labels = ["Chrome · Windows", "Safari · iPhone", "Firefox · macOS", "Edge · Windows", "Chrome · Android"];
  return Array.from({ length: count }, (_, index) => ({
    session_id: `00000000-0000-4000-8000-${String(index + 1).padStart(12, "0")}`,
    device_label: index > 0 && index === count - 1 ? null : labels[index % labels.length],
    created_at: new Date(now - (index + 1) * 864e5).toISOString(),
    last_used_at: new Date(now - index * 3 * 36e5).toISOString(),
    current: index === 0,
  }));
}

async function mockAccount(page: Page, initialSessions = 3) {
  let sessions = deviceSessions(initialSessions);
  const view = () => ({ schema: "account-sessions/v2", sessions });
  const writes: { path: string; method: string; origin: string | undefined; body: unknown }[] = [];
  await page.route("**/v1/profile", (route) => route.fulfill({ json: { schema: "learner-profile/v1", display_name: "以安" } }));
  await page.route("**/v1/account/sessions", (route) => route.fulfill({ json: view() }));
  await page.route("**/v1/account/storage", (route) => route.fulfill({ json:
    { schema: "account-storage/v1", used_bytes: 5368709120, limit_bytes: 5368709120, remaining_bytes: 0 } }));
  await page.route("**/v1/account/sessions/*", (route) => {
    const target = new URL(route.request().url()).pathname.split("/").at(-1)!;
    writes.push({ path: target, method: route.request().method(), origin: route.request().headers().origin, body: route.request().postData() });
    sessions = target === "others" ? sessions.slice(0, 1) : sessions.filter((item) => item.session_id !== target);
    return route.fulfill({ json: view() });
  });
  const passwordReplies: (() => Parameters<import("@playwright/test").Route["fulfill"]>[0])[] = [];
  await page.route("**/v1/account/password", (route) => {
    writes.push({ path: "password", method: route.request().method(), origin: route.request().headers().origin, body: route.request().postDataJSON() });
    const reply = passwordReplies.shift();
    if (reply) return route.fulfill(reply());
    sessions = sessions.slice(0, 1);
    return route.fulfill({ json: view() });
  });
  return { writes, passwordReplies };
}

test("password change validates input, reports a wrong current password and then succeeds", async ({ page }) => {
  await mockKnowledgeMapApi(page);
  const { writes, passwordReplies } = await mockAccount(page);
  passwordReplies.push(() => apiError("CURRENT_PASSWORD_INVALID", 400));
  passwordReplies.push(() => apiError("TOO_MANY_ATTEMPTS", 429, true));
  await page.goto("/profile");
  const dialog = page.getByRole("region", { name: "帳號設定" });
  const section = dialog.getByRole("region", { name: "修改密碼" });
  await expect(dialog.getByRole("region", { name: "個人名稱" })).toBeVisible();
  // 帳號空間已滿：顯示已用、剩餘，以及哪些動作會被擋下。
  const storage = page.getByRole("region", { name: "帳號空間" });
  await expect(storage).toContainText("已用 5 GB／上限 5 GB · 剩餘 0 B");
  await expect(storage).toContainText("無法上傳教材、建立 Podcast 或生成影片");
  // 登入裝置是帳號設定之外的獨立卡片。
  // 三個登入先顯示兩個，其餘收合。
  await expect(page.getByRole("region", { name: "登入裝置" }).getByRole("listitem")).toHaveCount(2);
  await expect(page.getByRole("button", { name: "顯示其他 1 個裝置" })).toBeVisible();
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
  await expect(page.getByRole("region", { name: "登入裝置" }).getByRole("listitem")).toHaveText([/Chrome · Windows這個裝置/]);
  await expect(page.getByRole("button", { name: "登出其他裝置" })).toBeDisabled();
  expect(writes).toHaveLength(3);
  for (const write of writes) {
    expect(write).toMatchObject({ path: "password", method: "POST", origin: new URL(page.url()).origin });
    expect(write.body).toEqual({ schema: "password-change/v1", current_password: "old synthetic secret", new_password: "new synthetic secret 1" });
  }
  // 離開再回到個人檔案時不保留上次輸入與訊息。
  await page.getByRole("navigation", { name: "主要導覽" }).getByRole("link", { name: "首頁" }).click();
  await page.getByRole("button", { name: "個人檔案：以安" }).click();
  await expect(section.getByRole("status")).toHaveCount(0);
  await expect(section.getByLabel("新密碼", { exact: true })).toHaveValue("");
});

test("device list signs out one device after confirming in place and expands long lists", async ({ page }) => {
  await mockKnowledgeMapApi(page);
  const { writes } = await mockAccount(page, 6);
  await page.goto("/profile");
  const devices = page.getByRole("region", { name: "登入裝置" });
  const rows = devices.getByRole("listitem");
  // 這個裝置排第一且不能在這裡登出；最後使用時間為相對時間，沒有標籤的顯示未知裝置。
  await expect(rows).toHaveText([/^Chrome · Windows這個裝置使用中 · /, /^Safari · iPhone最後使用 3 小時前 · /]);
  await expect(rows.first().getByRole("button")).toHaveCount(0);
  await devices.getByRole("button", { name: "顯示其他 4 個裝置" }).click();
  await expect(rows).toHaveCount(6);
  await expect(rows.nth(2)).toContainText("Firefox · macOS最後使用 6 小時前");
  await expect(rows.last()).toContainText("未知裝置");
  await devices.getByRole("button", { name: "收合" }).click();
  await expect(rows).toHaveCount(2);

  const opener = devices.getByRole("button", { name: "登出「Safari · iPhone」" });
  await opener.click();
  const confirm = devices.getByRole("group", { name: "登出「Safari · iPhone」確認" });
  await expect(confirm.getByRole("button", { name: "取消" })).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(confirm).toHaveCount(0);
  await expect(opener).toBeFocused();
  expect(writes).toEqual([]);
  await opener.click();
  await confirm.getByRole("button", { name: "登出", exact: true }).click();
  await expect(devices.getByRole("status")).toHaveText("已登出「Safari · iPhone」。");
  await expect(devices).not.toContainText("Safari · iPhone最後使用");
  await expect(rows).toHaveText([/這個裝置/, /Firefox · macOS/]);
  await expect(devices.getByRole("button", { name: "顯示其他 3 個裝置" })).toBeVisible();
  expect(writes).toEqual([{ path: "00000000-0000-4000-8000-000000000002", method: "DELETE", origin: new URL(page.url()).origin, body: null }]);
  // 個別登出失敗時留在確認列並顯示原因。
  await page.route("**/v1/account/sessions/*", (route) => route.fulfill(apiError("RESOURCE_NOT_FOUND", 404)));
  await devices.getByRole("button", { name: "登出「Firefox · macOS」" }).click();
  await devices.getByRole("group", { name: "登出「Firefox · macOS」確認" }).getByRole("button", { name: "登出", exact: true }).click();
  await expect(devices.getByRole("alert")).toHaveText("找不到這筆資料，或你沒有權限讀取。");
  await expect(rows).toHaveCount(2);
});

test("sign out other devices keeps this device and still works when the list cannot be read", async ({ page }) => {
  await mockKnowledgeMapApi(page);
  const { writes } = await mockAccount(page, 2);
  await page.goto(mapPath);
  await page.getByRole("button", { name: "個人檔案：以安" }).click();
  const devices = page.getByRole("region", { name: "登入裝置" });
  await expect(devices.getByRole("listitem")).toHaveCount(2);
  await devices.getByRole("button", { name: "登出其他裝置" }).click();
  await expect(devices.getByRole("status")).toHaveText("已登出其他裝置，目前只有這個裝置登入中。");
  await expect(devices.getByRole("listitem")).toHaveText([/這個裝置/]);
  expect(writes).toEqual([{ path: "others", method: "DELETE", origin: new URL(page.url()).origin, body: null }]);
  await expect(page).toHaveURL(/\/profile$/);

  await page.unroute("**/v1/account/sessions");
  await page.route("**/v1/account/sessions", (route) => route.fulfill(apiError("STORAGE_UNAVAILABLE", 503, true)));
  await page.unroute("**/v1/account/sessions/*");
  await page.route("**/v1/account/sessions/*", (route) => route.fulfill(apiError("STORAGE_UNAVAILABLE", 503, true)));
  await page.reload();
  await expect(devices).toContainText("無法讀取登入裝置清單；仍可讓其他裝置上的登入全部失效，這個裝置會維持登入。");
  await expect(devices.getByRole("listitem")).toHaveCount(0);
  await devices.getByRole("button", { name: "登出其他裝置" }).click();
  await expect(devices.getByRole("alert")).toHaveText("資料服務暫時無法使用，請稍後再試。");
  await expect(page.getByRole("region", { name: "帳號設定" })).toBeVisible();
});

test("profile fits a 390px phone and jumps to account settings", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await mockKnowledgeMapApi(page);
  await mockAccount(page);
  await page.goto("/");
  await page.getByRole("button", { name: "個人檔案：以安" }).click();
  const settings = page.getByRole("region", { name: "帳號設定" });
  // 三個登入先顯示兩個，其餘收合。
  await expect(page.getByRole("region", { name: "登入裝置" }).getByRole("listitem")).toHaveCount(2);
  await expect(page.getByRole("button", { name: "顯示其他 1 個裝置" })).toBeVisible();
  const box = (await settings.boundingBox())!;
  expect(box.x).toBeGreaterThanOrEqual(0);
  expect(box.x + box.width).toBeLessThanOrEqual(390);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.getByRole("button", { name: "帳號設定", exact: true }).click();
  await expect(settings.getByRole("heading", { name: "帳號設定" })).toBeInViewport();
  const devicesButton = page.getByRole("button", { name: "登出其他裝置" });
  await devicesButton.scrollIntoViewIfNeeded();
  await expect(devicesButton).toBeInViewport();
});
