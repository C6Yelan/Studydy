import { expect, test } from "@playwright/test";
import { studyLayoutFixture } from "../fixtures/study-layout.mjs";

for (const width of [1366, 390]) {
  for (const [name, stage, scenario] of [
    ["entry", "preparation", null],
    ["remediation", "ready", { kind: "remediation" }],
    ["passed", "completed", { kind: "remediation", wrong: [], navigation: true }],
  ] as const)
    test(`history-only rail ${name} at ${width}px`, async ({ page }) => {
      await page.setViewportSize({ width, height: width === 390 ? 844 : 1024 });
      const fixture = await studyLayoutFixture(page, stage, scenario);
      await fixture.open();
      const rail = page.getByRole("complementary", { name: "學習紀錄" });
      await expect(rail).toBeVisible();
      await expect(rail.locator(":scope > *")).toHaveCount(1);
      await expect(rail.locator(".assessment-set-history")).toHaveAttribute("open", "");
      await expect(rail).not.toContainText(
        /學習中|尚未開始|已掌握|已練習|本輪檢測通過|作答 \d+ 次|答對 \d+ 次/,
      );
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
        true,
      );
      await rail.locator("summary").click();
      await expect(rail.locator(".assessment-set-history")).not.toHaveAttribute("open", "");
      await rail.locator("summary").click();
      await rail.getByRole("button").last().click();
      await expect(page.locator(".assessment-cycle")).toBeVisible();
      await page.locator(".assessment-answer-review > summary").click();
      await expect(page.locator(".feedback-card").first()).toBeVisible();
    });

  test(`empty history removes rail and frees main width at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 1024 });
    const fixture = await studyLayoutFixture(page, "preparation", { noHistory: true });
    await fixture.open();
    await expect(page.locator(".study-rail")).toHaveCount(0);
    const workspace = (await page.locator(".study-workspace").boundingBox())!;
    const main = (await page.locator(".study-main").boundingBox())!;
    expect(main.width).toBeCloseTo(workspace.width, 0);
    await expect(page.getByRole("button", { name: "開始本輪 6 題", exact: true })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
      true,
    );
  });
}
