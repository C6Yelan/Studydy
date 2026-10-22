import { expect, test } from '@playwright/test';
import { materialId, runId, structureRevision, structureView, mockKnowledgeMapApi, json } from '../fixtures/knowledge-map';

const researchId = '55555555-5555-4555-8555-555555555555';
const topicId = '77777777-7777-4777-8777-777777777777';
const source = (id: string, relevance = 'direct', eligible = true) => ({ id, kind: 'web', title: id === 'unrelated' ? 'C++ memory' : `Agent ${id}`,
  authors: 'Fixture', year: 2026, url: 'https://example.org/' + id, doi: null, license: eligible ? 'CC-BY-4.0' : 'unknown',
  license_url: null, version: '1', eligible, reason: eligible ? '已取得正文' : '已讀取公開內容，匯入條件待確認。', state: 'candidate',
  review: { phase: 'body', relevance, reason: '說明工具呼叫後如何利用觀察結果決定下一步。', topics: [0] },
  access: { status: 'read', method: 'rss', attempts: [{ url: 'https://example.org/' + id, outcome: 'failed', http_status: 403, error_code: 'RESEARCH_SOURCE_BLOCKED' }, { url: 'https://example.org/feed', outcome: 'read', method: 'rss' }] } });

for (const mode of ['research', 'topic']) test(`${mode} shows durable research stages, cancellation and curated references`, async ({ page }) => {
  await mockKnowledgeMapApi(page, structureView());
  const progress = { stage: 'reading', round: 2, completed: 1, total: 3, unit: 'sources', candidate_count: 4,
    read_count: 1, recommended_count: 1, importable_count: 1, summary: '正在補查工具使用的具體例子。', warnings: [], finish_reason: null,
    topics: [{ topic: 'Agent 執行迴圈', status: 'partial', reason: '缺少完整案例。', source_ids: [] }],
    unavailable_sources: [{ id: 'blocked', title: '未完成的來源', url: 'https://example.org/blocked', access: { status: 'unavailable',
      attempts: [{ url: 'https://example.org/' + 'long-path-'.repeat(30), outcome: 'failed', http_status: 403, error_code: 'RESEARCH_SOURCE_BLOCKED' }] } }] };
  let work: any = { research_id: researchId, material_id: mode === 'research' ? materialId : null, query: 'Agent 原理', mode: 'review', approved_scope:{title:'Agent',goals:['理解迴圈'],topics:['Agent 執行迴圈']},
    status: 'searching', candidates: [source('ready'), { ...source('reference', 'related', false), screening: { decision: 'reference', reason: '提供背景概念', basis: 'abstract' }, review: { phase: 'metadata', relevance: 'related', reason: '摘要說明背景概念', topics: [0] } }, source('unrelated', 'unrelated', false), { ...source('duplicate', 'direct', false), duplicate_of: 'ready' }],
    selection: [], cursor: null, error_code: null, run_id: null, is_current_revision: true, progress };
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
  const panel = page.getByRole('region', { name: '背景研究進度' });
  await expect(panel).toContainText('第 2 輪');
  await expect(panel.locator('ol')).not.toBeVisible();
  await panel.getByText('研究詳情', { exact: true }).click();
  await expect(panel.locator('ol > li')).toHaveCount(5);
  await panel.getByText('研究詳情', { exact: true }).click();
  await expect(panel.getByRole('progressbar')).toHaveAttribute('value', '1');
  await expect(panel.getByRole('progressbar')).toHaveAttribute('max', '3');
  await expect(page.locator('.research-candidates')).toHaveCount(0);
  await page.reload(); await expect(panel).toContainText('第 2 輪');
  await page.getByRole('button', { name: '停止處理', exact: true }).click();
  await expect(panel).toContainText('研究停在');
  await page.getByRole('button', { name: mode === 'research' ? '重試未完成部分' : '接續處理', exact: true }).click();
  await expect(panel.getByRole('progressbar')).toBeVisible();
  work = { ...work, status: 'selecting', progress: { ...progress, stage: 'complete', completed: null, total: null, finish_reason: 'gaps_remaining', summary: '已整理可用來源，仍缺少案例。' } };
  await expect(panel).toContainText('研究已完成，可選擇要加入的來源');
  await expect(page.locator('.research-candidates article')).toHaveCount(1);
  await expect(page.getByRole('checkbox', { name: '選取 Agent reference', exact: true })).toHaveCount(0);
  await expect(page.getByText('Agent reference', { exact: true })).not.toBeVisible();
  await page.locator('.source-other-results > summary').click();
  await expect(page.getByText('Agent reference', { exact: true })).toBeVisible();
  await expect(page.locator('.source-other-results')).toContainText('尚未核對正文');
  await page.locator('.source-other-results > summary').click();
  await expect(page.getByText('C++ memory', { exact: true })).not.toBeVisible();
  const first = page.locator('.research-candidates article').first();
  await first.getByText('查看存取紀錄', { exact: true }).click();
  await expect(first).toContainText('403'); await expect(first).toContainText('已取得（rss）');
  await expect(page.getByRole('button', { name: '依缺口繼續研究', exact: true })).toBeVisible();
  expect(actions).toEqual(['cancel', 'retry']);
  await first.getByText('查看存取紀錄', { exact: true }).click();
  for (const size of [{ width: 1280, height: 720 }, { width: 1366, height: 768 }]) {
    await page.setViewportSize(size);
    const progressBox = await panel.boundingBox();
    expect(progressBox!.height).toBeLessThan(90);
    const list = await page.locator('.research-candidates').boundingBox();
    const card = await first.boundingBox();
    expect(card!.y + card!.height).toBeLessThanOrEqual(list!.y + list!.height + 1);
  }
  await page.setViewportSize({ width: 390, height: 844 });
  await panel.getByText('研究詳情', { exact: true }).click();
  await expect(panel).not.toContainText('此入口回應 403');
  await expect(panel).toContainText('本次尚未完整涵蓋：Agent 執行迴圈。');
  await expect(panel).toContainText('本輪排定的查找與核對已結束。');
  await expect(panel).not.toContainText('因此研究結束');
  await expect(panel.locator('.research-progress-counts')).not.toContainText('／');
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(390);
  await panel.scrollIntoViewIfNeeded();
});


test('prepared sources require submission and published partial results open the map without another confirmation', async ({page}) => {
  await mockKnowledgeMapApi(page, structureView());
  let work:any={research_id:researchId,material_id:materialId,query:'補充教材狀態',mode:'review',
    status:'ready',approved_scope:{title:'Agent',topics:['Agent 執行迴圈'],goals:['理解迴圈']},candidates:[{...source('ready'),state:'ready'}],selection:['ready'],cursor:null,error_code:null,run_id:null,is_current_revision:true};
  let submits=0;
  await page.route(`**/v1/research/${researchId}`, r=>json(r,work));
  await page.route(`**/v1/research/${researchId}/submit`, r=>{
    expect(r.request().postDataJSON()).toEqual({confirmed:true});submits++;
    work={...work,status:'submitted',run_id:runId,run:{status:'partial',error_code:null,output_binding:{knowledge_structure_revision:structureRevision}}};
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


test('focused followup preserves the confirmed scope and sends the optional direction', async ({page}) => {
  await mockKnowledgeMapApi(page, structureView());
  const scope={title:'Agent',goals:['理解迴圈，不需要完整程式'],topics:['Agent 執行迴圈']};
  let work:any={research_id:researchId,material_id:materialId,query:'Agent',mode:'review',approved_scope:scope,
    status:'selecting',candidates:[source('ready')],selection:[],cursor:null,error_code:null,run_id:null,is_current_revision:true};
  await page.route(`**/v1/research/${researchId}`,r=>json(r,work));
  await page.route(`**/v1/research/${researchId}/actions`,r=>{
    expect(r.request().postDataJSON()).toEqual({action:'more',guidance:'只補停止條件的官方說明'});
    work={...work,status:'searching'};return json(r,work);
  });
  await page.goto(`/materials/${materialId}/research/${researchId}`);
  await page.getByText('調整補查方向',{exact:true}).click();
  await page.getByLabel('補查方向（選填）').fill('只補停止條件的官方說明');
  await page.getByRole('button',{name:'依缺口繼續研究',exact:true}).click();
  await expect(page.getByRole('button',{name:'停止處理',exact:true})).toBeVisible();
  expect(work.approved_scope).toEqual(scope);
});


test('published partial delivery can retry failed sources without claiming they were included', async ({page}) => {
  await mockKnowledgeMapApi(page, structureView());
  let work:any={research_id:researchId,material_id:materialId,query:'Partial source delivery',mode:'review',
    status:'submitted',candidates:[{...source('ready'),state:'ready'},{...source('failed'),state:'failed',error_code:'RESEARCH_DOWNLOAD_FAILED'}],
    selection:['ready','failed'],cursor:null,error_code:'RESEARCH_ITEMS_FAILED',run_id:'33333333-3333-4333-8333-333333333333',run:{status:'succeeded',error_code:null}};
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
