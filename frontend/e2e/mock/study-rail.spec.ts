import { expect, test } from "@playwright/test";
import { studyLayoutFixture } from "../fixtures/study-layout.mjs";

for (const width of [1440, 390]) {
  test(`result history navigation at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 900 });
    const fixture = await studyLayoutFixture(page, "completed", { kind: "remediation", wrong: [], navigation: true }); await fixture.open();
    const history = page.getByRole("complementary", { name: "作答紀錄", exact: true });
    await expect(history).toBeVisible();
    await history.locator("li button").last().click();
    await expect(page.locator(".assessment-cycle")).toBeVisible();
    await expect(page.locator(".feedback-card").first()).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  });
  test(`empty history keeps full content width at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 900 });
    const fixture = await studyLayoutFixture(page, "preparation", { noHistory: true }); await fixture.open();
    const workspace = (await page.locator(".study-workspace").boundingBox())!;
    expect((await page.locator(".study-main").boundingBox())!.width).toBeCloseTo(workspace.width, 0);
    await expect(page.getByRole("button", { name: "開始學習", exact: true })).toBeVisible();
  });
}
