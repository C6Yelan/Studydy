import { expect, test, type Page } from "@playwright/test";

test.skip(process.env.STUDYDY_E2E_LIBRARY !== "true", "Requires the local library API/DB fixture");

async function login(page: Page, email: string) {
  await page.goto("/");
  await page.getByLabel("Email", { exact: true }).fill(email);
  await page.getByLabel("密碼", { exact: true }).fill("Synthetic test password 42");
  await page.getByRole("button", { name: "登入", exact: true }).click();
  await expect(page.getByRole("heading", { name: "歡迎回來！", level: 1, exact: true })).toBeVisible();
  await page.getByRole("button", { name: "教材庫", exact: true }).click();
  await expect(page.getByRole("heading", { name: "我的教材", exact: true })).toBeVisible();
}

test("fresh profiles discover their own materials and reopen both exact published versions", async ({ browser }) => {
  const original = await browser.newContext();
  const page = await original.newPage();
  await login(page, "learner_test@example.com");
  const first = page.getByRole("article", { name: "堆疊講義.pdf", exact: true });
  await expect(first).toContainText("最新處理：處理失敗");
  await expect(first.getByRole("button", { name: "開啟知識地圖", exact: true })).toHaveClass("primary-button");
  await expect(page.getByRole("article", { name: "處理中的筆記.pdf", exact: true }).getByRole("button", { name: "查看處理狀態", exact: true })).toHaveClass("primary-button");
  await expect(page.getByRole("article", { name: "尚未處理.pdf", exact: true }).locator(".primary-button")).toHaveCount(1);
  await expect(first).toContainText("先前已發布的知識地圖仍可開啟");
  await expect(page.getByRole("article", { name: "尚未處理.pdf", exact: true })).toContainText("尚未開始處理");
  await expect(page.getByRole("article", { name: "處理中的筆記.pdf", exact: true })).toContainText("正在分析完整教材");
  await expect(page.getByText("B 的私人教材.pdf", { exact: true })).toHaveCount(0);
  await first.getByRole("button", { name: "開啟知識地圖", exact: true }).click();
  await expect(page.getByRole("button", { name: "教材概念：Stack", exact: true })).toBeVisible();
  const newerPath = new URL(page.url()).pathname;
  await expect(page.getByRole("navigation", { name: "學習導覽" }).locator(".navigator-position")).toHaveText(["1"]);
  await page.reload();
  await expect(page.getByRole("button", { name: "教材概念：Stack", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "登出", exact: true }).click();
  await expect(page.getByRole("heading", { name: "登入您的帳戶" })).toBeVisible();
  await original.close();

  // 全新 cookie jar/storage，只用帳密和教材名稱導航，不注入 UUID 或已知網址。
  const fresh = await browser.newContext();
  const freshPage = await fresh.newPage();
  await login(freshPage, "learner_test@example.com");
  expect(await freshPage.evaluate(() => localStorage.length)).toBe(0);
  await expect(freshPage.locator(".sidebar-helper")).toHaveCount(0);
  await expect(freshPage.getByRole("button", { name: "開啟知識地圖", exact: true })).toHaveClass("primary-button");
  await freshPage.getByRole("button", { name: "開啟知識地圖", exact: true }).click();
  await expect(freshPage).toHaveURL(`http://127.0.0.1:4173${newerPath}`);
  await expect(freshPage.getByRole("button", { name: "教材概念：Stack", exact: true })).toBeVisible();
  await freshPage.getByRole("button", { name: "教材庫", exact: true }).click();
  const card = freshPage.getByRole("article", { name: "堆疊講義.pdf", exact: true });
  await expect(card.getByRole("button", { name: "堆疊講義.pdf", exact: true })).toHaveCount(0);
  const pdfUrl = await card.getByRole("link", { name: "原始 PDF", exact: true }).getAttribute("href");
  const pdf = await fresh.request.get(`http://127.0.0.1:4173${pdfUrl}`);
  expect(pdf.status()).toBe(200);
  expect(pdf.headers()["cache-control"]).toBe("private, no-store");
  expect((await pdf.body()).subarray(0, 4).toString()).toBe("%PDF");
  await freshPage.getByRole("article", { name: "堆疊講義.pdf", exact: true }).getByRole("button", { name: "開啟知識地圖", exact: true }).click();
  await expect(freshPage).toHaveURL(`http://127.0.0.1:4173${newerPath}`);
  await expect(freshPage.getByRole("button", { name: "教材概念：Stack", exact: true })).toBeVisible();
  await freshPage.getByRole("button", { name: "登出", exact: true }).click();
  await expect(freshPage.getByRole("heading", { name: "登入您的帳戶" })).toBeVisible();
  await login(freshPage, "library_b@example.com");
  await expect(freshPage.getByRole("article", { name: "B 的私人教材.pdf", exact: true })).toBeVisible();
  await expect(freshPage.getByText("堆疊講義.pdf", { exact: true })).toHaveCount(0);
  expect((await fresh.request.get(`http://127.0.0.1:4173${pdfUrl}`)).status()).toBe(404);
  await freshPage.goBack();
  await expect(freshPage.getByRole("button", { name: "教材概念：Stack", exact: true })).toHaveCount(0);
  await fresh.close();
});
