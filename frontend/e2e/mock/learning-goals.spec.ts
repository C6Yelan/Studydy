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
  const panel = page.locator(".review-diagnosis");
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
  await expect(panel).toContainText("前掌握 60%");
  await expect(panel).toContainText("10/30 前掌握");
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
  const panel = page.locator(".review-diagnosis");
  await expect(panel).toContainText("今天 0/2 個重點");
  await expect(panel).toContainText("落後 2 個重點");
  await expect(panel).toContainText("建議 20");
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
  const panel = page.locator(".review-diagnosis");
  await panel.getByRole("button", { name: "更改學習目標", exact: true }).click();
  const editor = page.getByRole("dialog", { name: "更改學習目標", exact: true });
  await expect(editor.getByLabel("目標日期", { exact: true })).toHaveValue("2026-10-19");
  await editor.getByRole("radio", { name: /60%/ }).check();
  await editor.getByRole("button", { name: "儲存目標", exact: true }).click();
  await expect(panel).toContainText("前掌握 60%");
  await expect(panel).toContainText("10/19 前掌握");
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
  await expect(daily).toContainText("4/4");
  await daily.getByRole("button", { name: "查看關卡：資料結構" }).click();
  const panel = page.locator(".review-diagnosis");
  await expect(panel).toContainText("目前沒有可檢測重點");
  await expect(panel).toContainText("尚無法計算");
  const today = page.getByRole("region", { name: "學習進度", exact: true });
  await expect(today).toContainText("今日關卡已完成");
  await expect(today).toContainText("今天 4/");
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
  const panel = page.locator(".review-diagnosis");
  const today = page.getByRole("region", { name: "學習進度", exact: true });
  await expect(today).toContainText("今日關卡已完成");
  await expect(today).toContainText("今天 4/");
  await panel.getByRole("button", { name: "更改學習目標", exact: true }).click();
  const editor = page.getByRole("dialog", { name: "更改學習目標", exact: true });
  await editor.getByLabel("目標日期", { exact: true }).fill("2026-10-27");
  await editor.getByRole("button", { name: "儲存目標", exact: true }).click();
  await expect(today).toContainText("今日關卡已完成");
  await expect(today).toContainText("今天 4/");
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
  const panel = page.locator(".review-diagnosis");
  await expect(panel).toContainText("目前沒有可檢測重點");
  await expect(panel).toContainText("尚無法計算");
  Object.assign(view.goal!, { assessable_points: 20, completed_points: 16, target_points: 16, achieved: true,
    target_date: "2026-10-19", days_remaining: 0, overdue_days: 5,
    today_stage: { ...view.goal!.today_stage, done: true } });
  await page.reload();
  await expect(panel).toContainText("目標已達成");
  await expect(panel.getByRole("progressbar", { name: "學習目標進度", exact: true })).toHaveAttribute("aria-valuetext", "已完成 16／需完成 16 個重點");
  await expect(panel).not.toContainText("已逾期 5 天");
  await expect(page.getByRole("region", { name: "測驗紀錄" })).toContainText("還沒有測驗紀錄");
});

for (const width of [1440, 390]) test(`home shows separate material goals and target progress rings at ${width}px`, async ({ page }) => {
  await page.setViewportSize({ width, height: 900 });
  await mockKnowledgeMapApi(page);
  const secondMaterial = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
  const base = { schema: "material-library-item/v1", source_artifact_id: artifactId, size_bytes: 100, created_at: run.created_at,
    latest_attempt: run, head_revision: structureRevision, available_structures: [{ run_id: runId, knowledge_structure_revision: structureRevision, created_at: run.created_at, status: "succeeded" }], study_sessions: [] };
  await page.route("**/v1/materials", route => json(route, { schema: "material-library/v1", materials: [
    { ...base, material_id: materialId, display_name: "資料結構" }, { ...base, material_id: secondMaterial, display_name: "作業系統" },
  ] }));
  const first = goalSummary(goalView(materialId, structureRevision, runId, firstConcept));
  Object.assign(first, { behind_points: 0 });
  const second = goalSummary(goalView(secondMaterial, structureRevision, runId, secondConcept), "作業系統");
  Object.assign(second, { target_date: goalToday, days_remaining: 1, behind_points: 0 });
  await page.route("**/v1/learning-goals", route => json(route, { schema: "learning-goals/v1", today: goalToday, goals: [first, second] }));
  const starts: string[] = [];
  page.on("request", request => { if (request.url().endsWith("/study-start")) starts.push(request.url()); });
  await page.goto("/");
  const tasks = page.getByRole("region", { name: "今日任務" });
  await expect(tasks.getByText("關卡｜資料結構", { exact: true })).toBeVisible();
  await expect(tasks.getByText("關卡｜作業系統", { exact: true })).toBeVisible();
  const island = page.locator(".map-island").filter({ hasText: "資料結構" });
  const normalFlag = island.locator(".island-goal-flag");
  await expect(normalFlag).toHaveText("⚑ 80% · 剩 7 天");
  await expect(island.locator(".island-flag-text")).toHaveText("資料結構尚未開始");
  await expect(island.locator(".progress-ring")).toContainText("38%");
  await expect(normalFlag).toHaveClass("island-goal-flag");
  const dueToday = page.locator(".map-island").filter({ hasText: "作業系統" }).locator(".island-goal-flag");
  await expect(dueToday).toContainText("今天到期");
  await expect(dueToday).toHaveClass(/is-warning/);
  for (const flag of [normalFlag, dueToday]) {
    await flag.locator("..").focus();
    await expect.poll(async () => {
      const rect = (await flag.boundingBox())!;
      return rect.x >= 0 && rect.x + rect.width <= width;
    }).toBe(true);
    const box = (await flag.boundingBox())!;
    expect(box.x).toBeGreaterThanOrEqual(0);
    expect(box.x + box.width).toBeLessThanOrEqual(width);
    expect(await flag.evaluate(element => getComputedStyle(element).whiteSpace)).toBe("nowrap");
    for (const label of await page.locator(".island-flag").all()) {
      const other = (await label.boundingBox())!;
      expect(box.x + box.width <= other.x || other.x + other.width <= box.x || box.y + box.height <= other.y || other.y + other.height <= box.y).toBe(true);
    }
  }
  const normalColor = await normalFlag.evaluate(element => getComputedStyle(element).backgroundColor);
  expect(await dueToday.evaluate(element => getComputedStyle(element).backgroundColor)).not.toBe(normalColor);
  Object.assign(first, { behind_points: 2 });
  Object.assign(second, { days_remaining: 0, overdue_days: 3, target_date: "2026-10-21" });
  await page.reload();
  await expect(normalFlag).toHaveClass(/is-warning/);
  await expect(dueToday).toHaveText("⚑ 80% · 逾期 3 天");
  await expect(dueToday).toHaveClass(/is-warning/);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await tasks.getByRole("button", { name: "查看關卡：資料結構", exact: true }).click();
  await expect(page.getByRole("tab", { name: "學習與測驗", exact: true })).toHaveAttribute("aria-selected", "true");
  await expect(page.locator(".review-diagnosis")).toBeVisible();
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
  const today = page.getByRole("region", { name: "學習進度", exact: true });
  await expect(today).toContainText("今日關卡已完成");
  await expect(today).toContainText("今天 3/2 個重點");
  await today.scrollIntoViewIfNeeded();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: testInfo.outputPath(`goal-completed-${width}.png`) });
});

for (const kind of ["extend_date", "lower_target"] as const) test(`goal suggestion ${kind} writes only after explicit click and can retry`, async ({ page }) => {
  await mockKnowledgeMapApi(page);
  const view = goalView(materialId, structureRevision, runId, firstConcept);
  const suggestion = { kind, target_date: kind === "extend_date" ? "2026-11-10" : "2026-10-30", target_percent: kind === "lower_target" ? 60 as const : 80 as const, daily_points: 18 };
  Object.assign(view.goal!, { quota_over_limit: true, suggestions: [suggestion] });
  const writes: unknown[] = [];
  await page.route(`**/v1/materials/${materialId}/goal**`, route => {
    if (route.request().method() === "PUT") {
      const body = route.request().postDataJSON(); writes.push(body);
      if (writes.length === 1) return json(route, failure, 503);
      Object.assign(view.goal!, { target_date: body.target_date, target_percent: body.target_percent, quota_over_limit: false, suggestions: [] });
    }
    return json(route, view);
  });
  await page.goto(mapPath); await page.getByRole("tab", { name: "學習與測驗", exact: true }).click();
  const button = page.getByRole("button", { name: kind === "extend_date" ? /延後到 11\/10/ : /改成 60%/ });
  await expect(button).toBeVisible(); expect(writes).toEqual([]);
  await button.click(); await expect(page.getByRole("alert")).toBeVisible(); await expect(button).toBeEnabled();
  await button.click(); await expect(button).toHaveCount(0);
  expect(writes).toEqual(Array(2).fill({ schema: "learning-goal-update/v1", target_date: suggestion.target_date, target_percent: suggestion.target_percent }));
});
