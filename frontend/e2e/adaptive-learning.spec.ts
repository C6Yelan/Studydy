import { expect, test, type Page, type Route } from "@playwright/test";

import {
  adaptiveView,
  assessmentView,
  contextView,
  feedbackView,
  learningStateView,
  mapRevision,
  mapView,
  materialId,
  firstConceptId,
  runId,
  runView,
  sessionView,
  studySessionId,
  targetConceptId,
  weaknessView,
} from "./fixtures/learning";

const newStudySessionId = "bf9619ff-8b86-4e3a-a2f1-2bb9424d5c88";

async function fulfillJson(route: Route, json: unknown, status = 200) {
  await route.fulfill({ status, json });
}

async function baseRoutes(page: Page, knowledgeMap = mapView()) {
  await page.route("**/v1/session/refresh", (route) => route.fulfill({ status: 204 }));
  await page.route(`**/v1/material-processing-runs/${runId}`, (route) => fulfillJson(route, runView()));
  await page.route("**/v1/materials/*/knowledge-maps/**", (route) => fulfillJson(route, knowledgeMap));
}

async function capture(page: Page, filename: string, selector?: string) {
  if (process.env.STUDYDY_CAPTURE_ACCEPTANCE !== "1") return;
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.locator(".app-main").evaluate((main) => { main.scrollTop = 0; });
  if (selector) await page.locator(selector).screenshot({ path: `../docs_local/p07_acceptance/${filename}` });
  else await page.screenshot({ path: `../docs_local/p07_acceptance/${filename}`, fullPage: true });
}

function bindSession<T>(value: T, nextId: string): T {
  const next = structuredClone(value) as T & Record<string, unknown>;
  const replace = (item: unknown) => {
    if (!item || typeof item !== "object") return;
    const record = item as Record<string, unknown>;
    if (record.study_session_id === studySessionId) record.study_session_id = nextId;
    Object.values(record).forEach(replace);
  };
  replace(next);
  return next;
}

test("no-safe 暫緩可前進、重整保存並依順序回到原重點", async ({ page }) => {
  const knowledgeMap = mapView();
  await baseRoutes(page, knowledgeMap);
  let phase: "defer" | "advanced" | "resume" | "returned" = "defer";
  const isAdvanced = () => phase === "advanced" || phase === "resume";
  const eventWatermark = () => phase === "resume" || phase === "returned" ? 1 : 0;
  const currentConceptId = () =>
    phase === "defer" || phase === "returned" ? firstConceptId : targetConceptId;
  const noSafeDeferredIds = () => isAdvanced() ? [firstConceptId] : [];
  const currentAdaptive = () => {
    if (phase === "defer") return adaptiveView({
      action: "defer",
      currentConceptId: firstConceptId,
      planValue: "6",
      targetConceptId,
      targetLabel: "目標概念",
    });
    if (phase === "resume") return adaptiveView({
      action: "resume",
      currentConceptId: targetConceptId,
      eventWatermark: 1,
      noSafeDeferredConceptIds: [firstConceptId],
      planValue: "8",
      targetConceptId: firstConceptId,
      targetLabel: "第一個概念",
    });
    return adaptiveView({
      action: "collect_more_data",
      currentConceptId: currentConceptId(),
      eventWatermark: eventWatermark(),
      noSafeDeferredConceptIds: noSafeDeferredIds(),
      planValue: phase === "advanced" ? "7" : "9",
      targetConceptId: currentConceptId(),
      targetLabel: phase === "advanced" ? "目標概念" : "第一個概念",
    });
  };

  await page.route(`**/v1/study-sessions/${studySessionId}`, (route) => fulfillJson(route, sessionView({
    current_formal_concept_id: currentConceptId(),
    no_safe_deferred_formal_concept_ids: noSafeDeferredIds(),
    event_watermark: eventWatermark(),
  })));
  await page.route(`**/v1/study-sessions/${studySessionId}/context`, (route) => fulfillJson(route, contextView({
    current_formal_concept_id: currentConceptId(),
    no_safe_deferred_formal_concept_ids: noSafeDeferredIds(),
  })));
  await page.route(`**/v1/study-sessions/${studySessionId}/learning-state`, (route) => fulfillJson(route, learningStateView({
    eventWatermark: eventWatermark(),
    firstStatus: "not_started",
    targetStatus: phase === "resume" || phase === "returned" ? "mastered" : "not_started",
  })));
  await page.route(`**/v1/study-sessions/${studySessionId}/weakness`, (route) => fulfillJson(route, weaknessView({
    currentConceptId: currentConceptId(),
    eventWatermark: eventWatermark(),
  })));
  await page.route(`**/v1/study-sessions/${studySessionId}/adaptive-plan`, (route) =>
    fulfillJson(route, currentAdaptive()));
  await page.route(`**/v1/study-sessions/${studySessionId}/adaptive-plan/apply`, async (route) => {
    expect((await route.request().postDataJSON()).adaptive_plan_revision).toBe(
      currentAdaptive().plan.adaptive_plan_revision,
    );
    phase = phase === "defer" ? "advanced" : "returned";
    await fulfillJson(route, sessionView({
      current_formal_concept_id: currentConceptId(),
      no_safe_deferred_formal_concept_ids: noSafeDeferredIds(),
      event_watermark: eventWatermark(),
    }));
  });
  await page.route(`**/v1/study-sessions/${studySessionId}/assessments`, (route) =>
    fulfillJson(route, assessmentView(1), 201));
  await page.route(`**/v1/study-sessions/${studySessionId}/assessments/*/submissions`, async (route) => {
    const assessment = assessmentView(1);
    const body = await route.request().postDataJSON();
    phase = "resume";
    await fulfillJson(route, feedbackView(assessment, body.selected_option_id, true, 1), 201);
  });

  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto(`/materials/${materialId}/runs/${runId}/knowledge-maps/${encodeURIComponent(mapRevision)}/study-sessions/${studySessionId}`);
  await expect(page.getByRole("heading", { name: "先前往下一個教材重點" })).toBeVisible();
  await page.getByRole("button", { name: "前往下一個重點" }).click();
  await expect(page.getByRole("heading", { name: "目標概念", exact: true }).first()).toBeVisible();
  await expect(page.getByText("稍後回到這裡")).toBeVisible();

  await page.setViewportSize({ width: 390, height: 844 });
  await page.reload();
  await expect(page.getByRole("heading", { name: "目標概念", exact: true }).first()).toBeVisible();
  await expect(page.getByText("稍後回到這裡")).toBeVisible();
  await page.getByRole("button", { name: "開始評量" }).click();
  await page.getByRole("radio", { name: /選項 A/ }).check();
  await page.getByRole("button", { name: "送出答案" }).click();
  await expect(page.getByRole("heading", { name: "回到先前的教材重點" })).toBeVisible();
  await page.getByRole("button", { name: "回到暫緩重點" }).click();
  await expect(page.getByRole("heading", { name: "第一個概念", exact: true }).first()).toBeVisible();
  await expect(page.getByText("稍後回到這裡")).toHaveCount(0);
  expect(await page.locator("body").innerText()).not.toMatch(
    /no[-_ ]safe|canonical|StudySession|Formal Concept|AnswerEvent/i,
  );
});

test("新 StudySession 不繼承前一個 session 的 mastery 或 weakness", async ({ page }) => {
  await baseRoutes(page);
  const oldLearning = learningStateView({ eventWatermark: 2, firstStatus: "mastered", targetStatus: "needs_review" });
  const oldWeakness = weaknessView({ category: "observed_weak", currentConceptId: firstConceptId, eventWatermark: 2 });
  const oldAdaptive = adaptiveView({
    action: "no_action",
    currentConceptId: firstConceptId,
    eventWatermark: 2,
    targetConceptId: null,
    targetLabel: null,
  });
  await page.route(`**/v1/study-sessions/${studySessionId}`, (route) => fulfillJson(route, sessionView({
    current_formal_concept_id: firstConceptId,
    event_watermark: 2,
  })));
  await page.route(`**/v1/study-sessions/${studySessionId}/context`, (route) => fulfillJson(route, contextView({ current_formal_concept_id: firstConceptId })));
  await page.route(`**/v1/study-sessions/${studySessionId}/learning-state`, (route) => fulfillJson(route, oldLearning));
  await page.route(`**/v1/study-sessions/${studySessionId}/weakness`, (route) => fulfillJson(route, oldWeakness));
  await page.route(`**/v1/study-sessions/${studySessionId}/adaptive-plan`, (route) => fulfillJson(route, oldAdaptive));
  await page.route(`**/v1/study-sessions/${studySessionId}/complete`, (route) => fulfillJson(route, sessionView({
    current_formal_concept_id: firstConceptId,
    event_watermark: 2,
    status: "completed",
    completed_at: "2026-08-27T00:30:00Z",
  })));

  await page.goto(`/materials/${materialId}/runs/${runId}/knowledge-maps/${encodeURIComponent(mapRevision)}/study-sessions/${studySessionId}`);
  await expect(page.getByText("本次已掌握")).toBeVisible();
  await page.getByText("查看需要留意的學習觀察").click();
  await expect(page.getByText("已觀察到的弱點")).toBeVisible();
  await page.getByRole("button", { name: "完成本次學習" }).click();
  await page.getByRole("button", { name: /回到知識地圖/ }).click();

  await page.route("**/v1/study-sessions", (route) => fulfillJson(route, bindSession(sessionView(), newStudySessionId), 201));
  await page.route(`**/v1/study-sessions/${newStudySessionId}`, (route) => fulfillJson(route, bindSession(sessionView(), newStudySessionId)));
  await page.route(`**/v1/study-sessions/${newStudySessionId}/context`, (route) => fulfillJson(route, bindSession(contextView(), newStudySessionId)));
  await page.route(`**/v1/study-sessions/${newStudySessionId}/learning-state`, (route) => fulfillJson(route, bindSession(learningStateView(), newStudySessionId)));
  await page.route(`**/v1/study-sessions/${newStudySessionId}/weakness`, (route) => fulfillJson(route, bindSession(weaknessView(), newStudySessionId)));
  await page.route(`**/v1/study-sessions/${newStudySessionId}/adaptive-plan`, (route) => fulfillJson(route, bindSession(adaptiveView(), newStudySessionId)));

  await page.getByRole("button", { name: "教材概念：目標概念" }).click();
  await page.getByRole("button", { name: "從這個概念開始" }).click();
  await expect(page).toHaveURL(new RegExp(`/study-sessions/${newStudySessionId}$`));
  await expect(page.getByText("尚未開始", { exact: true })).toBeVisible();
  await expect(page.getByText(/另有 1 個概念尚未開始/)).toBeVisible();
  await expect(page.getByText("本次已掌握")).toHaveCount(0);
  await expect(page.getByText("已觀察到的弱點")).toHaveCount(0);
});
