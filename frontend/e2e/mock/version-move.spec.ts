import { expect, test, type Page } from "@playwright/test";
import type { MaterialLibraryItem, VersionMovePreview } from "../../src/api/contracts";
import { artifactId, materialId, run, runId, sessionId, structureRevision, structureView, mockKnowledgeMapApi, json } from "../fixtures/knowledge-map";

// 教材新版承接：教材頁的「有新版本」提示、預覽、確認與重送，以及來源頁的重新檢核入口。
const oldRevision = `knowledge-structure:sha256:${"f".repeat(64)}`;
const oldRunId = "22222222-2222-4222-8222-999999999999";
const cardSetId = "55555555-5555-4555-8555-555555555555";
const newCardSetId = "55555555-5555-4555-8555-666666666666";
const newSessionId = "33333333-3333-4333-8333-444444444444";
const digest = `version-move:sha256:${"e".repeat(64)}`;
const mapPath = `/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}`;

function material(structures = 2): MaterialLibraryItem {
  return {
    schema: "material-library-item/v1", material_id: materialId, source_artifact_id: artifactId,
    display_name: "資料結構講義.pdf", size_bytes: 100, created_at: "2026-10-02T00:00:00Z",
    head_revision: structureRevision, latest_attempt: run as MaterialLibraryItem["latest_attempt"],
    study_sessions: [{ study_session_id: sessionId, run_id: oldRunId, knowledge_structure_revision: oldRevision,
      current_concept_id: structureView().concepts[0].concept_id, status: "active", started_at: "2026-10-02T00:00:00Z" }],
    available_structures: [
      { run_id: runId, knowledge_structure_revision: structureRevision, created_at: "2026-10-20T00:00:00Z", status: "succeeded" },
      { run_id: oldRunId, knowledge_structure_revision: oldRevision, created_at: "2026-10-02T00:00:00Z", status: "succeeded" },
    ].slice(0, structures),
  };
}

function preview(): VersionMovePreview {
  const [stack, array] = structureView().concepts;
  return {
    schema: "version-move-preview/v1", material_id: materialId, to_revision: structureRevision,
    preview_digest: digest, kept_podcasts: 2,
    card_sets: [{
      card_set_id: cardSetId, name: "資料結構重點", from_revision: oldRevision, concept_count: 3, target_count: 2,
      carried_reviews: 1, reused_designs: 1, movable: true,
      mapped: [
        { from_concept_id: stack.concept_id, from_label: "Stack", to_concept_id: stack.concept_id, to_label: stack.label, basis: "evidence" },
        { from_concept_id: array.concept_id, from_label: "Array", to_concept_id: array.concept_id, to_label: array.label, basis: "label" },
      ],
      unmapped: [{ concept_id: `concept:sha256:${"9".repeat(64)}`, label: "雜湊表", reason: "split" }],
    }],
    study: {
      study_session_id: sessionId, from_revision: oldRevision, target_study_session_id: null, completed_count: 2,
      carried: [{ concept_id: stack.concept_id, label: stack.label, from_labels: ["Stack"] }],
      not_carried: [{ concept_id: `concept:sha256:${"7".repeat(64)}`, label: "佇列", reason: "not_found" }],
      current_concept: { concept_id: stack.concept_id, label: stack.label },
    },
  };
}

async function mockMove(page: Page, structures = 2) {
  await mockKnowledgeMapApi(page);
  await page.route(`**/v1/materials/${materialId}`, (route) => json(route, material(structures)));
  const reads: string[] = [];
  const posts: { key: string | undefined; body: unknown }[] = [];
  let fail = true;
  let moved = false;
  await page.route(`**/v1/materials/${materialId}/version-move`, async (route) => {
    if (route.request().method() === "GET") {
      reads.push(route.request().url());
      return json(route, moved ? { ...preview(), preview_digest: null, card_sets: [], study: null } : preview());
    }
    posts.push({ key: route.request().headers()["idempotency-key"], body: route.request().postDataJSON() });
    // 第一次模擬回應遺失，前端以同一個鍵重送。
    if (fail) { fail = false; return route.abort("failed"); }
    moved = true;
    return json(route, {
      schema: "version-move/v1", move_id: "77777777-7777-4777-8777-777777777777", material_id: materialId,
      to_revision: structureRevision, created_at: "2026-10-22T01:00:00Z", study_session_id: newSessionId,
      card_sets: [{ from_card_set_id: cardSetId, card_set_id: newCardSetId, status: "pending" }],
    });
  });
  return { reads, posts };
}

for (const width of [1440, 390]) {
  test(`material page offers moving cards and progress to the new version at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    const { reads, posts } = await mockMove(page);
    await page.goto(mapPath);
    const notice = page.getByRole("region", { name: "有新版本" });
    await expect(notice).toContainText("你的 1 個卡組與學習進度還在舊版");
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await notice.getByRole("button", { name: "移到新版" }).click();
    const dialog = page.getByRole("dialog", { name: "移到新版" });
    await expect(dialog).toContainText("卡組「資料結構重點」");
    await expect(dialog).toContainText("2 張卡移到新版，沿用 1 張的複習排程");
    await expect(dialog).toContainText("1 張卡的內容在新版有更動");
    await expect(dialog).toContainText("這 1 張卡在新版找不到對應概念，不會移動：");
    await expect(dialog).toContainText("雜湊表內容在新版分散到多個概念");
    await expect(dialog).toContainText("舊版已完成 2 個概念，其中 1 個在新版標示為已完成");
    await expect(dialog).toContainText("佇列新版找不到對應的概念");
    await expect(dialog).toContainText("2 集 Podcast 維持舊版內容");
    await dialog.getByText("查看概念對應").click();
    await expect(dialog).toContainText("依相同原文");
    await expect(dialog).toContainText("依相同名稱");
    // 取消全部選項時不能確認。
    const confirm = dialog.getByRole("button", { name: "確認移到新版" });
    await dialog.getByRole("checkbox", { name: /卡組「資料結構重點」/ }).uncheck();
    await dialog.getByRole("checkbox", { name: /學習進度/ }).uncheck();
    await expect(confirm).toBeDisabled();
    await dialog.getByRole("checkbox", { name: /卡組「資料結構重點」/ }).check();
    await dialog.getByRole("checkbox", { name: /學習進度/ }).check();
    expect(await dialog.evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true);

    await confirm.click();
    await expect(dialog.getByRole("alert")).toBeVisible();
    await confirm.click();
    await expect(dialog.getByRole("heading", { name: "已移到新版" })).toBeVisible();
    await expect(dialog).toContainText("已建立 1 個新版卡組，新版學習已承接可對應的進度");
    await expect(dialog).toContainText("部分卡面正在依新版內容重新製作");
    expect(posts).toHaveLength(2);
    expect(posts[0].key).toBeTruthy();
    expect(posts[1].key).toBe(posts[0].key);
    expect(posts[1].body).toEqual({
      schema: "version-move-create/v1", to_revision: structureRevision, card_set_ids: [cardSetId],
      include_study: true, preview_digest: digest,
    });
    expect(reads.length).toBeGreaterThanOrEqual(1);
    await dialog.getByRole("button", { name: "完成" }).click();
    // 重新載入後已沒有可移動的項目，提示消失。
    await expect(page.getByRole("tab", { name: "知識地圖" })).toBeVisible();
    await expect(page.getByRole("region", { name: "有新版本" })).toHaveCount(0);
  });
}

test("a material with only its current version does not ask for a move preview", async ({ page }) => {
  const { reads } = await mockMove(page, 1);
  await page.goto(mapPath);
  await expect(page.getByRole("tab", { name: "知識地圖" })).toBeVisible();
  await page.waitForLoadState("networkidle");
  expect(reads).toEqual([]);
  await expect(page.getByRole("region", { name: "有新版本" })).toHaveCount(0);
});

test("sources page starts a review-only recheck of the current version", async ({ page }) => {
  await mockKnowledgeMapApi(page);
  const item = { ...material(1), source_count: 1 };
  await page.route(`**/v1/materials/${materialId}`, (route) => json(route, item));
  await page.route("**/v1/materials", (route) => json(route, { schema: "material-library/v1", materials: [item] }));
  await page.route(`**/v1/materials/${materialId}/sources`, (route) => json(route, {
    schema: "material-sources/v1", material_id: materialId, sources: [{
      source_id: "11111111-1111-4111-8111-000000000010", normalization_id: "11111111-1111-4111-8111-000000000011",
      original_artifact_id: "11111111-1111-4111-8111-000000000012", normalized_artifact_id: artifactId,
      original_name: "A.pdf", media_type: "application/pdf", status: "ready", page_count: 2, error_code: null, included: true,
    }],
  }));
  const reviewRun = { ...run, run_id: "22222222-2222-4222-8222-888888888888", base_revision: structureRevision,
    status: "pending", progress_stage: "queued", completed_pages: 0, output_binding: null, completed_at: null };
  const sent: { key: string | undefined; body: unknown }[] = [];
  await page.route(`**/v1/materials/${materialId}/review`, (route) => {
    sent.push({ key: route.request().headers()["idempotency-key"], body: route.request().postDataJSON() });
    return json(route, reviewRun, 202);
  });
  await page.route(`**/v1/material-processing-runs/${reviewRun.run_id}`, (route) => json(route, reviewRun));
  await page.goto(`/materials/${materialId}/sources`);
  const section = page.getByRole("region", { name: "重新分析與重新檢核" });
  await expect(section).toContainText("舊版的卡組與學習進度會保留");
  page.once("dialog", (dialog) => void dialog.dismiss());
  await section.getByRole("button", { name: "重新檢核" }).click();
  expect(sent).toEqual([]);
  page.once("dialog", (dialog) => void dialog.accept());
  await section.getByRole("button", { name: "重新檢核" }).click();
  await expect(page).toHaveURL(new RegExp(`/materials/${materialId}/runs/${reviewRun.run_id}$`));
  expect(sent).toEqual([{ key: expect.any(String), body: { schema: "material-review-create/v1", base_revision: structureRevision } }]);
});
