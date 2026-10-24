import { expect, test } from "@playwright/test";
import { diagnosisView, difficultyView } from "../fixtures/learning-diagnosis";
import { materialId, runId, sessionId, structureRevision, firstConcept, firstClaim, structureView, workspaceView, mockKnowledgeMapApi, json, openMapConcept } from "../fixtures/knowledge-map";
import { studyLayoutFixture } from "../fixtures/study-layout.mjs";

const mapPath = `/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}`;
const hard = { concept_id: firstConcept, label: "Stack", level: "hard" as const, reason: "需先學 3 層先備概念。", personal_difficulty: false, incorrect_count: 0, answer_count: 0 };

for (const width of [1366, 390]) {
  test(`structural difficulty and review entry at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await mockKnowledgeMapApi(page);
    await page.route("**/concept-difficulty", route => json(route, { ...difficultyView(materialId, structureRevision), concepts: [hard] }));
    await page.goto(mapPath);
    await expect(page.locator(".react-flow__node").filter({ hasText: "Stack" })).toContainText("預估偏難");
    await openMapConcept(page, "Stack");
    await expect(page.getByRole("dialog", { name: "概念詳情" })).toContainText(hard.reason);
    await page.keyboard.press("Escape");
    await page.getByRole("tab", { name: "學習與測驗", exact: true }).click();
    const difficulties = page.getByRole("region", { name: "教材難點" });
    await expect(difficulties).toContainText(hard.reason);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    const start = page.waitForRequest(request => request.method() === "POST" && /\/study-start$/.test(new URL(request.url()).pathname));
    await difficulties.getByRole("button", { name: "複習 Stack" }).click();
    expect((await start).postDataJSON().current_concept_id).toBe(firstConcept);
  });
}

test("personal difficulty is independent of structural difficulty", async ({ page }) => {
  await mockKnowledgeMapApi(page);
  await page.route("**/concept-difficulty", route => json(route, { ...difficultyView(materialId, structureRevision),
    concepts: [{ ...hard, level: "standard", reason: "包含 1 個重點。", personal_difficulty: true, incorrect_count: 2, answer_count: 3 }] }));
  await page.goto(mapPath);
  await expect(page.locator(".react-flow__node").filter({ hasText: "Stack" }).locator(".difficulty-badge")).toHaveText(["你的難點"]);
  await openMapConcept(page, "Stack");
  await expect(page.getByRole("dialog", { name: "概念詳情" })).toContainText("答錯率 67%（2 / 3 題）");
  await page.keyboard.press("Escape");
  await page.getByRole("tab", { name: "學習與測驗", exact: true }).click();
  const difficulties = page.getByRole("region", { name: "教材難點" });
  await expect(difficulties).toContainText("你的難點");
  await expect(difficulties).toContainText("答錯率 67%（2 / 3 題）");
  await expect(difficulties.getByRole("button", { name: "複習 Stack" })).toBeEnabled();
});

for (const width of [1366, 390]) test(`material diagnosis lists short titles, real counts and sources at ${width}px`, async ({ page }) => {
  await page.setViewportSize({ width, height: 900 });
  await mockKnowledgeMapApi(page);
  const point = { claim_id: firstClaim, title: "後進先出的操作順序…", answer_count: 3, correct_count: 2, latest_is_correct: false,
    evidence: structureView().concepts[0].claims[0].evidence.map(({ evidence_id, page }) => ({ evidence_id, page })) };
  const finding = { concept_id: firstConcept, label: "Stack", points: [point] };
  await page.route("**/learning-diagnosis", route => json(route, { ...diagnosisView(materialId, structureRevision),
    weaknesses: [finding], recommendations: [{ concept_id: firstConcept, label: "Stack", reason: "1 個重點最近答錯。" }] }));
  await page.goto(mapPath);
  await page.getByRole("tab", { name: "學習與測驗", exact: true }).click();
  const diagnosis = page.getByRole("region", { name: "學習診斷", exact: true });
  await expect(diagnosis.getByRole("heading", { name: "弱點", exact: true })).toBeVisible();
  await expect(diagnosis.getByRole("heading", { name: "優勢", exact: true })).toHaveCount(0);
  await expect(diagnosis).toContainText(point.title);
  await expect(diagnosis).toContainText("答對 2 / 3 次");
  await expect(diagnosis).toContainText("最近答錯");
  const recommendations = diagnosis.getByRole("region", { name: "建議先複習" });
  await expect(recommendations.getByRole("listitem")).toHaveText(["Stack1 個重點最近答錯。複習 Stack"]);
  await diagnosis.getByRole("button", { name: "查看第 1 頁來源" }).click();
  await expect(page.getByRole("dialog", { name: "教材來源" })).toBeVisible();
  await expect(page.getByRole("link", { name: "開啟 PDF 來源頁" })).toHaveAttribute("href", /#page=1$/);
  await page.keyboard.press("Escape");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: test.info().outputPath("diagnosis-points.png"), fullPage: true });
  await page.reload();
  await expect(diagnosis).toContainText(point.title);
});

test("recommendations preserve the three supplied concepts and their review targets", async ({ page }) => {
  const view = workspaceView(3, true);
  await mockKnowledgeMapApi(page, view);
  const recommendations = [view.concepts[2], view.concepts[0], view.concepts[1]].map((concept, index) => ({
    concept_id: concept.concept_id, label: concept.label, reason: `${3 - index} 個重點最近答錯。`,
  }));
  await page.route("**/learning-diagnosis", route => json(route, { ...diagnosisView(materialId, structureRevision), recommendations }));
  await page.goto(mapPath);
  await page.getByRole("tab", { name: "學習與測驗", exact: true }).click();
  const section = page.getByRole("region", { name: "建議先複習" });
  await expect(section.locator("li strong")).toHaveText(recommendations.map(item => item.label));
  const start = page.waitForRequest(request => request.method() === "POST" && /\/study-start$/.test(new URL(request.url()).pathname));
  await section.getByRole("button").first().click();
  expect((await start).postDataJSON().current_concept_id).toBe(recommendations[0].concept_id);
});

test("set diagnosis reads the selected result and opens its concept for review", async ({ page }) => {
  const fixture = await studyLayoutFixture(page, "completed", {});
  const concept = fixture.knowledgeStructure.concepts[0];
  await page.route("**/learning-diagnosis?*", route => {
    const url = new URL(route.request().url()), parts = decodeURIComponent(url.pathname).split("/");
    return json(route, { ...diagnosisView(parts[3], parts[5], url.searchParams.get("set_id")),
      recommendations: [{ concept_id: concept.concept_id, label: concept.label, reason: "2 個重點最近答錯。" }] });
  });
  await fixture.open();
  await expect(page.locator(".assessment-set-summary")).toContainText("答對2 / 4 題");
  const diagnosis = page.getByRole("region", { name: "本組學習診斷" });
  await expect(diagnosis).toContainText("2 個重點最近答錯。");
  await diagnosis.getByRole("button", { name: `複習 ${concept.label}` }).click();
  await expect(page).toHaveURL(fixture.path.split("/study-sessions/")[0]);
  expect(await page.evaluate(() => window.history.state.knowledgeMap.detail)).toBe(concept.concept_id);
});

test("a failed diagnosis read preserves the score and can be retried", async ({ page }) => {
  const fixture = await studyLayoutFixture(page, "completed", {});
  let failed = true;
  await page.route("**/learning-diagnosis?*", route => {
    if (failed) return json(route, { schema: "api-error/v1", request_id: sessionId, reason_code: "REQUEST_INVALID", retryable: true, message: "Request could not be completed." }, 503);
    const url = new URL(route.request().url()), parts = decodeURIComponent(url.pathname).split("/");
    return json(route, { ...diagnosisView(parts[3], parts[5], url.searchParams.get("set_id")),
      recommendations: [{ concept_id: fixture.knowledgeStructure.concepts[0].concept_id, label: "伺服器", reason: "2 個重點最近答錯。" }] });
  });
  await fixture.open();
  const diagnosis = page.getByRole("region", { name: "本組學習診斷" });
  await expect(diagnosis).toContainText("診斷暫時無法讀取");
  await expect(page.locator(".assessment-set-summary")).toContainText("答對2 / 4 題");
  failed = false;
  await diagnosis.getByRole("button", { name: "重新讀取診斷" }).click();
  await expect(diagnosis).toContainText("2 個重點最近答錯。");
  await page.locator(".assessment-answer-review summary").click();
  await expect(page.locator(".assessment-answer-review .feedback-card")).toHaveCount(4);
});
