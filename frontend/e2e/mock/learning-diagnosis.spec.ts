import { expect, test } from "@playwright/test";
import { diagnosisView, difficultyView } from "../fixtures/learning-diagnosis";
import { materialId, runId, structureRevision, firstConcept, firstClaim, structureView, mockKnowledgeMapApi, json, openMapConcept } from "../fixtures/knowledge-map";
import { studyLayoutFixture } from "../fixtures/study-layout.mjs";

const mapPath = `/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}`;
const hard = { concept_id: firstConcept, label: "Stack", level: "hard" as const, reason: "需要先理解後進先出的操作順序。", personal_difficulty: false, incorrect_count: 0, answer_count: 0 };

for (const width of [1366, 390]) {
  test(`difficulty before answers and review entry at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await mockKnowledgeMapApi(page);
    await page.route("**/concept-difficulty", route => json(route, { ...difficultyView(materialId, structureRevision), concepts: [hard] }));
    await page.goto(mapPath);
    await expect(page.locator(".react-flow__node").filter({ hasText: "Stack" })).toContainText("偏難");
    await openMapConcept(page, "Stack");
    await expect(page.getByRole("dialog", { name: "概念詳情" })).toContainText(hard.reason);
    await page.keyboard.press("Escape");
    await page.getByRole("tab", { name: "學習與測驗", exact: true }).click();
    await expect(page.getByRole("region", { name: "學習診斷", exact: true })).toContainText("還沒有作答紀錄");
    const difficulties = page.getByRole("region", { name: "教材難點" });
    await expect(difficulties).toContainText(hard.reason);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: test.info().outputPath("material-diagnosis.png"), fullPage: true });
    const start = page.waitForRequest(request => request.method() === "POST" && /\/assessment-sets$/.test(new URL(request.url()).pathname));
    await difficulties.getByRole("button", { name: "複習 Stack" }).click();
    expect((await start).postDataJSON().target_concept_id).toBe(firstConcept);
  });
}

for (const [status, failureReason] of [["pending", null], ["failed", "DIFFICULTY_CALL_BUDGET_EXCEEDED"], ["failed", "DIFFICULTY_INPUT_TOO_LARGE"], ["not_generated", null]] as const) {
  test(`personal difficulty stays visible when model evaluation is ${failureReason ?? status}`, async ({ page }) => {
    await mockKnowledgeMapApi(page);
    await page.route("**/concept-difficulty", route => json(route, { ...difficultyView(materialId, structureRevision), status, failure_reason: failureReason,
      concepts: [{ ...hard, level: null, reason: null, personal_difficulty: true, incorrect_count: 2, answer_count: 3 }] }));
    await page.goto(mapPath);
    const node = page.locator(".react-flow__node").filter({ hasText: "Stack" });
    await expect(node).toContainText("你的難點");
    await openMapConcept(page, "Stack");
    const detail = page.getByRole("dialog", { name: "概念詳情" });
    await expect(detail).toContainText("答錯率 67%（2 / 3 題）");
    await expect(detail.locator(".concept-difficulty")).not.toContainText("一般");
    await page.keyboard.press("Escape");
    await page.getByRole("tab", { name: "學習與測驗", exact: true }).click();
    const difficulties = page.getByRole("region", { name: "教材難點" });
    await expect(difficulties).toContainText("你的難點");
    await expect(difficulties).toContainText("答錯率 67%（2 / 3 題）");
    if (failureReason) await expect(difficulties).toContainText("難度評估尚未完成");
    if (failureReason === "DIFFICULTY_CALL_BUDGET_EXCEEDED") await expect(difficulties).toContainText("目前上限（5 次）");
    await expect(difficulties.getByRole("button", { name: "複習 Stack" })).toBeEnabled();
  });
}

for (const width of [1366, 390]) test(`material diagnosis lists short titles, real counts and sources at ${width}px`, async ({ page }) => {
  await page.setViewportSize({ width, height: 900 });
  await mockKnowledgeMapApi(page);
  const point = { claim_id: firstClaim, title: "後進先出的操作順序…", answer_count: 3, correct_count: 2, latest_is_correct: false,
    evidence: structureView().concepts[0].claims[0].evidence.map(({ evidence_id, page }) => ({ evidence_id, page })) };
  const finding = { concept_id: firstConcept, label: "Stack", points: [point] };
  await page.route("**/learning-diagnosis", route => json(route, { ...diagnosisView(materialId, structureRevision), status: "ready", event_watermark: 3,
    strengths: [], weaknesses: [finding], recommendations: [finding] }));
  await page.route("**/concept-difficulty", route => json(route, { ...difficultyView(materialId, structureRevision), concepts: [hard] }));
  await page.goto(mapPath);
  await page.getByRole("tab", { name: "學習與測驗", exact: true }).click();
  const diagnosis = page.getByRole("region", { name: "學習診斷", exact: true });
  for (const heading of ["弱點", "建議先複習"]) await expect(diagnosis.getByRole("heading", { name: heading, exact: true })).toBeVisible();
  await expect(diagnosis.getByRole("heading", { name: "優勢", exact: true })).toHaveCount(0);
  await expect(diagnosis).toContainText(point.title);
  await expect(diagnosis).toContainText("答對 2 / 3 次");
  await expect(diagnosis).toContainText("最近答錯");
  await expect(diagnosis).not.toContainText(/。；|。。|目前還沒有已完成重點/);
  await diagnosis.getByRole("button", { name: "查看第 1 頁來源" }).first().click();
  await expect(page.getByRole("dialog", { name: "教材來源" })).toBeVisible();
  await expect(page.getByRole("link", { name: "開啟 PDF 來源頁" })).toHaveAttribute("href", /#page=1$/);
  await page.keyboard.press("Escape");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: test.info().outputPath("diagnosis-points.png"), fullPage: true });
  const difficulties = page.getByRole("region", { name: "教材難點" });
  await expect(difficulties).toContainText("偏難");
  await page.reload();
  await expect(diagnosis).toContainText(point.title);
});

test("completed score stays visible while diagnosis finishes and is read again after reload", async ({ page }) => {
  const fixture = await studyLayoutFixture(page, "completed", {});
  let ready = false;
  let reads = 0;
  await page.route("**/learning-diagnosis?*", route => {
    reads++;
    const url = new URL(route.request().url());
    const parts = decodeURIComponent(url.pathname).split("/");
    const value = diagnosisView(parts[3], parts[5], url.searchParams.get("set_id"));
    return json(route, { ...value, status: ready ? "ready" : "pending", event_watermark: 4,
      recommendations: ready ? [{ concept_id: fixture.knowledgeStructure.concepts[0].concept_id, label: "伺服器", points: [{
        claim_id: fixture.knowledgeStructure.concepts[0].claims[0].claim_id, title: "用戶端與伺服器的角色", answer_count: 1, correct_count: 0, latest_is_correct: false,
        evidence: fixture.knowledgeStructure.concepts[0].claims[0].evidence,
      }] }] : [] });
  });
  await fixture.open();
  await expect(page.locator(".assessment-set-summary")).toContainText("答對2 / 4 題");
  const diagnosis = page.getByRole("region", { name: "本組學習診斷" });
  await expect(diagnosis).toContainText("正在根據作答整理");
  ready = true;
  await expect(diagnosis).toContainText("用戶端與伺服器的角色");
  expect(reads).toBeGreaterThanOrEqual(2);
  await page.reload();
  await expect(diagnosis).toContainText("用戶端與伺服器的角色");
  expect(fixture.requests.filter((request: { path: string }) => request.path.includes("diagnosis"))).toHaveLength(0);
});

test("diagnosis failure leaves the saved score and answer review available", async ({ page }) => {
  const fixture = await studyLayoutFixture(page, "completed", {});
  await page.route("**/learning-diagnosis?*", route => {
    const url = new URL(route.request().url()), parts = decodeURIComponent(url.pathname).split("/");
    return json(route, { ...diagnosisView(parts[3], parts[5], url.searchParams.get("set_id")), status: "failed", failure_reason: "PROVIDER_FAILED" });
  });
  await fixture.open();
  await expect(page.getByRole("region", { name: "本組學習診斷" })).toContainText("作答結果已保留");
  await expect(page.locator(".assessment-set-summary")).toContainText("答對2 / 4 題");
  await page.locator(".assessment-answer-review summary").click();
  await expect(page.locator(".assessment-answer-review .feedback-card")).toHaveCount(4);
});

test("historical records without a report do not look like unanswered or running work", async ({ page }) => {
  await mockKnowledgeMapApi(page);
  await page.route("**/learning-diagnosis", route => json(route, { ...diagnosisView(materialId, structureRevision), status: "not_generated", event_watermark: 4 }));
  await page.route("**/concept-difficulty", route => json(route, { ...difficultyView(materialId, structureRevision), status: "not_generated" }));
  await page.goto(mapPath);
  await page.getByRole("tab", { name: "學習與測驗", exact: true }).click();
  await expect(page.getByRole("region", { name: "學習診斷", exact: true })).toContainText("既有作答尚未產生學習診斷");
  await expect(page.getByRole("region", { name: "教材難點" })).toContainText("這個教材版本尚未產生難度評估");
});
