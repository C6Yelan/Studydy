import { expect, type Page } from "@playwright/test";

/** 真 API 的新教材由使用者在畫面選擇程度，不從測試端預先寫入偏好。 */
export async function chooseBeginner(page: Page) {
  const picker = page.getByRole("dialog", { name: "你想從哪裡開始？" });
  await expect(picker).toBeVisible();
  await picker.getByRole("radio", { name: /剛開始/ }).check();
  await picker.getByRole("button", { name: "儲存選擇", exact: true }).click();
  await expect(page.getByRole("region", { name: "先閱讀概念說明" })).toBeVisible();
}
