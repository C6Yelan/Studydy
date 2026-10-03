import { expect, test, type Page } from "@playwright/test";
import type { PodcastView } from "../../src/api/contracts";
import { artifactId, materialId, runId, sessionId, structureRevision, structureView, mockKnowledgeMapApi, json } from "../fixtures/knowledge-map";

const podcastId = "77777777-7777-4777-8777-777777777777";
async function mockPodcasts(page: Page) {
  const structure = structureView();
  await mockKnowledgeMapApi(page, structure);
  const claims = structure.concepts.map(c => ({ ...c.claims[0], concept_id: c.concept_id, label: c.label }));
  const labels = ["TCP 連線（Connection）", "TCP 三向握手 / 確認號"];
  claims.forEach((claim, i) => { claim.label = labels[i]; claim.text = "合成測試重點，保留必要的條件與數值。".repeat(20); });
  const episode = () => ({ delivery: "solo" as const, claims, script: null, audio: null });
  const view: PodcastView = {
    schema: "podcast/v1", podcast_id: podcastId, material_id: materialId,
    material_name: "資料結構講義.pdf", knowledge_structure_revision: structureRevision,
    name: "我的 Podcast", mode: "quick", delivery: "solo", concept_ids: claims.map(c => c.concept_id),
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
  await page.getByRole("button", { name: "此教材的 Podcast", exact: true }).click();
  await page.getByRole("button", { name: "管理 Podcast「更新後的名稱」" }).click();
  await page.getByRole("button", { name: "刪除 Podcast", exact: true }).click();
  await page.getByRole("dialog").getByRole("button", { name: "取消", exact: true }).click();
  expect(fixture.deletes()).toBe(0);
  await page.getByRole("button", { name: "管理 Podcast「更新後的名稱」" }).click();
  await page.getByRole("button", { name: "刪除 Podcast", exact: true }).click();
  await page.getByRole("button", { name: "確認刪除 Podcast" }).click();
  await expect(page.getByRole("heading", { name: "讓教材說給你聽" })).toBeVisible();
  expect(fixture.deletes()).toBe(1);
  expect(await page.evaluate(key => localStorage.getItem(key), positionKey)).toBeNull();
});

test("progress follows saved stages and transcripts; episode numbers and header stay visible", async ({ page }) => {
  const fixture = await mockPodcasts(page);
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto(`/podcasts/${podcastId}`);
  const progress = page.getByRole("region", { name: "Podcast 生成進度" });
  await expect(progress.getByRole("status")).toHaveText("第 2 / 3 集 · 整理內容");
  fixture.view.episodes[1].script = fixture.script;
  await expect(progress.getByRole("status")).toHaveText("第 2 / 3 集 · 製作音訊");
  await expect(page.getByRole("tabpanel", { name: "逐字稿" })).toContainText("這是合成的教材講解。");
  const list = page.getByRole("complementary", { name: "分集清單" });
  await expect(list.locator("strong").first()).toHaveText("TCP 連線（Connection） · TCP 三向握手 / 確認號");
  await expect(list.locator("strong").first()).toHaveAttribute("title", "TCP 連線（Connection） · TCP 三向握手 / 確認號");
  for (const row of await list.getByRole("button").all()) expect((await row.boundingBox())!.height).toBe(44);
  await list.getByRole("button").nth(1).click();
  await expect(list.getByRole("button").nth(1)).toHaveAttribute("aria-current", "true");
  const number = list.locator(".podcast-episode-number").nth(1);
  expect((await number.boundingBox())!.height).toBeLessThan(30);
  await page.evaluate(() => window.scrollTo(0, 600));
  expect(Math.round((await page.locator(".app-header").boundingBox())!.y)).toBe(0);
  fixture.view.status = "failed";
  await expect(progress.getByRole("heading", { name: "生成中斷" })).toBeVisible();
  await expect(progress).toContainText("已暫停");
  await expect(progress.getByRole("button", { name: "接續生成" })).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(390);
  await page.evaluate(() => window.scrollTo(0, 500));
  expect(Math.round((await page.locator(".app-header").boundingBox())!.y)).toBe(0);
});

test("creation keeps material and name together and reflows on mobile", async ({ page }) => {
  await mockPodcasts(page);
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/podcasts/new");
  await page.getByLabel("選擇教材").selectOption(materialId);
  const material = page.getByLabel("選擇教材"), name = page.getByLabel("Podcast 名稱", { exact: true });
  await expect(name).toBeVisible();
  expect(Math.abs((await material.boundingBox())!.y - (await name.boundingBox())!.y)).toBeLessThan(2);
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(390);
  await name.fill("手機建立測試");
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
  expect(navBox.y).toBeGreaterThanOrEqual(searchBox.y + searchBox.height);
  await expect(page.getByRole('heading', { name: '別的教材 Podcast' })).toHaveCount(0);
  await page.getByRole('button', { name: '查看進度', exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`/materials/${materialId}/podcasts/${podcastId}$`));
  await expect(page.locator('.app-sidebar')).toHaveCount(0);
  await expect(page.getByRole('tablist', { name: '教材學習內容' })).toBeVisible();
  await page.getByRole('button', { name: '此教材的 Podcast', exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`/materials/${materialId}/podcasts$`));
  await page.getByRole('tablist', { name: '教材學習內容' }).getByRole('tab', { name: '概念卡', exact: true }).click();
  await expect(page.locator('.library-item')).toHaveCount(1);
  await expect(page.getByRole('heading', { name: '此教材卡組' })).toBeVisible();
  const cardsNav = (await page.getByRole('tablist', { name: '教材學習內容' }).boundingBox())!;
  const cardsSearch = (await page.getByRole('searchbox', { name: '搜尋卡組', exact: true }).boundingBox())!;
  expect(cardsNav.y).toBeGreaterThanOrEqual(cardsSearch.y + cardsSearch.height);
  await page.getByRole('searchbox', { name: '搜尋卡組', exact: true }).fill('不存在的卡組');
  await expect(page.locator('.library-item')).toHaveCount(0);
  await page.getByRole('searchbox', { name: '搜尋卡組', exact: true }).fill('');
  await page.getByRole('button', { name: '建立卡組', exact: true }).click();
  await expect(page.getByLabel('選擇教材')).toHaveValue(materialId);
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
  await page.getByRole('button', { name: '播放', exact: true }).click();
  await expect(page.getByRole('button', { name: '暫停', exact: true })).toBeVisible();
  await page.getByRole('button', { name: '前進 10 秒', exact: true }).click();
  await expect.poll(() => page.locator('audio').evaluate((a: HTMLAudioElement) => a.currentTime)).toBeGreaterThan(9);
  await page.getByRole('button', { name: '暫停', exact: true }).click();
  await page.getByLabel('自動播放下一集').uncheck();
  await page.locator('.media-player').focus();
  const original = await page.locator('audio').evaluate((a: HTMLAudioElement) => ({ time: a.currentTime, paused: a.paused, muted: a.muted }));
  for (const key of ['k', 'j', 'l', 'm']) await page.keyboard.press(key);
  expect(await page.locator('audio').evaluate((a: HTMLAudioElement) => ({ time: a.currentTime, paused: a.paused, muted: a.muted }))).toEqual(original);
  await page.getByRole('button', { name: '播放速度', exact: true }).click();
  await expect(page.getByRole('group', { name: '播放速度選單' }).getByRole('checkbox')).toHaveCount(0);
  await page.getByRole('button', { name: '1.5×', exact: true }).click();
  expect(await page.locator('audio').evaluate((a: HTMLAudioElement) => a.playbackRate)).toBe(1.5);
  await page.keyboard.press('Escape');
  await page.getByRole('button', { name: '下一集', exact: true }).click();
  await expect(page.getByRole('region', { name: '第 2 集播放器' })).toBeVisible();
  await expect(page.getByLabel('自動播放下一集')).not.toBeChecked();
  await page.locator('audio').evaluate((a: HTMLAudioElement) => { a.pause(); a.dispatchEvent(new Event('ended')); });
  await expect(page.getByRole('region', { name: '第 2 集播放器' })).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(390);
  const buttons = page.locator('.media-transport button');
  let end = 0;
  for (const button of await buttons.all()) { const box = (await button.boundingBox())!; expect(box.width).toBeGreaterThanOrEqual(40); expect(box.x).toBeGreaterThanOrEqual(end); end = box.x + box.width; }
  await page.getByRole('button', { name: '播放速度', exact: true }).click();
  const panel = (await page.getByRole('group', { name: '播放速度選單' }).boundingBox())!;
  expect(panel.x).toBeGreaterThanOrEqual(0); expect(panel.x + panel.width).toBeLessThanOrEqual(390);
});

test("podcast creation separates recommendations, selection and settings without losing choices", async ({ page }) => {
  const fixture = await mockPodcasts(page);
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto('/podcasts/new');
  await page.getByLabel('選擇教材').selectOption(materialId);
  const recommend = page.getByRole('region', { name: '幫我選概念', exact: true });
  const selection = page.getByRole('region', { name: '選擇 Podcast 概念' });
  const settings = page.getByRole('region', { name: '講解設定', exact: true });
  await expect(recommend).toBeVisible(); await expect(selection).toBeVisible(); await expect(settings).toBeVisible();
  expect((await recommend.boundingBox())!.x).toBeLessThan((await selection.boundingBox())!.x);
  expect((await selection.boundingBox())!.x).toBeLessThan((await settings.boundingBox())!.x);
  await recommend.getByLabel('最多概念數').fill('1');
  await recommend.getByRole('button', { name: '套用推薦', exact: true }).click();
  await expect(selection).toContainText('已選 1 / 2');
  await page.setViewportSize({ width: 390, height: 844 });
  const controls = page.getByRole('navigation', { name: 'Podcast 設定區域' });
  await controls.getByRole('button', { name: '講解設定', exact: true }).click();
  await expect(settings).toBeVisible(); await expect(selection).toBeHidden();
  await settings.getByLabel('單人解說', { exact: false }).check();
  await settings.getByRole('button', { name: '完整講解', exact: false }).click();
  await controls.getByRole('button', { name: '選擇概念', exact: true }).click();
  await expect(selection).toContainText('已選 1 / 2');
  await selection.getByLabel('只看已選').uncheck();
  await selection.getByRole('button', { name: '取消全選', exact: true }).click();
  await expect(page.getByRole('button', { name: '開始生成 Podcast' })).toBeDisabled();
  const first = fixture.structure.concepts[0];
  await selection.getByRole('searchbox').fill(first.label);
  await selection.getByRole('button', { name: '選取搜尋結果', exact: true }).click();
  await expect(selection).toContainText('已選 1 / 2');
  await selection.getByRole('button', { name: `查看「${first.label}」重點` }).click();
  await expect(page.getByRole('region', { name: `${first.label}重點預覽` })).toBeVisible();
  await page.getByRole('button', { name: '關閉預覽', exact: true }).click();
  await page.getByLabel('Podcast 名稱', { exact: true }).fill('三區操作測試');
  let body: any;
  await page.route(`**/v1/materials/${materialId}/podcasts`, route => {
    body = route.request().postDataJSON();
    const claims = fixture.view.episodes[0].claims.filter(c => body.concept_ids.includes(c.concept_id));
    Object.assign(fixture.view, { name: body.name, mode: body.mode, delivery: body.delivery, concept_ids: body.concept_ids, status: 'pending', episode_count: 1, completed_episodes: 0,
      episodes: [{ delivery: body.delivery, claims, script: null, audio: null }] });
    return json(route, fixture.view, 201);
  });
  await page.getByRole('button', { name: '開始生成 Podcast' }).click();
  await expect(page).toHaveURL(new RegExp(`/materials/${materialId}/podcasts/${podcastId}$`));
  expect(body.concept_ids).toEqual([first.concept_id]); expect(body.delivery).toBe('solo'); expect(body.mode).toBe('full');
  await expect(page.getByRole('tablist', { name: '教材學習內容' })).toBeVisible();
});

test("saved dialogue transcripts keep roles and switch with the selected episode", async ({ page }) => {
  const fixture = await mockPodcasts(page);
  fixture.view.delivery = 'dialogue';
  for (const episode of fixture.view.episodes) episode.delivery = 'dialogue';
  const claims = fixture.view.episodes[0].claims;
  fixture.view.episodes[0].script = { provider: 'synthetic', segments: [
    { claim_id: claims[0].claim_id, turns: [{ speaker: 'guest', text: '第一集：8 MB 與 8 Mb 不同。' }] },
    { claim_id: claims[1].claim_id, turns: [{ speaker: 'host', text: '這個條件要保留。\n不能略過。' }] },
  ] };
  fixture.view.episodes[1].script = { provider: 'synthetic', segments: [
    { claim_id: claims[0].claim_id, turns: [{ speaker: 'host', text: '第二集的疑問。' }] },
    { claim_id: claims[1].claim_id, turns: [{ speaker: 'guest', text: '第二集的說明。' }] },
  ] };
  await page.goto(`/podcasts/${podcastId}`);
  const first = page.getByRole('tabpanel', { name: '逐字稿' });
  await expect(first.locator('.podcast-transcript-speaker')).toHaveText(['講解者', '學習者']);
  await expect(first.locator('li p')).toHaveText(['第一集：8 MB 與 8 Mb 不同。', '這個條件要保留。\n不能略過。']);
  await page.getByRole('tab', { name: '本集重點', exact: true }).click();
  await expect(first).toBeHidden();
  await expect(page.getByRole('tabpanel', { name: '本集重點' })).toContainText('合成測試重點');
  await page.getByRole('tab', { name: '逐字稿', exact: true }).click();
  await expect(first).toBeVisible();
  await page.keyboard.press('ArrowRight');
  await expect(page.getByRole('tab', { name: '本集重點', exact: true })).toBeFocused();
  await page.keyboard.press('ArrowLeft');
  await expect(first).toBeVisible();
  await page.getByRole('complementary', { name: '分集清單' }).getByRole('button').nth(1).click();
  const second = page.getByRole('tabpanel', { name: '逐字稿' });
  await expect(second).toContainText('第二集的說明。');
  await expect(second).not.toContainText('第一集');
  await page.getByRole('complementary', { name: '分集清單' }).getByRole('button').nth(2).click();
  await expect(page.getByRole('tabpanel', { name: '逐字稿' })).toContainText('尚未生成');
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(390);
});

test('synchronized scenes follow real media pause, seek and playback rate',async({page})=>{
 const fixture=await mockPodcasts(page);fixture.view.status='ready';fixture.view.completed_episodes=3;
 for(const episode of fixture.view.episodes){episode.script=fixture.script;episode.audio={...fixture.view.episodes[0].audio!,duration_seconds:12};}
 const wav=Buffer.alloc(44+24000*12*2);wav.write('RIFF');wav.writeUInt32LE(wav.length-8,4);wav.write('WAVEfmt ',8);wav.writeUInt32LE(16,16);wav.writeUInt16LE(1,20);wav.writeUInt16LE(1,22);wav.writeUInt32LE(24000,24);wav.writeUInt32LE(48000,28);wav.writeUInt16LE(2,32);wav.writeUInt16LE(16,34);wav.write('data',36);wav.writeUInt32LE(wav.length-44,40);
 await page.route(`**/v1/podcasts/${podcastId}/episodes/*/audio`,r=>{const range=r.request().headers()['range'];const m=range?.match(/bytes=(\d+)-(\d*)/);const start=m?Number(m[1]):0,end=m&&m[2]?Math.min(Number(m[2]),wav.length-1):wav.length-1;return r.fulfill({status:m?206:200,contentType:'audio/wav',headers:{'Accept-Ranges':'bytes',...(m?{'Content-Range':`bytes ${start}-${end}/${wav.length}`}:{})},body:wav.subarray(start,end+1)});});
 const manifest={audio_sha256:'a'.repeat(64),duration:12,source_resolver:fixture.structure.source_resolver,scenes:fixture.view.episodes[0].claims.map((c,i)=>({index:i,start:i*6,end:(i+1)*6,claim_id:c.claim_id,title:c.label,text:c.text,evidence:c.evidence,kind:i?'flow':'concept',steps:i?['來源步驟一','來源步驟二']:[],columns:[]}))};
 await page.route(`**/v1/podcasts/${podcastId}/scenes`,r=>json(r,{podcast_id:podcastId,episodes:[0,1,2].map(index=>({index,status:'ready',version:1,error_code:null,manifest}))}));
 await page.goto(`/podcasts/${podcastId}`);await page.getByRole('tab',{name:'同步畫面',exact:true}).click();await expect(page.locator('.synced-scene')).toHaveAttribute('data-scene-index','0');
 await page.getByRole('button',{name:'播放',exact:true}).click();await expect.poll(()=>page.locator('audio').evaluate((a:HTMLAudioElement)=>a.currentTime)).toBeGreaterThan(.15);
 await page.getByRole('button',{name:'暫停',exact:true}).click();const paused=await page.locator('audio').evaluate((a:HTMLAudioElement)=>a.currentTime);await page.waitForTimeout(350);expect(Math.abs(Number(await page.locator('.synced-scene').getAttribute('data-media-time'))-paused)).toBeLessThan(.1);
 await page.locator('audio').evaluate((a:HTMLAudioElement)=>{a.currentTime=7;});await expect(page.locator('.synced-scene')).toHaveAttribute('data-scene-index','1');await expect(page.locator('.scene-flow')).toBeVisible();
 await page.getByRole('button',{name:'播放速度',exact:true}).click();await page.getByRole('button',{name:'2×',exact:true}).click();await page.getByRole('button',{name:'播放',exact:true}).click();await page.waitForTimeout(450);
 const actual=await page.locator('audio').evaluate((a:HTMLAudioElement)=>({time:a.currentTime,rate:a.playbackRate}));expect(actual.rate).toBe(2);expect(Math.abs(Number(await page.locator('.synced-scene').getAttribute('data-media-time'))-actual.time)).toBeLessThan(.25);
 await page.locator('.scene-sources summary').click();await expect.poll(()=>page.locator('audio').evaluate((a:HTMLAudioElement)=>a.paused)).toBe(true);
 await page.getByRole('button',{name:'播放速度',exact:true}).click();await page.getByLabel('跳至同步段落').selectOption('0');await expect(page.locator('.synced-scene')).toHaveAttribute('data-scene-index','0');
 await page.setViewportSize({width:390,height:844});expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
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
