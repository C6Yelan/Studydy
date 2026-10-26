import { expect, test } from "@playwright/test";
import { emptyGoal, goalSummary, goalToday, goalView } from "../fixtures/learning-goals";
import { materialId, runId, artifactId, structureRevision, firstConcept, secondConcept, mockKnowledgeMapApi, json, run } from "../fixtures/knowledge-map";

const mapPath = `/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}`;
const failure = { schema: "api-error/v1", request_id: runId, reason_code: "STORAGE_UNAVAILABLE", retryable: true, message: "Request could not be completed." };

for (const width of [1366, 390]) test(`goal form saves date and percentage and keeps failed input at ${width}px`, async ({ page }) => {
  await page.setViewportSize({ width, height: 900 });
  await mockKnowledgeMapApi(page);
  let view = emptyGoal(materialId, structureRevision, runId);
  const writes: unknown[] = [];
  await page.route(`**/v1/materials/${materialId}/goal**`, route => {
    if (route.request().method() === "GET") return json(route, view);
    const body = route.request().postDataJSON(); writes.push(body);
    if (writes.length === 1) return json(route, failure, 503);
    view = goalView(materialId, structureRevision, runId, firstConcept);
    Object.assign(view.goal!, { target_date: body.target_date, target_percent: body.target_percent, target_points: 12, remaining_points: 6 });
    Object.assign(view.goal!.today_stage, { quota_points: 1, remaining_points: 1 });
    return json(route, view);
  });
  await page.goto(mapPath);
  await page.getByRole("tab", { name: "學習與測驗", exact: true }).click();
  const panel = page.getByRole("region", { name: "學習目標與每日關卡" });
  await panel.getByRole("button", { name: "設定學習目標", exact: true }).click();
  const editor = page.getByRole("dialog", { name: "設定學習目標", exact: true });
  await expect(editor.getByRole("radio")).toHaveCount(3);
  await expect(editor.getByRole("button", { name: "儲存目標" })).toBeDisabled();
  await editor.getByLabel("目標日期", { exact: true }).fill("2026-10-30");
  await editor.getByRole("radio", { name: /60%/ }).check();
  await editor.getByRole("button", { name: "取消", exact: true }).click();
  expect(writes).toHaveLength(0);
  await panel.getByRole("button", { name: "設定學習目標", exact: true }).click();
  await editor.getByLabel("目標日期", { exact: true }).fill("2026-10-30");
  await editor.getByRole("radio", { name: /60%/ }).check();
  await editor.getByRole("button", { name: "儲存目標" }).click();
  await expect(editor.getByRole("alert")).toBeVisible();
  await expect(editor.getByLabel("目標日期", { exact: true })).toHaveValue("2026-10-30");
  await expect(editor.getByRole("radio", { name: /60%/ })).toBeChecked();
  await editor.getByRole("button", { name: "儲存目標" }).click();
  await expect(panel).toContainText("60% · 及格");
  await expect(panel).toContainText("2026/10/30");
  expect(writes).toEqual(Array(2).fill({ schema: "learning-goal-update/v1", target_date: "2026-10-30", target_percent: 60 }));
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test("today shows fixed quota, whole-concept workload and manual evidence gaps", async ({ page }) => {
  await mockKnowledgeMapApi(page);
  const view = goalView(materialId, structureRevision, runId, firstConcept);
  Object.assign(view.goal!, { scheduled_over_limit: true, manual_review: [{ concept_id: secondConcept, label: "Array", remaining_points: 2 }] });
  Object.assign(view.goal!.today_stage, { scheduled_points: 24, concepts: [{ concept_id: firstConcept, label: "Stack", remaining_points: 24 }] });
  const starts: any[] = [];
  await page.route(`**/v1/materials/${materialId}/goal**`, route => json(route, view));
  await page.route(`**/v1/materials/${materialId}/study-start`, route => { starts.push(route.request().postDataJSON()); return json(route, failure, 503); });
  await page.goto(mapPath);
  await page.getByRole("tab", { name: "學習與測驗", exact: true }).click();
  const panel = page.getByRole("region", { name: "學習目標與每日關卡" });
  await expect(panel).toContainText("今日目標 2 ／ 實際安排 24 個重點");
  await expect(panel).toContainText("落後 2 個重點");
  await expect(panel).toContainText("實際安排超過每日建議的 20 個重點");
  await expect(panel.getByRole("button", { name: "學習 Stack", exact: true })).toBeEnabled();
  expect(starts).toHaveLength(0);
  await panel.getByRole("button", { name: "手動複習 Array", exact: true }).click();
  await expect.poll(() => starts.length).toBe(1);
  expect(starts[0].current_concept_id).toBe(secondConcept);
  expect(starts[0].knowledge_structure_revision).toBe(structureRevision);
});

test("an overdue goal can lower its percentage without postponing its date", async ({ page }) => {
  await mockKnowledgeMapApi(page);
  const view = goalView(materialId, structureRevision, runId, firstConcept);
  Object.assign(view.goal!, { target_date: "2026-10-19", target_percent: 100, completed_points: 14, target_points: 20,
    remaining_points: 6, days_remaining: 0, overdue_days: 5, future_stages: [] });
  Object.assign(view.goal!.today_stage, { quota_points: 6, remaining_points: 6, scheduled_points: 6 });
  const writes: unknown[] = [];
  await page.route(`**/v1/materials/${materialId}/goal**`, route => {
    if (route.request().method() === "PUT") {
      const body = route.request().postDataJSON(); writes.push(body);
      Object.assign(view.goal!, { target_date: body.target_date, target_percent: body.target_percent, target_points: 12, remaining_points: 0, achieved: true });
      Object.assign(view.goal!.today_stage, { quota_points: 0, remaining_points: 0, scheduled_points: 0, done: true, concepts: [] });
    }
    return json(route, view);
  });
  await page.goto(mapPath);
  await page.getByRole("tab", { name: "學習與測驗", exact: true }).click();
  const panel = page.getByRole("region", { name: "學習目標與每日關卡" });
  await panel.getByRole("button", { name: "更改學習目標", exact: true }).click();
  const editor = page.getByRole("dialog", { name: "更改學習目標", exact: true });
  await expect(editor.getByLabel("目標日期", { exact: true })).toHaveValue("2026-10-19");
  await editor.getByRole("radio", { name: /60%/ }).check();
  await editor.getByRole("button", { name: "儲存目標", exact: true }).click();
  await expect(panel).toContainText("60% · 及格");
  await expect(panel).toContainText("2026/10/19");
  expect(writes).toEqual([{ schema: "learning-goal-update/v1", target_date: "2026-10-19", target_percent: 60 }]);
});

test("earned daily progress survives when every point later becomes unavailable", async ({ page }) => {
  await mockKnowledgeMapApi(page);
  const view = goalView(materialId, structureRevision, runId, firstConcept);
  Object.assign(view.goal!, { assessable_points: 0, completed_points: 0, target_points: 0, remaining_points: 0, achieved: false,
    today_stage: { date: goalToday, quota_points: 4, completed_points: 4, remaining_points: 0, scheduled_points: 0, done: true, concepts: [] },
    future_stages: [], manual_review: [] });
  await page.route(`**/v1/materials/${materialId}/goal**`, route => json(route, view));
  await page.route("**/v1/learning-goals", route => json(route, { schema: "learning-goals/v1", today: goalToday, goals: [goalSummary(view)] }));
  await page.route("**/v1/materials", route => json(route, { schema: "material-library/v1", materials: [{
    schema: "material-library-item/v1", material_id: materialId, display_name: "資料結構", source_artifact_id: artifactId,
    size_bytes: 100, created_at: run.created_at, latest_attempt: run, head_revision: structureRevision, study_sessions: [],
    available_structures: [{ run_id: runId, knowledge_structure_revision: structureRevision, created_at: run.created_at, status: "succeeded" }],
  }] }));
  await page.goto("/");
  const daily = page.getByRole("region", { name: "今日任務" }).locator(".goal-quest");
  await expect(daily).toHaveClass(/is-done/);
  await expect(daily).toContainText("今天已完成 4 個重點");
  await daily.getByRole("button", { name: "查看關卡：資料結構" }).click();
  const panel = page.getByRole("region", { name: "學習目標與每日關卡" });
  await expect(panel).toContainText("目前沒有可檢測重點");
  await expect(panel).toContainText("尚無法計算");
  const today = panel.getByRole("region", { name: "今天的關卡" });
  await expect(today).toContainText("今日關卡已完成");
  await expect(today).toContainText("今天已完成 4 個重點");
  await expect(panel.getByText("目標已達成", { exact: true })).toHaveCount(0);
  await expect(panel.getByRole("alert")).toHaveCount(0);
});

test("editing the target date preserves all of today's earned points", async ({ page }) => {
  await mockKnowledgeMapApi(page);
  const view = goalView(materialId, structureRevision, runId, firstConcept);
  Object.assign(view.goal!, { target_date: "2026-10-26", completed_points: 8, remaining_points: 8, days_remaining: 3, behind_points: 0,
    today_stage: { date: goalToday, quota_points: 4, completed_points: 4, remaining_points: 0, scheduled_points: 0, done: true, concepts: [] },
    future_stages: [{ date: "2026-10-25", quota_points: 4, completed_points: 0, remaining_points: 4, scheduled_points: 8, done: false,
      concepts: [{ concept_id: secondConcept, label: "Array", remaining_points: 8 }] }] });
  const writes: unknown[] = [];
  await page.route(`**/v1/materials/${materialId}/goal**`, route => {
    if (route.request().method() === "PUT") {
      const body = route.request().postDataJSON(); writes.push(body);
      Object.assign(view.goal!, { target_date: body.target_date, days_remaining: 4 });
      // 以日初已完成4個重點為基準重算quota；整日取得的4個成果仍保留。
      view.goal!.today_stage.quota_points = 3;
      Object.assign(view.goal!.future_stages[0], { quota_points: 3, remaining_points: 3 });
    }
    return json(route, view);
  });
  await page.goto(mapPath);
  await page.getByRole("tab", { name: "學習與測驗", exact: true }).click();
  const panel = page.getByRole("region", { name: "學習目標與每日關卡" });
  const today = panel.getByRole("region", { name: "今天的關卡" });
  await expect(today).toContainText("今日關卡已完成");
  await expect(today).toContainText("今天已完成 4 個重點");
  await panel.getByRole("button", { name: "更改學習目標", exact: true }).click();
  const editor = page.getByRole("dialog", { name: "更改學習目標", exact: true });
  await editor.getByLabel("目標日期", { exact: true }).fill("2026-10-27");
  await editor.getByRole("button", { name: "儲存目標", exact: true }).click();
  await expect(today).toContainText("今日關卡已完成");
  await expect(today).toContainText("今天已完成 4 個重點");
  expect(writes).toEqual([{ schema: "learning-goal-update/v1", target_date: "2026-10-27", target_percent: 80 }]);
});

test("zero assessable points stays unavailable and achieved goals stay distinct", async ({ page }) => {
  await mockKnowledgeMapApi(page);
  const view = goalView(materialId, structureRevision, runId, firstConcept);
  Object.assign(view.goal!, { assessable_points: 0, completed_points: 0, target_points: 0, remaining_points: 0, achieved: false,
    today_stage: { date: goalToday, quota_points: 0, completed_points: 0, remaining_points: 0, scheduled_points: 0, done: false, concepts: [] }, future_stages: [] });
  await page.route(`**/v1/materials/${materialId}/goal**`, route => json(route, view));
  await page.goto(mapPath);
  await page.getByRole("tab", { name: "學習與測驗", exact: true }).click();
  const panel = page.getByRole("region", { name: "學習目標與每日關卡" });
  await expect(panel).toContainText("目前沒有可檢測重點");
  await expect(panel).toContainText("尚無法計算");
  Object.assign(view.goal!, { assessable_points: 20, completed_points: 16, target_points: 16, achieved: true,
    target_date: "2026-10-19", days_remaining: 0, overdue_days: 5,
    today_stage: { ...view.goal!.today_stage, done: true } });
  await page.reload();
  await expect(panel).toContainText("目標已達成");
  await expect(panel).toContainText("需完成 16 個重點");
  await expect(panel).not.toContainText("已逾期 5 天");
  await expect(page.getByRole("region", { name: "測驗紀錄" })).toContainText("還沒有測驗紀錄");
});

test("revision notice acknowledgement persists without changing the goal or daily quota", async ({ page }) => {
  await mockKnowledgeMapApi(page);
  const view = goalView(materialId, structureRevision, runId, firstConcept);
  Object.assign(view.goal!, { assessable_points: 24, target_points: 20, remaining_points: 14,
    update_notice: { knowledge_structure_revision: structureRevision, previous_points: 20, current_points: 24 } });
  const acknowledgements: unknown[] = [];
  await page.route(`**/v1/materials/${materialId}/goal**`, route => {
    if (route.request().method() === "POST") { acknowledgements.push(route.request().postDataJSON()); view.goal!.update_notice = null; }
    return json(route, view);
  });
  await page.goto(mapPath);
  await page.getByRole("tab", { name: "學習與測驗", exact: true }).click();
  const panel = page.getByRole("region", { name: "學習目標與每日關卡" });
  await expect(panel).toContainText("教材已更新，重點數 20 → 24");
  await panel.getByRole("button", { name: "關閉教材更新提示", exact: true }).click();
  await expect(panel.getByRole("button", { name: "關閉教材更新提示", exact: true })).toHaveCount(0);
  await page.reload();
  await expect(panel).toContainText("80% · 熟練");
  await expect(panel).toContainText("2026/10/30");
  await expect(panel).toContainText("今日目標 2 ／ 實際安排 3 個重點");
  await expect(panel.getByRole("button", { name: "關閉教材更新提示", exact: true })).toHaveCount(0);
  expect(acknowledgements).toEqual([{ schema: "learning-goal-ack/v1", knowledge_structure_revision: structureRevision }]);
});

test("a stale notice cannot acknowledge an unseen revision", async ({ page }) => {
  await mockKnowledgeMapApi(page);
  const view = goalView(materialId, structureRevision, runId, firstConcept);
  view.goal!.update_notice = { knowledge_structure_revision: structureRevision, previous_points: 18, current_points: 20 };
  const newerRevision = `knowledge-structure:sha256:${"9".repeat(64)}`;
  const seen: string[] = [];
  await page.route(`**/v1/materials/${materialId}/goal**`, route => {
    if (route.request().method() === "GET") return json(route, view);
    seen.push(route.request().postDataJSON().knowledge_structure_revision);
    if (seen.length === 1) {
      view.knowledge_structure_revision = newerRevision;
      view.run_id = "66666666-6666-4666-8666-666666666666";
      Object.assign(view.goal!, { assessable_points: 30, target_points: 24, remaining_points: 18,
        update_notice: { knowledge_structure_revision: newerRevision, previous_points: 18, current_points: 30 } });
      Object.assign(view.goal!.today_stage, { quota_points: 3, remaining_points: 3 });
      return json(route, { ...failure, reason_code: "LEARNING_GOAL_STALE" }, 409);
    }
    view.goal!.update_notice = null;
    return json(route, view);
  });
  await page.goto(mapPath);
  await page.getByRole("tab", { name: "學習與測驗", exact: true }).click();
  const panel = page.getByRole("region", { name: "學習目標與每日關卡" });
  await panel.getByRole("button", { name: "關閉教材更新提示" }).click();
  await expect(panel.getByRole("alert")).toContainText("請重新讀取學習目標");
  await panel.getByRole("button", { name: "重新讀取目標" }).click();
  await expect(panel).toContainText("重點數 18 → 30");
  await panel.getByRole("button", { name: "關閉教材更新提示" }).click();
  await expect(panel).toContainText("今日目標 3 ／ 實際安排 3 個重點");
  expect(seen).toEqual([structureRevision, newerRevision]);
});

test("future stage pages are read-only previews", async ({ page }) => {
  await mockKnowledgeMapApi(page);
  const view = goalView(materialId, structureRevision, runId, firstConcept);
  Object.assign(view.goal!, { target_date: "2026-11-10", days_remaining: 18, preview_has_more: true });
  const methods: string[] = [];
  await page.route(`**/v1/materials/${materialId}/goal**`, route => {
    methods.push(route.request().method());
    const offset = Number(new URL(route.request().url()).searchParams.get("preview_offset"));
    return json(route, { ...view, goal: { ...view.goal, preview_offset: offset, preview_has_more: offset === 0,
      future_stages: [{ ...view.goal!.future_stages[0], date: offset === 0 ? "2026-10-25" : "2026-11-01" }] } });
  });
  await page.goto(mapPath);
  await page.getByRole("tab", { name: "學習與測驗", exact: true }).click();
  const preview = page.getByRole("region", { name: "未來關卡預覽" });
  await expect(preview).toContainText("預覽，會隨作答調整");
  await preview.getByRole("button", { name: "後 7 天" }).click();
  await expect(preview).toContainText("2026/11/01");
  await preview.getByRole("button", { name: "前 7 天" }).click();
  await expect(preview).toContainText("2026/10/25");
  expect(methods).toEqual(["GET", "GET", "GET"]);
});

for (const width of [1440, 390]) test(`home shows separate material goals without replacing concept rings at ${width}px`, async ({ page }) => {
  await page.setViewportSize({ width, height: 900 });
  await mockKnowledgeMapApi(page);
  const secondMaterial = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
  const base = { schema: "material-library-item/v1", source_artifact_id: artifactId, size_bytes: 100, created_at: run.created_at,
    latest_attempt: run, head_revision: structureRevision, available_structures: [{ run_id: runId, knowledge_structure_revision: structureRevision, created_at: run.created_at, status: "succeeded" }], study_sessions: [] };
  await page.route("**/v1/materials", route => json(route, { schema: "material-library/v1", materials: [
    { ...base, material_id: materialId, display_name: "資料結構" }, { ...base, material_id: secondMaterial, display_name: "作業系統" },
  ] }));
  const first = goalSummary(goalView(materialId, structureRevision, runId, firstConcept));
  const second = goalSummary(goalView(secondMaterial, structureRevision, runId, secondConcept), "作業系統");
  await page.route("**/v1/learning-goals", route => json(route, { schema: "learning-goals/v1", today: goalToday, goals: [first, second] }));
  const starts: string[] = [];
  page.on("request", request => { if (request.url().endsWith("/study-start")) starts.push(request.url()); });
  await page.goto("/");
  const tasks = page.getByRole("region", { name: "今日任務" });
  await expect(tasks.getByText("今天的關卡 · 資料結構", { exact: true })).toBeVisible();
  await expect(tasks.getByText("今天的關卡 · 作業系統", { exact: true })).toBeVisible();
  const island = page.locator(".map-island").filter({ hasText: "資料結構" });
  await expect(island).toContainText("目標 80% · 剩 7 天");
  await expect(island.locator(".progress-ring")).toContainText("0");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await tasks.getByRole("button", { name: "查看關卡：資料結構", exact: true }).click();
  await expect(page.getByRole("tab", { name: "學習與測驗", exact: true })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("region", { name: "學習目標與每日關卡" })).toBeVisible();
  expect(starts).toEqual([]);
});

for (const width of [1440, 390]) test(`completed daily stage shows earned points at ${width}px`, async ({ page }, testInfo) => {
  await page.setViewportSize({ width, height: width === 390 ? 844 : 900 });
  await mockKnowledgeMapApi(page);
  const view = goalView(materialId, structureRevision, runId, firstConcept);
  Object.assign(view.goal!.today_stage, { quota_points: 2, completed_points: 3, remaining_points: 0, scheduled_points: 0, done: true, concepts: [] });
  await page.route(`**/v1/materials/${materialId}/goal**`, route => json(route, view));
  await page.goto(mapPath);
  await page.getByRole("tab", { name: "學習與測驗", exact: true }).click();
  const today = page.getByRole("region", { name: "今天的關卡" });
  await expect(today).toContainText("今日關卡已完成");
  await expect(today).toContainText("今天已完成 3 個重點");
  await today.scrollIntoViewIfNeeded();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: testInfo.outputPath(`goal-completed-${width}.png`) });
});
