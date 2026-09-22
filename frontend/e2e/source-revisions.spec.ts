import { expect, test } from "@playwright/test";
import type { MaterialLibraryItem, MaterialProcessingRunView, SourceView } from "../src/api/contracts";

const uuid = (value: number) => `11111111-1111-4111-8111-${value.toString().padStart(12, "0")}`;
const material = uuid(1), oldRun = uuid(2), newRun = uuid(3), study = uuid(4);
const revision = `knowledge-structure:sha256:${"a".repeat(64)}`;
const timestamp = "2026-09-18T00:00:00Z";

for (const width of [1536, 390]) test(`append queue and run-only cancellation preserve current learning at ${width}px`, async ({ page }, info) => {
  await page.setViewportSize({ width, height: 844 });
  await page.clock.setFixedTime(new Date("2026-09-18T00:01:30Z"));
  const lastFilename = `C-${"LongChapterFilename".repeat(5)}.pdf`;
  await page.route("**/v1/session", route => route.fulfill({ json: { schema: "learner-identity/v1", learner_id: uuid(99) } }));
  await page.route("**/v1/session/refresh", route => route.fulfill({ json: { schema: "learner-identity/v1", learner_id: "33333333-3333-4333-8333-333333333333" } }));
  await page.route("**/v2/source-capabilities", route => route.fulfill({ json: { schema: "source-capabilities/v1", quality_notice: "PDF 優先", formats: [
    { extension: ".pdf", media_type: "application/pdf", max_bytes: 104857600 },
    { extension: ".txt", media_type: "text/plain", max_bytes: 104857600 },
  ] } }));
  const sources: SourceView[] = [{ source_id: uuid(10), normalization_id: uuid(11), original_artifact_id: uuid(12), normalized_artifact_id: uuid(13), original_name: "A.pdf", media_type: "application/pdf", status: "ready", page_count: 1, error_code: null, included: true }];
  let run: MaterialProcessingRunView | null = null;
  let wholeDeletes = 0, cancels = 0, starts = 0;
  const item = () => ({ schema: "material-library-item/v3", ingestion_kind: "sources-v2", material_id: material,
    head_revision: revision, source_count: sources.length, source_artifact_id: uuid(13), display_name: "資料結構", size_bytes: 1024, created_at: timestamp,
    source: sources[0], latest_attempt: run, available_structures: [{ run_id: oldRun, knowledge_structure_revision: revision, created_at: timestamp, status: "succeeded" }],
    study_sessions: [{ study_session_id: study, run_id: oldRun, knowledge_structure_revision: revision, current_concept_id: `concept:sha256:${"b".repeat(64)}`, status: "active", started_at: timestamp }] });
  await page.route(`**/v1/materials/${material}`, route => { if (route.request().method() === "DELETE") wholeDeletes++; return route.fulfill({ json: item() }); });
  await page.route("**/v1/materials", route => route.fulfill({ json: { schema: "material-library/v2", materials: [item()] } }));
  await page.route(`**/v1/material-processing-runs/${oldRun}`, route => route.fulfill({ json: {
    schema: "material-processing-run/v6", run_id: oldRun, material_id: material, source_artifact_id: uuid(13),
    status: "running", progress_stage: "semantics", completed_pages: 1, total_pages: 3,
    output_binding: null, error_code: null, cancel_requested_at: null, created_at: timestamp, updated_at: timestamp, completed_at: null,
  } }));
  await page.route(`**/v2/materials/${material}/sources`, route => {
    if (route.request().method() === "POST") {
      const index = sources.length;
      sources.push({ source_id: uuid(20 + index), normalization_id: uuid(30 + index), original_artifact_id: uuid(40 + index), normalized_artifact_id: uuid(50 + index),
        original_name: decodeURIComponent(route.request().headers()["x-material-name"]), media_type: route.request().headers()["content-type"], status: index === 2 ? "pending" : "ready", page_count: 1, error_code: null, included: false });
    }
    return route.fulfill({ json: { schema: "material-sources/v1", material_id: material, sources } });
  });
  await page.route(`**/v2/materials/${material}/revisions`, route => {
    starts++;
    expect(route.request().postDataJSON()).toEqual({ schema: "material-revision-create/v1", base_revision: revision, normalization_ids: [uuid(31), uuid(32)] });
    run = { schema: "material-processing-run/v6", run_id: newRun, material_id: material, input_source_set_id: uuid(9), base_revision: revision, source_names: ["A.pdf", "B.txt", lastFilename], source_artifact_id: uuid(13), status: "running", progress_stage: "semantics", completed_pages: 1, total_pages: 3, output_binding: null, error_code: null, cancel_requested_at: null, created_at: timestamp, updated_at: timestamp, completed_at: null };
    return route.fulfill({ status: 202, json: run });
  });
  await page.route(`**/v1/material-processing-runs/${newRun}`, route => route.fulfill({ json: run }));
  await page.route(`**/v2/material-processing-runs/${newRun}/cancel`, route => {
    cancels++;
    expect(route.request().postDataJSON()).toEqual({ schema: "material-revision-cancel/v1", base_revision: revision });
    if (!run) throw new Error("RUN_NOT_STARTED");
    run = { ...run, status: "cancelled", cancel_requested_at: timestamp, completed_at: timestamp };
    return route.fulfill({ json: run });
  });
  await page.goto(`/materials/${material}/runs/${oldRun}`);
  await expect(page.getByRole("heading", { name: "整體流程進度（估計）", exact: true })).toBeVisible();
  const initialColumns = await page.locator(".processing-grid").evaluate(element => getComputedStyle(element).gridTemplateColumns);
  const initialMascot = await page.locator(".processing-timeline-heading img").boundingBox();
  await page.goto(`/materials/${material}/sources`);
  await expect(page.getByRole("heading", { name: "新增教材", exact: true })).toBeVisible();
  await page.getByLabel("選擇新增教材", { exact: true }).setInputFiles([
    { name: "B.txt", mimeType: "text/plain", buffer: Buffer.from("Queue uses FIFO.") },
    { name: lastFilename, mimeType: "application/pdf", buffer: Buffer.from("%PDF-synthetic") },
  ]);
  await page.getByRole("button", { name: "上傳新增教材" }).click();
  await expect(page.getByLabel("加入這次更新")).toHaveCount(2);
  await expect(page.getByRole("button", { name: "確認新增並更新地圖" })).toHaveCount(0);
  sources[2].status = "ready";
  await expect(page.getByRole("button", { name: "確認新增並更新地圖" })).toBeEnabled();
  await expect(page.getByRole("button", { name: "開啟目前地圖" })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy();
  await page.screenshot({ path: info.outputPath(`append-${width}.png`), fullPage: true });
  await page.getByRole("button", { name: "確認新增並更新地圖" }).click();
  await expect(page.getByRole("heading", { name: "正在更新教材" })).toBeVisible();
  await expect(page.locator(".processing-grid > section.processing-card")).toHaveCount(2);
  expect(await page.locator(".processing-grid").evaluate(element => getComputedStyle(element).gridTemplateColumns)).toBe(initialColumns);
  const updatingMascot = await page.locator(".processing-timeline-heading img").boundingBox();
  expect(updatingMascot!.width).toBe(initialMascot!.width);
  expect(updatingMascot!.height).toBe(initialMascot!.height);
  await expect(page.getByRole("progressbar", { name: "整體流程進度（估計） 57%", exact: true })).toHaveAttribute("value", "57");
  await expect(page.getByRole("progressbar", { name: "本階段進度 33%，已完成 1 / 3 頁", exact: true })).toHaveAttribute("value", "33");
  await expect(page.locator(".processing-times dt")).toHaveText(["已耗時", "最近更新"]);
  await expect(page.locator(".processing-times")).toContainText("1 分 30 秒");
  await expect(page.locator(".status-timeline li")).toHaveCount(4);
  await expect(page.locator(".status-timeline li.is-active")).toContainText("建立概念、關係與學習順序");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath(`updating-${width}.png`), fullPage: true });
  await expect(page.getByRole("button", { name: "繼續學習", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "取消並刪除教材" })).toHaveCount(0);
  await page.getByRole("button", { name: "取消這次更新", exact: true }).click();
  await expect(page.getByRole("heading", { name: "已取消這次更新" })).toBeVisible();
  await page.reload();
  await expect(page.getByRole("heading", { name: "已取消這次更新" })).toBeVisible();
  await page.getByRole("button", { name: "返回教材庫", exact: true }).click();
  await expect(page.getByRole("button", { name: "繼續學習", exact: true })).toBeVisible();
  expect({ starts, cancels, wholeDeletes }).toEqual({ starts: 1, cancels: 1, wholeDeletes: 0 });
});

for (const width of [1536, 390]) test(`quality notices stay in processing while the library opens the current map at ${width}px`, async ({ page }) => {
  await page.setViewportSize({ width, height: 844 });
  const newRevision = `knowledge-structure:sha256:${"c".repeat(64)}`;
  const run: MaterialProcessingRunView = {
    schema: "material-processing-run/v6", run_id: newRun, material_id: material, base_revision: revision,
    source_names: ["A.pdf", "B.pdf"], source_artifact_id: uuid(13), status: "partial", progress_stage: "completed",
    completed_pages: 2, total_pages: 2, error_code: null, cancel_requested_at: null,
    created_at: timestamp, updated_at: timestamp, completed_at: timestamp,
    output_binding: { schema: "material-run-output-binding/v4", knowledge_structure_revision: newRevision,
      runtime_lock_sha256: "d".repeat(64), page_count: 2, processing: "partial", quality: "needs_review",
      decision: "review", reason_codes: ["RELATIONS_REJECTED"], ocr_calls: 0, semantic_calls: 1 },
  };
  const item: MaterialLibraryItem = {
    schema: "material-library-item/v3", material_id: material, head_revision: newRevision,
    source_artifact_id: uuid(13), display_name: "資料結構", size_bytes: 1024, created_at: timestamp,
    latest_attempt: run, study_sessions: [], available_structures: [
      { run_id: newRun, knowledge_structure_revision: newRevision, created_at: timestamp, status: "partial", base_revision: revision },
    ],
  };
  await page.route("**/v1/session", route => route.fulfill({ json: { schema: "learner-identity/v1", learner_id: uuid(99) } }));
  await page.route("**/v1/session/refresh", route => route.fulfill({ json: { schema: "learner-identity/v1", learner_id: "33333333-3333-4333-8333-333333333333" } }));
  await page.route(`**/v1/material-processing-runs/${newRun}`, route => route.fulfill({ json: run }));
  await page.route(`**/v1/materials/${material}`, route => route.fulfill({ json: item }));
  await page.route("**/v1/materials", route => route.fulfill({ json: { schema: "material-library/v2", materials: [item] } }));
  await page.route("**/v1/materials/*/knowledge-structures/*", route => route.fulfill({ status: 503, json: {} }));
  await page.goto(`/materials/${material}/runs/${newRun}`);
  await expect(page.getByRole("heading", { name: "教材更新完成", exact: true })).toBeVisible();
  await expect(page.getByRole("status")).toContainText("部分內容建議對照來源確認");
  await expect(page.getByRole("button", { name: "查看待審結果" })).toHaveCount(0);
  await page.getByRole("button", { name: "開啟目前地圖", exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`/runs/${newRun}/knowledge-structures/${encodeURIComponent(newRevision)}$`));
  await page.goto("/materials");
  await expect(page.getByRole("article")).not.toContainText(/最新處理|更新完成|部分內容待複核|partial|needs_review/);
  await expect(page.getByRole("article").locator(".library-state")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "繼續學習", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "開啟知識地圖", exact: true })).toBeVisible();
  item.study_sessions = [{ study_session_id: uuid(77), run_id: newRun, knowledge_structure_revision: newRevision,
    status: "active", started_at: timestamp, current_concept_id: null }];
  await page.reload();
  const card = page.getByRole("article");
  await expect(card.locator(".primary-button")).toHaveText("繼續學習");
  await expect(card.locator(".secondary-button")).toHaveText("開啟知識地圖");
  await expect(card.locator(".state-actions > button")).toHaveCount(2);
  await expect(card).not.toContainText(/最新處理|部分內容待複核|先前題目與作答/);
  await page.screenshot({ path: `/tmp/studydy-partial-study-${width}.png`, fullPage: true });
  await card.getByRole("button", { name: "繼續學習", exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`/runs/${newRun}/knowledge-structures/${encodeURIComponent(newRevision)}/study-sessions/${uuid(77)}$`));
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});
