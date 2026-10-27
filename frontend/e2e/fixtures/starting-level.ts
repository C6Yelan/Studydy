import { expect, type Page } from "@playwright/test";

/** 真 API 的新教材由使用者在畫面選擇程度，不從測試端預先寫入偏好。 */
export async function chooseFirstLevel(page: Page) {
  const picker = page.getByRole("dialog", { name: "你想從哪裡開始？" });
  await expect(picker).toBeVisible();
  await picker.getByRole("radio", { name: /第一次接觸/ }).check();
  await picker.getByRole("button", { name: "儲存選擇", exact: true }).click();

}
