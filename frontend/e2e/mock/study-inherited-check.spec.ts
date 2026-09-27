import { expect, test } from "@playwright/test";
import { studyLayoutFixture } from "../fixtures/study-layout.mjs";

for (const width of [1366, 390]) {
  test(`inherited pass survives refresh and advances without generating at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 844 });
    const fixture = await studyLayoutFixture(page, "completed", {
      navigation: true, wrong: [], inheritedCheck: true, nextLabel: "Next topic",
    });
    await fixture.open();
    const summary = page.locator(".inherited-check");
    await expect(summary.getByRole("heading", { name: "檢測通過", exact: true })).toBeVisible();
    await expect(summary.getByRole("button", { name: "查看原始結果", exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "開始本輪 6 題", exact: true })).toHaveCount(0);
    const box = (await summary.boundingBox())!;
    const heading = (await summary.locator("h2").boundingBox())!;
    const buttons = await summary.getByRole("button").evaluateAll(nodes =>
      nodes.map(node => node.getBoundingClientRect().toJSON()));
    expect(heading.x - box.x).toBeGreaterThanOrEqual(16);
    expect(heading.y - box.y).toBeGreaterThanOrEqual(16);
    for (const button of buttons) {
      expect(button.x - box.x).toBeGreaterThanOrEqual(16);
      expect(box.x + box.width - button.right).toBeGreaterThanOrEqual(16);
      expect(box.y + box.height - button.bottom).toBeGreaterThanOrEqual(16);
    }
    if (width > 600) expect(buttons[0].y).toBeCloseTo(buttons[1].y, 0);
    else expect(buttons[1].y).toBeGreaterThanOrEqual(buttons[0].bottom);
    await page.reload();
    await expect(summary).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await summary.getByRole("button", { name: "下一個觀念：Next topic", exact: true }).click();
    await expect(page.getByRole("heading", { name: "Next topic", level: 1, exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "開始本輪 6 題", exact: true })).toBeVisible();
    expect(fixture.requests.filter(request => request.path.endsWith("/assessment-sets"))).toHaveLength(0);
    expect(fixture.requests.filter(request => request.path.endsWith("/guidance/apply"))).toHaveLength(1);
  });
}

test("long inherited next-step label stays inside the card", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const fixture = await studyLayoutFixture(page, "completed", {
    navigation: true, wrong: [], inheritedCheck: true,
    nextLabel: "NetworkApplicationProtocolResponsibilities".repeat(4),
  });
  await fixture.open();
  expect(await page.locator(".inherited-check button").evaluateAll(nodes =>
    nodes.every(node => node.scrollWidth <= node.clientWidth))).toBe(true);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});
