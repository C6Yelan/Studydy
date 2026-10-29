import { expect, test } from "@playwright/test";

test.skip(process.env.STUDYDY_E2E_LEARNING_GOALS !== "true", "Requires isolated learning goal API/DB fixture");
const data = JSON.parse(process.env.STUDYDY_E2E_GOAL_DATA ?? "{}");
const mapPath = `/materials/${data.material}/runs/${data.run}/knowledge-structures/${encodeURIComponent(data.revision)}`;

test("a real goal leads through source reading and answers without completing the material", async ({ page }) => {
  await page.setViewportSize({ width: 1536, height: 900 });
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.goto("/");
  await page.getByLabel("Email", { exact: true }).fill("learner_test@example.com");
  await page.getByLabel("密碼", { exact: true }).fill("Synthetic test password 42");
  await page.getByRole("button", { name: "登入", exact: true }).click();
  await expect(page.getByRole("heading", { name: "歡迎回來！今天想探索哪座島？", level: 1, exact: true })).toBeVisible();

  await page.goto(mapPath);
  await page.getByRole("tab", { name: "學習與測驗", exact: true }).click();
  const goalPanel = page.locator(".review-diagnosis");
  await goalPanel.getByRole("button", { name: "設定學習目標", exact: true }).click();
  const editor = page.getByRole("dialog", { name: "設定學習目標", exact: true });
  await editor.getByLabel("目標日期", { exact: true }).fill(data.target_date);
  await editor.getByRole("radio", { name: /60%/ }).check();
  await editor.getByRole("button", { name: "儲存目標", exact: true }).click();
  await expect(goalPanel).toContainText("前掌握 60%");
  const today = page.getByRole("region", { name: "學習進度", exact: true });
  await expect(today).toContainText("今天 0/1 個重點");
  await expect(page.getByRole("region", { name: "今天的概念", exact: true })).toContainText("Signals");

  // 設定目標只保存計畫；題組仍等實際開始學習時建立。
  const setsBefore = await (await page.request.get(`/v1/study-sessions/${data.session}/assessment-sets`)).json();
  expect(setsBefore.sets).toEqual([]);
  await page.getByRole("region", { name: "今天的概念", exact: true }).getByRole("button", { name: "學習 Signals", exact: true }).click();
  await expect(page).toHaveURL(/assessment-sets\/[0-9a-f-]+$/);
  await expect(page.locator(".assessment-set-item")).toHaveCount(1);
  const setId = new URL(page.url()).pathname.split("/").at(-1)!;
  const setPath = `/v1/study-sessions/${data.session}/assessment-sets/${setId}`;
  const published = await (await page.request.get(setPath)).json();
  expect(published.target_concept_id).toBe(data.concept);
  expect(published.knowledge_structure_revision).toBe(data.revision);
  expect(JSON.stringify(published)).not.toMatch(/"correct_option_id"|"prepared_document"|"private_answer_document"|"generation_provenance"/);


  await page.getByRole("article", { name: "第 1 題", exact: true }).getByRole("radio", { name: /\bcode0\b/ }).check();
  await page.getByRole("button", { name: "下一題", exact: true }).click();
  await page.getByRole("article", { name: "第 2 題", exact: true }).getByRole("radio", { name: /\bcode1\b/ }).check();
  await page.getByRole("button", { name: "交卷", exact: true }).click();
  await expect(page.locator(".question-result-summary")).toContainText("答對 2／2 題");
  // 完成作答後從每題回顧透過真 API 回查 PDF。
  await page.locator(".feedback-evidence").first().getByRole("button").first().click();
  const source = page.getByRole("dialog", { name: "教材來源", exact: true });
  await expect(source).toBeVisible();
  const preview = source.getByRole("link", { name: "開啟 PDF 來源頁", exact: true });
  await expect(preview).toHaveAttribute("href", /#page=1$/);
  const sourceUrl = (await preview.getAttribute("href"))!.split("#")[0];
  expect((await page.request.get(sourceUrl)).status()).toBe(200);
  await page.keyboard.press("Escape");

  const badgeNotice = page.getByRole("status", { name: "獲得新徽章" });
  if (await badgeNotice.isVisible()) await badgeNotice.getByRole("button", { name: "太好了", exact: true }).click();
  await page.getByRole("tab", { name: "學習與測驗", exact: true }).click();
  await expect(goalPanel.getByText("學習目標已達成。", { exact: true })).toBeVisible();
  await expect(goalPanel.getByRole("progressbar", { name: "學習目標進度", exact: true })).toHaveAttribute("aria-valuetext", "已完成 2／需完成 2 個重點");
  await expect(today).toContainText("今日關卡已完成");
  await expect(today).toContainText("今天 2/1 個重點");
  await page.reload();
  await expect(goalPanel.getByText("學習目標已達成。", { exact: true })).toBeVisible();
  await expect(goalPanel.getByRole("progressbar", { name: "學習目標進度", exact: true })).toHaveAttribute("aria-valuetext", "已完成 2／需完成 2 個重點");
  await expect(today).toContainText("今日關卡已完成");
  await expect(today).toContainText("今天 2/1 個重點");

  const saved = await (await page.request.get(`/v1/materials/${data.material}/goal`)).json();
  expect(saved.goal.target_date).toBe(data.target_date);
  expect(saved.goal.target_percent).toBe(60);
  expect(saved.goal.assessable_points).toBe(3);
  expect(saved.goal.completed_points).toBe(2);
  expect(saved.goal.achieved).toBe(true);
  expect(saved.goal.today_stage.quota_points).toBe(1);
  expect(saved.goal.today_stage.completed_points).toBe(2);
  const workspace = await (await page.request.get(`/v1/materials/${data.material}/knowledge-structures/${encodeURIComponent(data.revision)}/study-sessions/${data.session}/workspace?run_id=${data.run}&set_id=${setId}`)).json();
  expect(workspace.session.status).toBe("active");
  expect(errors).toEqual([]);
});
