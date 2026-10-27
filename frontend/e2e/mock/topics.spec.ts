import {expect,test,type Page} from '@playwright/test';
import {materialId,runId,structureRevision,structureView,mockKnowledgeMapApi,json,run as runFixture} from '../fixtures/knowledge-map';
const id='77777777-7777-4777-8777-777777777777';
const rid='88888888-8888-4888-8888-888888888888';
const plan={title:'TCP 入門',goals:['理解握手，不討論進階壅塞演算法'],topics:['SYN、SYN-ACK、ACK']};
const candidate={id:'source',kind:'official',title:'TCP handshake',authors:'Mozilla Contributors',year:null,url:'https://developer.mozilla.org/en-US/docs/Glossary/TCP_handshake',license:'CC-BY-SA',license_url:null,eligible:true,state:'candidate'};

async function mockTopicApi(page:Page) {
 await mockKnowledgeMapApi(page,structureView());
 await page.route('**/v1/materials',r=>json(r,{schema:'material-library/v1',materials:[]}));
}

for(const width of [1920,1440,390]) test(`scope workflow at ${width}px`,async({page})=>{
 await page.setViewportSize({width,height:width===390?844:1000});
 await mockTopicApi(page);let approved=0,created=0;let topic:any={topic_id:id,request:'TCP',proposal:plan,version:2,status:'ready',research_id:null,error_code:null};
 await page.route('**/v1/topics',r=>r.request().method()==='POST'?json(r,topic,202):json(r,{topics:[topic]}));
 await page.route(`**/v1/topics/${id}`,r=>json(r,topic));
 await page.route(`**/v1/material-processing-runs/${runId}`,r=>{const done=topic.research?.run?.status==='succeeded';return json(r,{...runFixture,status:done?'succeeded':'pending',progress_stage:done?'completed':'queued',completed_pages:done?2:0,output_binding:done?runFixture.output_binding:null,completed_at:done?runFixture.completed_at:null});});
 await page.route(`**/v1/topics/${id}/approve`,r=>{const body=r.request().postDataJSON();expect(body.expected_version).toBe(2);expect(body.proposal).toEqual(plan);approved++;topic={...topic,proposal:body.proposal,version:3,status:'approved',research_id:rid,research:{research_id:rid,material_id:null,query:'TCP',approved_scope:plan,status:'selecting',candidates:[candidate],selection:[],error_code:null,run_id:null}};return json(r,topic);});
 await page.route(`**/v1/topics/${id}/material`,r=>{expect(r.request().postDataJSON()).toEqual({selected:['source']});created++;topic.research={...topic.research,material_id:materialId,selection:['source'],status:'submitted',run_id:runId,run:{status:'pending',output_binding:null}};return json(r,{material_id:materialId,research_id:rid},202);});
 await page.goto('/topics');await expect(page.getByRole('heading',{name:'你想學什麼？'})).toBeVisible();
 const workspace=await page.locator('.topic-request').boundingBox();
 expect(workspace!.width).toBeGreaterThan(width===390?300:1000);
 await page.getByLabel('想學的主題').fill('TCP');await page.getByRole('button',{name:'產生學習範圍'}).click();await expect(page.getByRole('heading',{name:/確認學習範圍/})).toBeVisible();expect(approved).toBe(0);expect(created).toBe(0);
 await expect(page.getByRole('complementary',{name:'對照學習需求'})).toContainText('原始學習需求TCP');
 await expect(page.locator('.topic-scope-confirm-actions').getByRole('button',{name:'確認範圍並搜尋'})).toBeVisible();
 await page.getByRole('button',{name:'確認範圍並搜尋'}).click();
 await expect(page).toHaveURL(/\/materials$/);await expect(page.getByRole('heading',{name:'我的教材',exact:true})).toBeVisible();
 const preparing=page.getByRole('article',{name:'TCP 入門',exact:true});await expect(preparing).toContainText('等待選取來源');
 await page.reload();await preparing.getByRole('button',{name:'選取來源',exact:true}).click();
 await expect(page).toHaveURL(new RegExp(`/topics/${id}$`));await expect(page.getByRole('checkbox',{name:'選取 TCP handshake'})).toBeVisible();expect(approved).toBe(1);expect(created).toBe(0);
 await page.getByRole('checkbox',{name:'選取 TCP handshake'}).check();await page.getByRole('button',{name:'建立教材與知識地圖'}).click();
 await expect(page.getByRole('heading',{name:'等待開始處理',exact:true})).toBeVisible();
 await expect(page.locator('.learning-steps [aria-current=step]')).toContainText('建立教材');
 topic.research.run={status:'succeeded',error_code:null,output_binding:{knowledge_structure_revision:structureRevision}};
 await page.reload();await expect(page.getByRole('button',{name:'開啟知識地圖',exact:true})).toBeVisible();expect(created).toBe(1);await expect(page.locator('.run-actions').getByRole('button',{name:'開啟知識地圖',exact:true})).toBeVisible();
 await page.reload();await expect(page.getByRole('button',{name:'開啟知識地圖',exact:true})).toBeVisible();expect(created).toBe(1);expect(approved).toBe(1);await expect(page.getByLabel('想學的主題')).toHaveCount(0);await expect(page.getByRole('checkbox',{name:'選取 TCP handshake'})).toHaveCount(0);
 await page.getByRole('button',{name:'開啟知識地圖',exact:true}).click();await expect(page).toHaveURL(new RegExp(`/runs/${runId}/knowledge-structures/`));
});

test('empty source search offers no fake map or enabled create action',async({page})=>{
 await mockTopicApi(page);const topic={topic_id:id,request:'空結果',proposal:plan,version:3,status:'approved',research_id:rid,error_code:null,research:{research_id:rid,material_id:null,query:'空結果',approved_scope:plan,status:'selecting',candidates:[],selection:[],error_code:null,run_id:null}};
 await page.route('**/v1/topics',r=>json(r,{topics:[topic]}));await page.route(`**/v1/topics/${id}`,r=>json(r,topic));
 await page.goto(`/topics/${id}`);await expect(page.getByText('沒有找到可加入的來源，可用「重新規劃」調整學習範圍後再搜尋。')).toBeVisible();await expect(page.getByRole('button',{name:'建立教材與知識地圖'})).toBeDisabled();await expect(page.getByRole('button',{name:'開啟知識地圖',exact:true})).toHaveCount(0);
});

for(const width of [1920,1440,390]) test(`saved planning states reopen from their links at ${width}px`,async({page})=>{
 await page.setViewportSize({width,height:width===390?844:1000});
 await mockTopicApi(page);
 const topics=['pending','failed','cancelled'].map((status,index)=>({topic_id:`77777777-7777-4777-8777-77777777777${index}`,request:`測試規劃 ${status}`,proposal:null,version:1,status,research_id:null,error_code:null}));
 await page.route('**/v1/topics',r=>json(r,{topics}));
 for(const topic of topics) await page.route(`**/v1/topics/${topic.topic_id}`,r=>json(r,topic));
 await page.goto('/topics');
 await expect(page.getByLabel('想學的主題')).toHaveValue('');
 for(const topic of topics){
  await page.goto(`/topics/${topic.topic_id}`);
  await expect(page.getByLabel('想學的主題')).toHaveValue(topic.request);
  await expect(page.locator('.learning-steps [aria-current=step]')).toContainText('學習需求');
  if(topic.status==='pending'){
   await expect(page.getByLabel('想學的主題')).toBeDisabled();
   await expect(page.getByRole('button',{name:'重新規劃主題'})).toBeDisabled();
   await expect(page.getByRole('button',{name:'取消這個規劃'})).toBeEnabled();
  }else if(topic.status==='failed') await expect(page.getByRole('button',{name:'重試規劃'})).toBeEnabled();
  else await expect(page.getByRole('button',{name:'重新規劃主題'})).toBeEnabled();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
 }
});

for(const width of [1920,1440,390]) test(`scope review edits preserve payload and keyboard focus at ${width}px`,async({page})=>{
 await page.setViewportSize({width,height:width===390?844:1000});
 await mockTopicApi(page);
 const proposal={title:'RAG 入門原理',topics:['RAG 整體架構','文件載入與切分','嵌入向量','相似度檢索'],goals:['說明 RAG 的基本概念與用途','理解文件切分、向量化、檢索與生成的關係']};
 let topic:any={topic_id:id,request:'學習 RAG',proposal,version:2,status:'ready',research_id:null,error_code:null};
 let attempts=0,reads=0;const keys:string[]=[];
 await page.route('**/v1/topics',r=>json(r,{topics:[topic]}));
 await page.route(`**/v1/topics/${id}`,r=>{reads++;return json(r,topic);});
 await page.route(`**/v1/topics/${id}/approve`,r=>{
  attempts++;keys.push(r.request().headers()['idempotency-key']);
  expect(r.request().postDataJSON()).toEqual({expected_version:2,proposal:{title:'我的 RAG 教材',topics:['RAG 應用架構','文件載入與切分','嵌入向量','檢索與生成'],goals:['解釋 RAG 架構','比較檢索方法，不討論模型訓練']}});
  if(attempts===1)return json(r,{schema:'api-error/v1',request_id:id,reason_code:'INTERNAL_ERROR',retryable:true,message:'Retry'},500);
  topic={...topic,proposal:r.request().postDataJSON().proposal,status:'approved',version:3,research_id:rid,research:{research_id:rid,material_id:null,status:'searching',query:'RAG',candidates:[],selection:[],error_code:null,run_id:null}};
  return json(r,topic);
 });
 await page.goto(`/topics/${id}`);
 const title=page.getByLabel('教材名稱',{exact:true});await expect(title).toHaveValue(proposal.title);await expect(title).toHaveAttribute('maxlength','180');
 await expect(page.getByLabel('學習目標 2',{exact:true})).toHaveValue(proposal.goals[1]);
 await expect(page.getByRole('button',{name:/^(編輯|完成)/})).toHaveCount(0);
 await title.fill('');await expect(page.getByText('請填寫教材名稱。')).toBeVisible();await expect(page.getByRole('button',{name:'確認範圍並搜尋'})).toBeDisabled();
 await title.fill('我的 RAG 教材');

 await page.getByLabel('觀念名稱 1',{exact:true}).fill('RAG 應用架構');
 await page.getByRole('button',{name:'移除主題 4',exact:true}).click();await expect(page.getByLabel('觀念名稱 4',{exact:true})).toHaveCount(0);await expect(page.getByLabel('觀念名稱 3',{exact:true})).toBeFocused();
 await page.getByRole('button',{name:'新增主題',exact:true}).click();await expect(page.getByLabel('觀念名稱 4',{exact:true})).toBeFocused();await page.keyboard.type('檢索與生成');
 await page.keyboard.press('Enter');await expect(page.getByLabel('觀念名稱 5',{exact:true})).toBeFocused();await page.keyboard.type('  ');
 const goals=[page.getByLabel('學習目標 1',{exact:true}),page.getByLabel('學習目標 2',{exact:true})];
 await goals[0].fill(' ');await goals[1].fill('');await expect(page.getByText('請至少保留一個學習目標。')).toBeVisible();await expect(page.getByRole('button',{name:'確認範圍並搜尋'})).toBeDisabled();
 await goals[0].fill('解釋 RAG 架構');await goals[1].fill('比較檢索方法，不討論模型訓練');await page.getByRole('button',{name:'新增學習目標',exact:true}).click();await expect(page.getByLabel('學習目標 3',{exact:true})).toBeFocused();
 await page.getByRole('button',{name:'移除學習目標 3',exact:true}).click();await expect(page.getByLabel('學習目標 2',{exact:true})).toBeFocused();
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
 await page.getByRole('button',{name:'確認範圍並搜尋'}).click();await expect(page.locator('.topic-scope [role=alert]')).toBeVisible();
 const before=reads;await page.getByRole('button',{name:'重新讀取',exact:true}).click();await expect.poll(()=>reads).toBeGreaterThan(before);
 await expect(page.getByLabel('教材名稱',{exact:true})).toHaveValue('我的 RAG 教材');await expect(page.getByLabel('觀念名稱 1',{exact:true})).toHaveValue('RAG 應用架構');
 await page.getByRole('button',{name:'確認範圍並搜尋'}).click();await expect(page).toHaveURL(/\/materials$/);await expect(page.getByRole('article',{name:'我的 RAG 教材',exact:true})).toContainText('正在搜尋來源');expect(attempts).toBe(2);expect(keys[0]).toBeTruthy();expect(keys[1]).toBe(keys[0]);
});

test('request editing and replan keep one editable scope',async({page})=>{
 await mockTopicApi(page);
 const secondId='99999999-9999-4999-8999-999999999999';
 const first={topic_id:id,request:'TCP',proposal:plan,version:2,status:'ready',research_id:null,error_code:null};
 const second={...first,topic_id:secondId,request:'改學 RAG',proposal:{...plan,title:'全新 RAG 規劃',topics:['RAG']}};
 await page.route('**/v1/topics',r=>r.request().method()==='POST'?json(r,second,202):json(r,{topics:[first,second]}));
 await page.route(`**/v1/topics/${id}`,r=>json(r,first));await page.route(`**/v1/topics/${secondId}`,r=>json(r,second));
 await page.goto(`/topics/${id}`);await page.getByLabel('教材名稱',{exact:true}).fill('未提交微調');
 await page.getByRole('button',{name:'修改需求',exact:true}).click();await page.getByRole('button',{name:'返回目前規劃',exact:true}).click();await expect(page.getByLabel('教材名稱',{exact:true})).toHaveValue('未提交微調');
 await page.goto(`/topics/${secondId}`);await expect(page.getByLabel('教材名稱',{exact:true})).toHaveValue('全新 RAG 規劃');
 await page.getByRole('button',{name:'移除主題 1',exact:true}).click();await expect(page.getByText('請至少保留一個要涵蓋的主題。')).toBeVisible();await expect(page.getByRole('button',{name:'確認範圍並搜尋'})).toBeDisabled();
 await page.goto(`/topics/${id}`);await expect(page.getByLabel('教材名稱',{exact:true})).toHaveValue('TCP 入門');
 await page.getByRole('button',{name:'修改需求',exact:true}).click();await page.getByLabel('想學的主題').fill('改學 RAG');await page.getByRole('button',{name:'重新規劃主題',exact:true}).click();await expect(page.getByLabel('教材名稱',{exact:true})).toHaveValue('全新 RAG 規劃');
 await page.route(`**/v1/topics/${secondId}/approve`,r=>{expect(Object.keys(r.request().postDataJSON().proposal).sort()).toEqual(['goals','title','topics']);return json(r,second);});
 const approval=page.waitForRequest(`**/v1/topics/${secondId}/approve`);
 await page.getByRole('button',{name:'確認範圍並搜尋'}).click();await approval;
});


test('retry and pending polling load the new proposal, cancel remains available',async({page})=>{
 await mockTopicApi(page);
 let topic:any={topic_id:id,request:'TCP',proposal:null,version:1,status:'failed',research_id:null,error_code:'FAILED'};
 await page.route('**/v1/topics',r=>json(r,{topics:[topic]}));
 await page.route(`**/v1/topics/${id}`,r=>json(r,topic));
 await page.route(`**/v1/topics/${id}/actions`,r=>{
  const body=r.request().postDataJSON();expect(body.expected_version).toBe(topic.version);
  topic={...topic,status:body.action==='retry'?'pending':'cancelled',version:topic.version+1,error_code:null};return json(r,topic);
 });
 await page.goto(`/topics/${id}`);await page.getByRole('button',{name:'重試規劃'}).click();await expect(page.getByLabel('想學的主題')).toBeDisabled();
 topic={...topic,status:'ready',version:3,proposal:plan};
 await expect(page.getByLabel('教材名稱',{exact:true})).toHaveValue('TCP 入門');
 await page.getByRole('button',{name:'修改需求'}).click();await page.getByRole('button',{name:'取消這個規劃'}).click();await expect(page.getByText('這個規劃已停止，可以建立新主題。')).toBeVisible();
});


test('only selectable sources are listed and there is no manual gap followup',async({page})=>{
 await mockTopicApi(page);
 // 後端只送可選或已選的來源；沒有可選來源時不列出未通過核對的搜尋結果。
 const topic:any={topic_id:id,request:'TCP',proposal:plan,version:3,status:'approved',research_id:rid,error_code:null,research:{research_id:rid,material_id:null,query:'TCP',approved_scope:plan,status:'selecting',candidates:[],selection:[],error_code:null,run_id:null}};
 await page.route('**/v1/topics',r=>json(r,{topics:[topic]}));await page.route(`**/v1/topics/${id}`,r=>json(r,topic));
 await page.goto(`/topics/${id}`);
 await expect(page.getByText('沒有找到可加入的來源，可用「重新規劃」調整學習範圍後再搜尋。')).toBeVisible();
 await expect(page.getByRole('button',{name:'建立教材與知識地圖'})).toBeDisabled();await expect(page.locator('.research-candidates')).toHaveCount(0);
 // 研究會自動補查缺口；結束後不再提供手動補查與補查方向。
 await expect(page.getByRole('button',{name:'依缺口繼續研究'})).toHaveCount(0);await expect(page.getByText('調整補查方向')).toHaveCount(0);
 topic.research.candidates=[candidate];await page.reload();
 await expect(page.getByRole('checkbox',{name:'選取 TCP handshake'})).toBeVisible();
 await page.getByRole('button',{name:'全選可用來源'}).click();await expect(page.locator('.tool-stage-footer')).toContainText('已選 1 份來源');
 await page.getByLabel('搜尋來源',{exact:true}).fill('不存在');await expect(page.getByText('沒有符合篩選的來源。')).toBeVisible();await page.getByRole('button',{name:'清除篩選'}).click();await expect(page.getByRole('checkbox',{name:'選取 TCP handshake'})).toBeChecked();
});

for(const width of [1920,1440,390]) test(`topic uses canonical run progress within step four at ${width}px`,async({page})=>{
 await page.setViewportSize({width,height:width===390?844:1000});await mockTopicApi(page);
 const sources=Array.from({length:23},(_,i)=>({...candidate,id:`source-${i}`,title:`已選文件 ${i+1}`}));
 const topic={topic_id:id,request:'TCP',proposal:plan,version:3,status:'approved',research_id:rid,error_code:null,research:{research_id:rid,material_id:materialId,status:'submitted',candidates:sources,selection:sources.map(c=>c.id),error_code:null,run_id:runId,run:{status:'succeeded',output_binding:{knowledge_structure_revision:runFixture.output_binding.knowledge_structure_revision}}}};
 let current:any={...runFixture,status:'running',progress_stage:'semantics',total_pages:10,completed_pages:4,source_names:sources.map(c=>c.title),output_binding:null,completed_at:null,created_at:new Date(Date.now()-65000).toISOString()};
 await page.route('**/v1/topics',r=>json(r,{topics:[topic]}));await page.route(`**/v1/topics/${id}`,r=>json(r,topic));await page.route(`**/v1/material-processing-runs/${runId}`,r=>json(r,current));
 await page.goto(`/topics/${id}`);
 await expect(page.getByRole('heading',{name:'第 3 / 6 階段',exact:true})).toBeVisible();
 await expect(page.getByText('此階段尚無可計算的工作總數。')).toBeVisible();
 await expect(page.getByRole('progressbar')).toBeVisible();await expect(page.getByRole('progressbar')).not.toHaveAttribute('aria-valuenow');
 await expect(page.locator('.processing-times')).toContainText('1 分');
 await expect(page.locator('.learning-steps > li')).toHaveCount(4);await expect(page.locator('.learning-steps [aria-current=step]')).toContainText('建立教材');
 await expect(page.locator('.processing-timeline [aria-current=step]')).toContainText('建立概念、關係與學習順序');
 await expect(page.getByRole('button',{name:'開啟知識地圖',exact:true})).toHaveCount(0);
 await expect(page.locator('.topic-selected-sources').getByText('已選文件 1',{exact:true})).not.toBeVisible();
 const summary=page.locator('.topic-selected-sources > summary');await expect(summary).toHaveText('已選來源 · 23 份');await summary.focus();await page.keyboard.press('Enter');await expect(page.locator('.topic-selected-sources').getByText('已選文件 1',{exact:true})).toBeVisible();await summary.click();
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
 current={...current,progress_stage:'publishing',completed_pages:10};await expect(page.locator('.processing-timeline [aria-current=step]')).toContainText('組裝與發布知識地圖');await expect(page.getByRole('progressbar',{name:/本階段進度/})).toBeVisible();await expect(page.getByRole('progressbar',{name:/本階段進度/})).not.toHaveAttribute('aria-valuenow');
 current={...runFixture,total_pages:10,completed_pages:10,output_binding:{...runFixture.output_binding,page_count:10}};await expect(page.getByRole('button',{name:'開啟知識地圖',exact:true})).toBeVisible();await expect(page.getByRole('progressbar')).toHaveCount(0);await expect(page.locator('.learning-steps li.is-complete')).toHaveCount(4);
});

for(const status of ['failed','cancelled','partial'] as const) test(`topic material run ${status} retains canonical outcome`,async({page})=>{
 await mockTopicApi(page);
 const current={...runFixture,status,cancel_requested_at:status==='cancelled'?runFixture.updated_at:null,progress_stage:status==='partial'?'completed':'semantics',completed_pages:status==='partial'?2:1,analysis_saved:status==='failed',output_binding:status==='partial'?runFixture.output_binding:null,error_code:status==='failed'?'MATERIAL_ANALYSIS_FAILED':null};
 const topic={topic_id:id,request:'TCP',proposal:plan,version:3,status:'approved',research_id:rid,error_code:null,research:{research_id:rid,material_id:materialId,status:'submitted',candidates:[candidate],selection:['source'],error_code:null,run_id:runId,run:{status:'running',output_binding:null}}};
 await page.route('**/v1/topics',r=>json(r,{topics:[topic]}));await page.route(`**/v1/topics/${id}`,r=>json(r,topic));await page.route(`**/v1/material-processing-runs/${runId}`,r=>json(r,current));
 await page.goto(`/topics/${id}`);await expect(page.getByRole('heading',{name:status==='failed'?'教材處理失敗':status==='cancelled'?'已取消教材處理':'教材與知識地圖已建立',exact:true})).toBeVisible();
 await expect(page.getByRole('progressbar')).toHaveCount(0);
 if(status==='partial'){await expect(page.locator('.flow-chip')).toContainText('處理完成');await expect(page.getByText('由 AI 整理，每個重點都可點開原文對照。',{exact:true})).toBeVisible();await expect(page.getByRole('button',{name:'開啟知識地圖',exact:true})).toBeVisible();}
 else {await expect(page.getByRole('button',{name:'開啟知識地圖',exact:true})).toHaveCount(0);await expect(page.getByRole('region',{name:'教材分析子流程'})).toContainText('已處理 1 / 2 頁');}
 if(status==='failed'){await expect(page.getByText('已完成的處理進度已保存；使用相同來源與設定重試，會接續未完成的部分。')).toBeVisible();await expect(page.getByRole('button',{name:'接續已保存的分析',exact:true})).toBeVisible();}
 else await expect(page.getByRole('button',{name:'接續已保存的分析',exact:true})).toHaveCount(0);
 await expect(page.getByRole('button',{name:'查看教材建立結果',exact:true})).toHaveCount(0);
});

test('cancelled topic material can be deleted and leaves once the material is gone',async({page})=>{
 await mockTopicApi(page);
 const topic={topic_id:id,request:'TCP',proposal:plan,version:3,status:'approved',research_id:rid,error_code:null,research:{research_id:rid,material_id:materialId,status:'submitted',candidates:[candidate],selection:['source'],error_code:null,run_id:runId,run:{status:'cancelled',output_binding:null}}};
 let gone=false,discards=0;
 await page.route('**/v1/topics',r=>json(r,{topics:[topic]}));await page.route(`**/v1/topics/${id}`,r=>json(r,topic));
 await page.route(`**/v1/material-processing-runs/${runId}`,r=>gone?r.fulfill({status:404,json:{schema:'api-error/v1',request_id:id,reason_code:'RESOURCE_NOT_FOUND',retryable:false,message:'Request could not be completed.'}}):json(r,{...runFixture,status:'cancelled',cancel_requested_at:runFixture.updated_at,progress_stage:'semantics',completed_pages:1,output_binding:null}));
 await page.route(`**/v1/materials/${materialId}`,r=>{expect(r.request().method()).toBe('DELETE');discards++;return json(r,{schema:'material-discard/v1',material_id:materialId,state:'removing'},202);});
 await page.goto(`/topics/${id}`);await expect(page.getByRole('heading',{name:'已取消教材處理',exact:true})).toBeVisible();
 await page.getByRole('button',{name:'刪除教材',exact:true}).click();await page.getByRole('button',{name:'確認刪除',exact:true}).click();
 await expect(page.getByRole('heading',{name:'正在刪除教材…',exact:true})).toBeVisible();expect(discards).toBe(1);
 await expect(page.getByRole('button',{name:'刪除教材',exact:true})).toHaveCount(0);
 gone=true;await expect(page).toHaveURL(/\/materials$/);
});

test('topic run loading failures retry and unknown progress remains indeterminate',async({page})=>{
 await mockTopicApi(page);
 const topic={topic_id:id,request:'TCP',proposal:plan,version:3,status:'approved',research_id:rid,error_code:null,research:{research_id:rid,material_id:materialId,status:'submitted',candidates:[candidate],selection:['source'],error_code:null,run_id:runId,run:{status:'running',output_binding:null}}};
 await page.route('**/v1/topics',r=>json(r,{topics:[topic]}));await page.route(`**/v1/topics/${id}`,r=>json(r,topic));let fail=true;
 await page.route(`**/v1/material-processing-runs/${runId}`,r=>fail?json(r,{schema:'api-error/v1',request_id:id,reason_code:'INTERNAL_ERROR',retryable:true,message:'Retry'},500):json(r,{...runFixture,status:'running',progress_stage:'evidence',total_pages:null,completed_pages:0,completed_at:null,output_binding:null}));
 await page.goto(`/topics/${id}`);await expect(page.getByRole('alert')).toContainText('無法讀取處理狀態');await expect(page.getByRole('button',{name:'開啟知識地圖',exact:true})).toHaveCount(0);
 fail=false;await page.getByRole('button',{name:'重新讀取進度'}).click();await expect(page.getByRole('heading',{name:'第 2 / 6 階段',exact:true})).toBeVisible();await expect(page.getByText('此階段尚無可計算的工作總數。')).toBeVisible();await expect(page.getByRole('progressbar',{name:/本階段進度/})).toBeVisible();await expect(page.getByRole('progressbar',{name:/本階段進度/})).not.toHaveAttribute('aria-valuenow');
});


test('topic rejects a foreign run and ignores late responses after switching topics',async({page})=>{
 await mockTopicApi(page);
 const topic={topic_id:id,request:'TCP',proposal:plan,version:3,status:'approved',research_id:rid,error_code:null,research:{research_id:rid,material_id:materialId,status:'submitted',candidates:[candidate],selection:['source'],error_code:null,run_id:runId,run:{status:'running',output_binding:null}}};
 await page.route('**/v1/topics',r=>json(r,{topics:[topic]}));await page.route(`**/v1/topics/${id}`,r=>json(r,topic));
 let release!:()=>void;const held=new Promise<void>(resolve=>{release=resolve;});let retry=false,waiting=false;
 await page.route(`**/v1/material-processing-runs/${runId}`,async r=>{if(!retry)return json(r,{...runFixture,material_id:id});waiting=true;await held;return json(r,runFixture);});
 await page.goto(`/topics/${id}`);await expect(page.getByRole('alert')).toContainText('無法讀取處理狀態');await expect(page.getByRole('button',{name:'開啟知識地圖',exact:true})).toHaveCount(0);
 retry=true;await page.getByRole('button',{name:'重新讀取進度'}).click();await expect.poll(()=>waiting).toBe(true);await page.getByRole('button',{name:'返回我的教材',exact:true}).click();release();
 await expect(page).toHaveURL(/\/materials$/);await expect(page.getByRole('button',{name:'開啟知識地圖',exact:true})).toHaveCount(0);await expect(page.getByRole('progressbar')).toHaveCount(0);
});

test('failed sources and partial service failures remain clear',async({page})=>{
 await mockTopicApi(page);
 const source={...candidate,state:'failed',error_code:'RESEARCH_SOURCE_BLOCKED',acquisition:{license_text:'CC BY-SA 4.0 授權全文',acquired_at:'2026-10-01T00:00:00Z'}};
 const topic={topic_id:id,request:'TCP',proposal:plan,version:3,status:'approved',research_id:rid,error_code:null,research:{research_id:rid,material_id:null,status:'selecting',query:'TCP',candidates:[source],selection:[],error_code:'RESEARCH_PARTIAL_SEARCH',run_id:null}};
 await page.route('**/v1/topics',r=>json(r,{topics:[topic]}));await page.route(`**/v1/topics/${id}`,r=>json(r,topic));
 await page.goto(`/topics/${id}`);await expect(page.getByText('已保留搜尋結果；部分觀念還沒找到合適來源，或有來源未完成。')).toBeVisible();
 await expect(page.getByText('來源網站拒絕讀取，未取得正文。')).toBeVisible();await expect(page.locator('.source-state.is-failed')).toHaveText('取得失敗');
 await page.getByRole('button',{name:'詳細資訊'}).click();const detail=page.getByRole('dialog',{name:'TCP handshake'});await expect(detail.getByRole('heading',{name:'授權與取得資訊'})).toBeVisible();await expect(detail).toContainText('CC-BY-SA');await expect(detail.locator('pre')).toHaveText('CC BY-SA 4.0 授權全文');await expect(detail).toContainText('取得時間：');
 // 搜尋管道、版本與存取紀錄屬於內部除錯資訊，不顯示。
 await expect(detail).not.toContainText('搜尋來源');await expect(detail).not.toContainText('版本');await expect(detail).not.toContainText('存取紀錄');
 await detail.getByRole('button',{name:'關閉詳細資訊'}).click();await expect(detail).toHaveCount(0);
 await page.getByRole('checkbox',{name:'選取 TCP handshake'}).check();await expect(page.getByRole('button',{name:'建立教材與知識地圖'})).toBeEnabled();
});


test('approval failure stays on scope and confirmed topics can be cancelled from the library',async({page})=>{
 await mockTopicApi(page);
 let topic:any={topic_id:id,request:'TCP',proposal:plan,version:2,status:'ready',research_id:null,error_code:null};
 await page.route('**/v1/topics',r=>json(r,{topics:topic.status==='cancelled'?[]:[topic]}));
 await page.route(`**/v1/topics/${id}`,r=>json(r,topic));
 await page.route(`**/v1/topics/${id}/approve`,r=>r.fulfill({status:409,contentType:'application/json',body:JSON.stringify({error:{code:'TOPIC_CONFLICT'}})}));
 await page.goto(`/topics/${id}`);await page.getByRole('button',{name:'確認範圍並搜尋'}).click();
 await expect(page.getByRole('alert')).toBeVisible();await expect(page).toHaveURL(new RegExp(`/topics/${id}$`));
 topic={...topic,status:'approved',version:3,research_id:rid,research:{status:'searching',material_id:null}};
 await page.route(`**/v1/topics/${id}/actions`,r=>{expect(r.request().postDataJSON()).toEqual({action:'cancel',expected_version:3});topic.status='cancelled';return json(r,topic);});
 await page.goto('/materials');const card=page.getByRole('article',{name:'TCP 入門',exact:true});await expect(card).toContainText('正在搜尋來源');
 page.on('dialog',()=>{throw new Error('native confirmation is not allowed');});
 await card.getByRole('button',{name:'取消建立'}).click();await expect(card.getByRole('group',{name:'取消建立這份教材並停止來源準備？'})).toBeVisible();await card.getByRole('button',{name:'保留',exact:true}).click();await expect(card).toBeVisible();
 await card.getByRole('button',{name:'取消建立'}).click();await card.getByRole('button',{name:'取消建立',exact:true}).click();await expect(card).toHaveCount(0);
});
test('topics still being planned stay reachable from the library',async({page})=>{
 await mockTopicApi(page);
 let topic:any={topic_id:id,request:'TCP',proposal:plan,version:2,status:'ready',research_id:null,error_code:null,research:null};
 await page.route('**/v1/topics',r=>json(r,{topics:topic.status==='cancelled'?[]:[topic]}));
 await page.route(`**/v1/topics/${id}`,r=>json(r,topic));
 await page.route(`**/v1/topics/${id}/actions`,r=>{expect(r.request().postDataJSON()).toEqual({action:'cancel',expected_version:2});topic={...topic,status:'cancelled'};return json(r,topic);});
 await page.goto('/materials');
 const card=page.getByRole('article',{name:'TCP 入門',exact:true});
 await expect(card).toContainText('等待確認觀念與細節');
 await expect(page.locator('main')).toContainText('已保存 0 份教材，1 份準備中');
 await card.getByRole('button',{name:'確認範圍'}).click();
 await expect(page).toHaveURL(new RegExp(`/topics/${id}$`));
 await page.goto('/materials');
 await card.getByRole('button',{name:'取消建立'}).click();await expect(card.getByRole('group',{name:'取消這個主題規劃？'})).toBeVisible();
 await card.getByRole('button',{name:'取消建立',exact:true}).click();await expect(card).toHaveCount(0);
});


test('topic analysis cancellation explains removal of search results and requires confirmation',async({page},testInfo)=>{
 await mockTopicApi(page);let gone=false,deletes=0,publishedDuringDelete=false;const captureTime=Date.now();
 const topic={topic_id:id,request:'TCP',proposal:plan,version:3,status:'approved',research_id:rid,error_code:null,research:{research_id:rid,material_id:materialId,status:'submitted',candidates:[candidate],selection:['source'],error_code:null,run_id:runId,run:{status:'running',output_binding:null}}};
 const missing={schema:'api-error/v1',request_id:id,reason_code:'RESOURCE_NOT_FOUND',retryable:false,message:'Request could not be completed.'};
 await page.route('**/v1/topics',r=>json(r,{topics:gone?[]:[topic]}));
 await page.route(`**/v1/topics/${id}`,r=>gone?r.fulfill({status:404,json:missing}):json(r,topic));
 await page.route(`**/v1/material-processing-runs/${runId}`,r=>gone?r.fulfill({status:404,json:missing}):json(r,publishedDuringDelete?runFixture:{...runFixture,status:'running',progress_stage:'evidence',completed_pages:1,total_pages:2,source_names:['TCP handshake'],created_at:new Date(captureTime-65000).toISOString(),updated_at:new Date(captureTime).toISOString(),output_binding:null,completed_at:null}));
 await page.route(`**/v1/materials/${materialId}`,r=>{expect(r.request().method()).toBe('DELETE');deletes++;return json(r,{schema:'material-discard/v1',material_id:materialId,state:'removing'},202);});
 await page.goto(`/topics/${id}`);const cancel=page.getByRole('button',{name:'取消並刪除教材',exact:true});await expect(cancel).toBeVisible();
 for(const [width,height] of [[1440,900],[1300,900],[1200,900],[1024,768],[390,844]]){
  await page.setViewportSize({width,height});await page.screenshot({path:testInfo.outputPath(`topic-running-${width}.png`),animations:'disabled'});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
 }
 await cancel.click();const confirmation=page.getByRole('region',{name:'確定要刪除這份教材嗎？'});
 await expect(confirmation).toContainText('這次搜尋結果不會保留，這個主題也會從列表移除。');await expect(confirmation.getByRole('button',{name:'取消',exact:true})).toBeFocused();expect(deletes).toBe(0);
 await page.screenshot({path:testInfo.outputPath('topic-discard-confirmation-390.png'),animations:'disabled'});
 await page.keyboard.press('Escape');await expect(cancel).toBeFocused();expect(deletes).toBe(0);
 await cancel.click();await page.getByRole('button',{name:'確認刪除',exact:true}).click();await expect(page.getByRole('heading',{name:'正在刪除教材…',exact:true})).toBeVisible();expect(deletes).toBe(1);
 const completedRead=page.waitForResponse(async response=>response.url().endsWith(`/v1/material-processing-runs/${runId}`)&&response.status()===200&&(await response.json()).status==='succeeded');
 publishedDuringDelete=true;await (await completedRead).finished();await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(resolve)));
 await expect(page.getByText('正在停止處理並刪除這份教材，完成後會返回我的教材。',{exact:true})).toBeVisible();await expect(page.getByRole('button',{name:'開啟知識地圖',exact:true})).toHaveCount(0);
 gone=true;await expect(page).toHaveURL(/\/materials$/);expect(deletes).toBe(1);
});


for(const supplemental of [false,true])test(`six scope concepts preserve the request and editable details for ${supplemental?'supplement':'new material'}`,async({page},testInfo)=>{
 await mockTopicApi(page);
 const request='Transformer 從輸入到輸出的完整運作：位置編碼、多頭注意力、前饋網路、殘差連接與正規化。\n請比較各步驟的作用。';
 const topics=['輸入與位置編碼：說明輸入表示與位置信息的作用','多頭注意力:比較不同注意力頭：各自能捕捉什麼關係','前饋網路：說明注意力後的處理','殘差連接：說明原始資訊如何保留','層正規化：比較正規化前後的輸入','輸出與逐步生成'];
 const proposal={title:'Transformer 完整運作',goals:['解釋各步驟如何銜接','用一個例子說明輸入到輸出'],topics};
 let topic:any={topic_id:id,material_id:supplemental?materialId:null,request,proposal,version:2,status:'ready',research_id:null,error_code:null};let submitted:any;
 await page.route('**/v1/topics',r=>json(r,{topics:[topic]}));await page.route(`**/v1/topics/${id}`,r=>json(r,topic));
 await page.route(`**/v1/topics/${id}/approve`,r=>{submitted=r.request().postDataJSON();topic={...topic,status:'approved',version:3,research_id:rid};return json(r,topic);});
 await page.route(`**/v1/research/${rid}`,r=>json(r,{research_id:rid,material_id:materialId,mode:'review',query:request,approved_scope:proposal,status:'searching',selection:[],candidates:[],cursor:null,error_code:null,run_id:null,created_at:'2026-10-06T00:00:00Z'}));
 await page.goto(`/topics/${id}`);
 const title=page.getByLabel(supplemental?'這次補充的名稱':'教材名稱',{exact:true});await expect(title).toHaveValue(proposal.title);
 expect(await page.locator('.topic-original-request').textContent()).toBe(request);
 await expect(page.getByLabel('觀念名稱 2',{exact:true})).toHaveValue('多頭注意力');await expect(page.getByLabel('想了解的細節 2',{exact:true})).toHaveValue('比較不同注意力頭：各自能捕捉什麼關係');
 await expect(page.getByLabel('觀念名稱 6',{exact:true})).toHaveValue('輸出與逐步生成');await expect(page.getByLabel('想了解的細節 6',{exact:true})).toHaveValue('');
 for(const [width,height] of [[1440,900],[1300,900],[1280,900],[1200,900],[1024,768],[390,844]]){
  await page.setViewportSize({width,height});await page.evaluate(()=>window.scrollTo(0,0));
  if(width>=1280){for(const card of await page.locator('.topic-concept-card').all())await expect(card).toBeInViewport({ratio:1});await expect(page.getByRole('button',{name:'確認範圍並搜尋',exact:true})).toBeInViewport({ratio:1});}
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  await page.screenshot({path:testInfo.outputPath(`scope-six-${supplemental?'supplement':'new'}-${width}.png`),animations:'disabled'});
 }
 const detail=page.getByLabel('想了解的細節 1',{exact:true});await detail.fill('第一行');await detail.press('End');await detail.press('Enter');await detail.pressSequentially('第二行');await expect(detail).toHaveValue('第一行\n第二行');
 await detail.fill('字'.repeat(500));await expect(page.getByText('觀念名稱與細節合計最多 500 字，請縮短後再確認。')).toBeVisible();await expect(page.getByRole('button',{name:'確認範圍並搜尋'})).toBeDisabled();await expect(detail).toHaveValue('字'.repeat(500));
 await detail.fill('第一行\n第二行');
 const goal=page.getByLabel('學習目標 1',{exact:true});await goal.fill('字'.repeat(501));await expect(goal).toHaveValue('字'.repeat(501));await expect(page.getByText('每個學習目標最多 500 字，請縮短後再確認。')).toBeVisible();await expect(page.getByRole('button',{name:'確認範圍並搜尋'})).toBeDisabled();await goal.fill(proposal.goals[0]);
 await page.getByRole('button',{name:'確認範圍並搜尋'}).click();
 await expect.poll(()=>submitted).toBeTruthy();expect(submitted.proposal.topics).toEqual(['輸入與位置編碼：第一行\n第二行',...topics.slice(1)]);expect(submitted.proposal.title).toBe(proposal.title);
});
