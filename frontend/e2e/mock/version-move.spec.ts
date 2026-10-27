import { expect, test, type Page } from "@playwright/test";
import type { MaterialLibraryItem, VersionMovePreview, CardSetSummary } from "../../src/api/contracts";
import { artifactId, materialId, run, runId, sessionId, structureRevision, structureView, mockKnowledgeMapApi, json, emptyVersionMovePreview } from "../fixtures/knowledge-map";

// 教材更新承接：伺服器預覽、跨裝置提醒、確認與返回入口。
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
    schema: "version-move-preview/v2", material_id: materialId, to_revision: structureRevision,
    automatic_safe: false, blocking_reasons: [{ code: "unmapped_cards", count: 1 }, { code: "unmapped_progress", count: 1 }],
    notice_dismissed: false, previous: null, automatic_move: null,
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

async function mockMove(page: Page, structures = 2, server = { value: preview() }) {
  await mockKnowledgeMapApi(page);
  await page.route(`**/v1/materials/${materialId}`, (route) => json(route, material(structures)));
  const reads: string[] = [];
  const posts: { key: string | undefined; body: unknown }[] = [];
  let fail = true;
  await page.route(`**/v1/materials/${materialId}/version-move`, async (route) => {
    if (route.request().method() === "GET") {
      reads.push(route.request().url());
      return json(route, server.value);
    }
    posts.push({ key: route.request().headers()["idempotency-key"], body: route.request().postDataJSON() });
    // 第一次模擬回應遺失，前端以同一個鍵重送。
    if (fail) { fail = false; return route.abort("failed"); }
    server.value = { ...server.value, preview_digest: null, card_sets: [], study: null,
      previous: { move_id: "77777777-7777-4777-8777-777777777777", revision: oldRevision, card_set_ids: [cardSetId], study_session_id: sessionId } };
    return json(route, {
      schema: "version-move/v1", move_id: "77777777-7777-4777-8777-777777777777", material_id: materialId,
      to_revision: structureRevision, created_at: "2026-10-22T01:00:00Z", study_session_id: newSessionId,
      card_sets: [{ from_card_set_id: cardSetId, card_set_id: newCardSetId, status: "pending" }],
    });
  });
  await page.route(`**/v1/materials/${materialId}/version-move/notice`, route => {
    expect(route.request().method()).toBe("PUT");
    expect(route.request().postDataJSON()).toEqual({ schema: "version-move-notice/v1", dismissed: true });
    server.value = { ...server.value, notice_dismissed: true };
    return json(route, { schema: "version-move-notice/v1", dismissed: true });
  });
  return { reads, posts, server };
}

for (const width of [1440, 1024, 390]) {
  test(`material page offers moving cards and progress to the new version at ${width}px`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: width === 390 ? 844 : width === 1024 ? 768 : 900 });
    const { reads, posts } = await mockMove(page);
    await page.goto(mapPath);
    const notice = page.getByRole("region", { name: "教材已更新" });
    await expect(notice).toContainText("卡組與學習進度還在更新前的內容");
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await page.screenshot({ path: testInfo.outputPath(`update-notice-${width}.png`), animations: "disabled" });
    await notice.getByRole("button", { name: "更新", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "更新卡組與學習進度" });
    await expect(dialog).toContainText("卡組「資料結構重點」");
    await expect(dialog.getByRole("button", { name: "確認更新", exact: true })).toBeInViewport({ ratio: 1 });
    await page.screenshot({ path: testInfo.outputPath(`update-preview-${width}.png`), animations: "disabled" });
    await expect(dialog).toContainText("2 張卡將更新，沿用 1 張的學習標記");
    await expect(dialog).toContainText("1 張卡的內容在更新後有更動");
    await expect(dialog).toContainText("這 1 張卡在更新後找不到對應概念，不會移動：");
    await expect(dialog).toContainText("雜湊表內容在更新後分散到多個概念");
    await expect(dialog).toContainText("更新前已完成 2 個概念，其中 1 個在更新後標示為已完成");
    await expect(dialog).toContainText("佇列更新後找不到對應的概念");
    await expect(dialog).toContainText("2 集 Podcast 維持更新前內容");
    await dialog.getByText("查看概念對應").click();
    await expect(dialog).toContainText("依相同原文");
    await expect(dialog).toContainText("依相同名稱");
    // 取消全部選項時不能確認。
    const confirm = dialog.getByRole("button", { name: "確認更新" });
    await dialog.getByRole("checkbox", { name: /卡組「資料結構重點」/ }).uncheck();
    await dialog.getByRole("checkbox", { name: /學習進度/ }).uncheck();
    await expect(confirm).toBeDisabled();
    await dialog.getByRole("checkbox", { name: /卡組「資料結構重點」/ }).check();
    await dialog.getByRole("checkbox", { name: /學習進度/ }).check();
    expect(await dialog.evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true);

    await confirm.click();
    await expect(dialog.getByRole("alert")).toBeVisible();
    await confirm.click();
    await expect(dialog.getByRole("heading", { name: "卡組與學習進度已更新" })).toBeVisible();
    await expect(dialog).toContainText("已建立 1 個更新後卡組，更新後學習已承接可對應的進度");
    await expect(dialog).toContainText("部分卡面正在依更新後內容重新製作");
    expect(posts).toHaveLength(2);
    expect(posts[0].key).toBeTruthy();
    expect(posts[1].key).toBe(posts[0].key);
    expect(posts[1].body).toEqual({
      schema: "version-move-create/v1", to_revision: structureRevision, card_set_ids: [cardSetId],
      include_study: true, preview_digest: digest,
    });
    expect(reads.length).toBeGreaterThanOrEqual(1);
    await dialog.getByRole("button", { name: "完成" }).click();
    // 手動更新後仍能找到上一批入口。
    await expect(page.getByRole("tab", { name: "知識地圖" })).toBeVisible();
    await expect(page.getByRole("button", { name: "回到更新前內容", exact: true })).toBeVisible();
  });
}

test("a material with one listed structure still shows server automatic completion", async ({ page }) => {
  const server = { value: { ...emptyVersionMovePreview(materialId, structureRevision),
    automatic_move: { status: "completed" as const, move_id: null, error_code: null } } };
  const { reads } = await mockMove(page, 1, server);
  await page.goto(mapPath);
  await expect(page.getByText("卡組和學習進度已一起更新", { exact: true })).toBeVisible();
  expect(reads.length).toBeGreaterThanOrEqual(1);
});


test("update completion shows saved structure changes and one secondary move preview",async({page},testInfo)=>{
 const {reads,posts}=await mockMove(page);
 const completed={...run,base_revision:oldRevision,source_names:['舊教材.pdf','新增影片'],added_sources:[{source_id:artifactId,display_name:'新增影片'}],existing_source_count:3,structure_changes:{before:{concepts:34,relations:24},after:{concepts:38,relations:29}}};
 await page.route(`**/v1/material-processing-runs/${runId}`,route=>json(route,completed));
 await page.goto(`/materials/${materialId}/runs/${runId}`);
 await expect(page.getByRole('heading',{name:'教材更新完成',exact:true})).toBeVisible();
 await expect(page.getByRole('region',{name:'這次更新改了什麼',exact:true})).toContainText('新增 1 份來源 · 概念 34 → 38 個 · 關係 24 → 29 條');
 const notice=page.getByRole('region',{name:'教材已更新',exact:true});
 await expect(notice).toContainText('卡組與學習進度還在更新前的內容');
 const open=page.getByRole('button',{name:'開啟知識地圖',exact:true});
 await expect(page.locator('.processing-page .primary-button')).toHaveCount(1);
 for(const [width,height] of [[1440,900],[1300,900],[1200,900],[1024,768],[390,844]]){
  await page.setViewportSize({width,height});await page.evaluate(()=>window.scrollTo(0,0));
  if(width===1440)await expect(open).toBeInViewport({ratio:1});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  await page.screenshot({path:testInfo.outputPath(`update-completed-${width}.png`),animations:'disabled'});
 }
 await notice.getByRole('button',{name:'更新',exact:true}).click();
 const dialog=page.getByRole('dialog',{name:'更新卡組與學習進度',exact:true});await expect(dialog).toContainText('卡組「資料結構重點」');
 expect(reads).toHaveLength(1);expect(posts).toHaveLength(0);
 await dialog.getByRole('button',{name:'關閉更新預覽',exact:true}).click();
 await open.click();await expect(page).toHaveURL(new RegExp(`${mapPath}$`));
});


const moveId = "77777777-7777-4777-8777-777777777777";
const otherCardSetId = "55555555-5555-4555-8555-777777777777";
const secondCardName = "更新前卡組二：資料結構與演算法LongUnbrokenCardSetNameForReview";
function summary(id = cardSetId, name = "更新前卡組一"): CardSetSummary {
  return { version: 1, card_set_id: id, material_id: materialId, material_name: "資料結構講義.pdf",
    knowledge_structure_revision: oldRevision, name, card_count: 2,
    generation: { status: "ready", stage: "complete", completed: 2, total: 2, error_code: null, retained_original: 0, cached: 0 },
    created_at: "2026-10-02T00:00:00Z", is_current_revision: false };
}

test("dismissal persists across independent browsers and source/card entries remain available", async ({ page, browser }, testInfo) => {
  const server = { value: preview() };
  await mockMove(page, 2, server);
  await page.goto(mapPath);
  const notice = page.getByRole("region", { name: "教材已更新" });
  await expect(notice).toContainText("1 張卡無法完整帶過去");
  await expect(notice).toContainText("1 個已完成概念無法直接保留完成狀態");
  await notice.getByRole("button", { name: "關閉教材更新提醒" }).click();
  await expect(notice).toHaveCount(0);
  const other = await browser.newContext();
  const second = await other.newPage();
  const fixture = await mockMove(second, 2, server);
  await second.goto(mapPath);
  await expect.poll(() => fixture.reads.length).toBeGreaterThan(0);
  await expect(second.getByRole("region", { name: "教材已更新" })).toHaveCount(0);
  await second.route(`**/v1/materials/${materialId}/sources`, route => json(route, { schema: "material-sources/v1", material_id: materialId, sources: [] }));
  const view = structureView(oldRevision);
  const card = { ...summary(), generation: { ...summary().generation, status: "pending", stage: "preparing", completed: 0 } };
  await second.route(`**/v1/card-sets/${cardSetId}/study`, route => json(route, { ...card, schema: "card-study/v1",
    source_resolver: decodeURIComponent(view.source_resolver), status: view.status,
    navigation: view.concepts.map(item => ({ concept_id: item.concept_id, label: item.label, page_count: null })), batch: null }));
  await second.route(`**/v1/card-sets/${cardSetId}/generation`, route => json(route, card.generation));
  for (const path of [`/materials/${materialId}/sources`, `/materials/${materialId}/concept-cards/${cardSetId}`]) {
    await second.goto(path);
    const entry = second.getByRole("region", { name: "教材已更新" });
    await expect(entry).toContainText("卡組與學習進度還在更新前的內容");
    await expect(entry).toHaveClass("version-move-entry");
    for (const [width, height] of [[1440, 900], [1024, 768], [390, 844]]) {
      await second.setViewportSize({ width, height });
      expect(await second.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      await second.screenshot({ path: testInfo.outputPath(`compact-${path.endsWith("sources") ? "sources" : "cards"}-${width}.png`), animations: "disabled" });
    }
    await entry.getByRole("button", { name: "更新", exact: true }).click();
    const dialog = second.getByRole("dialog", { name: "更新卡組與學習進度" });
    await expect(dialog).toContainText("雜湊表");
    await dialog.getByRole("button", { name: "關閉更新預覽" }).click();
  }
  await other.close();
});

for (const outcome of ["completed", "failed"] as const)
  test(`automatic update polls pending to ${outcome} without frontend move requests`, async ({ page }) => {
    const safe = preview();
    safe.card_sets = safe.card_sets.map(item => ({ ...item, concept_count: item.target_count, unmapped: [] }));
    safe.study = { ...safe.study!, completed_count: 1, not_carried: [] };
    const server = { value: { ...safe, automatic_safe: true, blocking_reasons: [],
      automatic_move: { status: "pending" as "pending" | "completed" | "failed", move_id: null as string | null, error_code: null as string | null } } };
    const { posts } = await mockMove(page, 2, server);
    await page.clock.install();
    await page.goto(mapPath);
    await expect(page.getByText("正在一起更新卡組和學習進度…", { exact: true })).toBeVisible();
    server.value = outcome === "completed" ? { ...server.value, preview_digest: null, card_sets: [], study: null,
      automatic_move: { status: outcome, move_id: moveId, error_code: null } } : { ...server.value,
      automatic_move: { status: outcome, move_id: null, error_code: "STORAGE_UNAVAILABLE" } };
    await page.clock.runFor(2100);
    if (outcome === "completed") await expect(page.getByText("卡組和學習進度已一起更新", { exact: true })).toBeVisible();
    else {
      await expect(page.getByText("自動更新未完成，請查看預覽後再試。", { exact: true })).toBeVisible();
      await page.getByRole("region", { name: "教材已更新" }).getByRole("button", { name: "更新", exact: true }).click();
      await expect(page.getByRole("dialog", { name: "更新卡組與學習進度" })).toBeVisible();
    }
    expect(posts).toHaveLength(0);
  });

for (const entry of ["card", "study"] as const)
  test(`return uses the latest move id and preserves each ${entry} entry binding`, async ({ page }, testInfo) => {
    const previousHead = `knowledge-structure:sha256:${"d".repeat(64)}`;
    const previous = { move_id: moveId, revision: previousHead, card_set_ids: [cardSetId, otherCardSetId], study_session_id: sessionId };
    const server = { value: { ...emptyVersionMovePreview(materialId, structureRevision), previous,
      automatic_move: { status: "completed" as const, move_id: moveId, error_code: null } } };
    const { posts } = await mockMove(page, 2, server);
    await page.route(`**/v1/materials/${materialId}`, route => json(route, { ...material(),
      available_structures: [...material().available_structures, { run_id: "22222222-2222-4222-8222-888888888888", knowledge_structure_revision: previousHead, created_at: "2026-10-10T00:00:00Z", status: "succeeded" }] }));
    await page.route("**/v1/card-sets", route => json(route, { schema: "card-set-list/v1", card_sets: [summary(), summary(otherCardSetId, secondCardName)] }));
    const returned: unknown[] = [];
    await page.route(`**/v1/materials/${materialId}/version-move/return`, route => {
      expect(route.request().method()).toBe("POST");
      returned.push(route.request().postDataJSON());
      return json(route, { schema: "version-move-return/v1", ...previous });
    });
    await page.goto(mapPath);
    await page.getByRole("button", { name: "回到更新前內容", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "回到更新前的內容" });
    await expect(dialog).toContainText("教材維持目前內容，更新後的作答與標記也會保留");
    await expect(dialog.getByRole("button", { name: "更新前卡組一", exact: true })).toBeVisible();
    await expect(dialog.getByRole("button", { name: secondCardName, exact: true })).toBeVisible();
    expect(returned).toEqual([{ schema: "version-move-return/v1", expected_move_id: moveId }]);
    if (entry === "card") for (const [width, height] of [[1440, 900], [1024, 768], [390, 844]]) {
      await page.setViewportSize({ width, height });
      const bounds = (await dialog.boundingBox())!;
      expect(bounds.x).toBeGreaterThanOrEqual(0);
      expect(bounds.x + bounds.width).toBeLessThanOrEqual(width);
      expect(bounds.y + bounds.height).toBeLessThanOrEqual(height);
      await page.screenshot({ path: testInfo.outputPath(`previous-entries-${width}.png`), animations: "disabled" });
    }
    await dialog.getByRole("button", { name: entry === "card" ? secondCardName : "更新前的學習", exact: true }).click();
    const expected = entry === "card" ? `/materials/${materialId}/concept-cards/${otherCardSetId}`
      : `/materials/${materialId}/runs/${oldRunId}/knowledge-structures/${encodeURIComponent(oldRevision)}/study-sessions/${sessionId}`;
    await expect.poll(() => new URL(page.url()).pathname).toBe(expected);
    expect(posts).toHaveLength(0);
    expect(server.value.to_revision).toBe(structureRevision);
  });

for (const code of ["VERSION_MOVE_PREVIEW_STALE", "VERSION_MOVE_PREVIOUS_UNAVAILABLE"]) test(`return ${code} refreshes the latest server entry`, async ({ page }) => {
  const previous = { move_id: moveId, revision: oldRevision, card_set_ids: [], study_session_id: sessionId };
  const server: { value: VersionMovePreview } = { value: { ...emptyVersionMovePreview(materialId, structureRevision), previous } };
  const { reads } = await mockMove(page, 2, server);
  await page.route(`**/v1/materials/${materialId}/version-move/return`, route => {
    server.value = { ...server.value, previous: code === "VERSION_MOVE_PREVIEW_STALE" ? { ...previous, move_id: newSessionId } : null };
    return json(route, { schema: "api-error/v1", request_id: runId, reason_code: code, retryable: false, message: "Request could not be completed." }, 409);
  });
  await page.goto(mapPath);
  await page.getByRole("button", { name: "回到更新前內容", exact: true }).click();
  await expect.poll(() => reads.length).toBeGreaterThanOrEqual(2);
  await expect(page.getByRole("alert")).toContainText(code === "VERSION_MOVE_PREVIEW_STALE" ? "可返回的內容剛有變動" : "目前沒有可返回的更新前內容");
  if (code === "VERSION_MOVE_PREVIEW_STALE") {
    let requested: unknown;
    await page.route(`**/v1/materials/${materialId}/version-move/return`, route => {
      requested = route.request().postDataJSON();
      return json(route, { schema: "version-move-return/v1", ...server.value.previous });
    });
    await page.getByRole("button", { name: "回到更新前內容", exact: true }).click();
    await expect(page.getByRole("dialog", { name: "回到更新前的內容" })).toBeVisible();
    expect(requested).toEqual({ schema: "version-move-return/v1", expected_move_id: newSessionId });
  }
});


test("a pending read cannot reopen a notice after dismissal is saved", async ({ page }) => {
  const server: { value: VersionMovePreview } = { value: { ...preview(), automatic_move: { status: "pending", move_id: null, error_code: null } } };
  await mockMove(page, 2, server);
  let release!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  let reads = 0;
  let waiting = false;
  await page.route(`**/v1/materials/${materialId}/version-move`, async route => {
    expect(route.request().method()).toBe("GET");
    const snapshot = structuredClone(server.value);
    if (++reads === 2) { waiting = true; await gate; }
    return json(route, snapshot);
  });
  await page.clock.install();
  await page.goto(mapPath);
  await expect(page.getByText("正在一起更新卡組和學習進度…", { exact: true })).toBeVisible();
  await page.clock.runFor(2100);
  await expect.poll(() => waiting).toBe(true);
  await page.getByRole("button", { name: "關閉教材更新提醒" }).click();
  await expect.poll(() => reads).toBe(3);
  await expect(page.getByRole("region", { name: "教材已更新" })).toHaveCount(0);
  const stale = page.waitForResponse(async response => response.url().endsWith("/version-move") && !(await response.json()).notice_dismissed);
  release();
  await (await stale).finished();
  await page.clock.runFor(100);
  await expect(page.getByRole("region", { name: "教材已更新" })).toHaveCount(0);
  expect(server.value.notice_dismissed).toBe(true);
});

test("remaining older items take precedence over an earlier automatic success", async ({ page }) => {
  await mockMove(page, 2, { value: { ...preview(), automatic_move: { status: "completed", move_id: moveId, error_code: null } } });
  await page.goto(mapPath);
  const notice = page.getByRole("region", { name: "教材已更新" });
  await expect(notice).toContainText("卡組與學習進度還在更新前的內容");
  await notice.getByRole("button", { name: "更新", exact: true }).click();
  await expect(page.getByRole("dialog", { name: "更新卡組與學習進度" })).toContainText("卡組「資料結構重點」");
});
