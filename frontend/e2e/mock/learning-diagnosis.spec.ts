import { expect, test } from "@playwright/test";
import { diagnosisView, difficultyView } from "../fixtures/learning-diagnosis";
import { materialId, runId, sessionId, structureRevision, firstConcept, firstClaim, structureView, workspaceView, mockKnowledgeMapApi, json, openMapConcept } from "../fixtures/knowledge-map";

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
    await page.getByRole("region", { name: "測驗紀錄" }).getByRole("button", { name: /^待加強 / }).click();
    await expect(page.getByRole("region", { name: "測驗紀錄" })).toContainText("目前沒有待加強的概念");
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
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
  const difficulties = page.getByRole("region", { name: "測驗紀錄" });
  await expect(difficulties).toContainText("常答錯");
  await expect(difficulties).toContainText("答對 1/3");
  await expect(difficulties.getByRole("button", { name: "複習 Stack" })).toBeEnabled();
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
  const section = page.getByRole("region", { name: "測驗紀錄" });
  await expect(section.locator(".diagnosis-point-list > li strong")).toHaveText(recommendations.map(item => item.label));
  const start = page.waitForRequest(request => request.method() === "POST" && /\/study-start$/.test(new URL(request.url()).pathname));
  await section.getByRole("button", { name: /^複習/ }).first().click();
  expect((await start).postDataJSON().current_concept_id).toBe(recommendations[0].concept_id);
});

test("diagnosis exports a PDF summary for the current material revision", async ({ page }) => {
  await mockKnowledgeMapApi(page);
  await page.route("**/learning-diagnosis", route => json(route, { ...diagnosisView(materialId, structureRevision),
    recommendations: [{ concept_id: firstConcept, label: "Stack", reason: "先複習最近答錯的重點。" }] }));
  let path = "";
  await page.route("**/learning-diagnosis/export/pdf", route => {
    path = decodeURIComponent(new URL(route.request().url()).pathname);
    return route.fulfill({ status: 200, contentType: "application/pdf", headers: { "Content-Disposition": "attachment; filename*=UTF-8''diagnosis.pdf" }, body: "%PDF" });
  });
  await page.goto(mapPath);
  await page.getByRole("tab", { name: "學習與測驗", exact: true }).click();
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "匯出（PDF）", exact: true }).click();
  expect((await download).suggestedFilename()).toBe("diagnosis.pdf");
  expect(path).toBe(`/v1/materials/${materialId}/knowledge-structures/${structureRevision}/learning-diagnosis/export/pdf`);
});

test("completed personal difficulty remains mastered despite a backend recommendation", async ({ page }) => {
  await mockKnowledgeMapApi(page);
  await page.route("**/concept-difficulty", route => json(route, { ...difficultyView(materialId, structureRevision), concepts: [{ ...hard, personal_difficulty: true, answer_count: 6, incorrect_count: 3 }] }));
  await page.route("**/learning-diagnosis", route => json(route, { ...diagnosisView(materialId, structureRevision), strengths: [{ concept_id: firstConcept, label: "Stack", points: [{ claim_id: firstClaim, title: "已完成的重點", answer_count: 6, correct_count: 3, latest_is_correct: true, consecutive_correct_count: 2, required_consecutive_correct: 2, completed: true, evidence: [] }] }], recommendations: [{ concept_id: firstConcept, label: "Stack", reason: "答錯 3/6 題，是你的難點。" }] }));
  await page.goto(mapPath); await page.getByRole("tab", { name: "學習與測驗", exact: true }).click();
  await expect(page.getByRole("region", { name: "測驗紀錄" }).getByRole("button", { name: "待加強 0", exact: true })).toBeVisible();
  const diagnosis = page.getByRole("region", { name: "測驗紀錄" });
  await diagnosis.getByRole("button", { name: /^待加強 / }).click();
  await expect(diagnosis).toContainText("目前沒有待加強的概念");
  await diagnosis.getByRole("button", {name:/^已掌握 /}).click();
  await expect(diagnosis).toContainText("Stack常答錯");
  await expect(diagnosis).toContainText("答對 3/6");
});
