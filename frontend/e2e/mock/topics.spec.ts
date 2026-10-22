import {expect,test,type Page} from '@playwright/test';
import {materialId,runId,structureRevision,structureView,mockKnowledgeMapApi,json,run as runFixture} from '../fixtures/knowledge-map';
const id='77777777-7777-4777-8777-777777777777';
const rid='88888888-8888-4888-8888-888888888888';
const plan={title:'TCP 入門',goals:['理解握手，不討論進階壅塞演算法'],topics:['SYN、SYN-ACK、ACK']};
const candidate={id:'source',kind:'official',title:'TCP handshake',authors:'Mozilla Contributors',year:null,url:'https://developer.mozilla.org/en-US/docs/Glossary/TCP_handshake',doi:null,license:'CC-BY-SA',license_url:null,version:'current',eligible:true,reason:'來源可使用',state:'candidate'};

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
 await page.route(`**/v1/topics/${id}/approve`,r=>{const body=r.request().postDataJSON();expect(body.expected_version).toBe(2);expect(body.proposal).toEqual(plan);approved++;topic={...topic,proposal:body.proposal,version:3,status:'approved',research_id:rid,research:{research_id:rid,material_id:null,query:'TCP',mode:'self-study',approved_scope:plan,status:'selecting',candidates:[candidate],selection:[],cursor:null,error_code:null,run_id:null}};return json(r,topic);});
 await page.route(`**/v1/topics/${id}/material`,r=>{expect(r.request().postDataJSON()).toEqual({selected:['source']});created++;topic.research={...topic.research,material_id:materialId,selection:['source'],status:'submitted',run_id:runId,run:{status:'pending',error_code:null}};return json(r,{material_id:materialId,research_id:rid},202);});
 await page.goto('/topics');await expect(page.getByRole('heading',{name:'你想學什麼？'})).toBeVisible();
 const workspace=await page.locator('.topic-request').boundingBox();
 expect(workspace!.width).toBeGreaterThan(width===390?300:1000);
 await page.getByLabel('想學的主題').fill('TCP');await page.getByRole('button',{name:'產生學習範圍'}).click();await expect(page.getByRole('heading',{name:/確認學習範圍/})).toBeVisible();expect(approved).toBe(0);expect(created).toBe(0);
 const scopeEnd=await page.locator('.topic-scope-fields').boundingBox();
 const scopeFooter=await page.locator('.topic-scope .tool-stage-footer').boundingBox();
 expect(scopeFooter!.y).toBeGreaterThanOrEqual(scopeEnd!.y+scopeEnd!.height);
 await page.getByRole('button',{name:'確認範圍並搜尋'}).click();
 await expect(page).toHaveURL(/\/materials$/);await expect(page.getByRole('heading',{name:'我的教材',exact:true})).toBeVisible();
 const preparing=page.getByRole('article',{name:'TCP 入門',exact:true});await expect(preparing).toContainText('等待選取來源');
 await page.reload();await preparing.getByRole('button',{name:'選取來源',exact:true}).click();
 await expect(page).toHaveURL(new RegExp(`/topics/${id}$`));await expect(page.getByRole('checkbox',{name:'選取 TCP handshake'})).toBeVisible();expect(approved).toBe(1);expect(created).toBe(0);
 await page.getByRole('checkbox',{name:'選取 TCP handshake'}).check();await page.getByRole('button',{name:'建立教材與知識地圖'}).click();
 await expect(page.getByRole('heading',{name:'等待開始處理',exact:true})).toBeVisible();
 await expect(page.locator('.learning-steps [aria-current=step]')).toContainText('建立教材');
 topic.research.run={status:'succeeded',error_code:null,output_binding:{knowledge_structure_revision:structureRevision}};
 await page.reload();await expect(page.getByRole('button',{name:'開啟知識地圖',exact:true})).toBeVisible();expect(created).toBe(1);await expect(page.locator('.tool-stage-footer').getByRole('button',{name:'開啟知識地圖',exact:true})).toBeVisible();
 await page.reload();await expect(page.getByRole('button',{name:'開啟知識地圖',exact:true})).toBeVisible();expect(created).toBe(1);expect(approved).toBe(1);await expect(page.getByLabel('想學的主題')).toHaveCount(0);await expect(page.getByRole('checkbox',{name:'選取 TCP handshake'})).toHaveCount(0);
 await page.getByRole('button',{name:'開啟知識地圖',exact:true}).click();await expect(page).toHaveURL(new RegExp(`/runs/${runId}/knowledge-structures/`));
});

test('empty source search offers no fake map or enabled create action',async({page})=>{
 await mockTopicApi(page);const topic={topic_id:id,request:'空結果',proposal:plan,version:3,status:'approved',research_id:rid,error_code:null,research:{research_id:rid,material_id:null,query:'空結果',mode:'self-study',approved_scope:plan,status:'selecting',candidates:[],selection:[],cursor:null,error_code:null,run_id:null}};
 await page.route('**/v1/topics',r=>json(r,{topics:[topic]}));await page.route(`**/v1/topics/${id}`,r=>json(r,topic));
 await page.goto(`/topics/${id}`);await expect(page.getByText('沒有找到來源，請調整主題重新規劃。')).toBeVisible();await expect(page.getByRole('button',{name:'建立教材與知識地圖'})).toBeDisabled();await expect(page.getByRole('button',{name:'開啟知識地圖',exact:true})).toHaveCount(0);
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
  topic={...topic,proposal:r.request().postDataJSON().proposal,status:'approved',version:3,research_id:rid,research:{research_id:rid,material_id:null,status:'searching',query:'RAG',mode:'self-study',candidates:[],selection:[],cursor:null,error_code:null,run_id:null}};
  return json(r,topic);
 });
 await page.goto(`/topics/${id}`);
 const title=page.getByLabel('教材名稱',{exact:true});await expect(title).toHaveValue(proposal.title);await expect(title).toHaveAttribute('maxlength','180');
 await expect(page.getByLabel('學習目標 2',{exact:true})).toHaveValue(proposal.goals[1]);
 await expect(page.getByRole('button',{name:/^(編輯|完成)/})).toHaveCount(0);
 await title.fill('');await expect(page.getByText('請填寫教材名稱。')).toBeVisible();await expect(page.getByRole('button',{name:'確認範圍並搜尋'})).toBeDisabled();
 await title.fill('我的 RAG 教材');

 await page.getByLabel('主題 1',{exact:true}).fill('RAG 應用架構');
 await page.getByRole('button',{name:'移除主題 4',exact:true}).click();await expect(page.getByLabel('主題 4',{exact:true})).toHaveCount(0);await expect(page.getByLabel('主題 3',{exact:true})).toBeFocused();
 await page.getByRole('button',{name:'新增主題',exact:true}).click();await expect(page.getByLabel('主題 4',{exact:true})).toBeFocused();await page.keyboard.type('檢索與生成');
 await page.keyboard.press('Enter');await expect(page.getByLabel('主題 5',{exact:true})).toBeFocused();await page.keyboard.type('  ');
 const goals=[page.getByLabel('學習目標 1',{exact:true}),page.getByLabel('學習目標 2',{exact:true})];
 await goals[0].fill(' ');await goals[1].fill('');await expect(page.getByText('請至少保留一個學習目標。')).toBeVisible();await expect(page.getByRole('button',{name:'確認範圍並搜尋'})).toBeDisabled();
 await goals[0].fill('解釋 RAG 架構');await goals[1].fill('比較檢索方法，不討論模型訓練');await page.getByRole('button',{name:'新增學習目標',exact:true}).click();await expect(page.getByLabel('學習目標 3',{exact:true})).toBeFocused();
 await page.getByRole('button',{name:'移除學習目標 3',exact:true}).click();await expect(page.getByLabel('學習目標 2',{exact:true})).toBeFocused();
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
 await page.getByRole('button',{name:'確認範圍並搜尋'}).click();await expect(page.locator('.topic-scope [role=alert]')).toBeVisible();
 const before=reads;await page.getByRole('button',{name:'重新讀取',exact:true}).click();await expect.poll(()=>reads).toBeGreaterThan(before);
 await expect(page.getByLabel('教材名稱',{exact:true})).toHaveValue('我的 RAG 教材');await expect(page.getByLabel('主題 1',{exact:true})).toHaveValue('RAG 應用架構');
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
 await page.getByRole('button',{name:'修改學習需求',exact:true}).click();await page.getByRole('button',{name:'返回目前規劃',exact:true}).click();await expect(page.getByLabel('教材名稱',{exact:true})).toHaveValue('未提交微調');
 await page.goto(`/topics/${secondId}`);await expect(page.getByLabel('教材名稱',{exact:true})).toHaveValue('全新 RAG 規劃');
 await page.getByRole('button',{name:'移除主題 1',exact:true}).click();await expect(page.getByText('請至少保留一個要涵蓋的主題。')).toBeVisible();await expect(page.getByRole('button',{name:'確認範圍並搜尋'})).toBeDisabled();
 await page.goto(`/topics/${id}`);await expect(page.getByLabel('教材名稱',{exact:true})).toHaveValue('TCP 入門');
 await page.getByRole('button',{name:'修改學習需求',exact:true}).click();await page.getByLabel('想學的主題').fill('改學 RAG');await page.getByRole('button',{name:'重新規劃主題',exact:true}).click();await expect(page.getByLabel('教材名稱',{exact:true})).toHaveValue('全新 RAG 規劃');
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
 await page.getByRole('button',{name:'修改學習需求'}).click();await page.getByRole('button',{name:'取消這個規劃'}).click();await expect(page.getByText('這個規劃已停止，可以建立新主題。')).toBeVisible();
});


test('unavailable sources stay distinct from explicit gap followup',async({page})=>{
 await mockTopicApi(page);
 const blocked={...candidate,id:'blocked',title:'不可匯入論文',kind:'paper',eligible:false,license:'cc-by-nc-nd'};
 const topic:any={topic_id:id,request:'TCP',proposal:plan,version:3,status:'approved',research_id:rid,error_code:null,research:{research_id:rid,material_id:null,query:'TCP',mode:'self-study',approved_scope:plan,status:'selecting',candidates:[blocked],selection:[],cursor:'next',error_code:null,run_id:null}};
 await page.route('**/v1/topics',r=>json(r,{topics:[topic]}));await page.route(`**/v1/topics/${id}`,r=>json(r,topic));
 await page.route(`**/v1/research/${rid}/actions`,r=>{expect(r.request().postDataJSON()).toEqual({action:'more'});topic.research.candidates.push(candidate);topic.research.cursor=null;return json(r,topic.research);});
 await page.goto(`/topics/${id}`);
 await expect(page.getByText('目前尚無已核對可加入的來源，可查看其他搜尋結果的原因或接續研究。')).toBeVisible();
 await expect(page.getByRole('button',{name:'建立教材與知識地圖'})).toBeDisabled();await expect(page.getByRole('button',{name:'全選可用來源'})).toBeDisabled();
 await page.getByRole('button',{name:'依缺口繼續研究'}).click();await expect(page.getByRole('checkbox',{name:'選取 TCP handshake'})).toBeVisible();
 await page.getByRole('button',{name:'全選可用來源'}).click();await expect(page.locator('.tool-stage-footer')).toContainText('已選 1 份來源');
 await page.getByLabel('搜尋來源',{exact:true}).fill('不可匯入');await expect(page.getByText('沒有符合篩選的來源。')).toBeVisible();await page.getByRole('button',{name:'清除篩選'}).click();await expect(page.getByRole('checkbox',{name:'選取 TCP handshake'})).toBeChecked();
});

for(const width of [1920,1440,390]) test(`topic uses canonical run progress within step four at ${width}px`,async({page})=>{
 await page.setViewportSize({width,height:width===390?844:1000});await mockTopicApi(page);
 const sources=Array.from({length:23},(_,i)=>({...candidate,id:`source-${i}`,title:`已選文件 ${i+1}`}));
 const topic={topic_id:id,request:'TCP',proposal:plan,version:3,status:'approved',research_id:rid,error_code:null,research:{research_id:rid,material_id:materialId,status:'submitted',candidates:sources,selection:sources.map(c=>c.id),cursor:null,error_code:null,run_id:runId,run:{status:'succeeded',error_code:null,output_binding:runFixture.output_binding}}};
 let current:any={...runFixture,status:'running',progress_stage:'semantics',total_pages:10,completed_pages:4,source_names:sources.map(c=>c.title),output_binding:null,completed_at:null,created_at:new Date(Date.now()-65000).toISOString()};
 await page.route('**/v1/topics',r=>json(r,{topics:[topic]}));await page.route(`**/v1/topics/${id}`,r=>json(r,topic));await page.route(`**/v1/material-processing-runs/${runId}`,r=>json(r,current));
 await page.goto(`/topics/${id}`);
 await expect(page.getByRole('heading',{name:'第 3 / 6 階段',exact:true})).toBeVisible();
 await expect(page.getByText('此階段尚無可計算的工作總數。')).toBeVisible();
 await expect(page.getByRole('progressbar')).toHaveCount(0);
 await expect(page.locator('.processing-times')).toContainText('1 分');
 await expect(page.locator('.learning-steps > li')).toHaveCount(4);await expect(page.locator('.learning-steps [aria-current=step]')).toContainText('建立教材');
 await expect(page.locator('.processing-timeline [aria-current=step]')).toContainText('建立概念、關係與學習順序');
 await expect(page.getByRole('button',{name:'開啟知識地圖',exact:true})).toHaveCount(0);
 await expect(page.getByText('已選文件 1',{exact:true})).not.toBeVisible();
 const summary=page.locator('.topic-selected-sources > summary');await expect(summary).toHaveText('已選來源 · 23 份');await summary.focus();await page.keyboard.press('Enter');await expect(page.getByText('已選文件 1',{exact:true})).toBeVisible();await summary.click();
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
 current={...current,progress_stage:'publishing',completed_pages:10};await expect(page.locator('.processing-timeline [aria-current=step]')).toContainText('組裝與發布知識地圖');await expect(page.getByRole('progressbar',{name:/本階段進度/})).toHaveCount(0);
 current={...runFixture,total_pages:10,completed_pages:10,output_binding:{...runFixture.output_binding,page_count:10}};await expect(page.getByRole('button',{name:'開啟知識地圖',exact:true})).toBeVisible();await expect(page.getByRole('progressbar')).toHaveCount(0);await expect(page.locator('.learning-steps li.is-complete')).toHaveCount(4);
});

for(const status of ['failed','cancelled','partial'] as const) test(`topic material run ${status} retains canonical outcome`,async({page})=>{
 await mockTopicApi(page);
 const current={...runFixture,status,cancel_requested_at:status==='cancelled'?runFixture.updated_at:null,progress_stage:status==='partial'?'completed':'semantics',completed_pages:status==='partial'?2:1,analysis_saved:status==='failed',output_binding:status==='partial'?runFixture.output_binding:null,error_code:status==='failed'?'MATERIAL_ANALYSIS_FAILED':null};
 const topic={topic_id:id,request:'TCP',proposal:plan,version:3,status:'approved',research_id:rid,error_code:null,research:{research_id:rid,material_id:materialId,status:'submitted',candidates:[candidate],selection:['source'],cursor:null,error_code:null,run_id:runId,run:{status:'running',error_code:null}}};
 await page.route('**/v1/topics',r=>json(r,{topics:[topic]}));await page.route(`**/v1/topics/${id}`,r=>json(r,topic));await page.route(`**/v1/material-processing-runs/${runId}`,r=>json(r,current));
 await page.goto(`/topics/${id}`);await expect(page.getByRole('heading',{name:status==='failed'?'教材處理失敗':status==='cancelled'?'已取消教材處理':'教材與知識地圖已建立',exact:true})).toBeVisible();
 await expect(page.getByRole('progressbar')).toHaveCount(0);
 if(status==='partial'){await expect(page.locator('.topic-analysis-result').getByRole('status')).toContainText('部分結果可用');await expect(page.getByRole('button',{name:'開啟知識地圖',exact:true})).toBeVisible();}
 else {await expect(page.getByRole('button',{name:'開啟知識地圖',exact:true})).toHaveCount(0);await expect(page.locator('.topic-analysis-result')).toContainText('已處理 1 / 2 頁');}
 if(status==='failed'){await expect(page.getByText('已完成的處理進度已保存；重試會接續未完成的部分。')).toBeVisible();await expect(page.getByRole('button',{name:'接續已保存的分析',exact:true})).toBeVisible();}
 else await expect(page.getByRole('button',{name:'接續已保存的分析',exact:true})).toHaveCount(0);
 await expect(page.getByRole('button',{name:'查看教材建立結果',exact:true})).toHaveCount(0);
});

test('cancelled topic material can be deleted and leaves once the material is gone',async({page})=>{
 await mockTopicApi(page);
 const topic={topic_id:id,request:'TCP',proposal:plan,version:3,status:'approved',research_id:rid,error_code:null,research:{research_id:rid,material_id:materialId,status:'submitted',candidates:[candidate],selection:['source'],cursor:null,error_code:null,run_id:runId,run:{status:'cancelled',error_code:null}}};
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
 const topic={topic_id:id,request:'TCP',proposal:plan,version:3,status:'approved',research_id:rid,error_code:null,research:{research_id:rid,material_id:materialId,status:'submitted',candidates:[candidate],selection:['source'],cursor:null,error_code:null,run_id:runId,run:{status:'running',error_code:null}}};
 await page.route('**/v1/topics',r=>json(r,{topics:[topic]}));await page.route(`**/v1/topics/${id}`,r=>json(r,topic));let fail=true;
 await page.route(`**/v1/material-processing-runs/${runId}`,r=>fail?json(r,{schema:'api-error/v1',request_id:id,reason_code:'INTERNAL_ERROR',retryable:true,message:'Retry'},500):json(r,{...runFixture,status:'running',progress_stage:'evidence',total_pages:null,completed_pages:0,completed_at:null,output_binding:null}));
 await page.goto(`/topics/${id}`);await expect(page.getByRole('alert')).toContainText('無法讀取處理狀態');await expect(page.getByRole('button',{name:'開啟知識地圖',exact:true})).toHaveCount(0);
 fail=false;await page.getByRole('button',{name:'重新讀取進度'}).click();await expect(page.getByRole('heading',{name:'第 2 / 6 階段',exact:true})).toBeVisible();await expect(page.getByText('此階段尚無可計算的工作總數。')).toBeVisible();await expect(page.getByRole('progressbar',{name:/本階段進度/})).toHaveCount(0);
});


test('topic rejects a foreign run and ignores late responses after switching topics',async({page})=>{
 await mockTopicApi(page);
 const topic={topic_id:id,request:'TCP',proposal:plan,version:3,status:'approved',research_id:rid,error_code:null,research:{research_id:rid,material_id:materialId,status:'submitted',candidates:[candidate],selection:['source'],cursor:null,error_code:null,run_id:runId,run:{status:'running',error_code:null}}};
 await page.route('**/v1/topics',r=>json(r,{topics:[topic]}));await page.route(`**/v1/topics/${id}`,r=>json(r,topic));
 let release!:()=>void;const held=new Promise<void>(resolve=>{release=resolve;});let retry=false,waiting=false;
 await page.route(`**/v1/material-processing-runs/${runId}`,async r=>{if(!retry)return json(r,{...runFixture,material_id:id});waiting=true;await held;return json(r,runFixture);});
 await page.goto(`/topics/${id}`);await expect(page.getByRole('alert')).toContainText('無法讀取處理狀態');await expect(page.getByRole('button',{name:'開啟知識地圖',exact:true})).toHaveCount(0);
 retry=true;await page.getByRole('button',{name:'重新讀取進度'}).click();await expect.poll(()=>waiting).toBe(true);await page.getByRole('button',{name:'返回我的教材',exact:true}).click();release();
 await expect(page).toHaveURL(/\/materials$/);await expect(page.getByRole('button',{name:'開啟知識地圖',exact:true})).toHaveCount(0);await expect(page.getByRole('progressbar')).toHaveCount(0);
});

test('source search provenance and partial service failures remain clear',async({page})=>{
 await mockTopicApi(page);
 const source={...candidate,state:'failed',error_code:'RESEARCH_SOURCE_BLOCKED',discovered_by:['OpenAlex','Crossref']};
 const topic={topic_id:id,request:'TCP',proposal:plan,version:3,status:'approved',research_id:rid,error_code:null,research:{research_id:rid,material_id:null,status:'selecting',query:'TCP',mode:'self-study',candidates:[source],selection:[],cursor:'more',error_code:'RESEARCH_PARTIAL_SEARCH',run_id:null}};
 await page.route('**/v1/topics',r=>json(r,{topics:[topic]}));await page.route(`**/v1/topics/${id}`,r=>json(r,topic));
 await page.goto(`/topics/${id}`);await expect(page.getByText('已保留搜尋結果；部分主題仍缺少正文依據，或有來源未完成。請查看涵蓋情況與各來源說明。')).toBeVisible();
 await expect(page.getByText('此入口回應 403，未取得正文。')).toBeVisible();
 await page.getByText('授權與取得資訊',{exact:true}).click();await expect(page.getByText('搜尋來源：OpenAlex、Crossref（同篇文件合併顯示）')).toBeVisible();
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
 page.once('dialog',dialog=>dialog.dismiss());await card.getByRole('button',{name:'取消建立'}).click();await expect(card).toBeVisible();
 page.once('dialog',dialog=>dialog.accept());await card.getByRole('button',{name:'取消建立'}).click();await expect(card).toHaveCount(0);
});
