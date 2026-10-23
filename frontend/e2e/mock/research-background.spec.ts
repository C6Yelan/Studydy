import { expect, test } from '@playwright/test';
import { materialId, runId, structureRevision, structureView, mockKnowledgeMapApi, json } from '../fixtures/knowledge-map';

const researchId = '55555555-5555-4555-8555-555555555555';
const topicId = '77777777-7777-4777-8777-777777777777';
// 後端只送可選或已選的來源與畫面用到的欄位。
const source = (id: string, relevance = 'direct') => ({ id, kind: 'web', title: `Agent ${id}`,
  authors: 'Fixture', year: 2026, url: 'https://example.org/' + id, license: 'CC-BY-4.0', license_url: null, eligible: true, state: 'candidate',
  review: { phase: 'body', relevance, reason: '說明工具呼叫後如何利用觀察結果決定下一步。', scope: 'sections' } });

for (const mode of ['research', 'topic']) test(`${mode} shows a short research status, cancellation and the final result`, async ({ page }) => {
  await mockKnowledgeMapApi(page, structureView());
  const progress = { stage: 'reading', completed: 1, total: 3, unit: 'sources', summary: '正在補查工具使用的具體例子。', finish_reason: null, waiting_until: null,
    topics: [{ topic: 'Agent 執行迴圈：工具呼叫', status: 'partial' }, { topic: 'Agent 執行迴圈：觀察結果', status: 'missing' }, { topic: '記憶', status: 'covered' }] };
  let work: any = { research_id: researchId, material_id: mode === 'research' ? materialId : null, query: 'Agent 原理', approved_scope:{title:'Agent',goals:['理解迴圈'],topics:['Agent 執行迴圈']},
    status: 'searching', candidates: [source('ready')], selection: [], error_code: null, run_id: null, created_at: '2026-10-15T00:00:00Z', is_current_revision: true, progress };
  await page.route(`**/v1/research/${researchId}`, r => json(r, work));
  const actions: string[] = [];
  await page.route(`**/v1/research/${researchId}/actions`, r => {
    const { action } = r.request().postDataJSON(); actions.push(action);
    work = { ...work, status: action === 'cancel' ? 'cancelled' : 'searching' }; return json(r, work);
  });
  await page.route('**/v1/topics', r => json(r, { topics: [] }));
  await page.route(`**/v1/topics/${topicId}`, r => json(r, { topic_id: topicId, request: 'Agent', version: 3, status: 'approved',
    proposal: { title: 'Agent', goals: ['工具呼叫'], topics: ['Agent 執行迴圈'] }, research_id: researchId, research: work }));
  const path = mode === 'research' ? `/materials/${materialId}/research/${researchId}` : `/topics/${topicId}`;
  await page.goto(path);
  const panel = page.getByRole('region', { name: '研究進度' });
  await expect(panel).toContainText('正在閱讀來源內容');
  await expect(panel).toContainText('1 / 3 份來源');
  await expect(panel.getByRole('status')).toHaveText('正在補查工具使用的具體例子。');
  await expect(panel).toContainText('研究會在背景繼續，可離開頁面');
  await expect(panel.getByRole('progressbar')).toHaveAttribute('value', '1');
  await expect(panel.getByRole('progressbar')).toHaveAttribute('max', '3');
  // 內部階段列、處理計數與研究詳情不再顯示。
  await expect(panel.locator('ol, details')).toHaveCount(0);
  await expect(page.getByText('研究詳情', { exact: true })).toHaveCount(0);
  await expect(page.locator('.research-candidates')).toHaveCount(0);
  await page.reload(); await expect(panel).toContainText('正在閱讀來源內容');
  await page.getByRole('button', { name: '停止處理', exact: true }).click();
  await expect(panel).toContainText('搜尋已停止，目前 1 份來源仍可選取');
  await expect(panel).toContainText('這些觀念還沒找到合適來源：Agent 執行迴圈。');
  // 進行中的說明只在研究進行時顯示；停止後不殘留舊說明。
  await expect(panel).not.toContainText('正在補查工具使用的具體例子。');
  await expect(panel.getByRole('progressbar')).toHaveCount(0);
  await page.getByRole('button', { name: mode === 'research' ? '重試未完成部分' : '接續處理', exact: true }).click();
  await expect(panel.getByRole('progressbar')).toBeVisible();
  work = { ...work, status: 'selecting', progress: { ...progress, stage: 'complete', completed: null, total: null, finish_reason: 'gaps_remaining', summary: '' } };
  await expect(panel).toContainText('找到 1 份可用來源');
  await expect(panel).toContainText('這些觀念還沒找到合適來源：Agent 執行迴圈。');
  await expect(page.locator('.research-candidates article')).toHaveCount(1);
  const first = page.locator('.research-candidates article').first();
  await first.getByRole('button', { name: '詳細資訊' }).click();
  const detail = page.locator('.source-detail-panel');
  await expect(detail.getByRole('heading', { name: /閱讀判斷/ })).toBeVisible();
  await expect(detail).toContainText('已閱讀相關段落。');
  await expect(detail).not.toContainText('存取紀錄');
  await expect(detail.getByRole('heading', { name: '授權與取得資訊' })).toHaveCount(mode === 'topic' ? 1 : 0);
  await page.keyboard.press('Escape'); await expect(detail).toHaveCount(0);
  await expect(first.getByRole('button', { name: '詳細資訊' })).toBeFocused();
  expect(actions).toEqual(['cancel', 'retry']);
  for (const size of [{ width: 1280, height: 720 }, { width: 1366, height: 768 }]) {
    await page.setViewportSize(size);
    const progressBox = await panel.boundingBox();
    expect(progressBox!.height).toBeLessThan(90);
    const list = await page.locator('.research-candidates').boundingBox();
    const card = await first.boundingBox();
    expect(card!.y + card!.height).toBeLessThanOrEqual(list!.y + list!.height + 1);
  }
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(390);
});

test('finished research without sources says so plainly', async ({ page }) => {
  await mockKnowledgeMapApi(page, structureView());
  const work = { research_id: researchId, material_id: materialId, query: 'Agent 原理', approved_scope: null, status: 'selecting', candidates: [], selection: [],
    error_code: null, run_id: null, created_at: '2026-10-15T00:00:00Z', is_current_revision: true,
    progress: { stage: 'complete', completed: null, total: null, unit: 'queries', summary: '', finish_reason: 'references_only', waiting_until: null, topics: [] } };
  await page.route(`**/v1/research/${researchId}`, r => json(r, work));
  await page.goto(`/materials/${materialId}/research/${researchId}`);
  await expect(page.getByRole('region', { name: '研究進度' })).toHaveText('找到相關資料，但都沒有可直接加入的全文');
  await expect(page.getByText('可重新確認搜尋範圍後再搜尋。', { exact: true })).toBeVisible();
});


test('prepared sources require submission and published partial results open the map without another confirmation', async ({page}) => {
  await mockKnowledgeMapApi(page, structureView());
  let work:any={research_id:researchId,material_id:materialId,query:'補充教材狀態',
    status:'ready',approved_scope:{title:'Agent',topics:['Agent 執行迴圈'],goals:['理解迴圈']},candidates:[{...source('ready'),state:'ready'}],selection:['ready'],error_code:null,run_id:null,created_at:'2026-10-15T00:00:00Z',is_current_revision:true};
  let submits=0;
  await page.route(`**/v1/research/${researchId}`, r=>json(r,work));
  await page.route(`**/v1/research/${researchId}/submit`, r=>{
    expect(r.request().postDataJSON()).toEqual({confirmed:true});submits++;
    work={...work,status:'submitted',run_id:runId,run:{status:'partial',output_binding:{knowledge_structure_revision:structureRevision}}};
    return json(r,work,202);
  });
  await page.goto(`/materials/${materialId}/research/${researchId}`);
  await expect(page.locator('.research-confirm')).toContainText('這些來源目前尚未納入知識地圖');
  const submit=page.getByRole('button',{name:'確認加入教材',exact:true});
  await expect(submit).toBeDisabled();expect(submits).toBe(0);
  await page.getByRole('checkbox',{name:'我了解加入後不可逆',exact:true}).check();await submit.click();
  await expect(page.getByRole('heading',{name:'補充教材可查看',exact:true})).toBeVisible();
  await expect(page.getByRole('heading',{name:'補充教材已建立',exact:true})).toHaveCount(0);
  await expect(page.getByText('本次補充範圍',{exact:true})).toBeVisible();
  await expect(page.getByRole('button',{name:'確認加入教材',exact:true})).toHaveCount(0);
  expect(submits).toBe(1);
  await page.getByRole('button',{name:'查看補充教材',exact:true}).click();
  await expect(page).toHaveURL(new RegExp(`/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}$`));
});

test('stopped research is preserved in collapsed history while the current task stays visible', async ({page}) => {
  await mockKnowledgeMapApi(page, structureView());
  await page.route(`**/v1/materials/${materialId}/research`,r=>json(r,{researches:[
    {research_id:researchId,query:'目前選好的三份補充資料',status:'ready',selection:['a','b','c'],run_id:null,run_status:null,candidate_count:3,created_at:'2026-10-15T00:00:00Z'},
    {research_id:topicId,query:'已停止的舊測試查詢',status:'cancelled',selection:[],run_id:null,run_status:null,candidate_count:20,created_at:'2026-10-14T00:00:00Z'},
  ]}));
  await page.goto(`/materials/${materialId}/research`);
  await expect(page.getByRole('heading',{name:'目前選好的三份補充資料',exact:true})).toBeVisible();
  await expect(page.getByText('來源已準備，但尚未納入目前知識地圖；確認加入後才會分析。',{exact:true})).toBeVisible();
  await expect(page.getByRole('heading',{name:'已停止的舊測試查詢',exact:true})).not.toBeVisible();
  await page.getByText('已停止的查詢 · 1 筆',{exact:true}).click();
  await expect(page.getByRole('heading',{name:'已停止的舊測試查詢',exact:true})).toBeVisible();
});


test('published partial delivery can retry failed sources without claiming they were included', async ({page}) => {
  await mockKnowledgeMapApi(page, structureView());
  let work:any={research_id:researchId,material_id:materialId,query:'Partial source delivery',
    status:'submitted',candidates:[{...source('ready'),state:'ready'},{...source('failed'),state:'failed',error_code:'RESEARCH_DOWNLOAD_FAILED'}],
    selection:['ready','failed'],error_code:'RESEARCH_ITEMS_FAILED',run_id:'33333333-3333-4333-8333-333333333333',created_at:'2026-10-15T00:00:00Z',run:{status:'succeeded',output_binding:null}};
  await page.route(`**/v1/research/${researchId}`,r=>json(r,work));
  await page.route(`**/v1/research/${researchId}/actions`,r=>{
    expect(r.request().postDataJSON()).toEqual({action:'retry'});
    work={...work,status:'acquiring',run_id:null,run:null};return json(r,work);
  });
  await page.goto(`/materials/${materialId}/research/${researchId}`);
  await expect(page.getByText('已將 1 份來源加入教材，可查看更新後的知識地圖。',{exact:true})).toBeVisible();
  await page.getByRole('button',{name:'重試未完成來源',exact:true}).click();
  await expect(page.getByRole('button',{name:'停止處理',exact:true})).toBeVisible();
});
