import { expect, test } from "@playwright/test";
import type { MaterialLibraryItem, LearnerProgressView } from "../src/api/contracts";

test.skip(process.env.STUDYDY_E2E_REVISION_REAL !== "true", "Requires isolated B3-A API/database/worker fixture");
const materialId = process.env.STUDYDY_E2E_REVISION_MATERIAL ?? "";

for (const width of [1536, 390]) test(`real append keeps answers and source pages at ${width}px`, async ({ page }, info) => {
  await page.setViewportSize({ width, height: 844 });
  await page.goto("/login");
  await page.getByLabel("Email", { exact: true }).fill("learner_test@example.com");
  await page.getByLabel("密碼", { exact: true }).fill("Synthetic test password 42");
  await page.getByRole("button", { name: "登入", exact: true }).click();
  await expect(page.getByRole("heading", { name: "歡迎回來！", level: 1, exact: true })).toBeVisible();
  const beforeResponse = await page.request.get(`/v1/materials/${materialId}`);
  expect(beforeResponse.status()).toBe(200);
  const before: MaterialLibraryItem = await beforeResponse.json();
  await page.goto(`/materials/${materialId}/sources`);
  await expect(page.getByRole("heading", { name: "新增教材", exact: true })).toBeVisible();
  const filename = `addition-${width}.txt`;
  const content = width === 1536 ? "A queue follows FIFO order.\n" : "A binary tree has left and right children.\n";
  await page.getByLabel("選擇新增教材", { exact: true }).setInputFiles({ name: filename, mimeType: "text/plain", buffer: Buffer.from(content) });
  await page.getByRole("button", { name: "上傳選取的教材", exact: true }).click();
  const start = page.getByRole("button", { name: "確認新增並更新地圖", exact: true });
  await expect(start).toBeEnabled({ timeout: 20000 });
  await page.reload();
  await expect(start).toBeEnabled();
  await start.click();
  await expect(page.getByRole("heading", { name: "教材更新完成", exact: true })).toBeVisible({ timeout: 20000 });
  const after: MaterialLibraryItem = await (await page.request.get(`/v1/materials/${materialId}`)).json();
  expect(after.head_revision).not.toBe(before.head_revision);
  expect(after.source_count).toBe((before.source_count ?? 1) + 1);
  await page.getByRole("button", { name: "返回教材庫", exact: true }).click();
  const card = page.getByRole("article", { name: "Combined textbook", exact: true });
  await card.getByRole("button", { name: "接續更新後的學習", exact: true }).click();
  await expect(page).toHaveURL(/\/study-sessions\/[0-9a-f-]+$/);
  const sessionId = new URL(page.url()).pathname.split("/").at(-1)!;
  const progress: LearnerProgressView = await (await page.request.get(`/v1/study-sessions/${sessionId}/progress`)).json();
  expect(progress.concept_states.some(state => state.status === "mastered")).toBe(true);
  expect(progress.concept_states.some(state => state.status === "not_started")).toBe(true);
  expect(progress.event_watermark).toBe(0);
  await page.getByRole("button", { name: /A\.pdf · PDF 第 1 頁/ }).click();
  await expect(page.getByRole("dialog", { name: "教材來源" })).toBeVisible();
  const url = await page.getByRole("link", { name: "開啟 PDF 來源頁", exact: true }).getAttribute("href");
  expect(url).toMatch(/#page=1$/);
  expect((await page.request.get(url!.split("#")[0])).status()).toBe(200);
  await page.getByRole("button", { name: "關閉", exact: true }).click();
  await page.screenshot({ path: info.outputPath(`inherited-study-${width}.png`), fullPage: true });
  await page.reload();
  await expect(page.getByRole("button", { name: /A\.pdf · PDF 第 1 頁/ })).toBeVisible();
  for (const old of before.study_sessions) {
    const resume = await page.request.get(`/v1/materials/${materialId}/knowledge-structures/${encodeURIComponent(old.knowledge_structure_revision)}/study-sessions/${old.study_session_id}/resume?run_id=${old.run_id}`);
    expect(resume.status()).toBe(200);
  }
  await page.getByRole("button", { name: "登出", exact: true }).click();
});
