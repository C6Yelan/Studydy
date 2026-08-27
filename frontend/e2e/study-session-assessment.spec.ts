import { expect, test, type Page, type Route } from "@playwright/test";

import {
  adaptiveView,
  apiError,
  assessmentView,
  contextView,
  feedbackView,
  learningStateView,
  mapRevision,
  mapView,
  materialId,
  runId,
  runView,
  sessionView,
  studySessionId,
  targetConceptId,
  weaknessView,
} from "./fixtures/learning";

async function captureAcceptance(page: Page, filename: string) {
  if (process.env.STUDYDY_CAPTURE_ACCEPTANCE !== "1") return;
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.locator(".app-main").evaluate((main) => { main.scrollTop = 0; });
  await page.locator("img").evaluateAll(async (images) => {
    await Promise.all(images.map((item) => item.decode().catch(() => undefined)));
  });
  await page.screenshot({ path: `../docs_local/p07_acceptance/${filename}`, fullPage: true });
}

async function fulfillJson(route: Route, json: unknown, status = 200) {
  await route.fulfill({ status, json });
}

async function learningRoutes(page: Page, eventWatermark: () => number = () => 0) {
  await page.route("**/v1/session/refresh", (route) => route.fulfill({ status: 204 }));
  await page.route(`**/v1/material-processing-runs/${runId}`, (route) => fulfillJson(route, runView()));
  await page.route("**/v1/materials/*/knowledge-maps/**", (route) => fulfillJson(route, mapView()));
  await page.route("**/v1/study-sessions", async (route) => {
    expect(route.request().method()).toBe("POST");
    expect(await route.request().postDataJSON()).toEqual({
      schema: "study-session-create/v1",
      material_id: materialId,
      knowledge_map_revision: mapRevision,
      current_formal_concept_id: targetConceptId,
    });
    await fulfillJson(route, sessionView(), 201);
  });
  await page.route(`**/v1/study-sessions/${studySessionId}`, (route) => fulfillJson(route, sessionView({ event_watermark: eventWatermark() })));
  await page.route(`**/v1/study-sessions/${studySessionId}/context`, (route) => fulfillJson(route, contextView()));
  await page.route(`**/v1/study-sessions/${studySessionId}/learning-state`, (route) => fulfillJson(route, learningStateView({ eventWatermark: eventWatermark() })));
  await page.route(`**/v1/study-sessions/${studySessionId}/weakness`, (route) => fulfillJson(route, weaknessView({ eventWatermark: eventWatermark() })));
  await page.route(`**/v1/study-sessions/${studySessionId}/adaptive-plan`, (route) => fulfillJson(route, adaptiveView({ eventWatermark: eventWatermark() })));
}

async function openStudySession(page: Page) {
  await page.goto(`/materials/${materialId}/runs/${runId}/knowledge-maps/${encodeURIComponent(mapRevision)}`);
  await expect(page.getByRole("heading", { name: "知識地圖", exact: true })).toBeVisible();
  await page.locator(".concept-card").filter({ hasText: "目標概念" }).click();
  await page.getByRole("button", { name: "從這個概念開始" }).click();
  await expect(page).toHaveURL(new RegExp(`/study-sessions/${studySessionId}$`));
  await expect(page.getByRole("heading", { name: "目標概念", exact: true }).first()).toBeVisible();
}

test("StudySession assessment：錯誤回饋、新題重評與完成", async ({ page }) => {
  let eventWatermark = 0;
  await learningRoutes(page, () => eventWatermark);
  let assessmentRound = 0;
  await page.route(`**/v1/study-sessions/${studySessionId}/assessments`, async (route) => {
    assessmentRound += 1;
    const request = await route.request().postDataJSON();
    expect(request).toEqual({ schema: "assessment-create/v1", target_claim_id: mapView().concepts[1].claims[0].claim_id });
    await fulfillJson(route, assessmentView(assessmentRound), 201);
  });
  let submissionRound = 0;
  await page.route(`**/v1/study-sessions/${studySessionId}/assessments/*/submissions`, async (route) => {
    submissionRound += 1;
    eventWatermark = submissionRound;
    const request = await route.request().postDataJSON();
    expect(Object.keys(request).sort()).toEqual(["question_id", "schema", "selected_option_id"]);
    const assessment = assessmentView(submissionRound);
    expect(request.question_id).toBe(assessment.question_id);
    await fulfillJson(route, feedbackView(assessment, request.selected_option_id, submissionRound === 2, submissionRound), 201);
  });
  await page.route(`**/v1/study-sessions/${studySessionId}/complete`, (route) => fulfillJson(route, sessionView({
    status: "completed",
    completed_at: "2026-08-27T00:30:00Z",
    event_watermark: 2,
  })));

  await openStudySession(page);
  await captureAcceptance(page, "09_study_current_concept.png");
  await page.getByRole("button", { name: "開始評量" }).click();
  await expect(page.getByRole("heading", { name: "哪個選項符合目標概念？" })).toBeVisible();
  await expect(page.getByRole("radio")).toHaveCount(4);
  expect(await page.locator("body").innerHTML()).not.toMatch(/correct_option|answer_key|private_answer|generation_provenance/i);
  await captureAcceptance(page, "10_assessment.png");

  await page.getByRole("radio", { name: /選項 B/ }).check();
  await page.getByRole("button", { name: "送出答案" }).click();
  await expect(page.getByRole("heading", { name: "這題需要再想一下" })).toBeVisible();
  await expect(page.getByText("這個選項與教材 Evidence 不一致。")).toBeVisible();
  await captureAcceptance(page, "11_feedback.png");

  await page.getByRole("button", { name: "取得新題目" }).click();
  await expect(page.getByRole("heading", { name: "重新評量：哪個敘述符合教材？" })).toBeVisible();
  await page.getByRole("radio", { name: /選項 A/ }).check();
  await page.getByRole("button", { name: "送出答案" }).click();
  await expect(page.getByRole("heading", { name: "答對了" })).toBeVisible();
  expect(assessmentRound).toBe(2);
  expect(submissionRound).toBe(2);

  await page.getByRole("button", { name: "完成本次學習" }).click();
  await expect(page.getByRole("heading", { name: "本次學習已完成" })).toBeVisible();
  await expect(page.getByText(/只屬於這次 StudySession/)).toBeVisible();
  await captureAcceptance(page, "15_completed_session.png");
});

test("Assessment 沒有新安全題目時提供可理解 fallback", async ({ page }) => {
  await learningRoutes(page);
  await page.route(`**/v1/study-sessions/${studySessionId}/assessments`, (route) =>
    fulfillJson(route, apiError("RESOURCE_NOT_FOUND"), 404));

  await page.goto(`/materials/${materialId}/runs/${runId}/knowledge-maps/${encodeURIComponent(mapRevision)}/study-sessions/${studySessionId}`);
  await expect(page.getByRole("heading", { name: "目標概念", exact: true }).first()).toBeVisible();
  await page.getByRole("button", { name: "開始評量" }).click();
  await expect(page.getByRole("heading", { name: "目前沒有新的安全題目" })).toBeVisible();
  await expect(page.getByText(/先回到教材重點/)).toBeVisible();
});

test("Assessment stale/idempotency conflict 不會在 client 端猜測結果", async ({ page }) => {
  await learningRoutes(page);
  await page.route(`**/v1/study-sessions/${studySessionId}/assessments`, (route) => fulfillJson(route, assessmentView(1), 201));
  await page.route(`**/v1/study-sessions/${studySessionId}/assessments/*/submissions`, (route) =>
    fulfillJson(route, apiError("IDEMPOTENCY_CONFLICT"), 409));

  await page.goto(`/materials/${materialId}/runs/${runId}/knowledge-maps/${encodeURIComponent(mapRevision)}/study-sessions/${studySessionId}`);
  await page.getByRole("button", { name: "開始評量" }).click();
  await page.getByRole("radio", { name: /選項 A/ }).check();
  await page.getByRole("button", { name: "送出答案" }).click();
  await expect(page.getByRole("alert")).toContainText("與較新的學習狀態衝突");
  await expect(page.getByRole("heading", { name: "答對了" })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "這題需要再想一下" })).toHaveCount(0);
});
