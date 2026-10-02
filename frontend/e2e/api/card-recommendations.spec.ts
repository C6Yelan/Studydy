import { expect, test } from "@playwright/test";

test.skip(process.env.STUDYDY_E2E_CARD_RECOMMENDATIONS !== "true", "Requires isolated real learning records");

test("real wrong-answer progress recommends a source-bound card without changing learning state", async ({ page }) => {
  const material = process.env.STUDYDY_E2E_RECOMMENDATION_MATERIAL!;
  const run = process.env.STUDYDY_E2E_RECOMMENDATION_RUN!;
  const revision = process.env.STUDYDY_E2E_RECOMMENDATION_REVISION!;
  const mutations: string[] = [];
  page.on("request", (request) => {
    if (request.url().includes("/study-sessions") && request.method() !== "GET") mutations.push(request.method());
  });
  await page.goto("/login");
  await page.getByLabel("Email", { exact: true }).fill("learner_test@example.com");
  await page.getByLabel("密碼", { exact: true }).fill("Synthetic test password 42");
  await page.getByRole("button", { name: "登入", exact: true }).click();
  await expect(page.getByRole("heading", { name: "歡迎回來！", level: 1 })).toBeVisible();
  await page.goto(`/materials/${material}/runs/${run}/knowledge-structures/${encodeURIComponent(revision)}/create-cards`);
  await page.getByRole("button", { name: "幫我選卡", exact: true }).click();
  const panel = page.getByRole("region", { name: "幫我選卡", exact: true });
  await expect(panel.locator("li")).toHaveCount(1);
  await expect(panel.locator("li")).toContainText("Signals");
  await expect(panel.locator("li")).toContainText("有待複習重點");
  await page.getByRole("button", { name: "套用推薦", exact: true }).click();
  await expect(page.getByRole("checkbox", { checked: true })).toHaveCount(1);
  await page.getByRole("button", { name: "保存並開始複習" }).click();
  await expect(page).toHaveURL(/\/concept-cards\/[0-9a-f-]+$/);
  await expect(page.locator(".flashcard-title")).toHaveText("Signals");
  await page.reload();
  await expect(page.locator(".deck-count")).toHaveText("1 張卡片");
  await page.getByRole("button", { name: "管理卡組", exact: true }).click();
  await page.getByRole("button", { name: "幫我選卡", exact: true }).click();
  await page.getByRole("combobox", { name: "推薦方式", exact: true }).selectOption("path");
  await expect(panel.locator("li")).toHaveCount(2);
  await page.getByRole("button", { name: "套用推薦", exact: true }).click();
  await page.getByRole("button", { name: "保存變更", exact: true }).click();
  await expect(page).toHaveURL(/\/concept-cards\/[0-9a-f-]+$/);
  await expect(page.locator(".deck-count")).toHaveText("2 張卡片");
  expect(mutations).toEqual([]);
});
