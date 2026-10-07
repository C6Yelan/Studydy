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
    name: "我的 Podcast", delivery: "solo", concept_ids: claims.map(c => c.concept_id),
    status: "running", error_code: null, version: 1, created_at: "2026-10-03T00:00:00Z",
    episode_count: 3, completed_episodes: 1, is_current_revision: true,
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
  await page.route(`**/v1/podcasts/${podcastId}/episodes/*/timeline`,route=>json(route,{schema:'api-error/v1',request_id:sessionId,reason_code:'SOURCE_NOT_READY',retryable:true,message:'Synthetic timeline pending'},409));
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
  await expect(page.getByRole("heading", { name: "我的 Podcast", exact: true })).toHaveCount(0);
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
  await page.getByRole("form", { name: "刪除 Podcast 確認" }).getByRole("button", { name: "取消", exact: true }).click();
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
  await page.getByLabel("選擇教材").selectOption(materialId);
  const name = page.getByLabel("Podcast 名稱", { exact: true });
  await expect(name).toBeVisible();
  await expect(page).toHaveURL(new RegExp('/create-podcast$'));
  await expect(page.getByLabel("選擇教材")).toHaveCount(0);
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
    name: "此教材卡組", card_count: 2, created_at: fixture.view.created_at, is_current_revision: true };
  await page.route("**/v1/card-sets", route => json(route, { schema: "card-set-list/v1", card_sets: [card, { ...card, card_set_id: secondPodcast, material_id: otherMaterial, name: "別的教材卡組" }] }));
  await page.goto('/');
  await expect(page.locator('.dashboard-collections')).toHaveCount(0);
  await page.getByRole('navigation', { name: '主要導覽' }).getByRole('button', { name: 'Podcast', exact: true }).click();
  await expect(page.getByRole('navigation', { name: '主要導覽' }).getByRole('button', { name: 'Podcast', exact: true })).toHaveAttribute('aria-current', 'page');
  await expect(page.locator('.podcast-tile')).toHaveCount(2);
  await page.goto(`/materials/${materialId}/podcasts`);
  await expect(page.locator('.podcast-tile')).toHaveCount(1);
  await expect(page.getByRole('tablist', { name: '教材學習內容' }).getByRole('tab')).toHaveText(['概念地圖', '複習重點', '概念卡', 'Podcast', '補充學習', '教材來源']);
  await expect(page.locator('.app-sidebar')).toHaveCount(0);
  await expect(page.locator('.app-shell')).toHaveClass(/is-workspace/);
  const navBox = (await page.getByRole('tablist', { name: '教材學習內容' }).boundingBox())!;
  const searchBox = (await page.getByRole('searchbox', { name: '搜尋 Podcast', exact: true }).boundingBox())!;
  expect(searchBox.y).toBeGreaterThanOrEqual(navBox.y + navBox.height);
  await expect(page.getByRole('heading', { name: '別的教材 Podcast' })).toHaveCount(0);
  await page.getByRole('button', { name: '查看進度', exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`/materials/${materialId}/podcasts/${podcastId}$`));
  await expect(page.locator('.app-sidebar')).toHaveCount(0);
  await expect(page.getByRole('tablist', { name: '教材學習內容' })).toBeVisible();
  await page.getByRole('button', { name: '返回 Podcast', exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`/materials/${materialId}/podcasts$`));
  await page.getByRole('tablist', { name: '教材學習內容' }).getByRole('tab', { name: '概念卡', exact: true }).click();
  await expect(page.locator('.library-item')).toHaveCount(1);
  await expect(page.getByRole('heading', { name: '此教材卡組' })).toBeVisible();
  const cardsNav = (await page.getByRole('tablist', { name: '教材學習內容' }).boundingBox())!;
  const cardsSearch = (await page.getByRole('searchbox', { name: '搜尋卡組', exact: true }).boundingBox())!;
  expect(cardsSearch.y).toBeGreaterThanOrEqual(cardsNav.y + cardsNav.height);
  await page.getByRole('searchbox', { name: '搜尋卡組', exact: true }).fill('不存在的卡組');
  await expect(page.locator('.library-item')).toHaveCount(0);
  await page.getByRole('searchbox', { name: '搜尋卡組', exact: true }).fill('');
  await page.getByRole('button', { name: '建立卡組', exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}/create-cards$`));
  await expect(page.getByLabel('選擇教材')).toHaveCount(0);
  await expect(page.getByRole('tablist', { name: '教材學習內容' })).toBeVisible();
  await expect(page.locator('.app-sidebar')).toHaveCount(0);
  await page.goto('/');
  await expect(page.locator('.dashboard-collections')).toHaveCount(0);
  await page.getByRole('navigation', { name: '主要導覽' }).getByRole('button', { name: '概念卡', exact: true }).click();
  await expect(page.getByRole('navigation', { name: '主要導覽' }).getByRole('button', { name: '概念卡', exact: true })).toHaveAttribute('aria-current', 'page');
  await expect(page.locator('.library-item')).toHaveCount(2);
});

test("player plays, seeks and switches episodes with usable desktop and mobile controls", async ({ page }) => {
  const fixture = await mockPodcasts(page);
  fixture.view.status = 'ready'; fixture.view.completed_episodes = 3;
  for (const episode of fixture.view.episodes) { episode.script = fixture.script; episode.audio = { ...fixture.view.episodes[0].audio! }; }
  fixture.view.source_status.quality = 'needs_review'; fixture.view.source_status.decision = 'review';
  const pcm = Buffer.alloc(44 + 16000 * 40 * 2);
  pcm.write('RIFF'); pcm.writeUInt32LE(pcm.length - 8, 4); pcm.write('WAVEfmt ', 8); pcm.writeUInt32LE(16, 16);
  pcm.writeUInt16LE(1, 20); pcm.writeUInt16LE(1, 22); pcm.writeUInt32LE(16000, 24); pcm.writeUInt32LE(32000, 28);
  pcm.writeUInt16LE(2, 32); pcm.writeUInt16LE(16, 34); pcm.write('data', 36); pcm.writeUInt32LE(pcm.length - 44, 40);
  await page.route(`**/v1/podcasts/${podcastId}/episodes/*/audio`, route => { const range=route.request().headers()['range']?.match(/bytes=(\d+)-(\d*)/);const start=range?Number(range[1]):0,end=range?.[2]?Math.min(Number(range[2]),pcm.length-1):pcm.length-1;return route.fulfill({status:range?206:200,contentType:'audio/wav',headers:{'Accept-Ranges':'bytes',...(range?{'Content-Range':`bytes ${start}-${end}/${pcm.length}`}:{})},body:pcm.subarray(start,end+1)}); });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto(`/podcasts/${podcastId}`);
  await expect(page.getByText('教材內容待核對', { exact: true })).toHaveCount(0);
  await expect(page.getByRole('region',{name:'Podcast 音訊',exact:true})).toContainText('本集影片尚未生成');
  const display = (await page.locator('.media-display').boundingBox())!;
  expect(display.height).toBeLessThan(350);
  const playlist = (await page.locator('.podcast-episodes').boundingBox())!;
  expect(playlist.x).toBeGreaterThan(display.x + display.width);
  const reading = (await page.locator('.podcast-reading-area').boundingBox())!;
  expect(reading.x).toBeCloseTo(playlist.x,0);
  expect(reading.y).toBeGreaterThanOrEqual(playlist.y+playlist.height);
  for (const selector of ['.media-display','.media-playback-controls','.media-timeline']) {
    expect(await page.locator(selector).evaluate(element=>getComputedStyle(element).backgroundImage)).toBe('none');
  }
  await expect(page.getByRole('button',{name:'全螢幕',exact:true})).toHaveCount(0);
  const layout = (await page.locator('.podcast-listening-layout').boundingBox())!;
  expect(Math.abs(layout.x + layout.width / 2 - 720)).toBeLessThanOrEqual(1);
  expect(layout.width).toBeGreaterThan(1300);
  for(const [width,height] of [[1366,768],[1920,1080]]) {
    await page.setViewportSize({width,height});
    await expect.poll(async()=>{const box=(await page.locator('.media-player').boundingBox())!;return box.y+box.height<=height-8;}).toBe(true);
    const workspace=(await page.locator('.podcast-listening-layout').boundingBox())!;
    expect(workspace.width).toBeGreaterThanOrEqual(width-50);
    const side=(await page.locator('.podcast-companion').boundingBox())!;
    expect(side.y+side.height).toBeLessThanOrEqual(height-8);
  }
  await page.setViewportSize({width:1440,height:1000});
  await page.getByRole('button', { name: '播放', exact: true }).click();
  await expect(page.getByRole('button', { name: '暫停', exact: true })).toBeVisible();
  await page.getByRole('button', { name: '前進 10 秒', exact: true }).click();
  await expect.poll(() => page.locator('audio').evaluate((a: HTMLAudioElement) => a.currentTime)).toBeGreaterThan(9);
  await page.getByRole('button', { name: '暫停', exact: true }).click();
  await page.getByLabel('自動播放下一集').uncheck();
  await page.locator('.media-viewport').focus();
  const original = await page.locator('audio').evaluate((a: HTMLAudioElement) => ({ time: a.currentTime, paused: a.paused, muted: a.muted }));
  for (const key of ['k', 'j', 'l', 'm']) await page.keyboard.press(key);
  expect(await page.locator('audio').evaluate((a: HTMLAudioElement) => ({ time: a.currentTime, paused: a.paused, muted: a.muted }))).toEqual(original);
  await page.getByRole('button', { name: '播放設定', exact: true }).click();
  await expect(page.getByRole('group', { name: '播放設定選單' }).getByRole('checkbox')).toHaveCount(0);
  await page.getByRole('button', { name: '1.5×', exact: true }).click();
  expect(await page.locator('audio').evaluate((a: HTMLAudioElement) => a.playbackRate)).toBe(1.5);
  await page.keyboard.press('Escape');
  await page.getByRole('button', { name: '下一集', exact: true }).click();
  await expect(page.getByRole('region', { name: '第 2 集播放器' })).toBeVisible();
  await expect(page.getByLabel('自動播放下一集')).not.toBeChecked();
  await page.locator('audio').evaluate((a: HTMLAudioElement) => { a.pause(); a.dispatchEvent(new Event('ended')); });
  await expect(page.getByRole('region', { name: '第 2 集播放器' })).toBeVisible();
  await page.setViewportSize({width:900,height:900});
  const narrowPlayer=(await page.locator('.media-player').boundingBox())!;
  const narrowReading=(await page.locator('.podcast-reading-area').boundingBox())!;
  expect(narrowReading.x).toBeGreaterThanOrEqual(narrowPlayer.x+narrowPlayer.width);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.evaluate(() => window.scrollTo(0,0));
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(390);
  const playerBox = (await page.locator('.media-player').boundingBox())!;
  const mobileDisplay = (await page.locator('.media-display').boundingBox())!;
  expect(mobileDisplay.height).toBeLessThan(350);
  await expect(page.locator('.media-player').getByRole('group',{name:'播放控制',exact:true})).toBeVisible();
  const episodesBox = (await page.locator('.podcast-episodes').boundingBox())!;
  expect(playerBox.y + playerBox.height).toBeLessThanOrEqual(episodesBox.y);
  expect((await page.getByRole('button', {name:'播放設定',exact:true}).boundingBox())!.y).toBeLessThan(844);
  const buttons = page.locator('.media-transport button');
  let end = 0;
  for (const button of await buttons.all()) { const box = (await button.boundingBox())!; expect(box.width).toBeGreaterThanOrEqual(40); expect(box.x).toBeGreaterThanOrEqual(end); end = box.x + box.width; }
  await page.getByRole('button', { name: '播放設定', exact: true }).click();
  const panel = (await page.getByRole('group', { name: '播放設定選單' }).boundingBox())!;
  expect(panel.x).toBeGreaterThanOrEqual(0); expect(panel.x + panel.width).toBeLessThanOrEqual(390);
  await page.getByLabel('設定音量',{exact:true}).focus();
  await page.keyboard.press('Home');await page.keyboard.press('ArrowRight');
  expect(await page.locator('audio').evaluate((a:HTMLAudioElement)=>a.volume)).toBeCloseTo(.05,2);
});

test("podcast creation keeps manual selection and settings without recommendations or material switching", async ({ page }) => {
  const fixture = await mockPodcasts(page);
  const unnecessaryReads: string[] = [];
  page.on('request', request => { const path = new URL(request.url()).pathname; if (path === '/v1/materials' || path.endsWith('/progress')) unnecessaryReads.push(path); });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto(createPath);
  const selection = page.getByRole('region', { name: '選擇 Podcast 概念' });
  const settings = page.getByRole('region', { name: '講解設定', exact: true });
  await expect(page.getByRole('region', { name: '幫我選概念', exact: true })).toHaveCount(0);
  await expect(page.getByRole('button', { name: '套用推薦', exact: true })).toHaveCount(0);
  await expect(page.getByLabel('選擇教材')).toHaveCount(0);
  await expect(selection).toBeVisible(); await expect(settings).toBeVisible();
  expect((await selection.boundingBox())!.x).toBeLessThan((await settings.boundingBox())!.x);
  await selection.locator('.podcast-concept-option').last().getByRole('checkbox').uncheck();
  await selection.getByLabel('只看已選').check();
  await expect(selection).toContainText('已選 1 / 2');
  await page.setViewportSize({ width: 390, height: 844 });
  const controls = page.getByRole('navigation', { name: 'Podcast 設定區域' });
  await expect(controls.getByRole('button')).toHaveText(['選擇概念', '講解設定']);
  await controls.getByRole('button', { name: '講解設定', exact: true }).click();
  await expect(settings).toBeVisible(); await expect(selection).toBeHidden();
  await settings.getByLabel('單人解說', { exact: false }).check();
  await expect(settings.getByRole('group', { name: '講解模式' })).toHaveCount(0);
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
  await page.getByRole('button', { name: '開始生成 Podcast' }).click();
  await expect(page).toHaveURL(new RegExp(`/materials/${materialId}/podcasts/${podcastId}$`));
  expect(body.concept_ids).toEqual([first.concept_id]); expect(body.delivery).toBe('solo'); expect(body).not.toHaveProperty('mode');
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
    expect(left.x).toBeLessThanOrEqual(32); expect(width-right.x-right.width).toBeLessThanOrEqual(32);
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
    expect(action.y+action.height).toBeLessThan(voice.y);
    expect(action.y+action.height).toBeLessThan(height);
    expect(await page.evaluate(()=>document.documentElement.scrollHeight<=innerHeight+1&&document.documentElement.scrollWidth===innerWidth)).toBe(true);
  }
});

test("saved dialogue transcripts keep roles and switch with the selected episode", async ({ page }) => {
  const fixture = await mockPodcasts(page);
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
  await expect(first.locator('.podcast-transcript-speaker')).toHaveText(['講解者', '學習者']);
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
 const fixture=await mockPodcasts(page);fixture.view.status='ready';fixture.view.completed_episodes=3;
 for(const episode of fixture.view.episodes){episode.script=fixture.script;episode.audio=fixture.view.episodes[0].audio;}
 const wav=Buffer.alloc(44+48000);wav.write('RIFF');wav.writeUInt32LE(wav.length-8,4);wav.write('WAVEfmt ',8);wav.writeUInt32LE(16,16);wav.writeUInt16LE(1,20);wav.writeUInt16LE(1,22);wav.writeUInt32LE(24000,24);wav.writeUInt32LE(48000,28);wav.writeUInt16LE(2,32);wav.writeUInt16LE(16,34);wav.write('data',36);wav.writeUInt32LE(wav.length-44,40);
 await page.route(`**/v1/podcasts/${podcastId}/episodes/*/audio`,r=>r.fulfill({status:200,contentType:'audio/wav',body:wav}));
 await page.goto(`/podcasts/${podcastId}`);await page.getByRole('button',{name:'播放',exact:true}).waitFor();
 await page.locator('audio').evaluate((a:HTMLAudioElement)=>{a.play=()=>Promise.reject(new DOMException('intentional pause','AbortError'));});
 await page.getByRole('button',{name:'播放',exact:true}).click();await page.waitForTimeout(150);
 await expect(page.getByText('暫時無法播放，請按播放重試。')).toHaveCount(0);
 await page.locator('audio').evaluate((a:HTMLAudioElement)=>{a.play=()=>Promise.reject(new DOMException('blocked playback','NotAllowedError'));});
 await page.getByRole('button',{name:'播放',exact:true}).click();await expect(page.getByText('暫時無法播放，請按播放重試。')).toBeVisible();
});


// 合成純色MP4僅驗證播放與來源切換，不代表模型內容品質。
async function mockEpisodeVideo(page:Page, initial:'ready'|'pending'|'failed'='ready') {
  const fixture=await mockPodcasts(page);fixture.view.status='ready';fixture.view.completed_episodes=3;
  for(const e of fixture.view.episodes){e.script=fixture.script;e.audio={...fixture.view.episodes[0].audio!,duration_seconds:12};}
  const wav=Buffer.alloc(44+24000*12*2);wav.write('RIFF');wav.writeUInt32LE(wav.length-8,4);wav.write('WAVEfmt ',8);wav.writeUInt32LE(16,16);wav.writeUInt16LE(1,20);wav.writeUInt16LE(1,22);wav.writeUInt32LE(24000,24);wav.writeUInt32LE(48000,28);wav.writeUInt16LE(2,32);wav.writeUInt16LE(16,34);wav.write('data',36);wav.writeUInt32LE(wav.length-44,40);
  await page.route(`**/v1/podcasts/${podcastId}/episodes/*/audio`,r=>r.fulfill({status:200,contentType:'audio/wav',body:wav}));
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
  return {fixture,actions,ready:()=>{status='ready';version++}};
}

test('formal video player shares controls, transcript seek, pages and logout boundary',async({page})=>{
  await mockEpisodeVideo(page);
  await page.goto(`/podcasts/${podcastId}`);
  const video=page.locator('video');await expect(video).toHaveCount(1);await expect(page.locator('audio')).toHaveCount(0);
  await expect(page.getByRole('button',{name:'播放',exact:true})).toBeEnabled();
  const display=(await page.locator('.media-display').boundingBox())!,controls=(await page.locator('.media-playback-controls').boundingBox())!;
  expect(controls.y).toBeLessThan(display.y+display.height);
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
  await page.getByRole('button',{name:'登出',exact:true}).click();await expect(page.locator('video')).toHaveCount(0);
});

test('video becoming ready preserves audio position, play state and settings',async({page})=>{
  const mock=await mockEpisodeVideo(page,'pending');await page.goto(`/podcasts/${podcastId}`);
  await expect(page.getByRole('button',{name:'播放',exact:true})).toBeEnabled();
  await page.getByLabel('自動播放下一集').uncheck();
  await page.getByRole('button',{name:'靜音',exact:true}).click();await page.getByRole('button',{name:'播放',exact:true}).click();
  await page.locator('audio').evaluate((a:HTMLAudioElement)=>{a.currentTime=2;});mock.ready();
  await expect(page.locator('video')).toHaveCount(1);await expect(page.locator('audio')).toHaveCount(0);
  await expect.poll(()=>page.locator('video').evaluate((v:HTMLVideoElement)=>v.currentTime)).toBeGreaterThanOrEqual(2);
  await expect.poll(()=>page.locator('video').evaluate((v:HTMLVideoElement)=>v.paused)).toBe(false);
  expect(await page.locator('video').evaluate((v:HTMLVideoElement)=>v.muted)).toBe(true);
});

test('failed video can retry and cancel while existing audio keeps playing',async({page})=>{
  const mock=await mockEpisodeVideo(page,'failed');await page.goto(`/podcasts/${podcastId}`);
  await expect(page.getByText('分鏡版面需要調整，尚未發布影片。')).toBeVisible();
  await page.getByRole('button',{name:'播放',exact:true}).click();await page.getByRole('button',{name:'重試影片',exact:true}).click();
  await expect(page.getByRole('button',{name:'取消影片',exact:true})).toBeVisible();
  await expect.poll(()=>page.locator('audio').evaluate((a:HTMLAudioElement)=>a.paused)).toBe(false);
  await page.getByRole('button',{name:'取消影片',exact:true}).click();await expect(page.getByText('影片生成已取消')).toBeVisible();
  expect(mock.actions).toEqual(['retry','cancel']);
});

test('full dialogue video keeps both speakers inside a shared cue and seeks across turns',async({page})=>{
  const mock=await mockEpisodeVideo(page);
  mock.fixture.view.delivery='dialogue';
  for(const episode of mock.fixture.view.episodes){
    episode.delivery='dialogue';
    if(episode.script)episode.script={...episode.script,segments:episode.script.segments.map(s=>({...s,turns:[{speaker:'host',text:'為什麼需要這個條件？'},{speaker:'guest',text:'條件決定這個觀念的適用範圍。'}]}))};
  }
  await page.route(`**/v1/podcasts/${podcastId}/episodes/0/timeline`,r=>json(r,{
    schema:'podcast-transcript-timeline/v1',podcast_id:podcastId,episode_index:0,audio_sha256:'a'.repeat(64),script_sha256:'c'.repeat(64),source_resolver:mock.fixture.view.source_resolver,
    segments:[{id:'a',start:0,end:6,title:'提問與說明',text:'為什麼需要這個條件？\n條件決定這個觀念的適用範圍。',turns:[{speaker:'host',text:'為什麼需要這個條件？'},{speaker:'guest',text:'條件決定這個觀念的適用範圍。'}]},
      {id:'b',start:6,end:12,title:'下一個重點',text:'接著確認來源中的另一個限制。',turns:[{speaker:'guest',text:'接著確認來源中的另一個限制。'}]}]}));
  await page.goto(`/podcasts/${podcastId}`);
  const transcript=page.getByRole('tabpanel',{name:'逐字稿',exact:true}),first=transcript.getByRole('button',{name:/為什麼需要/}),second=transcript.getByRole('button',{name:/接著確認/});
  await expect(first.locator('small')).toHaveText(['學習者','講解者']);
  await expect(second.locator('small')).toHaveText(['講解者']);
  await expect(first).toBeEnabled();await second.click();
  await expect.poll(()=>page.locator('video').evaluate((v:HTMLVideoElement)=>v.currentTime)).toBeCloseTo(6,1);
  await expect(second).toHaveAttribute('aria-current','true');
  await first.click();await expect(first).toHaveAttribute('aria-current','true');
  await page.getByRole('button',{name:'播放',exact:true}).click();
  await expect.poll(()=>page.locator('video').evaluate((v:HTMLVideoElement)=>v.currentTime)).toBeGreaterThan(.2);
});

async function mockTeachingBeats(page:Page) {
  const mock=await mockEpisodeVideo(page);const view=mock.fixture.view;
  view.episodes=view.episodes.slice(0,1);view.episode_count=view.completed_episodes=1;view.is_current_revision=false;
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
  for(const name of ['問目前播放這一段','問這一段','查看這段來源','同步圖卡','字幕','檢測 Podcast 涵蓋的概念']) await expect(page.getByText(name,{exact:true})).toHaveCount(0);
  await expect(page.locator('video track')).toHaveCount(0);
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
 test('taps toggle once; pointer-focused controls really disappear and remain recoverable',async({page})=>{
  await mockEpisodeVideo(page);await page.goto(`/podcasts/${podcastId}`);
  const video=page.locator('video'),controls=page.locator('.media-playback-controls');
  await page.getByRole('button',{name:'播放',exact:true}).tap();
  await expect.poll(()=>video.evaluate((v:HTMLVideoElement)=>v.paused)).toBe(false);
  await expect(controls).toHaveCSS('opacity','0');
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
  const frame=(await page.locator('.media-viewport').boundingBox())!;
  for(const button of await page.locator('.media-controls button:visible').all()) {
    const box=(await button.boundingBox())!;
    expect(box.width).toBeGreaterThanOrEqual(40);
    expect(box.x+box.width).toBeLessThanOrEqual(frame.x+frame.width);
  }
 });
});

// 控制列只能覆蓋影片，顯示／隱藏都不得改變教材畫面的尺寸。
test('video overlay keeps 16:9 and one compact row at desktop, laptop and mobile widths', async ({ page }, testInfo) => {
  await mockEpisodeVideo(page);
  await page.goto(`/podcasts/${podcastId}`);
  await expect(page.getByRole('button', { name: '播放', exact: true })).toBeEnabled();
  for (const [width, height] of [[1920, 1080], [1366, 768], [390, 844]]) {
    await page.setViewportSize({ width, height });
    const viewport = page.locator('.media-viewport');
    const frame = (await viewport.boundingBox())!;
    const display = (await page.locator('.media-display').boundingBox())!;
    const controls = (await page.locator('.media-playback-controls').boundingBox())!;
    expect(frame.width / frame.height).toBeCloseTo(16 / 9, 2);
    expect(display.height).toBeCloseTo(frame.height, 0);
    expect(controls.y + controls.height).toBeCloseTo(frame.y + frame.height, 0);
    expect(controls.height).toBeLessThanOrEqual(90);
    await expect(page.locator('.media-playback-controls')).toHaveCSS('position', 'absolute');
    expect(await page.locator('.media-playback-controls').evaluate(el => getComputedStyle(el).backgroundImage)).toContain('linear-gradient');
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
    await page.locator('.media-player').screenshot({ path: testInfo.outputPath(`player-${width}.png`) });
    await page.getByRole('button', { name: '播放設定', exact: true }).click();
    const menu = (await page.getByRole('group', { name: '播放設定選單' }).boundingBox())!;
    expect(menu.y).toBeGreaterThanOrEqual(frame.y);
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

test('overlay stays visible while paused, keyboard focused, menu open or dragging a timeline', async ({ page }) => {
  await mockEpisodeVideo(page);
  await page.goto(`/podcasts/${podcastId}`);
  const video = page.locator('video'), controls = page.locator('.media-playback-controls'), viewport = page.locator('.media-viewport');
  await expect(page.getByRole('button', { name: '播放', exact: true })).toBeEnabled();
  const frame = (await viewport.boundingBox())!;
  await page.getByRole('button', { name: '播放', exact: true }).click();
  await expect(controls).toHaveCSS('opacity', '0');
  expect((await viewport.boundingBox())!.height).toBeCloseTo(frame.height, 0);
  await viewport.hover({ position: { x: 70, y: 40 } });
  await page.getByRole('button', { name: '播放設定', exact: true }).click();
  await page.waitForTimeout(2800);
  await expect(controls).toHaveCSS('opacity', '1');
  expect(await video.evaluate((v: HTMLVideoElement) => v.paused)).toBe(false);
  await page.keyboard.press('Escape');
  await page.waitForTimeout(2800);
  await expect(controls).toHaveCSS('opacity', '1');
  await page.getByRole('button', { name: '暫停', exact: true }).click();
  await page.waitForTimeout(2800);
  await expect(controls).toHaveCSS('opacity', '1');
  const timeline = page.getByRole('slider', { name: '播放進度', exact: true });
  await timeline.fill('0');
  await page.getByRole('button', { name: '播放', exact: true }).click();
  const track = (await timeline.boundingBox())!;
  await page.mouse.move(track.x + 20, track.y + track.height / 2);
  await page.mouse.down();
  await page.waitForTimeout(2800);
  await expect(controls).toHaveCSS('opacity', '1');
  expect(await video.evaluate((v: HTMLVideoElement) => v.paused)).toBe(false);
  await page.mouse.up();
  await expect(controls).toHaveCSS('opacity', '0');
  expect((await viewport.boundingBox())!.height).toBeCloseTo(frame.height, 0);
});

test('companion panels keep episode navigation visible while tabs, chapters and transcript share the reading area', async ({ page }, testInfo) => {
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
  await companion.screenshot({ path: testInfo.outputPath('companion-desktop-transcript.png') });
  await highlightsTab.click();
  await expect(page.getByRole('tabpanel', { name: '本集重點', exact: true })).toBeVisible();
  await expect(page.getByRole('tabpanel', { name: '逐字稿', exact: true })).toHaveCount(0);
  await expect(page.getByRole('navigation', { name: '本集章節' })).toHaveCount(0);
  const highlights = page.locator('.podcast-highlights');
  await highlights.evaluate(el => { el.scrollTop = 300; });
  expect(await highlights.evaluate(el => el.scrollTop)).toBeGreaterThan(0);
  expect((await episodes.boundingBox())!.y).toBeCloseTo(episodeBox.y, 0);
  expect(await video.evaluate((v: HTMLVideoElement) => v.paused)).toBe(true);
  await companion.screenshot({ path: testInfo.outputPath('companion-desktop-highlights.png') });
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
  expect(await transcript.evaluate(el => el.scrollTop)).toBe(0);
  await page.setViewportSize({ width: 390, height: 844 });
  await expect.poll(() => transcript.evaluate(el => el.scrollHeight - el.clientHeight)).toBeLessThanOrEqual(1);
  expect((await companion.boundingBox())!.height).toBeGreaterThan(844);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(390);
  await expect(companion.locator(':scope > .surface')).toHaveCount(2);
  await page.locator('.podcast-reading-area').screenshot({ path: testInfo.outputPath('companion-mobile-transcript.png') });
  await highlightsTab.click();
  await expect(page.getByRole('tabpanel', { name: '本集重點', exact: true })).toBeVisible();
  expect(await highlights.evaluate(el => el.scrollHeight - el.clientHeight)).toBeLessThanOrEqual(1);
});

test('desktop watch room fits measured page chrome and viewport height without shrinking mobile video', async ({ page }, testInfo) => {
  const fixture = await mockEpisodeVideo(page);
  const sizes = [[1280, 720], [1366, 768], [1440, 720], [1440, 900], [1920, 1080], [2560, 1080], [1920, 600]];
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
    expect(viewport.width / viewport.height).toBeCloseTo(16 / 9, 2);
    expect(companion.y).toBeCloseTo(player.y, 0);
    expect(companion.height).toBeCloseTo(player.height, 0);
    expect(companion.y + companion.height).toBeLessThanOrEqual(height - 20);
    expect(controls.y + controls.height).toBeLessThanOrEqual(height - 20);
    expect(await page.evaluate(() => window.scrollY)).toBe(0);
    expect(await page.evaluate(() => document.documentElement.scrollHeight)).toBeLessThanOrEqual(height);
    if ([1366, 1920, 2560].includes(width)) await page.screenshot({ path: testInfo.outputPath(`watch-${width}-${height}.png`) });
  }
  // 長標題與教材資訊改變時，依實際 chrome 高度重新計算，不以螢幕寬度猜測。
  await page.setViewportSize({ width: 1440, height: 720 });
  fixture.fixture.view.name = '長標題：逐步確認必要條件與例外情境。'.repeat(8);
  await page.goto(`/podcasts/${podcastId}`);
  await expect(page.getByRole('button', { name: '播放', exact: true })).toBeEnabled();
  const before = (await page.locator('.media-player').boundingBox())!;
  await page.locator('.material-context span').evaluate(el => { el.textContent = '較長的教材名稱與來源資訊。'.repeat(20); });
  await expect.poll(async () => (await page.locator('.media-player').boundingBox())!.y).toBeGreaterThan(before.y);
  await expect.poll(async () => {
    const box = (await page.locator('.media-player').boundingBox())!;
    return box.y + box.height;
  }).toBeLessThanOrEqual(700);
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
    expect((player.width - 2) / (player.height - 2)).toBeCloseTo(16 / 9, 2);
    const companion = (await page.locator('.podcast-companion').boundingBox())!;
    expect(companion.y).toBeGreaterThanOrEqual(player.y + player.height);
  }
});


test('material podcast player keeps the chat entry at the viewport corner',async({page})=>{
 const fixture=await mockPodcasts(page);fixture.view.status='ready';fixture.view.completed_episodes=3;
 for(const episode of fixture.view.episodes){episode.script=fixture.script;episode.audio={...fixture.view.episodes[0].audio!};}
 for(const width of [1920,1440,390]){
  await page.setViewportSize({width,height:900});await page.goto(`/materials/${materialId}/podcasts/${podcastId}`);
  await expect(page.locator('.podcast-listening-layout')).toBeVisible();
  const chat=page.getByRole('button',{name:'教材問答',exact:true});await expect(chat).toBeVisible();const box=(await chat.boundingBox())!;
  expect(width-box.x-box.width).toBeCloseTo(width===390?16:24,0);expect(900-box.y-box.height).toBeCloseTo(width===390?16:24,0);
 }
});
