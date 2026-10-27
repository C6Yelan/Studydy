import { expect, test } from "@playwright/test";
import { studyLayoutFixture } from "../fixtures/study-layout.mjs";

for (const width of [1440, 1300, 1200, 1024, 390]) {
  for (const kind of ["diagnostic"]) test(`one question, keyboard, preserved choices and expanded review ${kind} at ${width}px`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: width === 390 ? 844 : width === 1024 ? 768 : 900 });
    const fixture = await studyLayoutFixture(page, "ready", { count: 2, kind, navigation: true, });
    await fixture.open();
    const paper = page.getByRole("form", { name: "本組測驗" });
    await expect(paper.getByRole("article")).toHaveCount(1);
    await expect(paper).toContainText("第 1／2 題");
    await paper.getByRole("radio").first().focus();
    await page.keyboard.press("2");
    await expect(paper.getByRole("radio").nth(1)).toBeChecked();
    await page.keyboard.press("Enter");
    await expect(paper).toContainText("第 2／2 題");
    await page.keyboard.press("1");
    await page.getByRole("button", { name: "上一題", exact: true }).click();
    await expect(paper.getByRole("radio").nth(1)).toBeChecked();
    await paper.getByRole("radio").first().focus();
    await page.keyboard.press("1");
    await page.keyboard.press("Enter");
    await expect(paper.getByRole("radio").first()).toBeChecked();
    await page.keyboard.press("4");
    if (width === 1440) {
      await expect(page.getByRole("button", { name: "交卷", exact: true })).toBeInViewport({ ratio: 1 });
      await expect(paper.locator("label").last()).toBeInViewport({ ratio: 1 });
    }
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: testInfo.outputPath(`question-${kind}-${width}.png`) });
    await page.getByRole("button", { name: /題組紀錄（/ }).click();
    const historyDialog = page.getByRole("dialog", { name: "題組紀錄", exact: true });
    const historyClosed = historyDialog.evaluate(dialog => new Promise<void>(resolve => {
      dialog.addEventListener("close", () => resolve(), { once: true });
    }));
    await historyDialog.getByRole("button", { name: "關閉", exact: true }).click();
    await historyClosed;
    expect(fixture.requests.filter(request => request.path.endsWith("/submissions"))).toHaveLength(0);
    await paper.locator(".question-paper-card").focus();
    const submission = page.waitForRequest(request => request.url().endsWith("/submissions"));
    await page.keyboard.press("Enter");
    expect((await submission).postDataJSON().answers).toHaveLength(2);
    await expect(page.locator(".feedback-card")).toHaveCount(2);
    await expect(page.locator(".question-result-summary")).toContainText("答對 1／2 題 · 1 個重點需要補強");
    expect(fixture.requests.filter(request => request.path.endsWith("/submissions"))).toHaveLength(1);
    await expect(page.locator(".assessment-cycle .primary-button")).toBeEnabled();
    await page.screenshot({ path: testInfo.outputPath(`result-${kind}-${width}.png`) });
  });
  test(`long prompt rationale and source wrap at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 900 });
    const fixture = await studyLayoutFixture(page, "ready", { count: 3, longContent: true });
    await fixture.open();
    await expect(page.locator(".assessment-set-item")).toHaveCount(1);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    fixture.setStage("completed"); await page.reload();
    await expect(page.locator(".feedback-card")).toHaveCount(3);
    expect(await page.locator(".feedback-card").evaluateAll(nodes => nodes.every(element => element.scrollWidth <= element.clientWidth))).toBe(true);
    await expect(page.locator(".feedback-rationale").first()).toContainText("判斷時要檢查請求的方向");
    const source = page.locator(".feedback-evidence button").first();
    await expect(source).toContainText("網路服務角色");
    expect(await source.evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
  });
}

for (const kind of ["diagnostic"]) {
  test(`preparation skeleton uses actual progress ${kind}`, async ({ page }, testInfo) => {
    const fixture = await studyLayoutFixture(page, "preparing", { kind, count: 2, navigation: true, });
    fixture.setPreparedCount(0); await fixture.open();
    const label = "準備進度";
    const progress = page.getByRole("progressbar", { name: label, exact: true });
    await expect(progress).not.toHaveAttribute("aria-valuenow");
    await expect(page.locator(".question-skeleton")).toBeVisible();
    fixture.setPreparedCount(2); await expect(progress).toHaveAttribute("aria-valuenow", "2");
    await expect(progress).toHaveAttribute("aria-valuemax", "2");
    for (const width of [1440, 1300, 1200, 1024, 390]) {
      await page.setViewportSize({ width, height: width === 390 ? 844 : width === 1024 ? 768 : 900 });
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      await page.screenshot({ path: testInfo.outputPath(`preparing-${kind}-${width}.png`) });
    }
  });
  test(`lost submission response restores persisted results ${kind}`, async ({ page }) => {
    const fixture = await studyLayoutFixture(page, "ready", { kind, count: 2, navigation: true, lostSubmissionResponse: true, });
    await fixture.open();
    await page.getByRole("radio").first().check(); await page.getByRole("button", { name: "下一題", exact: true }).click();
    await page.getByRole("radio").nth(1).check();
    await page.getByRole("button", { name: "交卷", exact: true }).click();
    await page.getByRole("button", { name: "查回交卷結果", exact: true }).click();
    await expect(page.locator(".feedback-card")).toHaveCount(2);
    await expect(page.locator(".question-result-summary")).toContainText("答對 1／2 題");
    expect(fixture.requests.filter(request => request.path.endsWith("/submissions"))).toHaveLength(1);
  });
}
