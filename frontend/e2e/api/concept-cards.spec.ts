import { expect, test, type Page } from "@playwright/test";

test.skip(process.env.STUDYDY_E2E_CARDS !== "true", "Requires isolated card API/DB fixture");

async function login(page: Page) {
  await page.goto("/login");
  await page.getByLabel("Email", { exact: true }).fill("learner_test@example.com");
  await page.getByLabel("密碼", { exact: true }).fill("Synthetic test password 42");
  await page.getByRole("button", { name: "登入", exact: true }).click();
  await expect(page.getByRole("heading", { name: "歡迎回來！今天想探索哪座島？", exact: true })).toBeVisible();
}

// 教材與卡組內容不得保存在瀏覽器；這些操作只留下公開登入身分提示。
async function expectOnlyUiHints(page: Page) {
  await expect.poll(() => page.evaluate(() => Object.keys(localStorage).sort())).toEqual(["studydy.session-hint"]);
}

test("real API persists selected cards across fresh login and resolves original evidence", async ({ browser }) => {
  const first = await browser.newContext();
  const page = await first.newPage();
  await login(page);
  await page.getByRole("link", { name: "概念卡", exact: true }).click();
  await page.getByRole("button", { name: "建立卡組", exact: true }).click();
  await expect(page).toHaveURL(/\/concept-cards\/new$/);
  // 選擇教材是島嶼卡片清單，點選後進入該教材的建立卡組頁。
  await page.getByRole("group", { name: "選擇教材", exact: true }).getByRole("button", { name: /^Synthetic\.pdf/ }).click();
  await expect(page).toHaveURL(/\/create-cards$/);
  await page.getByLabel("卡組名稱", { exact: true }).fill("隔離測試卡組");
  await page.getByRole("button", { name: "保存並開始複習" }).click();
  await expect(page).toHaveURL(/\/concept-cards\/[0-9a-f-]+$/);
  await page.getByRole("button", { name: "翻到背面" }).click();
  await expect(page.locator(".visual-concept-card")).toHaveAttribute("title", "Stack｜概念卡");
  await expect(page.locator(".visual-concept-card")).toBeVisible();
  const savedPath = new URL(page.url()).pathname;
  await page.getByText("完整重點與來源", { exact: true }).click();
  await expect(page.locator(".flashcard-point")).toContainText("LIFO");
  await page.getByRole("button", { name: "Synthetic.pdf · 第 1 頁", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "教材來源" });
  await expect(dialog).toBeVisible();
  const href = await dialog.getByRole("link", { name: "開啟 PDF 來源頁" }).getAttribute("href");
  const source = await first.request.get(href!.split("#")[0]);
  expect(source.status()).toBe(200);
  expect((await source.body()).subarray(0, 4).toString()).toBe("%PDF");
  await page.keyboard.press("Escape");
  await page.goto("/materials");
  await page.getByRole("button", { name: "登出", exact: true }).click();
  await expect(page.getByRole("heading", { name: "登入您的帳戶" })).toBeVisible();
  await first.close();
  const second = await browser.newContext();
  const reopened = await second.newPage();
  await login(reopened);
  await reopened.getByRole("link", { name: "概念卡", exact: true }).click();
  await reopened.getByRole("button", { name: "開始複習", exact: true }).click();
  await expect(reopened).toHaveURL(new RegExp(`${savedPath}$`));
  await reopened.getByRole("button", { name: "翻到背面" }).click();
  await expect(reopened.locator(".visual-concept-card")).toHaveAttribute("title", "Stack｜概念卡");
  await reopened.getByRole("button", { name: "返回我的概念卡", exact: true }).click();
  await reopened.getByRole("button", { name: "管理卡組「隔離測試卡組」" }).click();
  await reopened.locator(".material-management-menu[open]").getByRole("button", { name: "管理卡組", exact: true }).click();
  await reopened.getByLabel("卡組名稱", { exact: true }).fill("隔離測試卡組已編輯");
  await reopened.getByRole("button", { name: "保存變更", exact: true }).click();
  await expect(reopened).toHaveURL(new RegExp(`${savedPath}$`));
  await reopened.reload();
  await expect(reopened.getByRole("heading", { name: "隔離測試卡組已編輯", exact: true })).toBeVisible();
  await reopened.getByRole('tablist', { name: '教材學習內容' }).getByRole('tab', { name: '概念卡', exact: true }).click();
  await reopened.getByRole("button", { name: "管理卡組「隔離測試卡組已編輯」" }).click();
  await reopened.getByRole("button", { name: "刪除卡組「隔離測試卡組已編輯」" }).click();
  await expect(reopened.getByRole("dialog")).toHaveCount(0);
  await expect(reopened.getByRole("group", { name: "刪除卡組確認" })).toBeVisible();
  await reopened.getByRole("button", { name: "確認刪除卡組" }).click();
  // 刪除最後一個卡組後回到空卡組引導。
  await expect(reopened.getByRole("region", { name: "空卡組引導" })).toBeVisible();
  await expectOnlyUiHints(reopened);
  const library = await (await second.request.get("/v1/materials")).json();
  expect(library.materials).toHaveLength(1);
  expect(library.materials[0].study_sessions).toEqual([]);
  await second.close();
});
