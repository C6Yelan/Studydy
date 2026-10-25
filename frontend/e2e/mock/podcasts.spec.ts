import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from 'node:fs';
import type { PodcastView } from "../../src/api/contracts";
import { artifactId, materialId, runId, sessionId, structureRevision, structureView, workspaceView, mockKnowledgeMapApi, json } from "../fixtures/knowledge-map";

const podcastId = "77777777-7777-4777-8777-777777777777";
const createPath = `/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}/create-podcast`;
async function mockPodcasts(page: Page) {
  const structure = structureView();
  await mockKnowledgeMapApi(page, structure);
  const claims = structure.concepts.map(c => ({ ...c.claims[0], concept_id: c.concept_id, label: c.label }));
  const labels = ["TCP 連線（Connection）", "TCP 三向握手 / 確認號"];
  claims.forEach((claim, i) => { claim.label = labels[i]; claim.text = "合成測試重點，保留必要的條件與數值。".repeat(20); });
  const episode = () => ({ delivery: "solo" as const, claims, script: null, audio: null });
  const view: PodcastView = {
    schema: "podcast/v1", run_id:runId, podcast_id: podcastId, material_id: materialId,
    material_name: "資料結構講義.pdf", knowledge_structure_revision: structureRevision,
    name: "我的 Podcast", delivery: "solo", video_enabled: true, concept_ids: claims.map(c => c.concept_id),
    status: "running", error_code: null, version: 1, created_at: "2026-10-03T00:00:00Z",
    episode_count: 3, completed_episodes: 1, duration_seconds: null as number | null, is_current_revision: true,
    source_resolver: `/v1/materials/${materialId}/knowledge-structures/${structureRevision}/evidence`,
    source_status: structure.status, excluded_pages: [], episodes: [episode(), episode(), episode()],
  };
  const script = { provider: "synthetic", segments: claims.map(c => ({ claim_id: c.claim_id, turns: [{ speaker: "host" as const, text: "這是合成的教材講解。" }] })) };
  view.episodes[0].script = script;
  view.episodes[0].audio = { artifact_id: artifactId, sha256: "a".repeat(64), duration_seconds: 195, provider: "synthetic" };
  let exists = true;
  let conflict = false;
  const actions: unknown[] = [];
  let deletes = 0;
  const summary = () => { const { schema, episodes, source_resolver, source_status, excluded_pages, ...rest } = view; return rest; };
  await page.route(`**/v1/podcasts/${podcastId}/episodes/*/video`, route=>json(route,{schema:'podcast-video/v1',podcast_id:podcastId,episode_index:Number(new URL(route.request().url()).pathname.split('/').at(-2)),status:'unprepared',version:0,error_code:null,video:null}));
  await page.route(`**/v1/podcasts/${podcastId}/episodes/*/timeline`,route=>json(route,{schema:'api-error/v1',request_id:sessionId,reason_code:'PODCAST_TIMELINE_NOT_READY',retryable:true,message:'Request could not be completed.'},409));
  await page.route("**/v1/podcasts", route => json(route, { schema: "podcast-list/v1", podcasts: exists ? [summary()] : [] }));
  await page.route(`**/v1/podcasts/${podcastId}`, route => {
    if (route.request().method() === "DELETE") { deletes++; exists = false; return json(route, { schema: "podcast-deleted/v1", podcast_id: podcastId }); }
    return json(route, view);
  });
  await page.route(`**/v1/podcasts/${podcastId}/actions`, route => {
    const body = route.request().postDataJSON(); actions.push(body);
    if (conflict) { conflict = false; view.version++; return json(route, { schema: "api-error/v1", request_id: sessionId, reason_code: "PODCAST_CONFLICT", retryable: false, message: "Synthetic conflict" }, 409); }
    expect(body.expected_version).toBe(view.version);
    if (body.action === "rename") view.name = body.name;
    view.version++; return json(route, summary());
  });
  const material = { schema: "material-library-item/v1", material_id: materialId, source_artifact_id: artifactId,
    display_name: view.material_name, size_bytes: 100, created_at: view.created_at, head_revision: structureRevision,
    latest_attempt: null, study_sessions: [], available_structures: [{ run_id: runId, knowledge_structure_revision: structureRevision, created_at: view.created_at, status: "succeeded" }] };
  await page.route(`**/v1/materials/${materialId}`, route => json(route, material));
  await page.route("**/v1/materials", route => json(route, { schema: "material-library/v1", materials: [material] }));
  return { view, structure, script, actions, conflict: () => { conflict = true; }, deletes: () => deletes };
}

test("management menu renames with conflict recovery; deletion needs confirmation", async ({ page }) => {
  const fixture = await mockPodcasts(page);
  await page.goto("/podcasts");
  const positionKey = await page.evaluate(async (id) => { const session = await (await fetch("/v1/session")).json(); const key = `studydy.podcast.position:${session.learner_id}:${id}`; localStorage.setItem(key, JSON.stringify({episode: 1, time: 10})); return key; }, podcastId);
  await page.getByRole("button", { name: "管理 Podcast「我的 Podcast」" }).click();
  await page.getByRole("button", { name: "重新命名", exact: true }).click();
  const dialog = page.getByRole("form", { name: "重新命名Podcast" });
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(page.locator(".library-item").getByRole("heading", { name: "我的 Podcast", exact: true })).toHaveCount(0);
  await dialog.getByLabel("Podcast 名稱").fill("更新後的名稱");
  fixture.conflict();
  await dialog.getByRole("button", { name: "儲存", exact: true }).click();
  await expect(dialog.getByRole("alert")).toBeVisible();
  await expect(dialog.getByLabel("Podcast 名稱")).toHaveValue("更新後的名稱");
  await expect.poll(() => fixture.view.version).toBe(2);
  await dialog.getByRole("button", { name: "儲存", exact: true }).click();
  await expect(page.getByRole("heading", { name: "更新後的名稱" })).toBeVisible();
  await expect(dialog).toHaveCount(0);
  await page.getByRole("button", { name: "查看進度" }).click();
  await expect(page.locator(".podcast-management")).toHaveCount(0);
  await page.getByRole("button", { name: "返回 Podcast", exact: true }).click();
  await page.getByRole("button", { name: "管理 Podcast「更新後的名稱」" }).click();
  await page.getByRole("button", { name: "刪除 Podcast", exact: true }).click();
  await page.getByRole("group", { name: "刪除 Podcast 確認" }).getByRole("button", { name: "取消", exact: true }).click();
  expect(fixture.deletes()).toBe(0);
  await page.getByRole("button", { name: "管理 Podcast「更新後的名稱」" }).click();
  await page.getByRole("button", { name: "刪除 Podcast", exact: true }).click();
  await page.getByRole("button", { name: "確認刪除 Podcast" }).click();
  await expect(page.getByRole("heading", { name: "讓教材說給你聽" })).toBeVisible();
  expect(fixture.deletes()).toBe(1);
  expect(await page.evaluate(key => localStorage.getItem(key), positionKey)).toBeNull();
});

test('unfinished Podcasts show only saved progress, cancel and retry',async({page})=>{
 const fixture=await mockPodcasts(page);await page.goto(`/podcasts/${podcastId}`);
 const progress=page.getByRole('region',{name:'Podcast 生成進度'});
 await expect(progress.getByRole('status')).toHaveText('第 2 / 3 集 · 整理內容');
 await expect(page.locator('video,audio,.media-playback-controls')).toHaveCount(0);
 fixture.view.episodes[1].script=fixture.script;
 await expect(progress.getByRole('status')).toHaveText('第 2 / 3 集 · 製作音訊');
 fixture.view.status='cancelled';
 await expect(progress.getByRole('button',{name:'接續生成'})).toBeVisible();
 await expect(page.locator('video,audio,.media-playback-controls')).toHaveCount(0);
 await page.setViewportSize({width:390,height:844});expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBe(390);
});

test("account creation chooses a material once and keeps name editing usable on mobile", async ({ page }) => {
  await mockPodcasts(page);
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/podcasts/new");
  await page.getByRole("group", { name: "選擇教材" }).getByRole("button").first().click();
  const name = page.getByLabel("Podcast 名稱", { exact: true });
  await expect(name).toBeVisible();
  await expect(page).toHaveURL(new RegExp('/create-podcast$'));
  await expect(page.getByLabel("選擇教材")).toHaveCount(0);
  // 從全站選教材進入（已在教材頁首內）仍保留返回全站 Podcast。
  await expect(page.getByRole("button", { name: "返回我的 Podcast", exact: true })).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(390);
  await name.fill("手機建立測試");
  await page.getByRole("button", {name:"講解設定",exact:true}).click();
  await expect(page.getByRole("button", { name: "開始生成 Podcast" })).toBeEnabled();
});

test("material collections filter both products while outer navigation opens the whole account", async ({ page }) => {
  const fixture = await mockPodcasts(page);
  const otherMaterial = "99999999-9999-4999-8999-999999999999";
  const secondPodcast = "88888888-8888-4888-8888-888888888888";
  await page.route("**/v1/podcasts", route => json(route, { schema: "podcast-list/v1", podcasts: [fixture.view, { ...fixture.view, podcast_id: secondPodcast, material_id: otherMaterial, name: "別的教材 Podcast" }] }));
  const card = { version: 1, card_set_id: "55555555-5555-4555-8555-555555555555", material_id: materialId,
    material_name: fixture.view.material_name, knowledge_structure_revision: structureRevision,
    name: "此教材卡組", card_count: 2, created_at: fixture.view.created_at, is_current_revision: true,
    generation: { status: "ready", stage: "complete", completed: 2, total: 2, error_code: null, retained_original: 0, cached: 0 } };
  await page.route("**/v1/card-sets", route => json(route, { schema: "card-set-list/v1", card_sets: [card, { ...card, card_set_id: secondPodcast, material_id: otherMaterial, name: "別的教材卡組" }] }));
  await page.goto('/');
  await page.getByRole('navigation', { name: '主要導覽' }).getByRole('link', { name: 'Podcast', exact: true }).click();
  await expect(page.getByRole('navigation', { name: '主要導覽' }).getByRole('link', { name: 'Podcast', exact: true })).toHaveAttribute('aria-current', 'page');
  await expect(page.locator('.podcast-tile')).toHaveCount(2);
  await page.goto(`/materials/${materialId}/podcasts`);
  await expect(page.locator('.podcast-tile')).toHaveCount(1);
  await expect(page.getByRole('tablist', { name: '教材學習內容' }).getByRole('tab')).toHaveText(['知識地圖', '學習與測驗', '概念卡', 'Podcast', '筆記', '補充學習', '教材來源']);
  await expect(page.locator('.app-shell')).toHaveClass(/is-workspace/);
  const navBox = (await page.getByRole('tablist', { name: '教材學習內容' }).boundingBox())!;
  const createBox = (await page.getByRole('button', { name: '建立 Podcast', exact: true }).boundingBox())!;
  expect(createBox.y).toBeGreaterThanOrEqual(navBox.y + navBox.height);
  await expect(page.getByRole('heading', { name: '別的教材 Podcast' })).toHaveCount(0);
  await page.getByRole('button', { name: '查看進度', exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`/materials/${materialId}/podcasts/${podcastId}$`));
  await expect(page.getByRole('tablist', { name: '教材學習內容' })).toBeVisible();
  // 教材內已有分頁列，不再顯示重複的返回；用 Podcast 分頁回到列表。
  await expect(page.getByRole('button', { name: '返回 Podcast', exact: true })).toHaveCount(0);
  await page.getByRole('tablist', { name: '教材學習內容' }).getByRole('tab', { name: 'Podcast', exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`/materials/${materialId}/podcasts$`));
  await page.getByRole('tablist', { name: '教材學習內容' }).getByRole('tab', { name: '概念卡', exact: true }).click();
  await expect(page.locator('.library-item')).toHaveCount(1);
  await expect(page.getByRole('heading', { name: '此教材卡組' })).toBeVisible();
  const cardsNav = (await page.getByRole('tablist', { name: '教材學習內容' }).boundingBox())!;
  const cardsCreate = (await page.getByRole('button', { name: '建立卡組', exact: true }).boundingBox())!;
  expect(cardsCreate.y).toBeGreaterThanOrEqual(cardsNav.y + cardsNav.height);
  await page.getByRole('button', { name: '建立卡組', exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}/create-cards$`));
  await expect(page.getByLabel('選擇教材')).toHaveCount(0);
  await expect(page.getByRole('tablist', { name: '教材學習內容' })).toBeVisible();
  await page.goto('/');
  await page.getByRole('navigation', { name: '主要導覽' }).getByRole('link', { name: '概念卡', exact: true }).click();
  await expect(page.getByRole('navigation', { name: '主要導覽' }).getByRole('link', { name: '概念卡', exact: true })).toHaveAttribute('aria-current', 'page');
  await expect(page.locator('.library-item')).toHaveCount(2);
});

test("podcast creation keeps manual selection and settings without recommendations or material switching", async ({ page }) => {
  const fixture = await mockPodcasts(page);
  const unnecessaryReads: string[] = [];
  page.on('request', request => { const path = new URL(request.url()).pathname; if (path === '/v1/materials' || path.endsWith('/progress')) unnecessaryReads.push(path); });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto(createPath);
  const selection = page.getByRole('region', { name: '選擇 Podcast 概念' });
  const settings = page.getByRole('region', { name: '講解設定', exact: true });
  await expect(page.getByLabel('選擇教材')).toHaveCount(0);
  await expect(selection).toBeVisible(); await expect(settings).toBeVisible();
  expect((await selection.boundingBox())!.x).toBeLessThan((await settings.boundingBox())!.x);
  // 全選／取消全選與搜尋同一列（與建立卡組相同）。
  const searchBox = (await selection.getByRole('searchbox').boundingBox())!;
  const selectAll = (await selection.getByRole('button', { name: '全選', exact: true }).boundingBox())!;
  expect(Math.abs(selectAll.y + selectAll.height / 2 - (searchBox.y + searchBox.height / 2))).toBeLessThanOrEqual(4);
  await selection.locator('.podcast-concept-option').last().getByRole('checkbox').uncheck();
  await selection.getByLabel('只看已選').check();
  await expect(selection).toContainText('已選 1 / 2');
  await page.setViewportSize({ width: 390, height: 844 });
  const controls = page.getByRole('navigation', { name: 'Podcast 設定區域' });
  await expect(controls.getByRole('button')).toHaveText(['選擇概念', '講解設定']);
  await controls.getByRole('button', { name: '講解設定', exact: true }).click();
  await expect(settings).toBeVisible(); await expect(selection).toBeHidden();
  await settings.getByLabel('單人解說', { exact: false }).check();
  await expect(settings.getByRole('radio')).toHaveCount(2);
  await controls.getByRole('button', { name: '選擇概念', exact: true }).click();
  await expect(selection).toContainText('已選 1 / 2');
  await selection.getByLabel('只看已選').uncheck();
  await selection.getByRole('button', { name: '取消全選', exact: true }).click();
  await controls.getByRole('button', { name: '講解設定', exact: true }).click();
  await expect(page.getByRole('button', { name: '開始生成 Podcast' })).toBeDisabled();
  await controls.getByRole('button', { name: '選擇概念', exact: true }).click();
  const first = fixture.structure.concepts[0];
  await selection.getByRole('searchbox').fill(first.label);
  await selection.getByRole('button', { name: '選取搜尋結果', exact: true }).click();
  await expect(selection).toContainText('已選 1 / 2');
  await selection.getByRole('button', { name: `查看「${first.label}」重點` }).click();
  await expect(page.getByRole('region', { name: `${first.label}重點預覽` })).toBeVisible();
  await page.getByRole('button', { name: '關閉預覽', exact: true }).click();
  await page.getByLabel('Podcast 名稱', { exact: true }).fill('手動選材操作測試');
  expect(unnecessaryReads).toEqual([]);
  let body: any;
  await page.route(`**/v1/materials/${materialId}/podcasts`, route => {
    body = route.request().postDataJSON();
    const claims = fixture.view.episodes[0].claims.filter(c => body.concept_ids.includes(c.concept_id));
    Object.assign(fixture.view, { name: body.name, delivery: body.delivery, concept_ids: body.concept_ids, status: 'pending', episode_count: 1, completed_episodes: 0,
      episodes: [{ delivery: body.delivery, claims, script: null, audio: null }] });
    return json(route, fixture.view, 201);
  });
  await controls.getByRole('button', { name: '講解設定', exact: true }).click();
  // 影片預設不勾；勾選後才隨音訊生成。
  const videoOption = page.getByRole('checkbox', { name: /同時生成教學影片/ });
  await expect(videoOption).not.toBeChecked();
  await expect(page.locator('.cards-create-summary')).toContainText('只有音訊');
  await videoOption.check();
  await page.getByRole('button', { name: '開始生成 Podcast' }).click();
  await expect(page).toHaveURL(new RegExp(`/materials/${materialId}/podcasts/${podcastId}$`));
  expect(body.concept_ids).toEqual([first.concept_id]); expect(body.delivery).toBe('solo'); expect(body).not.toHaveProperty('mode');
  expect(body.video).toBe(true);
  await expect(page.getByRole('tablist', { name: '教材學習內容' })).toBeVisible();
});

test('desktop podcast creation fills the workspace while long lists and previews scroll inside it', async ({ page }) => {
  const view = workspaceView(80);
  view.concepts[0].claims[0].text = '完整條件與數值必須保留。'.repeat(100) + ' x != 0';
  await mockKnowledgeMapApi(page, view);
  for (const [width, height] of [[1920,1080], [1440,900], [1366,768], [1024,768]]) {
    await page.setViewportSize({width,height});
    await page.goto(createPath);
    const selection = page.getByRole('region', {name:'選擇 Podcast 概念'});
    const settings = page.getByRole('region', {name:'講解設定',exact:true});
    await expect(selection.locator('.podcast-concept-option')).toHaveCount(80);
    const left = (await selection.boundingBox())!, right = (await settings.boundingBox())!;
    // 選概念與講解設定並排在同一張階段卡片內，卡片填滿內容寬度。
    const stage = (await page.locator('.podcast-create').boundingBox())!;
    expect(stage.x).toBeLessThanOrEqual(32); expect(width-stage.x-stage.width).toBeLessThanOrEqual(32);
    expect(left.x).toBeGreaterThan(stage.x); expect(right.x+right.width).toBeLessThan(stage.x+stage.width); expect(left.x+left.width).toBeLessThan(right.x);
    expect(Math.abs(left.y-right.y)).toBeLessThanOrEqual(1); expect(right.height).toBeLessThan(left.height);
    const list = selection.locator('.podcast-concept-list');
    expect(await list.evaluate(element=>element.scrollHeight>element.clientHeight)).toBe(true);
    await selection.getByRole('button', {name:`查看「${view.concepts[0].label}」重點`,exact:true}).click();
    const preview = page.getByRole('region', {name:`${view.concepts[0].label}重點預覽`});
    await expect(preview).toContainText('x != 0');
    expect(await preview.locator('div').evaluate(element=>element.scrollHeight>element.clientHeight)).toBe(true);
    await page.getByRole('button', {name:'關閉預覽',exact:true}).click();
    await list.evaluate(element=>{element.scrollTop=element.scrollHeight;});
    await expect(selection.getByRole('button', {name:`查看「${view.concepts.at(-1)!.label}」重點`,exact:true})).toBeInViewport();
    const create = page.getByRole('button', {name:'開始生成 Podcast',exact:true});
    await expect(create).toBeInViewport(); await create.click({trial:true});
    const action = (await create.boundingBox())!, voice = (await page.getByRole('button', {name:'教材問答',exact:true}).boundingBox())!;
    // 問答入口在分頁列，不與底部的生成按鈕重疊。
    expect(voice.y+voice.height).toBeLessThan(action.y);
    expect(action.y+action.height).toBeLessThan(height);
    expect(await page.evaluate(()=>document.documentElement.scrollHeight<=innerHeight+1&&document.documentElement.scrollWidth===innerWidth)).toBe(true);
  }
});

test("saved dialogue transcripts keep roles and switch with the selected episode", async ({ page }) => {
  const {fixture} = await mockEpisodeVideo(page);
  await page.route(`**/v1/podcasts/${podcastId}/episodes/*/timeline`,r=>json(r,{schema:'api-error/v1',request_id:sessionId,reason_code:'PODCAST_TIMELINE_NOT_READY',retryable:true,message:'Request could not be completed.'},409));
  fixture.view.delivery = 'dialogue';
  for (const episode of fixture.view.episodes) episode.delivery = 'dialogue';
  const claims = fixture.view.episodes[0].claims;
  fixture.view.status='ready'; fixture.view.completed_episodes=3;
  for (const e of fixture.view.episodes) { e.audio={...fixture.view.episodes[0].audio!};e.script={...fixture.script,segments:fixture.script.segments.map((s,i)=>({...s,turns:s.turns.map(t=>({...t,speaker:i?'guest':'host'}))}))}; }
  const firstTurn = '第一集：8 MB 與 8 Mb 不同。' + '這是用於驗證長逐字稿捲動的合成內容。'.repeat(100);
  const secondTurn = '第二集的說明。' + '這是另一集用於驗證閱讀位置的合成內容。'.repeat(100);
  fixture.view.episodes[0].script = { provider: 'synthetic', segments: [
    { claim_id: claims[0].claim_id, turns: [{ speaker: 'guest', text: firstTurn }] },
    { claim_id: claims[1].claim_id, turns: [{ speaker: 'host', text: '這個條件要保留。\n不能略過。' }] },
  ] };
  fixture.view.episodes[1].script = { provider: 'synthetic', segments: [
    { claim_id: claims[0].claim_id, turns: [{ speaker: 'host', text: '第二集的疑問。' }] },
    { claim_id: claims[1].claim_id, turns: [{ speaker: 'guest', text: secondTurn }] },
  ] };
  await page.goto(`/podcasts/${podcastId}`);
  const first = page.locator('.podcast-transcript');
  // 節目化前的舊集沒有主持人名字，以中性的主持人／來賓標示。
  await expect(first.locator('.podcast-transcript-speaker')).toHaveText(['來賓', '主持人']);
  await expect(first.locator('li p')).toHaveText([firstTurn, '這個條件要保留。\n不能略過。']);
  await page.getByRole('tab', { name: '本集重點', exact:true }).click();
  await expect(page.getByRole('tabpanel', { name: '本集重點', exact:true })).toContainText('合成測試重點');
  await page.getByRole('tab', { name: '逐字稿', exact:true }).click();
  await first.evaluate(element => {element.scrollTop = element.scrollHeight;});
  expect(await first.evaluate(element => element.scrollTop)).toBeGreaterThan(0);
  await page.getByRole('complementary', { name: '分集清單' }).getByRole('button').nth(1).click();
  const second = page.locator('.podcast-transcript');
  await expect(second).toContainText('第二集的說明。');
  await expect(second).not.toContainText('第一集');
  expect(await second.evaluate(element => element.scrollTop)).toBe(0);
  await page.getByRole('complementary', { name: '分集清單' }).getByRole('button').nth(2).click();
  await expect(page.locator('.podcast-transcript')).toContainText('這是合成的教材講解。');
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(390);
});

test('an intentional media pause does not display a playback failure',async({page})=>{
 const {fixture}=await mockEpisodeVideo(page);
 for(const episode of fixture.view.episodes){episode.script=fixture.script;episode.audio=fixture.view.episodes[0].audio;}
 const wav=Buffer.alloc(44+48000);wav.write('RIFF');wav.writeUInt32LE(wav.length-8,4);wav.write('WAVEfmt ',8);wav.writeUInt32LE(16,16);wav.writeUInt16LE(1,20);wav.writeUInt16LE(1,22);wav.writeUInt32LE(24000,24);wav.writeUInt32LE(48000,28);wav.writeUInt16LE(2,32);wav.writeUInt16LE(16,34);wav.write('data',36);wav.writeUInt32LE(wav.length-44,40);
 await page.route(`**/v1/podcasts/${podcastId}/episodes/*/audio`,r=>r.fulfill({status:200,contentType:'audio/wav',body:wav}));
 await page.goto(`/podcasts/${podcastId}`);await page.getByRole('button',{name:'播放',exact:true}).waitFor();
 await page.locator('video').evaluate((a:HTMLAudioElement)=>{a.play=()=>Promise.reject(new DOMException('intentional pause','AbortError'));});
 await page.getByRole('button',{name:'播放',exact:true}).click();await page.waitForTimeout(150);
 await expect(page.getByText('暫時無法播放，請按播放重試。')).toHaveCount(0);
 await page.locator('video').evaluate((a:HTMLAudioElement)=>{a.play=()=>Promise.reject(new DOMException('blocked playback','NotAllowedError'));});
 await page.getByRole('button',{name:'播放',exact:true}).click();await expect(page.getByText('暫時無法播放，請按播放重試。')).toBeVisible();
});


// 合成純色MP4僅驗證播放與來源切換，不代表模型內容品質。
async function mockEpisodeVideo(page:Page, initial:'ready'|'pending'|'failed'='ready') {
  const fixture=await mockPodcasts(page);fixture.view.status='ready';fixture.view.completed_episodes=3;
  for(const e of fixture.view.episodes){e.script=fixture.script;e.audio={...fixture.view.episodes[0].audio!,duration_seconds:12};}
  const wav=Buffer.alloc(44+24000*12*2);wav.write('RIFF');wav.writeUInt32LE(wav.length-8,4);wav.write('WAVEfmt ',8);wav.writeUInt32LE(16,16);wav.writeUInt16LE(1,20);wav.writeUInt16LE(1,22);wav.writeUInt32LE(24000,24);wav.writeUInt32LE(48000,28);wav.writeUInt16LE(2,32);wav.writeUInt16LE(16,34);wav.write('data',36);wav.writeUInt32LE(wav.length-44,40);
  await page.route(`**/v1/podcasts/${podcastId}/episodes/*/audio`,r=>r.fulfill({status:200,contentType:'audio/wav',body:wav}));
  const setGeneration = (ready:boolean) => { fixture.view.generation={active:!ready,preview_ready:ready,retryable_videos:initial==='failed'&&!ready,completed_videos:ready?3:1,failed_videos:initial==='failed'&&!ready?1:0}; };
  setGeneration(initial==='ready');
  let status:string=initial,version=1;
  const actions:string[]=[];
  const value=(index=0)=>({schema:'podcast-video/v1',podcast_id:podcastId,episode_index:index,status,version,error_code:status==='failed'?'VIDEO_LAYOUT_INVALID':null,
    video:status==='ready'?{audio_sha256:'a'.repeat(64),script_sha256:'c'.repeat(64),artifact_id:artifactId,sha256:'d'.repeat(64),width:1920,height:1080,fps:60,duration:12,pages:[{title:'前半說明',start:0,end:6},{title:'後半說明',start:6,end:12}]}:null});
  await page.route(`**/v1/podcasts/${podcastId}/episodes/*/video`,r=>json(r,value(Number(new URL(r.request().url()).pathname.split('/').at(-2)))));
  await page.route(`**/v1/podcasts/${podcastId}/episodes/*/video/actions`,r=>{actions.push(r.request().postDataJSON().action);status=actions.at(-1)==='cancel'?'cancelled':'pending';version++;return json(r,value())});
  const mp4=readFileSync(new URL('../fixtures/podcast-video.mp4',import.meta.url));
  await page.route(`**/v1/podcasts/${podcastId}/episodes/*/video/media`,r=>{
    const m=r.request().headers()['range']?.match(/bytes=(\d+)-(\d*)/),start=m?Number(m[1]):0,end=m?.[2]?Math.min(Number(m[2]),mp4.length-1):mp4.length-1;
    return r.fulfill({status:m?206:200,contentType:'video/mp4',headers:{'Accept-Ranges':'bytes',...(m?{'Content-Range':`bytes ${start}-${end}/${mp4.length}`}:{})},body:mp4.subarray(start,end+1)});
  });
  await page.route(`**/v1/podcasts/${podcastId}/episodes/*/timeline`,r=>{
    const index=Number(new URL(r.request().url()).pathname.split('/').at(-2));
    return json(r,{schema:'podcast-transcript-timeline/v1',podcast_id:podcastId,episode_index:index,audio_sha256:'a'.repeat(64),script_sha256:'c'.repeat(64),source_resolver:fixture.view.source_resolver,segments:[{id:'a',start:0,end:6,title:'第一點',text:'這是前半段的講解。',turns:[{speaker:'host',text:'這是前半段的講解。'}]},{id:'b',start:6,end:12,title:'第二點',text:'這是後半段的講解。',turns:[{speaker:'guest',text:'這是後半段的講解。'}]}]});
  });
  await page.route(`**/v1/podcasts/${podcastId}/episodes/*/subtitles`,r=>r.fulfill({contentType:'text/vtt',body:'WEBVTT\n\n00:00:00.000 --> 00:00:06.000\n前半段\n\n00:00:06.000 --> 00:00:12.000\n後半段\n'}));
  return {fixture,actions,ready:()=>{status='ready';version++;setGeneration(true)}};
}

test('timeline still aligning after audio completes keeps waiting, then shows timed transcript',async({page})=>{
  await mockEpisodeVideo(page);
  let aligned=false;
  await page.route(`**/v1/podcasts/${podcastId}/episodes/*/timeline`,r=>aligned
    ?r.fallback()
    :json(r,{schema:'api-error/v1',request_id:sessionId,reason_code:'PODCAST_TIMELINE_NOT_READY',retryable:true,message:'Request could not be completed.'},409));
  await page.goto(`/podcasts/${podcastId}`);
  const transcript=page.getByRole('tabpanel',{name:'逐字稿',exact:true});
  await expect(transcript).toContainText('時間軸尚未準備完成');
  await expect(transcript.getByRole('alert')).toHaveCount(0);
  aligned=true;
  await expect(transcript.getByRole('button',{name:/前半段/})).toBeVisible();
  await expect(transcript.getByRole('alert')).toHaveCount(0);
});

for (const [reason,message] of [
  ['PODCAST_TIMELINE_UNAVAILABLE','這一集的時間軸無法使用。'],
  ['PODCAST_TIMELINE_SOURCE_MISMATCH','時間軸與本集原稿、音訊或來源不一致。'],
] as const) test(`timeline ${reason} shows an error instead of polling`,async({page})=>{
  await mockEpisodeVideo(page);
  let reads=0;
  await page.route(`**/v1/podcasts/${podcastId}/episodes/*/timeline`,r=>{reads++;return json(r,{schema:'api-error/v1',request_id:sessionId,reason_code:reason,retryable:false,message:'Request could not be completed.'},409)});
  await page.goto(`/podcasts/${podcastId}`);
  await expect(page.getByRole('tabpanel',{name:'逐字稿',exact:true}).getByRole('alert')).toHaveText(message);
  const settled=reads;await page.waitForTimeout(2500);
  expect(reads).toBe(settled);
});

test('formal video player shares controls, transcript seek, pages and logout boundary',async({page})=>{
  await mockEpisodeVideo(page);
  await page.goto(`/podcasts/${podcastId}`);
  const video=page.locator('video');await expect(video).toHaveCount(1);await expect(page.locator('audio')).toHaveCount(0);
  await expect(page.getByRole('button',{name:'播放',exact:true})).toBeEnabled();
  const display=(await page.locator('.media-display').boundingBox())!,controls=(await page.locator('.media-playback-controls').boundingBox())!;
  // 控制列在畫面底部的常駐播放列，不疊在影片上。
  await expect(page.locator('.podcast-dock')).toBeVisible();expect(controls.y).toBeGreaterThanOrEqual(display.y+display.height);
  await page.getByRole('navigation',{name:'本集章節'}).getByRole('button',{name:/後半說明/}).click();
  await expect.poll(()=>video.evaluate((v:HTMLVideoElement)=>v.currentTime)).toBeCloseTo(6,1);
  const transcript=page.getByRole('tabpanel',{name:'逐字稿',exact:true});
  await expect(transcript.getByRole('button',{name:/後半段/})).toHaveAttribute('aria-current','true');
  await transcript.getByRole('button',{name:/前半段/}).click();await page.getByRole('button',{name:'播放',exact:true}).click();
  await expect.poll(()=>video.evaluate((v:HTMLVideoElement)=>v.currentTime)).toBeGreaterThan(.2);
  await page.getByRole('button',{name:'播放設定',exact:true}).click();await page.getByRole('button',{name:'1.5×',exact:true}).click();
  expect(await video.evaluate((v:HTMLVideoElement)=>v.playbackRate)).toBe(1.5);
  await page.setViewportSize({width:390,height:844});expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBe(390);
  await page.getByRole('button',{name:'播放設定',exact:true}).click();await page.getByRole('button',{name:'全螢幕',exact:true}).click();
  await expect.poll(()=>page.evaluate(()=>document.fullscreenElement?.classList.contains('media-viewport'))).toBe(true);
  await page.getByRole('button',{name:'退出全螢幕',exact:true}).click();
  // 教材內從頭像選單登出。
  await page.getByRole('button',{name:/^帳號選單/}).click();await page.getByRole('button',{name:'登出',exact:true}).click();await expect(page.locator('video')).toHaveCount(0);
});

test('preview waits for every video and audio, including direct links',async({page})=>{
  const mock=await mockEpisodeVideo(page,'pending');await page.goto(`/podcasts/${podcastId}`);
  await expect(page.getByRole('region',{name:'Podcast 生成進度'})).toBeVisible();
  await expect(page.locator('video,audio,.podcast-listening-layout')).toHaveCount(0);
  await expect(page.getByText('音訊 3 / 3 集 · 影片 1 / 3 集', {exact:false})).toBeVisible();
  mock.ready();
  await expect(page.locator('video')).toHaveCount(1);
  await expect(page.locator('audio')).toHaveCount(0);
  await expect(page.getByRole('region',{name:'Podcast 生成進度'})).toHaveCount(0);
});

test('audio-only podcast plays audio in the same player without video progress',async({page})=>{
  const {fixture}=await mockEpisodeVideo(page);
  fixture.view.video_enabled=false;
  fixture.view.generation={active:false,preview_ready:true,retryable_videos:false,completed_videos:0,failed_videos:0};
  await page.route(`**/v1/podcasts/${podcastId}/episodes/*/video`,r=>json(r,{schema:'podcast-video/v1',podcast_id:podcastId,episode_index:Number(new URL(r.request().url()).pathname.split('/').at(-2)),status:'unprepared',version:0,error_code:null,video:null}));
  await page.goto(`/podcasts/${podcastId}`);
  const media=page.locator('video');
  await expect(media).toHaveAttribute('src',new RegExp(`/v1/podcasts/${podcastId}/episodes/0/audio$`));
  await expect(page.getByRole('button',{name:'播放',exact:true})).toBeEnabled();
  await expect(page.getByRole('region',{name:'Podcast 生成進度'})).toHaveCount(0);
  await expect(page.getByRole('button',{name:'刪除本集影片'})).toHaveCount(0);
  await expect(page.getByRole('button',{name:'全螢幕',exact:true})).toHaveCount(0);
  // 逐字稿時間軸不依賴影片。
  await expect(page.getByRole('tabpanel',{name:'逐字稿',exact:true}).getByRole('button',{name:/前半段/})).toBeVisible();
  await page.goto('/podcasts');
  await expect(page.getByRole('button',{name:'開啟播放'})).toBeVisible();
  await expect(page.getByText(/影片 \d+ \/ \d+ 集/)).toHaveCount(0);
});

test('deleting one episode video keeps its audio, transcript and other episodes',async({page})=>{
  await mockEpisodeVideo(page);
  const removed=new Set<number>();const deletes:string[]=[];
  await page.route(`**/v1/podcasts/${podcastId}/episodes/*/video`,r=>{
    const index=Number(new URL(r.request().url()).pathname.split('/').at(-2));
    if(r.request().method()==='DELETE'){deletes.push(`${index}:${r.request().headers().origin??''}`);removed.add(index);}
    if(!removed.has(index))return r.fallback();
    return json(r,{schema:'podcast-video/v1',podcast_id:podcastId,episode_index:index,status:'removed',version:3,error_code:null,video:null});
  });
  await page.goto(`/podcasts/${podcastId}`);
  const media=page.locator('video');
  await expect(media).toHaveAttribute('src',/\/episodes\/0\/video\/media$/);
  await page.getByRole('button',{name:'刪除本集影片'}).click();
  const confirm=page.getByRole('group',{name:'刪除影片確認'});
  await expect(confirm).toContainText('音訊、逐字稿與收聽進度都會保留');
  await confirm.getByRole('button',{name:'保留影片'}).click();
  expect(deletes).toEqual([]);
  await page.getByRole('button',{name:'刪除本集影片'}).click();
  await page.getByRole('group',{name:'刪除影片確認'}).getByRole('button',{name:'刪除影片'}).click();
  await expect(media).toHaveAttribute('src',/\/episodes\/0\/audio$/);
  expect(deletes).toHaveLength(1);expect(deletes[0]).toMatch(/^0:http/);
  await expect(page.getByRole('button',{name:'刪除本集影片'})).toHaveCount(0);
  await expect(page.getByRole('tabpanel',{name:'逐字稿',exact:true}).getByRole('button',{name:/前半段/})).toBeVisible();
  await page.getByRole('navigation',{name:'分集清單'}).or(page.getByRole('complementary',{name:'分集清單'})).getByRole('button').nth(1).click();
  await expect(media).toHaveAttribute('src',/\/episodes\/1\/video\/media$/);
});

test('a ready episode video can be deleted while other episodes are still preparing',async({page})=>{
  const {fixture}=await mockEpisodeVideo(page,'pending');
  const deletes:number[]=[];let removed=false;
  await page.route(`**/v1/podcasts/${podcastId}/episodes/*/video`,r=>{
    const index=Number(new URL(r.request().url()).pathname.split('/').at(-2));
    if(r.request().method()==='DELETE'){deletes.push(index);removed=true;}
    const ready=index===0&&!removed;
    return json(r,{schema:'podcast-video/v1',podcast_id:podcastId,episode_index:index,status:ready?'ready':index===0?'removed':'pending',version:2,error_code:null,
      video:ready?{audio_sha256:'a'.repeat(64),script_sha256:'c'.repeat(64),artifact_id:artifactId,sha256:'d'.repeat(64),width:1920,height:1080,fps:60,duration:12,pages:[]}:null});
  });
  await page.goto(`/podcasts/${podcastId}`);
  await expect(page.getByRole('region',{name:'Podcast 生成進度'})).toBeVisible();
  await page.getByRole('button',{name:'刪除本集影片'}).click();
  await page.getByRole('group',{name:'刪除影片確認'}).getByRole('button',{name:'刪除影片'}).click();
  await expect(page.getByRole('button',{name:'刪除本集影片'})).toHaveCount(0);
  expect(deletes).toEqual([0]);
  // 其他仍在製作的分集沒有刪除入口。
  await page.getByLabel('匯出集數').selectOption('1');
  await expect(page.getByRole('button',{name:'刪除本集影片'})).toHaveCount(0);
  expect(fixture.view.generation?.preview_ready).toBe(false);
});

test('video progress counts only episodes that will have a video',async({page})=>{
  const {fixture}=await mockEpisodeVideo(page,'pending');
  // 勾選影片的舊節目：只有 1 集缺影片工作待補，其餘已完成或已刪除。
  fixture.view.generation={active:false,preview_ready:false,retryable_videos:true,completed_videos:1,failed_videos:0,expected_videos:2};
  await page.goto(`/podcasts/${podcastId}`);
  await expect(page.getByText('音訊 3 / 3 集 · 影片 1 / 2 集',{exact:false})).toBeVisible();
  await expect(page.getByRole('progressbar',{name:'已完成影音集數'})).toHaveAttribute('max','5');
  await expect(page.getByRole('button',{name:'接續生成'})).toBeVisible();
  await page.goto('/podcasts');
  await expect(page.getByText(/影片 1 \/ 2 集/)).toBeVisible();
});

test('cancelling video preparation confirms deletion of the whole podcast',async({page})=>{
  const mock=await mockEpisodeVideo(page,'failed');await page.goto(`/podcasts/${podcastId}`);
  await expect(page.getByText('1 集影片生成失敗；接續生成會保留已完成的影音。')).toBeVisible();
  await expect(page.locator('audio,video')).toHaveCount(0);
  await page.getByRole('button',{name:'接續生成',exact:true}).click();
  await expect(page.getByRole('button',{name:'取消並刪除',exact:true})).toBeVisible();
  await page.getByRole('button',{name:'取消並刪除',exact:true}).click();
  await expect(page.getByRole('group',{name:'取消並刪除 Podcast 確認'})).toContainText('已產生的講稿、音訊與影片也會刪除');
  expect(mock.fixture.deletes()).toBe(0);
  await page.getByRole('button',{name:'取消並刪除',exact:true}).click();
  await expect(page.getByRole('heading',{name:'讓教材說給你聽'})).toBeVisible();
  expect(mock.fixture.actions).toEqual([expect.objectContaining({action:'retry'})]);expect(mock.fixture.deletes()).toBe(1);
  await expect(page.locator('audio,video')).toHaveCount(0);
});

test('full dialogue video keeps both speakers inside a shared cue and seeks across turns',async({page})=>{
  const mock=await mockEpisodeVideo(page);
  mock.fixture.view.delivery='dialogue';
  for(const episode of mock.fixture.view.episodes){
    episode.delivery='dialogue';episode.hosts={host:'小晴',guest:'阿哲'};
    if(episode.script)episode.script={...episode.script,segments:episode.script.segments.map(s=>({...s,turns:[{speaker:'host',text:'為什麼需要這個條件？'},{speaker:'guest',text:'條件決定這個觀念的適用範圍。'}]}))};
  }
  await page.route(`**/v1/podcasts/${podcastId}/episodes/0/timeline`,r=>json(r,{
    schema:'podcast-transcript-timeline/v1',podcast_id:podcastId,episode_index:0,audio_sha256:'a'.repeat(64),script_sha256:'c'.repeat(64),source_resolver:mock.fixture.view.source_resolver,
    segments:[{id:'a',start:0,end:6,title:'提問與說明',text:'為什麼需要這個條件？\n條件決定這個觀念的適用範圍。',turns:[{speaker:'host',text:'為什麼需要這個條件？'},{speaker:'guest',text:'條件決定這個觀念的適用範圍。'}]},
      {id:'b',start:6,end:12,title:'下一個重點',text:'接著確認來源中的另一個限制。',turns:[{speaker:'guest',text:'接著確認來源中的另一個限制。'}]}]}));
  await page.goto(`/podcasts/${podcastId}`);
  const transcript=page.getByRole('tabpanel',{name:'逐字稿',exact:true}),first=transcript.getByRole('button',{name:/為什麼需要/}),second=transcript.getByRole('button',{name:/接著確認/});
  await expect(first.locator('small')).toHaveText(['小晴','阿哲']);
  await expect(second.locator('small')).toHaveText(['阿哲']);
  await expect(first).toBeEnabled();await second.click();
  await expect.poll(()=>page.locator('video').evaluate((v:HTMLVideoElement)=>v.currentTime)).toBeCloseTo(6,1);
  await expect(second).toHaveAttribute('aria-current','true');
  await first.click();await expect(first).toHaveAttribute('aria-current','true');
  await page.getByRole('button',{name:'播放',exact:true}).click();
  await expect.poll(()=>page.locator('video').evaluate((v:HTMLVideoElement)=>v.currentTime)).toBeGreaterThan(.2);
});

async function mockTeachingBeats(page:Page) {
  const mock=await mockEpisodeVideo(page);const view=mock.fixture.view;
  view.episodes=view.episodes.slice(0,1);view.episode_count=view.completed_episodes=1;view.generation!.completed_videos=1;view.is_current_revision=false;
  const e=view.episodes[0],texts=['第一個來源說明必要的條件。','第二個來源比較不同的結果。'];
  e.script_sha256='c'.repeat(64);
  e.script={schema:'podcast-script/v2',provider:'synthetic',review:{correctness:{passed:true,reason:'fixture'},teaching_quality:{passed:true,reason:'fixture'}},
    segments:[{beat_id:'beat-0',title:'比較必要條件',turns:[{speaker:'host',text:texts.join(''),parts:texts.map((text,i)=>({text,source_refs:[{source_index:i,evidence_ids:e.claims[i].evidence.map(e=>e.evidence_id)}]}))}]}]};
  const timeline={schema:'podcast-transcript-timeline/v1',podcast_id:podcastId,episode_index:0,audio_sha256:'a'.repeat(64),script_sha256:e.script_sha256,source_resolver:view.source_resolver,
    segments:texts.map((text,i)=>({id:`cue-${i}`,title:`比較 ${i+1}`,start:i*6,end:(i+1)*6,text,turns:[{speaker:'host',text}],evidence:e.claims[i].evidence,
      source_refs:[{segment_index:0,turn_index:0,start:i?texts[0].length:0,end:i?texts.join('').length:texts[0].length}]}))};
  await page.route(`**/v1/podcasts/${podcastId}/episodes/0/timeline`,r=>json(r,timeline));
  return {view,timeline};
}

test('Podcast keeps teaching transcript and removes retired interaction surfaces',async({page})=>{
  await mockTeachingBeats(page);
  await page.goto(`/podcasts/${podcastId}`);
  await expect(page.getByRole('tabpanel',{name:'逐字稿',exact:true})).toContainText('第二個來源比較');
  for(const name of ['問目前播放這一段','問這一段','查看這段來源','同步圖卡','檢測 Podcast 涵蓋的概念']) await expect(page.getByText(name,{exact:true})).toHaveCount(0);
  await expect(page.locator('video track')).toHaveCount(1);
  await expect(page.locator('.podcast-companion > .surface')).toHaveCount(2);
  await expect(page.getByRole('tabpanel',{name:'逐字稿',exact:true}).getByRole('navigation',{name:'本集章節'})).toBeVisible();
  await expect(page.getByRole('tab',{name:'逐字稿',exact:true})).toHaveAttribute('aria-selected','true');
  await expect(page.getByRole('tabpanel',{name:'本集重點',exact:true})).toHaveCount(0);
});

test('video surface toggles once and controls recover from idle with keyboard and pointer',async({page})=>{
  await mockEpisodeVideo(page);await page.goto(`/podcasts/${podcastId}`);
  const video=page.locator('video'), display=page.locator('.media-display'), viewport=page.locator('.media-viewport');
  await expect(page.getByRole('button',{name:'播放',exact:true})).toBeEnabled();
  await display.click({position:{x:80,y:30}});
  await expect.poll(()=>video.evaluate((v:HTMLVideoElement)=>v.paused)).toBe(false);
  await expect(viewport).toHaveClass(/controls-idle/);
  await display.hover({position:{x:90,y:40}});await expect(viewport).not.toHaveClass(/controls-idle/);
  await display.click({position:{x:80,y:30}});await expect.poll(()=>video.evaluate((v:HTMLVideoElement)=>v.paused)).toBe(true);
  await page.getByRole('button',{name:'播放',exact:true}).click();await expect.poll(()=>video.evaluate((v:HTMLVideoElement)=>v.paused)).toBe(false);
  await viewport.focus();await page.keyboard.press('Space');await expect.poll(()=>video.evaluate((v:HTMLVideoElement)=>v.paused)).toBe(true);
  await page.keyboard.press('Tab');await expect(viewport).not.toHaveClass(/controls-idle/);
  await page.setViewportSize({width:390,height:844});expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBe(390);
});

test.describe('touch playback',()=>{
 test.use({hasTouch:true,isMobile:true,viewport:{width:390,height:844}});
 // 播放列常駐在畫面底部（分頁列上方），播放中也不隱藏；點影片仍可暫停。
 test('taps toggle once; the docked controls stay visible at the bottom of the material page',async({page})=>{
  // 時鐘照常走，快轉越過 2.5 秒閒置計時，不必真的等待。
  await page.clock.install();
  await mockEpisodeVideo(page);await page.goto(`/podcasts/${podcastId}`);
  const video=page.locator('video'),controls=page.locator('.media-playback-controls');
  await page.getByRole('button',{name:'播放',exact:true}).tap();
  await expect.poll(()=>video.evaluate((v:HTMLVideoElement)=>v.paused)).toBe(false);
  await page.clock.fastForward(2800);
  await expect(controls).toHaveCSS('opacity','1');
  // 教材內沒有手機底部導覽；播放列貼近畫面底部且完整可見。
  await expect(page.getByRole('navigation',{name:'手機導覽'})).toBeHidden();
  const dockBox=(await page.locator('.podcast-dock').boundingBox())!;
  expect(dockBox.y+dockBox.height).toBeLessThanOrEqual(844-8);
  const display=(await page.locator('.media-display').boundingBox())!;
  await page.touchscreen.tap(display.x+80,display.y+20);
  await expect.poll(()=>video.evaluate((v:HTMLVideoElement)=>v.paused)).toBe(true);
  await expect(controls).toHaveCSS('opacity','1');
  await page.getByLabel('播放進度',{exact:true}).fill('0');
  await page.getByRole('button',{name:'播放設定',exact:true}).tap();
  await expect(page.getByRole('group',{name:'播放設定選單'}).getByLabel('自動播放下一集')).toBeVisible();
  await page.getByRole('button',{name:'0.5×',exact:true}).tap();
  expect(await video.evaluate((v:HTMLVideoElement)=>v.playbackRate)).toBe(0.5);
  await page.getByRole('button',{name:'播放設定',exact:true}).tap();
  await page.getByLabel('設定音量',{exact:true}).fill('0.5');
  expect(await video.evaluate((v:HTMLVideoElement)=>v.volume)).toBe(0.5);
  await page.getByRole('button',{name:'前進 10 秒',exact:true}).tap();
  await expect.poll(()=>video.evaluate((v:HTMLVideoElement)=>v.currentTime)).toBeCloseTo(10,0);
  await page.getByRole('button',{name:'後退 10 秒',exact:true}).tap();
  expect(await video.evaluate((v:HTMLVideoElement)=>v.paused)).toBe(true);
  await page.getByRole('button',{name:'播放設定',exact:true}).tap();
  const track=(await page.getByLabel('播放進度',{exact:true}).boundingBox())!;
  await page.touchscreen.tap(track.x+track.width/2,track.y+track.height/2);
  await expect.poll(()=>video.evaluate((v:HTMLVideoElement)=>v.currentTime)).toBeCloseTo(6,0);
  await page.getByRole('button',{name:'下一集',exact:true}).tap();
  await expect(page.getByRole('region',{name:'第 2 集播放器'})).toBeVisible();
  const frame=(await page.locator('.podcast-dock').boundingBox())!;
  for(const button of await page.locator('.media-controls button:visible').all()) {
    const box=(await button.boundingBox())!;
    expect(box.width).toBeGreaterThanOrEqual(40);
    expect(box.x+box.width).toBeLessThanOrEqual(frame.x+frame.width);
  }
 });
});

// 影片維持 16:9 不保留額外黑底；控制列是畫面底部的常駐播放列，維持一列。
test('video keeps 16:9 and the docked controls stay one compact row at desktop, laptop and mobile widths', async ({ page }) => {
  await mockEpisodeVideo(page);
  await page.goto(`/podcasts/${podcastId}`);
  await expect(page.getByRole('button', { name: '播放', exact: true })).toBeEnabled();
  for (const [width, height] of [[1920, 1080], [1366, 768], [390, 844]]) {
    await page.setViewportSize({ width, height });
    const viewport = page.locator('.media-viewport');
    const frame = (await page.locator('.podcast-dock').boundingBox())!;
    const display = (await page.locator('.media-display').boundingBox())!;
    const video = (await viewport.boundingBox())!;
    const controls = (await page.locator('.media-playback-controls').boundingBox())!;
    expect(display.width / display.height).toBeCloseTo(16 / 9, 2);
    expect(display.height).toBeCloseTo(video.height, 0);
    await expect(page.locator('.podcast-dock')).toHaveCSS('position', 'fixed');
    await expect(page.locator('.podcast-dock')).toContainText('第 1 集');
    expect(controls.y + controls.height).toBeLessThanOrEqual(height);
    expect(controls.height).toBeLessThanOrEqual(110);
    const play = (await page.getByRole('button', { name: '播放', exact: true }).boundingBox())!;
    const time = (await page.locator('.media-time').boundingBox())!;
    const fullscreen = (await page.getByRole('button', { name: '全螢幕', exact: true }).boundingBox())!;
    expect(play.x).toBeLessThan(time.x);
    expect(time.x + time.width).toBeLessThanOrEqual(fullscreen.x);
    expect(play.y).toBeCloseTo(fullscreen.y, 0);
    for (const button of await page.locator('.media-controls button:visible').all()) {
      const box = (await button.boundingBox())!;
      expect(box.width).toBeGreaterThanOrEqual(40);
      expect(box.height).toBeGreaterThanOrEqual(40);
      expect(box.x + box.width).toBeLessThanOrEqual(frame.x + frame.width);
    }
    await expect(page.locator('.media-time')).toHaveText(/\d+:\d{2} \/ \d+:\d{2}/);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.getByRole('button', { name: '播放設定', exact: true }).click();
    // 設定選單從播放列向上展開，整個留在畫面內。
    const menu = (await page.getByRole('group', { name: '播放設定選單' }).boundingBox())!;
    expect(menu.y).toBeGreaterThanOrEqual(0);
    expect(menu.y + menu.height).toBeLessThanOrEqual(frame.y + frame.height);
    expect(menu.x).toBeGreaterThanOrEqual(frame.x);
    expect(menu.x + menu.width).toBeLessThanOrEqual(frame.x + frame.width);
    await page.keyboard.press('Escape');
  }
});

test('overlay timeline, volume, speeds, episode navigation and fullscreen preserve media state', async ({ page }) => {
  await page.setViewportSize({ width: 1920, height: 1080 });
  await mockEpisodeVideo(page);
  await page.goto(`/podcasts/${podcastId}`);
  const video = page.locator('video');
  const viewport = page.locator('.media-viewport');
  await expect(page.getByRole('button', { name: '播放', exact: true })).toBeEnabled();
  await page.getByRole('slider', { name: '播放進度', exact: true }).fill('3.5');
  await expect.poll(() => video.evaluate((v: HTMLVideoElement) => v.currentTime)).toBeCloseTo(3.5, 1);
  expect(await video.evaluate((v: HTMLVideoElement) => v.paused)).toBe(true);
  await expect.poll(() => page.evaluate(key => JSON.parse(localStorage.getItem(key) ?? 'null')?.time, `studydy.podcast.position:${sessionId}:${podcastId}`)).toBeCloseTo(3.5, 1);
  await page.reload();
  await expect(page.getByRole('button', { name: '播放', exact: true })).toBeEnabled();
  await expect.poll(() => video.evaluate((v: HTMLVideoElement) => v.currentTime)).toBeCloseTo(3.5, 1);
  await expect(page.getByRole('button', { name: '上一集', exact: true })).toHaveCount(0);
  await page.getByRole('slider', { name: '音量', exact: true }).fill('0.35');
  expect(await video.evaluate((v: HTMLVideoElement) => v.volume)).toBeCloseTo(0.35);
  await page.getByRole('button', { name: '靜音', exact: true }).click();
  expect(await video.evaluate((v: HTMLVideoElement) => v.muted)).toBe(true);
  await page.getByRole('button', { name: '取消靜音', exact: true }).click();
  for (const [label, rate] of [['0.5×', 0.5], ['正常', 1], ['2×', 2]] as const) {
    await page.getByRole('button', { name: '播放設定', exact: true }).click();
    await page.getByRole('button', { name: label, exact: true }).click();
    expect(await video.evaluate((v: HTMLVideoElement) => v.playbackRate)).toBe(rate);
    expect(await video.evaluate((v: HTMLVideoElement) => v.paused)).toBe(true);
  }
  await viewport.focus();
  await page.keyboard.press('ArrowLeft');
  await expect.poll(() => video.evaluate((v: HTMLVideoElement) => v.currentTime)).toBe(0);
  await page.keyboard.press('ArrowRight');
  await expect.poll(() => video.evaluate((v: HTMLVideoElement) => v.currentTime)).toBeCloseTo(10, 1);
  await page.getByRole('button', { name: '下一集', exact: true }).click();
  await expect(page.getByRole('region', { name: '第 2 集播放器' })).toBeVisible();
  await expect(page.getByRole('button', { name: '上一集', exact: true })).toBeVisible();
  await page.getByRole('button', { name: '上一集', exact: true }).click();
  await expect(page.getByRole('region', { name: '第 1 集播放器' })).toBeVisible();
  await expect(page.getByRole('button', { name: '暫停', exact: true })).toBeEnabled();
  await page.getByRole('button', { name: '暫停', exact: true }).click();
  await page.getByRole('slider', { name: '播放進度', exact: true }).fill('1');
  await page.getByRole('button', { name: '全螢幕', exact: true }).click();
  await expect.poll(() => page.evaluate(() => !!document.fullscreenElement)).toBe(true);
  expect(await video.evaluate((v: HTMLVideoElement) => v.paused)).toBe(true);
  expect(await video.evaluate((v: HTMLVideoElement) => v.currentTime)).toBeCloseTo(1, 1);
  await page.getByRole('button', { name: '播放', exact: true }).click();
  await expect(page.locator('.media-playback-controls')).toHaveCSS('opacity', '0');
  await viewport.hover({ position: { x: 100, y: 100 } });
  await expect(page.locator('.media-playback-controls')).toHaveCSS('opacity', '1');
  await page.getByRole('button', { name: '退出全螢幕', exact: true }).click();
  await expect.poll(() => page.evaluate(() => !document.fullscreenElement)).toBe(true);
});

// 平時播放列常駐在畫面底部，播放中、開選單或拖曳進度時都不隱藏，也不改變影片高度。
test('docked controls stay visible while playing, with the menu open or while dragging a timeline', async ({ page }) => {
  // 時鐘照常走，快轉越過 2.5 秒閒置計時，不必真的等待。
  await page.clock.install();
  await mockEpisodeVideo(page);
  await page.goto(`/podcasts/${podcastId}`);
  const video = page.locator('video'), controls = page.locator('.media-playback-controls'), viewport = page.locator('.media-viewport');
  await expect(page.getByRole('button', { name: '播放', exact: true })).toBeEnabled();
  const frame = (await viewport.boundingBox())!;
  await page.getByRole('button', { name: '播放', exact: true }).click();
  await page.clock.fastForward(2800);
  await expect(controls).toHaveCSS('opacity', '1');
  expect(await video.evaluate((v: HTMLVideoElement) => v.paused)).toBe(false);
  await page.getByRole('button', { name: '播放設定', exact: true }).click();
  await page.clock.fastForward(2800);
  await expect(controls).toHaveCSS('opacity', '1');
  await page.keyboard.press('Escape');
  const timeline = page.getByRole('slider', { name: '播放進度', exact: true });
  const track = (await timeline.boundingBox())!;
  await page.mouse.move(track.x + 20, track.y + track.height / 2);
  await page.mouse.down();
  await page.clock.fastForward(2800);
  await expect(controls).toHaveCSS('opacity', '1');
  await page.mouse.up();
  expect(await video.evaluate((v: HTMLVideoElement) => v.paused)).toBe(false);
  expect((await viewport.boundingBox())!.height).toBeCloseTo(frame.height, 0);
});

test('companion panels keep episode navigation visible while tabs, chapters and transcript share the reading area', async ({ page }) => {
  await page.setViewportSize({ width: 1366, height: 768 });
  const mock = await mockEpisodeVideo(page);
  await page.route(`**/v1/podcasts/${podcastId}/episodes/*/timeline`, route => {
    const episodeIndex = Number(new URL(route.request().url()).pathname.split('/').at(-2));
    const texts = ['前半段：保留來源與必要條件。'.repeat(50), '後半段：比較另一個觀念。'.repeat(30)];
    return json(route, { schema: 'podcast-transcript-timeline/v1', podcast_id: podcastId, episode_index: episodeIndex,
      audio_sha256: 'a'.repeat(64), script_sha256: 'c'.repeat(64), source_resolver: mock.fixture.view.source_resolver,
      segments: texts.map((text, i) => ({ id: String(i), start: i * 6, end: (i + 1) * 6, title: `章節 ${i + 1}`, text, turns: [{ speaker: 'host', text }] })) });
  });
  await page.goto(`/podcasts/${podcastId}`);
  const companion = page.locator('.podcast-companion');
  const episodes = page.getByRole('complementary', { name: '分集清單' });
  const transcriptTab = page.getByRole('tab', { name: '逐字稿', exact: true });
  const highlightsTab = page.getByRole('tab', { name: '本集重點', exact: true });
  const transcript = page.locator('.podcast-transcript');
  const video = page.locator('video');
  await expect(page.getByRole('button', { name: '播放', exact: true })).toBeEnabled();
  await expect(transcript.getByRole('button').first()).toBeEnabled();
  await expect(companion.locator(':scope > .surface')).toHaveCount(2);
  await expect(transcriptTab).toHaveAttribute('aria-selected', 'true');
  await expect(companion.getByRole('tabpanel')).toHaveCount(1);
  const episodeBox = (await episodes.boundingBox())!;
  const reading = (await page.locator('.podcast-reading-area').boundingBox())!;
  expect(reading.y).toBeGreaterThanOrEqual(episodeBox.y + episodeBox.height);
  await expect(page.getByRole('tabpanel', { name: '逐字稿', exact: true }).getByRole('navigation', { name: '本集章節' })).toBeVisible();
  await transcript.evaluate(el => { el.scrollTop = 300; });
  expect(await transcript.evaluate(el => el.scrollTop)).toBeGreaterThan(0);
  expect((await episodes.boundingBox())!.y).toBeCloseTo(episodeBox.y, 0);
  expect(await companion.evaluate(el => el.scrollTop)).toBe(0);
  await highlightsTab.click();
  await expect(page.getByRole('tabpanel', { name: '本集重點', exact: true })).toBeVisible();
  await expect(page.getByRole('tabpanel', { name: '逐字稿', exact: true })).toHaveCount(0);
  await expect(page.getByRole('navigation', { name: '本集章節' })).toHaveCount(0);
  const highlights = page.locator('.podcast-highlights');
  await highlights.evaluate(el => { el.scrollTop = 300; });
  expect(await highlights.evaluate(el => el.scrollTop)).toBeGreaterThan(0);
  expect((await episodes.boundingBox())!.y).toBeCloseTo(episodeBox.y, 0);
  expect(await video.evaluate((v: HTMLVideoElement) => v.paused)).toBe(true);
  await highlightsTab.focus(); await page.keyboard.press('ArrowLeft');
  await expect(transcriptTab).toBeFocused();
  await expect(transcriptTab).toHaveAttribute('aria-selected', 'true');
  await page.keyboard.press('End'); await expect(highlightsTab).toBeFocused();
  await page.keyboard.press('Home'); await expect(transcriptTab).toBeFocused();
  await page.getByRole('navigation', { name: '本集章節' }).getByRole('button', { name: /後半說明/ }).click();
  await expect.poll(() => video.evaluate((v: HTMLVideoElement) => v.currentTime)).toBeCloseTo(6, 1);
  const activeCue = transcript.getByRole('button', { name: /後半段/ });
  await expect(activeCue).toHaveAttribute('aria-current', 'true');
  await page.getByRole('button', { name: '播放', exact: true }).click();
  await expect.poll(() => transcript.evaluate(el => el.scrollTop)).toBeGreaterThan(300);
  await highlightsTab.click();
  expect(await video.evaluate((v: HTMLVideoElement) => v.paused)).toBe(false);
  await transcriptTab.click();
  await expect(activeCue).toHaveAttribute('aria-current', 'true');
  await episodes.getByRole('button').nth(1).click();
  await expect(episodes.getByRole('button').nth(1)).toHaveAttribute('aria-current', 'true');
  await expect(video).toHaveAttribute('src', /episodes\/1\/video\/media$/);
  await expect(transcript.getByRole('button').first()).toBeEnabled();
  // 播放中切換集數不中斷：新的一集接著播放，逐字稿回到開頭附近跟著播放位置。
  await expect.poll(() => video.evaluate((v: HTMLVideoElement) => v.paused)).toBe(false);
  expect(await transcript.evaluate(el => el.scrollTop)).toBeLessThan(200);
  await page.setViewportSize({ width: 390, height: 844 });
  // 手機：逐字稿在固定高度內捲動，播放器黏在上方，不必捲回頂端才能暫停。
  await expect.poll(() => transcript.evaluate(el => el.scrollHeight - el.clientHeight)).toBeGreaterThan(100);
  expect(await page.locator('.podcast-listening-layout > .media-player').evaluate(el => getComputedStyle(el).position)).toBe('sticky');
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(390);
  await expect(companion.locator(':scope > .surface')).toHaveCount(2);
  await highlightsTab.click();
  await expect(page.getByRole('tabpanel', { name: '本集重點', exact: true })).toBeVisible();
  // 重點同樣在固定高度內捲動。
  expect(await highlights.evaluate(el => getComputedStyle(el).overflowY)).toBe('auto');
});

test('desktop watch room fits measured page chrome and viewport height without shrinking mobile video', async ({ page }) => {
  const fixture = await mockEpisodeVideo(page);
  const sizes = [[1000, 800], [1280, 720], [1366, 768], [1440, 720], [1440, 900], [1920, 1080], [2560, 1080], [1920, 600]];
  for (const [width, height] of sizes) {
    await page.setViewportSize({ width, height });
    await page.goto(`/podcasts/${podcastId}`);
    await expect(page.getByRole('button', { name: '播放', exact: true })).toBeEnabled();
    await expect.poll(async () => {
      const player = (await page.locator('.media-player').boundingBox())!;
      return player.y + player.height;
    }).toBeLessThanOrEqual(height - 20);
    const player = (await page.locator('.media-player').boundingBox())!;
    const viewport = (await page.locator('.media-viewport').boundingBox())!;
    const companion = (await page.locator('.podcast-companion').boundingBox())!;
    const controls = (await page.getByRole('group', { name: '播放控制', exact: true }).boundingBox())!;
    const display = (await page.locator('.media-display').boundingBox())!;
    expect(display.width / display.height).toBeCloseTo(16 / 9, 2);
    expect(companion.y).toBeCloseTo(player.y, 0);
    // 播放列接在影片正下方（與影片欄同寬），影片與播放列之間不留大塊空白。
    const dock = (await page.locator('.podcast-dock').boundingBox())!;
    expect(controls.y + controls.height).toBeLessThanOrEqual(height - 12);
    expect(player.y + player.height).toBeLessThanOrEqual(dock.y);
    expect(dock.y - (player.y + player.height)).toBeLessThanOrEqual(16);
    expect(dock.x).toBeCloseTo(player.x, 0);
    // 右欄不在播放列下方：延伸到播放列底部，逐字稿有完整高度。
    expect(companion.x).toBeGreaterThanOrEqual(dock.x + dock.width);
    expect(Math.abs(companion.y + companion.height - (dock.y + dock.height))).toBeLessThanOrEqual(2);
    expect(companion.y + companion.height).toBeLessThanOrEqual(height - 12);
    expect(await page.evaluate(() => window.scrollY)).toBe(0);
    expect(await page.evaluate(() => document.documentElement.scrollHeight)).toBeLessThanOrEqual(height);
  }
  // 長標題與教材資訊改變時，依實際 chrome 高度重新計算，不以螢幕寬度猜測。
  await page.setViewportSize({ width: 1440, height: 900 });
  fixture.fixture.view.name = '長標題：逐步確認必要條件與例外情境。'.repeat(8);
  await page.goto(`/podcasts/${podcastId}`);
  await expect(page.getByRole('button', { name: '播放', exact: true })).toBeEnabled();
  const before = (await page.locator('.media-player').boundingBox())!;
  // 教材頁首變高（例如出現提示）時重新量測。
  await page.locator('.material-learning-toolbar').evaluate(el => { (el as HTMLElement).style.paddingBottom = '60px'; });
  await expect.poll(async () => (await page.locator('.media-player').boundingBox())!.y).toBeGreaterThan(before.y);
  await expect.poll(async () => {
    const box = (await page.locator('.media-player').boundingBox())!;
    return box.y + box.height;
  }).toBeLessThanOrEqual(880);
  expect(await page.evaluate(() => window.scrollY)).toBe(0);
  // 手機只受寬度限制；低高度的直向視窗也不縮成桌面模式。
  fixture.fixture.view.name = '我的 Podcast';
  for (const height of [844, 500]) {
    await page.setViewportSize({ width: 390, height });
    await page.goto(`/podcasts/${podcastId}`);
    await expect(page.getByRole('button', { name: '播放', exact: true })).toBeEnabled();
    const player = (await page.locator('.media-player').boundingBox())!;
    const layout = (await page.locator('.podcast-listening-layout').boundingBox())!;
    expect(player.width).toBeCloseTo(layout.width, 0);
    const display = (await page.locator('.media-display').boundingBox())!;
    expect(display.width / display.height).toBeCloseTo(16 / 9, 2);
    const companion = (await page.locator('.podcast-companion').boundingBox())!;
    expect(companion.y).toBeGreaterThanOrEqual(player.y + player.height);
  }
});


test('material podcast player keeps the chat entry in the material tab row',async({page})=>{
 const {fixture}=await mockEpisodeVideo(page);
 for(const episode of fixture.view.episodes){episode.script=fixture.script;episode.audio={...fixture.view.episodes[0].audio!};}
 for(const width of [1920,1440,390]){
  await page.setViewportSize({width,height:900});await page.goto(`/materials/${materialId}/podcasts/${podcastId}`);
  await expect(page.locator('.podcast-listening-layout')).toBeVisible();
  const chat=page.getByRole('button',{name:'教材問答',exact:true});await expect(chat).toBeVisible();const box=(await chat.boundingBox())!;
  // 問答入口在教材頁首右端，緊鄰頭像選單。
  const toolbar=(await page.locator('.material-learning-toolbar').boundingBox())!;expect(box.y).toBeGreaterThanOrEqual(toolbar.y);expect(box.y+box.height).toBeLessThanOrEqual(toolbar.y+toolbar.height);
  const account=(await page.locator('.material-learning-toolbar .account-menu').boundingBox())!;expect(account.x-box.x-box.width).toBeLessThanOrEqual(12);expect(toolbar.x+toolbar.width-account.x-account.width).toBeLessThanOrEqual(12);
 }
});


test('video subtitles follow media time, toggle and switch with the episode', async ({ page }) => {
  await mockEpisodeVideo(page);
  await page.goto(`/podcasts/${podcastId}`);
  const video=page.locator('video');
  await expect.poll(()=>video.evaluate((v:HTMLVideoElement)=>v.textTracks[0]?.cues?.length)).toBe(2);
  await expect(page.locator('.media-subtitles')).toContainText('前半段');
  await video.evaluate((v:HTMLVideoElement)=>{v.currentTime=7;});
  await expect.poll(()=>video.evaluate((v:HTMLVideoElement)=>(v.textTracks[0]?.activeCues?.[0] as VTTCue)?.text)).toBe('後半段');
  await page.getByRole('button',{name:'播放設定',exact:true}).click();
  const toggle=page.getByRole('button',{name:'字幕',exact:true});
  await toggle.click();await expect(toggle).toHaveAttribute('aria-pressed','false');
  await expect(page.locator('.media-subtitles')).toHaveCount(0);
  await toggle.click();await expect(toggle).toHaveAttribute('aria-pressed','true');
  await expect(page.locator('.media-subtitles')).toContainText('後半段');
  await video.evaluate((v:HTMLVideoElement)=>{v.currentTime=1;});
  await expect.poll(()=>video.evaluate((v:HTMLVideoElement)=>(v.textTracks[0]?.activeCues?.[0] as VTTCue)?.text)).toBe('前半段');
  await page.getByRole('button',{name:'播放設定',exact:true}).click();
  await page.getByRole('button',{name:'下一集',exact:true}).click();
  await expect(video.locator('track')).toHaveAttribute('src',`/v1/podcasts/${podcastId}/episodes/1/subtitles`);
  await expect.poll(()=>video.evaluate((v:HTMLVideoElement)=>v.textTracks[0]?.cues?.length)).toBe(2);
});


test('long subtitles wrap inside mobile video and stay above visible controls', async ({ page }) => {
  await page.setViewportSize({width:390,height:844});
  await mockEpisodeVideo(page);
  const caption='這是較長的合成字幕，用來檢查手機換行與控制列是否遮住內容。'.repeat(3);
  await page.route(`**/v1/podcasts/${podcastId}/episodes/*/subtitles`,r=>r.fulfill({contentType:'text/vtt',body:`WEBVTT\n\n00:00:00.000 --> 00:00:12.000\n${caption}\n`}));
  await page.goto(`/podcasts/${podcastId}`);
  const text=page.locator('.media-subtitles');await expect(text).toHaveText(caption);
  const box=(await text.boundingBox())!,viewport=(await page.locator('.media-viewport').boundingBox())!,controls=(await page.locator('.media-playback-controls').boundingBox())!;
  expect(box.x).toBeGreaterThanOrEqual(viewport.x);
  const display=(await page.locator('.media-display').boundingBox())!;
  expect(box.y).toBeGreaterThanOrEqual(display.y);
  expect(box.x+box.width).toBeLessThanOrEqual(viewport.x+viewport.width);
  expect(box.y+box.height).toBeLessThanOrEqual(controls.y);
  expect(await text.evaluate(e=>e.scrollWidth<=e.clientWidth&&e.scrollHeight<=e.clientHeight)).toBe(true);
});


test('cancel and delete removes unfinished podcast instead of retaining a paused item',async({page})=>{
 const fixture=await mockPodcasts(page);await page.goto(`/podcasts/${podcastId}`);
 await page.getByRole('button',{name:'取消並刪除',exact:true}).click();
 await expect(page.getByRole('button',{name:'保留 Podcast',exact:true})).toBeFocused();
 await page.getByRole('button',{name:'保留 Podcast',exact:true}).click();expect(fixture.deletes()).toBe(0);
 await page.getByRole('button',{name:'取消並刪除',exact:true}).click();
 await page.getByRole('button',{name:'取消並刪除',exact:true}).click();
 await expect(page).toHaveURL(new RegExp(`/materials/${materialId}/podcasts$`));
 await expect(page.getByRole('heading',{name:'讓教材說給你聽'})).toBeVisible();
 expect(fixture.deletes()).toBe(1);expect(fixture.actions).toEqual([]);
 await page.reload();await expect(page.locator('.podcast-tile')).toHaveCount(0);
});

for(const entry of ['library','player']) test(`audio ready does not hide remaining generation or cancellation from ${entry}`,async({page})=>{
 const mock=await mockEpisodeVideo(page,'ready');
 mock.fixture.view.generation={active:true,completed_videos:1,failed_videos:0};
 await page.goto(entry==='library'?'/podcasts':`/podcasts/${podcastId}`);
 await expect(page.getByText(/影片.*仍在生成|影片.*生成中|音訊已完成，正在製作影片/)).toBeVisible();
 await expect(page.locator('video,audio')).toHaveCount(0);
 if(entry==='library')await expect(page.getByRole('button',{name:'開啟播放'})).toHaveCount(0);
 await page.getByRole('button',{name:'取消並刪除',exact:true}).click();
 await page.getByRole('button',{name:'取消並刪除',exact:true}).click();
 await expect(page.getByRole('heading',{name:'讓教材說給你聽'})).toBeVisible();
 expect(mock.fixture.deletes()).toBe(1);
});

// 雲端收聽進度：開啟時以伺服器位置為準，舊瀏覽器位置只在伺服器沒有紀錄時上傳一次。
async function mockProgress(page: Page, initial: { current: { episode_index: number; position_seconds: number } | null }) {
  const posts: Record<string, unknown>[] = [];
  let state = {
    schema: 'podcast-progress/v1', podcast_id: podcastId, credited_seconds: 0,
    current: initial.current && { ...initial.current, updated_at: '2026-10-21T00:00:00Z' },
    episodes: initial.current ? [{ episode_index: initial.current.episode_index, position_seconds: initial.current.position_seconds, completed: false }] : [],
  };
  await page.route(`**/v1/podcasts/${podcastId}/progress`, route => {
    if (route.request().method() === 'GET') return json(route, state);
    const body = route.request().postDataJSON();
    posts.push(body);
    const episode = { episode_index: body.episode_index, position_seconds: body.position_seconds, completed: !!body.ended };
    if (!(body.legacy && state.current)) state = {
      ...state, credited_seconds: body.listened_seconds,
      current: { episode_index: body.episode_index, position_seconds: body.position_seconds, updated_at: '2026-10-21T00:00:01Z' },
      episodes: [...state.episodes.filter(e => e.episode_index !== body.episode_index), episode],
    };
    return json(route, state);
  });
  return posts;
}

test('cloud progress resumes the server episode and position over this browser', async ({ page }) => {
  await mockEpisodeVideo(page);
  const posts = await mockProgress(page, { current: { episode_index: 1, position_seconds: 4 } });
  await page.addInitScript(([key]) => localStorage.setItem(key, JSON.stringify({ episode: 0, time: 9 })), [`studydy.podcast.position:${sessionId}:${podcastId}`]);
  await page.goto(`/podcasts/${podcastId}`);
  await expect(page.getByRole('region', { name: '第 2 集播放器' })).toBeVisible();
  await expect(page.getByRole('button', { name: '播放', exact: true })).toBeEnabled();
  await expect.poll(() => page.locator('video').evaluate((v: HTMLVideoElement) => v.currentTime)).toBeCloseTo(4, 1);
  // 伺服器已有紀錄：不上傳舊位置。
  expect(posts.filter(p => p.legacy)).toEqual([]);
});

test('legacy browser position is uploaded once and playback reports real listening time', async ({ page }) => {
  await mockEpisodeVideo(page);
  const posts = await mockProgress(page, { current: null });
  await page.addInitScript(([key]) => localStorage.setItem(key, JSON.stringify({ episode: 0, time: 3.5 })), [`studydy.podcast.position:${sessionId}:${podcastId}`]);
  await page.goto(`/podcasts/${podcastId}`);
  await expect(page.getByRole('button', { name: '播放', exact: true })).toBeEnabled();
  await expect.poll(() => page.locator('video').evaluate((v: HTMLVideoElement) => v.currentTime)).toBeCloseTo(3.5, 1);
  expect(posts).toEqual([{ schema: 'podcast-progress-update/v1', episode_index: 0, position_seconds: 3.5, listened_seconds: 0, legacy: true }]);
  await page.getByRole('button', { name: '播放', exact: true }).click();
  await page.waitForTimeout(1600);
  await page.getByRole('button', { name: '暫停', exact: true }).click();
  await expect.poll(() => posts.length).toBeGreaterThan(1);
  const report = posts.at(-1)!;
  expect(report.legacy).toBeUndefined();
  expect(report.episode_index).toBe(0);
  expect(report.listened_seconds).toBeGreaterThanOrEqual(1);
  expect(report.listened_seconds).toBeLessThanOrEqual(3);
  expect(Number(report.position_seconds)).toBeGreaterThan(3.5);
  // 切到下一集時送出原集位置；播放到結尾時標記聽完。
  await page.getByRole('slider', { name: '播放進度', exact: true }).fill('11.5');
  await page.getByRole('button', { name: '播放', exact: true }).click();
  await expect.poll(() => posts.some(p => p.ended === true && p.episode_index === 0), { timeout: 10_000 }).toBe(true);
});

test("podcast tiles show status only while work remains and leave version notes to the player", async ({ page }) => {
  const fixture = await mockPodcasts(page);
  const done = { ...fixture.view, name: "已完成的 Podcast", episode_count: 3, completed_episodes: 3, duration_seconds: 3910, status: "ready", is_current_revision: false,
    generation: { active: false, preview_ready: true, completed_videos: 3, failed_videos: 0 } };
  const working = { ...done, podcast_id: "88888888-8888-4888-8888-888888888888", name: "影片生成中", is_current_revision: true, status: "running", completed_episodes: 2, duration_seconds: null,
    generation: { active: true, preview_ready: false, completed_videos: 1, failed_videos: 0 } };
  await page.route("**/v1/podcasts", route => json(route, { schema: "podcast-list/v1", podcasts: [done, working] }));
  await page.goto("/podcasts");
  // 全部完成：按鈕已是「開啟播放」，不再顯示狀態列，也不在列表標示教材新版。
  const finished = page.getByRole("article").filter({ hasText: "已完成的 Podcast" });
  await expect(finished.getByRole("button", { name: "開啟播放" })).toBeVisible();
  await expect(finished.locator(".library-state")).toHaveCount(0);
  // 封面右下角是集數加總時長（滿 60 分鐘改用小時），螢幕閱讀器另有文字。
  await expect(finished.locator(".podcast-cover i")).toHaveText("3 集 · 1 小時 5 分");
  await expect(finished.locator(".sr-only")).toHaveText("單人解說 · 3 集 · 共 1 小時 5 分");
  // 音訊還沒全部完成：不顯示會變動的總時長。
  const pending = page.getByRole("article").filter({ hasText: "影片生成中" });
  await expect(pending.locator(".podcast-cover i")).toHaveText("3 集");
  await expect(pending.locator(".library-state")).toHaveText("正在生成 · 音訊 2 / 3 集 · 影片 1 / 3 集");
});

test("ready Podcast exports each available format for the selected episode", async ({ page }) => {
  await mockEpisodeVideo(page);
  const requests: string[] = [];
  await page.route("**/episodes/*/export/*", route => {
    requests.push(new URL(route.request().url()).pathname);
    const format = route.request().url().split("/").pop();
    return route.fulfill({ status: 200, contentType: "application/octet-stream", headers: { "Content-Disposition": `attachment; filename*=UTF-8''episode.${format}` }, body: "saved export" });
  });
  await page.goto(`/podcasts/${podcastId}`);
  const controls = page.getByRole("group", { name: "匯出本集" });
  for (const format of ["mp3", "md", "mp4"]) {
    await controls.getByRole("combobox").selectOption(format);
    const download = page.waitForEvent("download");
    await controls.getByRole("button", { name: "匯出", exact: true }).click();
    expect((await download).suggestedFilename()).toBe(`episode.${format}`);
  }
  expect(requests.every(path => path.includes("/episodes/0/export/"))).toBe(true);
});
