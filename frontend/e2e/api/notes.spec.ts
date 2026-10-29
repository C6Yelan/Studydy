import { expect, test, type Page } from "@playwright/test";
test.skip(process.env.STUDYDY_E2E_NOTES !== "true", "Requires isolated notes API/DB fixture");
const data = JSON.parse(process.env.STUDYDY_E2E_NOTES_DATA ?? "{}");
async function selectText(page: Page) {
  const text = page.locator(".note-paragraph :is(h2, h3, h4, h5) [data-note-text]").first();
  await expect(text).toBeVisible();
  await text.evaluate(element => {
    const range = document.createRange(); range.selectNodeContents(element);
    const selection = window.getSelection()!; selection.removeAllRanges(); selection.addRange(range);
    element.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
  });
}
test("real notes save an annotation, reopen its source and generate full notes only on demand", async ({ page }, testInfo) => {
  const errors: string[] = [];
  page.on("pageerror", e => errors.push(e.message));
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/");
  await page.getByLabel("Email", { exact: true }).fill("learner_test@example.com");
  await page.getByLabel("密碼", { exact: true }).fill("Synthetic test password 42");
  await page.getByRole("button", { name: "登入", exact: true }).click();
  await expect(page.locator(".dashboard-content")).toBeVisible();
  await page.goto(`/materials/${data.material}/notes`);
  await expect(page.locator(".note-paragraph")).toHaveCount(3);
  await selectText(page);
  await page.getByRole("button", { name: "標記黃色", exact: true }).click();
  await expect(page.locator(".note-mark-yellow")).toBeVisible();
  await page.getByRole("button", { name: "新增註記", exact: true }).first().click();
  await page.getByRole("textbox", { name: "個人註記", exact: true }).fill("真 API 保存我的複習想法");
  await page.getByRole("button", { name: "儲存註記", exact: true }).click();
  await expect(page.getByRole("dialog")).toBeHidden();
  await page.reload();
  const badgeNotice = page.getByRole("status", { name: "獲得新徽章" });
  await expect(badgeNotice).toContainText("寫下心得");
  await badgeNotice.getByRole("button", { name: "關閉", exact: true }).click();
  await expect(badgeNotice).toBeHidden();
  await expect(page.getByText("真 API 保存我的複習想法", { exact: true })).toBeVisible();
  await expect(page.locator(".note-mark-yellow")).toBeVisible();
  const widths = await page.locator(".note-markup").evaluate(element => ({
    column: parseFloat(getComputedStyle(element).gridTemplateColumns),
    prose: element.querySelector(".note-markup-prose")!.getBoundingClientRect().width,
  }));
  expect(widths.prose).toBeCloseTo(widths.column, 1);
  await testInfo.attach("notes-comments-1440-widths", { body: JSON.stringify(widths), contentType: "application/json" });
  await page.screenshot({ path: testInfo.outputPath("notes-comments-api-1440.png"), fullPage: true });
  const controls = page.getByRole("menu", { name: "匯出筆記" });
  for (const format of ["md", "pdf", "docx"]) {
    await page.getByRole("button", { name: "匯出筆記", exact: true }).click();
    const download = page.waitForEvent("download");
    await controls.getByRole("menuitem", { name: ({ md: "Markdown", pdf: "PDF", docx: "Word（.docx）" })[format], exact: true }).click();
    const file = await download;
    expect(file.suggestedFilename()).toBe(`Synthetic-複習速覽.${format}`);
    expect(await file.failure()).toBeNull();
  }
  await page.locator(".note-sources button").first().click();
  await expect(page.getByRole("dialog", { name: "教材來源" })).toBeVisible();
  await expect(page.getByRole("link", { name: "開啟 PDF 來源頁" })).toHaveAttribute("href", /page=1/);
  await page.getByRole("dialog", { name: "教材來源" }).getByRole("button", { name: "關閉", exact: true }).click();
  await page.getByRole("button", { name: "完整筆記", exact: true }).click();
  await expect(page.getByRole("button", { name: "生成完整筆記", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "生成完整筆記", exact: true }).click();
  await expect(page.locator(".note-paragraph")).toHaveCount(3);
  await selectText(page);
  await page.getByRole("button", { name: "新增註記", exact: true }).first().click();
  await page.getByRole("textbox", { name: "個人註記", exact: true }).fill("完整筆記的複習想法");
  await page.getByRole("button", { name: "儲存註記", exact: true }).click();
  await expect(page.getByRole("dialog")).toBeHidden();
  await page.reload();
  await page.getByRole("button", { name: "完整筆記", exact: true }).click();
  await expect(page.getByText("完整筆記的複習想法", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "複習速覽", exact: true }).click();
  await expect(page.getByText("真 API 保存我的複習想法", { exact: true })).toBeVisible();
  expect(errors).toEqual([]);
});
