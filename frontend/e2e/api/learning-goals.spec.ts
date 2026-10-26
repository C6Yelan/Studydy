import { expect, test } from "@playwright/test";

test.skip(process.env.STUDYDY_E2E_LEARNING_GOALS !== "true", "Requires isolated learning goal API/DB fixture");
const data = JSON.parse(process.env.STUDYDY_E2E_GOAL_DATA ?? "{}");
const mapPath = `/materials/${data.material}/runs/${data.run}/knowledge-structures/${encodeURIComponent(data.revision)}`;

test("a real goal leads through level selection, source reading and answers without completing the material", async ({ page }) => {
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
  const goalPanel = page.getByRole("region", { name: "學習目標與每日關卡" });
  await goalPanel.getByRole("button", { name: "設定學習目標", exact: true }).click();
  const editor = page.getByRole("dialog", { name: "設定學習目標", exact: true });
  await editor.getByLabel("目標日期", { exact: true }).fill(data.target_date);
  await editor.getByRole("radio", { name: /60%/ }).check();
  await editor.getByRole("button", { name: "儲存目標", exact: true }).click();
  await expect(goalPanel).toContainText("60% · 及格");
  const today = goalPanel.getByRole("region", { name: "今天的關卡", exact: true });
  await expect(today).toContainText("今日目標 1 ／ 實際安排 2 個重點");
  await expect(today).toContainText("Signals");

  // 設定目標只保存計畫；程度與題組仍等實際開始學習時處理。
  const levelBefore = await (await page.request.get(`/v1/materials/${data.material}/starting-level`)).json();
  expect(levelBefore.starting_level).toBeNull();
  const setsBefore = await (await page.request.get(`/v1/study-sessions/${data.session}/assessment-sets`)).json();
  expect(setsBefore.sets).toEqual([]);
  await today.getByRole("button", { name: "學習 Signals", exact: true }).click();
  const levels = page.getByRole("dialog", { name: "你想從哪裡開始？" });
  await expect(levels).toBeVisible();
  await levels.getByRole("radio", { name: /懂一點/ }).check();
  await levels.getByRole("button", { name: "儲存選擇", exact: true }).click();
  await expect(page).toHaveURL(/assessment-sets\/[0-9a-f-]+$/);
  await expect(page.locator(".assessment-set-item")).toHaveCount(2);
  const setId = new URL(page.url()).pathname.split("/").at(-1)!;
  const setPath = `/v1/study-sessions/${data.session}/assessment-sets/${setId}`;
  const published = await (await page.request.get(setPath)).json();
  expect(published.target_concept_id).toBe(data.concept);
  expect(published.knowledge_structure_revision).toBe(data.revision);
  expect(JSON.stringify(published)).not.toMatch(/"correct_option_id"|"prepared_document"|"private_answer_document"|"generation_provenance"/);

  // 懂一點直接作答，但仍能展開原教材並透過真 API 回查 PDF。
  await expect(page.locator(".study-material-summary")).not.toHaveAttribute("open", "");
  await page.locator(".study-material-summary > summary").click();
  await page.getByRole("region", { name: "教材來源", exact: true }).getByRole("button").click();
  const source = page.getByRole("dialog", { name: "教材來源", exact: true });
  await expect(source).toBeVisible();
  const preview = source.getByRole("link", { name: "開啟 PDF 來源頁", exact: true });
  await expect(preview).toHaveAttribute("href", /#page=1$/);
  const sourceUrl = (await preview.getAttribute("href"))!.split("#")[0];
  expect((await page.request.get(sourceUrl)).status()).toBe(200);
  await page.keyboard.press("Escape");
  await page.locator(".study-material-summary > summary").click();

  await page.getByRole("article", { name: "第 1 題", exact: true }).getByRole("radio", { name: /\bcode0\b/ }).check();
  await page.getByRole("article", { name: "第 2 題", exact: true }).getByRole("radio", { name: /\bcode1\b/ }).check();
  await page.getByRole("button", { name: "交卷並查看結果", exact: true }).click();
  await expect(page.locator(".assessment-set-summary")).toContainText("答對2 / 2 題");
  const badgeNotice = page.getByRole("dialog", { name: "獲得新徽章" });
  if (await badgeNotice.isVisible()) await badgeNotice.getByRole("button", { name: "太好了", exact: true }).click();
  await page.getByRole("tab", { name: "學習與測驗", exact: true }).click();
  await expect(goalPanel.getByText("目標已達成", { exact: true })).toBeVisible();
  await expect(goalPanel.locator(".goal-facts")).toContainText("2 / 3");
  await expect(today).toContainText("今日關卡已完成");
  await expect(today).toContainText("今天已完成 2 個重點");
  await page.reload();
  await expect(goalPanel.getByText("目標已達成", { exact: true })).toBeVisible();
  await expect(goalPanel.locator(".goal-facts")).toContainText("2 / 3");
  await expect(today).toContainText("今日關卡已完成");
  await expect(today).toContainText("今天已完成 2 個重點");

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
