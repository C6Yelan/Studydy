import { expect, test, type Page } from "@playwright/test";
import type { StudyResumeView, AnswerFeedbackView } from "../src/api/contracts";

test.skip(process.env.STUDYDY_E2E_RESUME !== "true", "Requires the local resume API/DB fixture");
const origin = "http://127.0.0.1:4173";
const pendingPrompt = "保存的未答題：Stack 如何取出資料？";

async function login(page: Page, email = "learner_test@example.com") {
  await page.goto("/");
  await page.getByLabel("Email", { exact: true }).fill(email);
  await page.getByLabel("密碼", { exact: true }).fill("Synthetic test password 42");
  await page.getByRole("button", { name: "登入", exact: true }).click();
  await expect(page.getByRole("heading", { name: "歡迎回來！", exact: true })).toBeVisible();
  await expect(page.locator(".dashboard-stat strong")).toHaveText(email === "learner_test@example.com" ? ["3", "1", "3", "1"] : ["1", "0", "0", "0"]);
  await page.getByRole("button", { name: "教材庫", exact: true }).click();
  await expect(page.getByRole("heading", { name: "我的教材", exact: true })).toBeVisible();
}

function resumeResponse(page: Page) {
  return page.waitForResponse(response => response.url().includes("/resume?") && response.ok());
}

async function continueStudy(page: Page): Promise<StudyResumeView> {
  const read = resumeResponse(page);
  await page.getByRole("article", { name: "堆疊講義.pdf", exact: true }).getByRole("button", { name: "接續上次學習", exact: true }).click();
  return (await read).json();
}

test("original learning and questions survive reload, new profiles and a lost committed answer response", async ({ browser }) => {
  const firstContext = await browser.newContext();
  const firstPage = await firstContext.newPage();
  await login(firstPage);
  const initial = await continueStudy(firstPage);
  await expect(firstPage.getByRole("heading", { name: pendingPrompt, exact: true })).toBeVisible();
  const pending = initial.assessments.find(record => record.assessment.assessment_revision === initial.selected_assessment_revision)!;
  expect(pending.feedback).toBeNull();
  const reloaded = resumeResponse(firstPage);
  await firstPage.reload();
  const afterReload: StudyResumeView = await (await reloaded).json();
  expect(afterReload.session).toEqual(initial.session);
  expect(afterReload.assessments).toEqual(initial.assessments);
  expect(afterReload.selected_assessment_revision).toBe(initial.selected_assessment_revision);
  await expect(firstPage.getByRole("heading", { name: pendingPrompt, exact: true })).toBeVisible();
  await firstPage.getByRole("button", { name: "登出", exact: true }).click();
  await expect(firstPage.getByRole("heading", { name: "登入您的帳戶" })).toBeVisible();
  await firstContext.close();

  const freshContext = await browser.newContext();
  const page = await freshContext.newPage();
  await login(page);
  expect(await page.evaluate(() => localStorage.length)).toBe(0);
  const fresh = await continueStudy(page);
  expect(fresh.session).toEqual(initial.session);
  expect(fresh.assessments).toEqual(initial.assessments);
  await expect(page.getByRole("heading", { name: pendingPrompt, exact: true })).toBeVisible();
  const earlier = fresh.assessments.find(record => record.feedback !== null)!;
  const selectedRead = resumeResponse(page);
  await page.locator(".study-record-picker summary").click();
  await page.locator(".study-history-row").filter({ has: page.getByText(earlier.assessment.prompt, { exact: true }) }).click();
  const earlierRead: StudyResumeView = await (await selectedRead).json();
  expect(earlierRead.selected_assessment_revision).toBe(earlier.assessment.assessment_revision);
  await expect(page.getByRole("heading", { name: "答對了", exact: true })).toBeVisible();
  const priorReload = resumeResponse(page);
  await page.reload();
  const priorBody: StudyResumeView = await (await priorReload).json();
  expect(priorBody.selected_assessment_revision).toBe(earlier.assessment.assessment_revision);
  expect(priorBody.assessments.find(record => record.feedback?.answer_event_id === earlier.feedback!.answer_event_id)?.feedback).toEqual(earlier.feedback);
  await page.getByRole("button", { name: "返回最新進度", exact: true }).click();
  await expect(page.getByRole("heading", { name: pendingPrompt, exact: true })).toBeVisible();

  let committed: AnswerFeedbackView | undefined;
  let submitUrl = "";
  let submitKey = "";
  let submittedBody: unknown;
  // 真 API 先成功 commit，再丟棄回應；不以假成功或 fixture 重建替代保存。
  await page.route("**/v1/study-sessions/*/assessments/*/submissions", async route => {
    submitUrl = route.request().url();
    submitKey = route.request().headers()["idempotency-key"];
    submittedBody = route.request().postDataJSON();
    const accepted = await route.fetch();
    expect(accepted.status()).toBe(201);
    committed = await accepted.json();
    await route.abort("failed");
  }, { times: 1 });
  await page.getByLabel(/LIFO/).check();
  await page.getByRole("button", { name: "送出答案", exact: true }).click();
  await expect(page.getByRole("button", { name: "查回作答結果", exact: true })).toBeVisible();
  expect(committed).toBeDefined();
  const recoveredRead = resumeResponse(page);
  await page.getByRole("button", { name: "查回作答結果", exact: true }).click();
  const recovered: StudyResumeView = await (await recoveredRead).json();
  expect(recovered.assessments.find(record => record.assessment.assessment_revision === pending.assessment.assessment_revision)?.feedback).toEqual(committed);
  await expect(page.getByRole("heading", { name: "答對了", exact: true })).toBeVisible();
  const replay = await freshContext.request.post(submitUrl, { headers: { Origin: origin, "Idempotency-Key": submitKey }, data: submittedBody });
  expect(replay.status()).toBe(201);
  expect(await replay.json()).toEqual(committed);
  await page.getByRole("button", { name: "登出", exact: true }).click();
  await expect(page.getByRole("heading", { name: "登入您的帳戶" })).toBeVisible();
  await freshContext.close();

  const lastContext = await browser.newContext();
  const lastPage = await lastContext.newPage();
  await login(lastPage);
  const relogged = await continueStudy(lastPage);
  expect(relogged.session.study_session_id).toBe(initial.session.study_session_id);
  expect(relogged.session.knowledge_structure_revision).toBe(initial.session.knowledge_structure_revision);
  expect(relogged.session.event_watermark).toBe(initial.session.event_watermark + 1);
  expect(relogged.assessments.find(record => record.assessment.assessment_revision === pending.assessment.assessment_revision)?.feedback).toEqual(committed);
  await expect(lastPage.getByRole("heading", { name: "答對了", exact: true })).toBeVisible();
  const studyUrl = lastPage.url();
  await lastPage.getByRole("button", { name: "教材庫", exact: true }).click();
  await lastPage.getByRole("button", { name: "堆疊講義.pdf", exact: true }).click();
  const completedRead = resumeResponse(lastPage);
  await lastPage.getByRole("button", { name: "開啟學習紀錄 1", exact: true }).click();
  const completed: StudyResumeView = await (await completedRead).json();
  expect(completed.session.status).toBe("completed");
  expect(completed.assessments[0].feedback).not.toBeNull();
  await expect(lastPage.getByRole("heading", { name: "本次學習已完成", exact: true })).toBeVisible();
  await expect(lastPage.getByRole("button", { name: "繼續練習", exact: true })).toHaveCount(0);
  await lastPage.getByRole("button", { name: "教材庫", exact: true }).click();
  await lastPage.getByRole("button", { name: "堆疊講義.pdf", exact: true }).click();
  const noSafeRead = resumeResponse(lastPage);
  await lastPage.getByRole("button", { name: "開啟學習紀錄 2", exact: true }).click();
  const noSafe: StudyResumeView = await (await noSafeRead).json();
  expect(noSafe.session.status).toBe("no_safe");
  expect(noSafe.session.no_safe_claim_ids.length).toBeGreaterThan(0);
  expect(noSafe.progress.deferred_concept_ids).toEqual(noSafe.session.deferred_concept_ids);
  await expect(lastPage.getByRole("heading", { name: "目前沒有適合的新題目", exact: true })).toBeVisible();
  await lastPage.getByRole("button", { name: "登出", exact: true }).click();
  await expect(lastPage.getByRole("heading", { name: "登入您的帳戶" })).toBeVisible();
  await login(lastPage, "library_b@example.com");
  await expect(lastPage.getByText("堆疊講義.pdf", { exact: true })).toHaveCount(0);
  await lastPage.goto(studyUrl);
  await expect(lastPage.getByRole("heading", { name: "無法開啟本次學習", exact: true })).toBeVisible();
  await expect(lastPage.getByText(pendingPrompt, { exact: true })).toHaveCount(0);
  await lastContext.close();
});
