import { answerPaper } from "../fixtures/answer-paper";
import { expect, test, type Page } from "@playwright/test";
import type { StartingLevel } from "../../src/api/contracts";
import { materialId, runId, sessionId, structureRevision, firstConcept, secondConcept, progress, session, run, artifactId, mockKnowledgeMapApi, json, openMapConcept } from "../fixtures/knowledge-map";
import { studyLayoutFixture } from "../fixtures/study-layout.mjs";
import { outlineView } from "../fixtures/knowledge-read-views.mjs";

const mapPath = `/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}`;

async function levelMap(page: Page, initial: StartingLevel | null, saved = false) {
  let level = initial;
  const mutations: { path: string; body: any }[] = [];
  await mockKnowledgeMapApi(page, undefined, () => ({ ...progress, starting_level: level as any, learning_path: [secondConcept, firstConcept] }));
  if (saved) await page.route(`**/v1/materials/${materialId}`, route => json(route, {
    schema: "material-library-item/v1", material_id: materialId, source_artifact_id: artifactId, display_name: "資料結構", size_bytes: 100,
    created_at: run.created_at, latest_attempt: run, head_revision: structureRevision,
    available_structures: [{ run_id: runId, knowledge_structure_revision: structureRevision, created_at: run.created_at, status: "succeeded" }],
    study_sessions: [{ ...session(), run_id: runId }],
  }));
  await page.route(`**/v1/materials/${materialId}/starting-level`, route => {
    if (route.request().method() === "PUT") {
      const body = route.request().postDataJSON();
      level = body.starting_level;
      mutations.push({ path: "level", body });
    }
    return json(route, { schema: "starting-level/v1", material_id: materialId, starting_level: level });
  });
  await page.route(`**/v1/materials/${materialId}/study-start`, route => {
    const body = route.request().postDataJSON(); mutations.push({ path: "start", body });
    if (level === null) return json(route, { schema: "api-error/v1", request_id: runId, reason_code: "STARTING_LEVEL_REQUIRED", retryable: false, message: "Request could not be completed." }, 409);
    return json(route, { schema: "study-start/v1", session: session(), selected_set_id: null, starting_level: level });
  });
  return { mutations, level: () => level };
}

for (const width of [1440, 390]) test(`legacy material defaults to the first of three choices at ${width}px`, async ({ page }) => {
  await page.setViewportSize({ width, height: 900 });
  const fixture = await studyLayoutFixture(page, "ready", { startingLevel: null });
  await page.goto(fixture.path);
  const picker = page.getByRole("dialog", { name: "你想從哪裡開始？" });
  await expect(picker).toBeVisible();
  await expect(picker.getByRole("radio")).toHaveCount(3);
  await expect(picker.getByRole("radio", { name: /第一次接觸/ })).toBeChecked();
  await picker.getByRole("button", { name: "儲存選擇" }).click();
  await expect(page.locator(".assessment-set-item")).toHaveCount(1);
  expect(fixture.getStartingLevel()).toBe("some_knowledge");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test("an existing session deep link asks for a missing choice before continuing", async ({ page }) => {
  const fixture = await studyLayoutFixture(page, "ready", { startingLevel: null });
  await page.goto(fixture.path);
  const picker = page.getByRole("dialog", { name: "你想從哪裡開始？" });
  await expect(picker).toBeVisible();
  await picker.getByRole("radio", { name: /第一次接觸，或還不太熟/ }).check();
  await picker.getByRole("button", { name: "儲存選擇" }).click();
  await expect(page.locator(".assessment-set-item")).toHaveCount(1);
  expect(fixture.getStartingLevel()).toBe("some_knowledge");
  expect(fixture.requests.filter((item: { path: string }) => /\/assessment-sets$/.test(item.path))).toHaveLength(0);
});

for (const [completed, inherited] of [[false, false], [true, false], [false, true]] as const) test(`beginner manual review keeps the selected concept when session completed=${completed}${inherited ? " inherited=true" : ""}`, async ({ page }) => {
  const fixture = await studyLayoutFixture(page, inherited ? "completed" : "preparation", { startingLevel: "beginner", navigation: !completed,
    sessionCompleted: completed, inheritedCheck: inherited, wrong: [], conceptStatus: completed || inherited ? "completed" : "in_progress" });
  let snapshot: any;
  page.on("response", async response => { if (response.url().includes("/workspace?") && response.ok()) snapshot = await response.json(); });
  await fixture.open();
  await expect.poll(() => !!snapshot).toBe(true);
  const stored = snapshot.session, mid = stored.material_id, rid = snapshot.run_id, revision = stored.knowledge_structure_revision;
  const selected = fixture.knowledgeStructure.concepts[completed || inherited ? 0 : 1];
  const published = { ...run, run_id: rid, material_id: mid, source_artifact_id: snapshot.source_artifact_id,
    output_binding: { ...run.output_binding, knowledge_structure_revision: revision } };
  await page.route(`**/v1/materials/${mid}`, route => json(route, {
    schema: "material-library-item/v1", material_id: mid, source_artifact_id: snapshot.source_artifact_id, display_name: "網路講義", size_bytes: 100,
    created_at: run.created_at, latest_attempt: published, head_revision: revision,
    available_structures: [{ run_id: rid, knowledge_structure_revision: revision, created_at: run.created_at, status: "succeeded" }],
    study_sessions: [{ ...stored, run_id: rid }],
  }));
  await page.route(`**/v1/material-processing-runs/${rid}`, route => json(route, published));
  await page.route(url => decodeURIComponent(url.pathname).endsWith(`/knowledge-structures/${revision}/outline`), route => json(route, outlineView(fixture.knowledgeStructure, mid, rid)));
  await page.route(`**/v1/study-sessions/${stored.study_session_id}/map-progress`, route => json(route, { ...snapshot.progress, learning_path: completed ? [] : snapshot.progress.learning_path }));
  await page.goto(fixture.path.split("/study-sessions/")[0]);
  await openMapConcept(page, selected.label);
  await page.getByRole("button", { name: completed || inherited ? "再次練習" : "檢測這個概念", exact: true }).click();
  await expect(page.getByRole("progressbar", { name: "準備進度", exact: true })).toBeVisible();
  const starts = fixture.requests.filter((item: { path: string }) => item.path.endsWith("/study-start"));
  expect(starts).toHaveLength(1);
  expect(JSON.parse(starts[0].body).current_concept_id).toBe(selected.concept_id);
  expect(fixture.requests.filter((item: { path: string }) => item.path.endsWith("/assessment-sets"))).toHaveLength(0);
  fixture.setStage("ready");
  await expect(page.locator(".question-paper-heading")).toContainText(selected.label);
  await expect(page.getByRole("button", { name: "下一題", exact: true })).toBeVisible();
});

for (const level of ["beginner", "some_knowledge", "gaps"] as const) test(`${level} resumes the supplied active set`, async ({ page }) => {
  const fixture = await studyLayoutFixture(page, "ready", { startingLevel: level });
  await fixture.open();
  await expect(page.locator(".assessment-set-item")).toHaveCount(1);
  expect(fixture.requests.filter((item: { path: string }) => /\/assessment-sets$/.test(item.path))).toHaveLength(0);
});

test("material explanation remembers the last choice across visits", async ({ page }) => {
  const fixture = await studyLayoutFixture(page, "no-safe");
  await fixture.open();
  const details = page.locator(".study-material-summary");
  await details.locator("summary").click();
  await expect(details).toHaveAttribute("open", "");
  await page.reload();
  await expect(details).toHaveAttribute("open", "");
  await details.locator("summary").click();
  await page.reload();
  await expect(details).not.toHaveAttribute("open", "");
});

test("changing the material level preserves work and shows the supplied next concept", async ({ page }) => {
  const fixture = await levelMap(page, "some_knowledge", true);
  await page.goto(mapPath);
  await page.getByRole("tab", { name: "學習與測驗", exact: true }).click();
  const setting = page.getByRole("region", { name: "起始程度設定" });
  await expect(setting).toContainText("接下來：Array");
  await setting.getByRole("button", { name: /起始程度：第一次接觸，或還不太熟/ }).click();
  const picker = page.getByRole("dialog", { name: "你想從哪裡開始？" });
  await picker.getByRole("radio", { name: /學過了，想跳過已經會的/ }).check();
  await picker.getByRole("button", { name: "儲存選擇" }).click();
  await expect(setting).toContainText("起始程度：學過了，想跳過已經會的");
  expect(fixture.mutations.map(item => item.path)).toEqual(["level"]);
});

test("cross-concept diagnostic keeps question concepts, sources and persisted results", async ({ page }) => {
  const fixture = await studyLayoutFixture(page, "ready", { startingLevel: "familiar", kind: "material_diagnostic", navigation: true, wrong: [1], count: 2 });
  await fixture.open();
  const diagnostic = page.getByRole("region", { name: "快速檢測", exact: true });
  await expect(diagnostic).toBeVisible();
  await expect(diagnostic.locator(".question-paper-heading")).toContainText("伺服器");
  await diagnostic.getByRole("article", { name: "第 1 題", exact: true }).getByRole("radio").first().check();
  await page.getByRole("button", { name: "下一題", exact: true }).click();
  await expect(diagnostic.locator(".question-paper-heading")).toContainText("使用者端");
  await diagnostic.getByRole("article", { name: "第 2 題", exact: true }).getByRole("radio").nth(1).check();
  await diagnostic.getByRole("button", { name: "交卷" }).click();
  await expect(diagnostic.locator(".question-result-summary")).toContainText("答對 1／2 題");
  await expect(diagnostic.locator(".feedback-card")).toHaveCount(2);
  await diagnostic.getByRole("button", { name: /PDF 第 1 頁/ }).first().click();
  await expect(page.getByRole("dialog", { name: "教材來源" })).toBeVisible();
  await page.keyboard.press("Escape");
  const starts = fixture.requests.filter((item: { path: string }) => item.path.endsWith("/study-start")).length;
  await page.reload();
  await expect(page.getByRole("heading", { name: "快速檢測結果" })).toBeVisible();
  expect(fixture.requests.filter((item: { path: string }) => item.path.endsWith("/study-start"))).toHaveLength(starts);
  expect(fixture.requests.filter((item: { path: string }) => item.path.endsWith("/submissions"))).toHaveLength(1);
});

test("a diagnostic that completes every assessable claim finishes through guidance", async ({ page }) => {
  const fixture = await studyLayoutFixture(page, "ready", { startingLevel: "familiar", kind: "material_diagnostic", navigation: true,
    nextAction: "complete", singleClaimConcepts: true, noHistory: true, wrong: [], count: 2 });
  await fixture.open();
  await answerPaper(page, [0, 0]);
  await page.getByRole("button", { name: "交卷", exact: true }).click();
  await page.getByRole("button", { name: "完成本次學習", exact: true }).click();
  await expect(page.getByText("這份教材已完成目前可檢測的重點。", { exact: true })).toBeVisible();
  expect(fixture.requests.filter((item: { path: string }) => item.path.endsWith("/guidance/apply"))).toHaveLength(1);
  await page.reload();
  await expect(page.getByText("這份教材已完成目前可檢測的重點。", { exact: true })).toBeVisible();
  expect(fixture.requests.filter((item: { path: string }) => item.path.endsWith("/guidance/apply"))).toHaveLength(1);
});
