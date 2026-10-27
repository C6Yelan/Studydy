import { expect, type Page } from "@playwright/test";

export async function answerPaper(page: Page, choices: number[]) {
  for (const [index, choice] of choices.entries()) {
    const question = page.getByRole("article", { name: `第 ${index + 1} 題`, exact: true });
    await question.getByRole("radio").nth(choice).check();
    if (index < choices.length - 1) await page.getByRole("button", { name: "下一題", exact: true }).click();
  }
  await expect(page.getByRole("button", { name: "交卷", exact: true })).toBeEnabled();
}
