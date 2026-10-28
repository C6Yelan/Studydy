import { expect, test } from "@playwright/test";
import { studyLayoutFixture } from "../fixtures/study-layout.mjs";

for (const width of [1440, 390]) test(`result summary, full answers, source and focused map at ${width}px`, async ({ page }) => {
  await page.setViewportSize({ width, height: width === 390 ? 844 : 900 });
  const fixture = await studyLayoutFixture(page, "completed", { preparingHistory: true });
  await fixture.open();
  await expect(page.locator(".question-result-summary")).toHaveText("答對 2／4 題 · 2 個重點需要補強");
  const review = page.getByRole("region", { name: "作答回顧", exact: true });
  await expect(review.locator(".feedback-card")).toHaveCount(4);
  for (const card of await review.locator(".feedback-card").all()) {
    await expect(card).toBeVisible();
    for (const label of ["你的答案", "正確答案", "解析"]) await expect(card.getByRole("heading", { name: label, exact: true })).toBeVisible();
  }
  const first = review.locator(".feedback-card").first();
  await expect(first.locator(".feedback-evidence")).toContainText(fixture.knowledgeStructure.concepts[0].claims[0].text);
  await review.getByRole("button", { name: /第 1 頁/ }).first().click();
  await expect(page.getByRole("dialog", { name: "教材來源" })).toBeVisible();
  await page.keyboard.press("Escape");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.getByRole("button", { name: "查看知識地圖", exact: true }).click();
  expect(await page.evaluate(() => window.history.state.knowledgeMap.detail)).toBe(fixture.knowledgeStructure.concepts[0].concept_id);
});

for (const [name, scenario, expected] of [
  ["all correct", { wrong: [] }, "答對 4／4 題 · 0 個重點需要補強"],
  ["unanswered", { wrong: [1], unanswered: 1 }, "未作答 1 個重點"],
  ["unavailable", { wrong: [1], unavailable: 1 }, "未檢測 1 個重點"],
  ["remediation", { kind: "remediation", wrong: [3] }, "答對 3／4 題 · 1 個重點需要補強"],
] as const) test(`result metrics: ${name}`, async ({ page }) => {
  const fixture = await studyLayoutFixture(page, "completed", scenario); await fixture.open();
  await expect(page.locator(".assessment-cycle")).toContainText(expected);
});

test("older feedback without a correct option remains readable", async ({ page }) => {
  const fixture = await studyLayoutFixture(page, "completed", { missingCorrectOption: true }); await fixture.open();
  await expect(page.locator(".feedback-card").first()).toContainText("這份作答紀錄尚未提供正確答案。");
  await expect(page.locator(".feedback-evidence").first()).toContainText(fixture.knowledgeStructure.concepts[0].claims[0].text);
});

test("history includes published results and resumes selected group", async ({ page }) => {
  const fixture = await studyLayoutFixture(page, "preparing", {}); await page.goto(fixture.historyPath);
  const history = page.getByRole("complementary", { name: "作答紀錄", exact: true });
  await expect(history.locator("li")).toHaveCount(1);
  await history.locator("li button").click();
  await expect(page.locator(".assessment-cycle")).toBeVisible();
});
