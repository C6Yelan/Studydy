import { expect, test, type Page } from "@playwright/test";
import { studyLayoutFixture } from "../fixtures/study-layout.mjs";

async function cardGrid(page: Page, count: number, width: number) {
  const cards = page.locator(".assessment-set-item");
  await expect(cards).toHaveCount(count);
  expect(
    await cards.evaluateAll((nodes) => nodes.map((element) => element.getAttribute("aria-label"))),
  ).toEqual(Array.from({ length: count }, (_, i) => `第 ${i + 1} 題`));
  const rects = await cards.evaluateAll((nodes) =>
    nodes.map((node) => node.getBoundingClientRect().toJSON()),
  );
  if (width >= 1280) {
    expect(rects[1].y).toBeCloseTo(rects[0].y, 0);
    expect(rects[1].x).toBeGreaterThan(rects[0].x + rects[0].width);
    for (let i = 2; i < count; i++) {
      expect(rects[i].x).toBeCloseTo(rects[i % 2].x, 0);
      expect(rects[i].y).toBeGreaterThanOrEqual(rects[i - 2].bottom);
      expect(rects[i].width).toBeCloseTo(rects[0].width, 0);
    }
  } else {
    for (let i = 1; i < count; i++) {
      expect(rects[i].x).toBeCloseTo(rects[0].x, 0);
      expect(rects[i].y).toBeGreaterThanOrEqual(rects[i - 1].bottom);
    }
  }
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  return rects;
}

// 1280px：雙欄題卡；760px：單欄題卡、雙欄選項；390px：題卡與選項皆單欄。
for (const width of [1280, 760, 390]) {
  for (const count of [3, 6]) {
    test(`${count} questions and review at ${width}px`, async ({ page }) => {
      await page.setViewportSize({ width, height: width === 390 ? 844 : 800 });
      const fixture = await studyLayoutFixture(page, "ready", { count });
      await fixture.open();
      await cardGrid(page, count, width);
      const labels = await page
        .locator(".assessment-options")
        .first()
        .locator("label")
        .evaluateAll((nodes) => nodes.map((node) => node.getBoundingClientRect().toJSON()));
      expect(new Set(labels.map((n) => n.x)).size).toBe(width >= 1280 || width < 760 ? 1 : 2);
      expect(Math.min(...labels.map((n) => n.height))).toBeGreaterThanOrEqual(44);
      if (count === 3) {
        const first = page.locator(".assessment-set-item").first().getByRole("radio");
        await first.first().focus();
        await page.keyboard.press("ArrowDown");
        await expect(first.nth(1)).toBeChecked();
        await page.keyboard.press("Tab");
        await expect(
          page.locator(".assessment-set-item").nth(1).getByRole("radio").first(),
        ).toBeFocused();
      }
      fixture.setStage("completed");
      await page.reload();
      const review = page.locator(".assessment-answer-review");
      await expect(review).not.toHaveAttribute("open", "");
      await review.locator("summary").click();
      await cardGrid(page, count, width);
      await expect(review.locator(".feedback-card")).toHaveCount(count);
    });
  }
  test(`long prompt rationale and source wrap at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 1024 });
    const fixture = await studyLayoutFixture(page, "ready", { count: 3, longContent: true });
    await fixture.open();
    const questions = await cardGrid(page, 3, width);
    if (width >= 1280) expect(questions[0].height).toBeGreaterThan(questions[1].height);
    fixture.setStage("completed");
    await page.reload();
    await page.locator(".assessment-answer-review > summary").click();
    const review = await cardGrid(page, 3, width);
    if (width >= 1280) expect(review[0].height).toBeGreaterThan(review[1].height);
    expect(
      await page
        .locator(".feedback-card")
        .evaluateAll((nodes) =>
          nodes.every((element) => element.scrollWidth <= element.clientWidth),
        ),
    ).toBe(true);
    await expect(page.locator(".feedback-rationale").first()).toContainText(
      "判斷時要檢查請求的方向",
    );
    const source = page.locator(".feedback-evidence button").first();
    await expect(source).toContainText("網路服務角色");
    expect(await source.evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(
      true,
    );
  });
}
