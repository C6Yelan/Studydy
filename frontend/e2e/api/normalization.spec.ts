import { expect, test } from "@playwright/test";

test.skip(
  process.env.STUDYDY_E2E_NORMALIZATION_REAL !== "true",
  "Requires isolated API/database/converter fixture",
);

const sourceText =
  "Stacks\nA stack follows LIFO order.\nPush adds an item to the top. Pop removes the top item.\n";

test("conversion preserves the original file and waits for explicit analysis", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("Email", { exact: true }).fill("learner_test@example.com");
  await page.getByLabel("密碼", { exact: true }).fill("Synthetic test password 42");
  await page.getByRole("button", { name: "登入", exact: true }).click();
  await expect(page.getByRole("heading", { name: "歡迎回來！", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "上傳教材", exact: true }).click();
  await page.getByLabel("選擇教材檔案", { exact: true }).setInputFiles({
    name: "normalization.txt",
    mimeType: "text/plain",
    buffer: Buffer.from(sourceText),
  });
  await page.getByRole("button", { name: "上傳並確認來源" }).click();
  await expect(page).toHaveURL(/\/materials\/[0-9a-f-]+\/sources$/);
  const materialId = new URL(page.url()).pathname.split("/")[2];
  const materialPath = `/v1/materials/${materialId}`;
  await expect(page.getByRole("button", { name: "開始分析教材" })).toBeVisible({ timeout: 20000 });
  await page.reload();
  const before = await (await page.request.get(materialPath)).json();
  expect(before.latest_attempt).toBeNull();
  const original = await page.getByRole("link", { name: "下載原檔" }).getAttribute("href");
  const content = await page.request.get(original!);
  expect(content.status()).toBe(200);
  expect(await content.text()).toBe(sourceText);
  const preview = await page.getByRole("link", { name: /預覽 PDF/ }).getAttribute("href");
  const pdf = await page.request.get(preview!);
  expect(pdf.status()).toBe(200);
  expect((await pdf.body()).subarray(0, 4).toString()).toBe("%PDF");
  await page.getByRole("button", { name: "開始分析教材" }).click();
  await expect(page).toHaveURL(/\/runs\/[0-9a-f-]+$/);
  await expect(page.getByRole("heading", { name: "等待開始處理", exact: true })).toBeVisible();
  const after = await (await page.request.get(materialPath)).json();
  expect(after.source.status).toBe("ready");
  expect(after.latest_attempt.status).toBe("pending");
});
