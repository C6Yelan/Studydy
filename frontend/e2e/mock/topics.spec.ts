import {expect,test} from '@playwright/test';
import {materialId,runId,structureRevision,structureView,mockKnowledgeMapApi,json,run as runFixture} from '../fixtures/knowledge-map';
const id='77777777-7777-4777-8777-777777777777';
const rid='88888888-8888-4888-8888-888888888888';
const plan={title:'TCP 入門',level:'入門',goals:['理解握手'],topics:['SYN、SYN-ACK、ACK'],exclude:['進階壅塞演算法']};
const candidate={id:'source',kind:'official',title:'TCP handshake',authors:'Mozilla Contributors',year:null,url:'https://developer.mozilla.org/en-US/docs/Glossary/TCP_handshake',doi:null,license:'CC-BY-SA',license_url:null,version:'current',eligible:true,reason:'來源可使用',state:'candidate'};

for(const width of [1920,1440,390]) test(`scope workflow at ${width}px`,async({page},testInfo)=>{
 await page.setViewportSize({width,height:width===390?844:1000});
 await mockKnowledgeMapApi(page,structureView());let approved=0,created=0;let topic:any={topic_id:id,request:'TCP',proposal:plan,version:2,status:'ready',research_id:null,error_code:null};
 await page.route('**/v1/topics',r=>r.request().method()==='POST'?json(r,topic,202):json(r,{topics:[topic]}));
 await page.route(`**/v1/topics/${id}`,r=>json(r,topic));
 await page.route(`**/v1/material-processing-runs/${runId}`,r=>{const done=topic.research?.run?.status==='succeeded';return json(r,{...runFixture,status:done?'succeeded':'pending',progress_stage:done?'completed':'queued',completed_pages:done?2:0,output_binding:done?runFixture.output_binding:null,completed_at:done?runFixture.completed_at:null});});
 await page.route(`**/v1/topics/${id}/approve`,r=>{const body=r.request().postDataJSON();expect(body.expected_version).toBe(2);expect(body.proposal.level).toBe('有基礎');approved++;topic={...topic,proposal:body.proposal,version:3,status:'approved',research_id:rid,research:{research_id:rid,material_id:null,query:'TCP',mode:'self-study',status:'selecting',candidates:[candidate],selection:[],cursor:null,error_code:null,run_id:null}};return json(r,topic);});
 await page.route(`**/v1/topics/${id}/material`,r=>{expect(r.request().postDataJSON()).toEqual({selected:['source']});created++;topic.research={...topic.research,material_id:materialId,selection:['source'],status:'submitted',run_id:runId,run:{status:'pending',error_code:null}};return json(r,{material_id:materialId,research_id:rid},202);});
 await page.goto('/topics');await expect(page.getByRole('heading',{name:'你想學什麼？'})).toBeVisible();
 const workspace=await page.locator('.topic-request').boundingBox();
 expect(workspace!.width).toBeGreaterThan(width===390?300:1000);
 expect(workspace!.width).toBeLessThanOrEqual(1180);
 await page.screenshot({path:testInfo.outputPath('new-topic.png'),fullPage:true});
 await page.getByLabel('想學的主題').fill('TCP');await page.getByRole('button',{name:'產生學習範圍'}).click();await expect(page.getByRole('heading',{name:/確認學習範圍/})).toBeVisible();expect(approved).toBe(0);expect(created).toBe(0);
 const scopeEnd=await page.locator('.topic-exclude').boundingBox();
 const scopeFooter=await page.locator('.topic-scope .tool-stage-footer').boundingBox();
 expect(scopeFooter!.y).toBeGreaterThanOrEqual(scopeEnd!.y+scopeEnd!.height);
 await page.screenshot({path:testInfo.outputPath('scope.png'),fullPage:true});
 await page.getByLabel('學習程度',{exact:true}).selectOption('有基礎');await page.getByRole('button',{name:'確認範圍並搜尋'}).click();await expect(page.getByRole('checkbox',{name:'選取 TCP handshake'})).toBeVisible();expect(approved).toBe(1);expect(created).toBe(0);await expect(page.getByLabel('學習程度',{exact:true})).toHaveCount(0);
 await page.screenshot({path:testInfo.outputPath('sources.png'),fullPage:true});await page.getByRole('checkbox',{name:'選取 TCP handshake'}).check();await page.getByRole('button',{name:'建立教材與知識地圖'}).click();
 await expect(page.getByRole('heading',{name:'等待開始處理',exact:true})).toBeVisible();
 await expect(page.locator('.learning-steps [aria-current=step]')).toContainText('建立教材');
 await page.screenshot({path:testInfo.outputPath('building.png'),fullPage:true});
 topic.research.run={status:'succeeded',error_code:null,output_binding:{knowledge_structure_revision:structureRevision}};
 await page.reload();await expect(page.getByRole('button',{name:'開啟知識地圖',exact:true})).toBeVisible();expect(created).toBe(1);await expect(page.locator('.tool-stage-footer').getByRole('button',{name:'開啟知識地圖',exact:true})).toBeVisible();await page.screenshot({path:testInfo.outputPath('complete.png'),fullPage:true});
 await page.reload();await expect(page.getByRole('button',{name:'開啟知識地圖',exact:true})).toBeVisible();expect(created).toBe(1);expect(approved).toBe(1);await expect(page.getByLabel('想學的主題')).toHaveCount(0);await expect(page.getByRole('checkbox',{name:'選取 TCP handshake'})).toHaveCount(0);
 await page.getByRole('button',{name:'開啟知識地圖',exact:true}).click();await expect(page).toHaveURL(new RegExp(`/runs/${runId}/knowledge-structures/`));
});

test('empty source search offers no fake map or enabled create action',async({page})=>{
 await mockKnowledgeMapApi(page,structureView());const topic={topic_id:id,request:'空結果',proposal:plan,version:3,status:'approved',research_id:rid,error_code:null,research:{research_id:rid,material_id:null,query:'空結果',mode:'self-study',status:'selecting',candidates:[],selection:[],cursor:null,error_code:null,run_id:null}};
 await page.route('**/v1/topics',r=>json(r,{topics:[topic]}));await page.route(`**/v1/topics/${id}`,r=>json(r,topic));
 await page.goto(`/topics/${id}`);await expect(page.getByText('沒有找到來源，請調整主題重新規劃。')).toBeVisible();await expect(page.getByRole('button',{name:'建立教材與知識地圖'})).toBeDisabled();await expect(page.getByRole('button',{name:'開啟知識地圖',exact:true})).toHaveCount(0);
});

for(const width of [1920,1440,390]) test(`saved topic switching and planning states at ${width}px`,async({page},testInfo)=>{
 await page.setViewportSize({width,height:width===390?844:1000});
 await mockKnowledgeMapApi(page,structureView());
 const topics=['pending','failed','cancelled'].map((status,index)=>({topic_id:`77777777-7777-4777-8777-77777777777${index}`,request:`測試規劃 ${status}`,proposal:null,version:1,status,research_id:null,error_code:null}));
 await page.route('**/v1/topics',r=>json(r,{topics}));
 for(const topic of topics) await page.route(`**/v1/topics/${topic.topic_id}`,r=>json(r,topic));
 await page.goto('/topics');
 const history=page.getByLabel('已保存的主題');
 await expect(history.locator('option')).toHaveCount(4);
 for(const topic of topics){
  await history.selectOption(topic.topic_id);
  await expect(page.getByLabel('想學的主題')).toHaveValue(topic.request);
  await expect(page.locator('.learning-steps [aria-current=step]')).toContainText('學習主題');
  if(topic.status==='pending'){
   await expect(page.getByLabel('想學的主題')).toBeDisabled();
   await expect(page.getByRole('button',{name:'重新規劃主題'})).toBeDisabled();
   await expect(page.getByRole('button',{name:'取消這個規劃'})).toBeEnabled();
  }else if(topic.status==='failed') await expect(page.getByRole('button',{name:'重試規劃'})).toBeEnabled();
  else await expect(page.getByRole('button',{name:'重新規劃主題'})).toBeEnabled();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  await page.screenshot({path:testInfo.outputPath(`${topic.status}.png`),fullPage:true});
 }
 await page.getByRole('button',{name:'新主題',exact:true}).click();
 await expect(page).toHaveURL(/\/topics$/);
 await expect(page.getByLabel('想學的主題')).toHaveValue('');
 await expect(history).toHaveValue('');
});

for(const width of [1920,1440,390]) test(`scope review edits preserve payload and keyboard focus at ${width}px`,async({page},testInfo)=>{
 await page.setViewportSize({width,height:width===390?844:1000});
 await mockKnowledgeMapApi(page,structureView());
 const proposal={title:'RAG 入門原理',level:'了解基本程式設計，希望從原理開始',topics:['RAG 整體架構','文件載入與切分','嵌入向量','相似度檢索'],goals:['說明 RAG 的基本概念與用途','理解文件切分、向量化、檢索與生成的關係'],exclude:['進階部署','效能調校']};
 let topic:any={topic_id:id,request:'學習 RAG',proposal,version:2,status:'ready',research_id:null,error_code:null};
 let attempts=0,reads=0;const keys:string[]=[];
 await page.route('**/v1/topics',r=>json(r,{topics:[topic]}));
 await page.route(`**/v1/topics/${id}`,r=>{reads++;return json(r,topic);});
 await page.route(`**/v1/topics/${id}/approve`,r=>{
  attempts++;keys.push(r.request().headers()['idempotency-key']);
  expect(r.request().postDataJSON()).toEqual({expected_version:2,proposal:{title:'我的 RAG 教材',level:'深入',topics:['RAG 應用架構','文件載入與切分','嵌入向量','檢索與生成'],goals:['解釋 RAG 架構','比較檢索方法'],exclude:['模型訓練']}});
  if(attempts===1)return json(r,{schema:'api-error/v1',request_id:id,reason_code:'INTERNAL_ERROR',retryable:true,message:'Retry'},500);
  topic={...topic,status:'approved',version:3,research_id:rid,research:{research_id:rid,material_id:null,status:'searching',query:'RAG',mode:'self-study',candidates:[],selection:[],cursor:null,error_code:null,run_id:null}};
  return json(r,topic);
 });
 await page.goto(`/topics/${id}`);
 await expect(page.getByRole('heading',{name:proposal.title,exact:true})).toBeVisible();
 await expect(page.locator('.topic-scope input, .topic-scope textarea:visible')).toHaveCount(0);
 await expect(page.locator('.topic-goals-summary li').filter({hasText:proposal.goals[1]})).toBeVisible();
 await page.screenshot({path:testInfo.outputPath('review.png'),fullPage:true});
 const editName=page.getByRole('button',{name:'編輯名稱',exact:true});await editName.focus();await page.keyboard.press('Enter');
 const title=page.getByLabel('教材名稱',{exact:true});await expect(title).toBeFocused();await expect(title).toHaveAttribute('maxlength','180');
 await title.fill('');await expect(page.getByText('請填寫教材名稱。')).toBeVisible();await expect(page.getByRole('button',{name:'確認範圍並搜尋'})).toBeDisabled();
 await title.fill('我的 RAG 教材');await title.press('Enter');await expect(editName).toBeFocused();await expect(title).toHaveCount(0);
 const level=page.getByLabel('學習程度',{exact:true});await expect(level).toHaveValue(proposal.level);await expect(level.locator('option')).toHaveCount(4);await level.focus();await page.keyboard.press('ArrowDown');await expect(level).toHaveValue('入門');await expect(page.getByText('第一次接觸：基本概念、先備知識與入門教學。',{exact:false})).toBeVisible();await level.selectOption('有基礎');await expect(page.getByText('已有基本認識：原理、實作教學與應用案例。',{exact:false})).toBeVisible();await level.selectOption('深入');await expect(level).toBeFocused();await expect(page.getByText('研究細節：技術規格、限制、取捨與研究論文。',{exact:false})).toBeVisible();

 await page.getByRole('button',{name:'編輯主題 1',exact:true}).click();await expect(page.getByLabel('主題 1',{exact:true})).toBeFocused();await page.getByLabel('主題 1',{exact:true}).fill('RAG 應用架構');await page.getByLabel('主題 1',{exact:true}).press('Enter');await expect(page.getByRole('button',{name:'編輯主題 1',exact:true})).toBeFocused();
 await page.getByRole('button',{name:'移除主題 4',exact:true}).click();await expect(page.getByRole('button',{name:'新增主題',exact:true})).toBeFocused();await page.keyboard.press('Enter');await expect(page.getByLabel('主題 4',{exact:true})).toBeFocused();await page.getByLabel('主題 4',{exact:true}).fill('檢索與生成');await page.getByLabel('主題 4',{exact:true}).press('Enter');
 await page.getByRole('button',{name:'新增主題',exact:true}).click();await page.getByLabel('主題 5',{exact:true}).fill('  ');await page.getByLabel('主題 5',{exact:true}).press('Enter');
 await page.getByRole('button',{name:'編輯學習目標',exact:true}).click();const goals=page.getByRole('textbox',{name:'學習目標',exact:true});await expect(goals).toBeFocused();await goals.fill(' \n');await expect(page.getByText('請至少保留一個學習目標。')).toBeVisible();await goals.fill('解釋 RAG 架構\n\n比較檢索方法\n ');await page.getByRole('button',{name:'完成目標編輯'}).click();await expect(page.getByRole('button',{name:'編輯學習目標',exact:true})).toBeFocused();
 const advanced=page.locator('.topic-exclude summary');await advanced.focus();await page.keyboard.press('Enter');await page.keyboard.press('Tab');await expect(page.getByLabel('本次不涵蓋',{exact:true})).toBeFocused();await page.getByLabel('本次不涵蓋',{exact:true}).fill('模型訓練\n \n');
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
 await page.evaluate(()=>window.scrollTo(0,0));await page.screenshot({path:testInfo.outputPath('edited.png'),fullPage:true});
 await page.getByRole('button',{name:'確認範圍並搜尋'}).click();await expect(page.locator('.topic-scope [role=alert]')).toBeVisible();
 const before=reads;await page.getByRole('button',{name:'重新讀取',exact:true}).click();await expect.poll(()=>reads).toBeGreaterThan(before);
 await expect(page.getByRole('heading',{name:'我的 RAG 教材',exact:true})).toBeVisible();await expect(page.getByText('RAG 應用架構',{exact:true})).toBeVisible();
 await page.getByRole('button',{name:'確認範圍並搜尋'}).click();await expect(page.getByText('正在搜尋論文與官方教學。')).toBeVisible();expect(attempts).toBe(2);expect(keys[0]).toBeTruthy();expect(keys[1]).toBe(keys[0]);
});

test('saved drafts, request editing, replan and empty exclusions',async({page})=>{
 await mockKnowledgeMapApi(page,structureView());
 const secondId='99999999-9999-4999-8999-999999999999';
 const first={topic_id:id,request:'TCP',proposal:plan,version:2,status:'ready',research_id:null,error_code:null};
 const second={...first,topic_id:secondId,request:'改學 RAG',proposal:{...plan,title:'全新 RAG 規劃',topics:['RAG'],exclude:[]}};
 await page.route('**/v1/topics',r=>r.request().method()==='POST'?json(r,second,202):json(r,{topics:[first,second]}));
 await page.route(`**/v1/topics/${id}`,r=>json(r,first));await page.route(`**/v1/topics/${secondId}`,r=>json(r,second));
 await page.goto(`/topics/${id}`);await page.getByRole('button',{name:'編輯名稱',exact:true}).click();await page.getByLabel('教材名稱',{exact:true}).fill('未提交微調');
 await page.getByRole('button',{name:'修改學習需求',exact:true}).click();await page.getByRole('button',{name:'返回目前規劃',exact:true}).click();await expect(page.getByRole('heading',{name:'未提交微調'})).toBeVisible();
 await page.getByLabel('已保存的主題').selectOption(secondId);await expect(page.getByRole('heading',{name:'全新 RAG 規劃'})).toBeVisible();await expect(page.getByLabel('本次不涵蓋',{exact:true})).not.toBeVisible();
 await page.locator('.topic-exclude summary').click();await expect(page.getByLabel('本次不涵蓋',{exact:true})).toHaveValue('');
 await page.getByRole('button',{name:'移除主題 1',exact:true}).click();await expect(page.getByText('請至少保留一個要涵蓋的主題。')).toBeVisible();await expect(page.getByRole('button',{name:'確認範圍並搜尋'})).toBeDisabled();
 await page.getByLabel('已保存的主題').selectOption(id);await expect(page.getByRole('heading',{name:'TCP 入門',exact:true})).toBeVisible();
 await page.getByRole('button',{name:'修改學習需求',exact:true}).click();await page.getByLabel('想學的主題').fill('改學 RAG');await page.getByRole('button',{name:'重新規劃主題',exact:true}).click();await expect(page.getByRole('heading',{name:'全新 RAG 規劃'})).toBeVisible();
 await page.route(`**/v1/topics/${secondId}/approve`,r=>{expect(r.request().postDataJSON().proposal.exclude).toEqual([]);return json(r,second);});
 const approval=page.waitForRequest(`**/v1/topics/${secondId}/approve`);
 await page.getByRole('button',{name:'確認範圍並搜尋'}).click();await approval;
});


test('retry and pending polling load the new proposal, cancel remains available',async({page})=>{
 await mockKnowledgeMapApi(page,structureView());
 let topic:any={topic_id:id,request:'TCP',proposal:null,version:1,status:'failed',research_id:null,error_code:'FAILED'};
 await page.route('**/v1/topics',r=>json(r,{topics:[topic]}));
 await page.route(`**/v1/topics/${id}`,r=>json(r,topic));
 await page.route(`**/v1/topics/${id}/actions`,r=>{
  const body=r.request().postDataJSON();expect(body.expected_version).toBe(topic.version);
  topic={...topic,status:body.action==='retry'?'pending':'cancelled',version:topic.version+1,error_code:null};return json(r,topic);
 });
 await page.goto(`/topics/${id}`);await page.getByRole('button',{name:'重試規劃'}).click();await expect(page.getByLabel('想學的主題')).toBeDisabled();
 topic={...topic,status:'ready',version:3,proposal:plan};
 await expect(page.getByRole('heading',{name:'TCP 入門',exact:true})).toBeVisible();
 await page.getByRole('button',{name:'修改學習需求'}).click();await page.getByRole('button',{name:'取消這個規劃'}).click();await expect(page.getByText('這個規劃已停止，可以建立新主題。')).toBeVisible();
});


test('unavailable sources are hidden and an all-unavailable page can load more',async({page})=>{
 await mockKnowledgeMapApi(page,structureView());
 const blocked={...candidate,id:'blocked',title:'不可匯入論文',kind:'paper',eligible:false,license:'cc-by-nc-nd'};
 const topic:any={topic_id:id,request:'TCP',proposal:plan,version:3,status:'approved',research_id:rid,error_code:null,research:{research_id:rid,material_id:null,query:'TCP',mode:'self-study',status:'selecting',candidates:[blocked],selection:[],cursor:'next',error_code:null,run_id:null}};
 await page.route('**/v1/topics',r=>json(r,{topics:[topic]}));await page.route(`**/v1/topics/${id}`,r=>json(r,topic));
 await page.route(`**/v1/research/${rid}/actions`,r=>{expect(r.request().postDataJSON()).toEqual({action:'more'});topic.research.candidates.push(candidate);topic.research.cursor=null;return json(r,topic.research);});
 await page.goto(`/topics/${id}`);
 await expect(page.getByText('不可匯入論文',{exact:true})).toHaveCount(0);await expect(page.getByText('僅供查看',{exact:true})).toHaveCount(0);
 await expect(page.getByText('目前沒有可加入的來源，可載入更多或調整主題後重新搜尋。')).toBeVisible();
 await expect(page.getByRole('button',{name:'建立教材與知識地圖'})).toBeDisabled();await expect(page.getByRole('button',{name:'全選可用來源'})).toBeDisabled();
 await page.getByRole('button',{name:'載入更多論文'}).click();await expect(page.getByRole('checkbox',{name:'選取 TCP handshake'})).toBeVisible();await expect(page.getByText('不可匯入論文',{exact:true})).toHaveCount(0);
 await page.getByRole('button',{name:'全選可用來源'}).click();await expect(page.locator('.tool-stage-footer')).toContainText('已選 1 份來源');
 await page.getByLabel('搜尋來源',{exact:true}).fill('不可匯入');await expect(page.getByText('沒有符合篩選的來源。')).toBeVisible();await page.getByRole('button',{name:'清除篩選'}).click();await expect(page.getByRole('checkbox',{name:'選取 TCP handshake'})).toBeChecked();
});

for(const width of [1920,1440,390]) test(`topic uses canonical run progress within step four at ${width}px`,async({page},testInfo)=>{
 await page.setViewportSize({width,height:width===390?844:1000});await mockKnowledgeMapApi(page,structureView());
 const sources=Array.from({length:23},(_,i)=>({...candidate,id:`source-${i}`,title:`已選文件 ${i+1}`}));
 const topic={topic_id:id,request:'TCP',proposal:plan,version:3,status:'approved',research_id:rid,error_code:null,research:{research_id:rid,material_id:materialId,status:'submitted',candidates:sources,selection:sources.map(c=>c.id),cursor:null,error_code:null,run_id:runId,run:{status:'succeeded',error_code:null,output_binding:runFixture.output_binding}}};
 let current:any={...runFixture,status:'running',progress_stage:'semantics',total_pages:10,completed_pages:4,source_names:sources.map(c=>c.title),output_binding:null,completed_at:null,created_at:new Date(Date.now()-65000).toISOString()};
 await page.route('**/v1/topics',r=>json(r,{topics:[topic]}));await page.route(`**/v1/topics/${id}`,r=>json(r,topic));await page.route(`**/v1/material-processing-runs/${runId}`,r=>json(r,current));
 await page.goto(`/topics/${id}`);
 await expect(page.getByRole('progressbar',{name:'整體流程進度（估計） 67%',exact:true})).toHaveAttribute('value','67');
 await expect(page.getByRole('progressbar',{name:/本階段進度 40%/})).toHaveAttribute('value','40');
 await expect(page.locator('.stage-pages')).toHaveText('已完成 4 / 10 頁');
 await expect(page.locator('.processing-times')).toContainText('1 分');
 await expect(page.locator('.learning-steps > li')).toHaveCount(4);await expect(page.locator('.learning-steps [aria-current=step]')).toContainText('建立教材');
 await expect(page.locator('.processing-timeline [aria-current=step]')).toContainText('建立概念、關係與學習順序');
 await expect(page.getByRole('button',{name:'開啟知識地圖',exact:true})).toHaveCount(0);
 await expect(page.getByText('已選文件 1',{exact:true})).not.toBeVisible();
 const summary=page.locator('.topic-selected-sources > summary');await expect(summary).toHaveText('已選來源 · 23 份');await summary.focus();await page.keyboard.press('Enter');await expect(page.getByText('已選文件 1',{exact:true})).toBeVisible();await summary.click();
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);await page.evaluate(()=>window.scrollTo(0,0));await page.screenshot({path:testInfo.outputPath('topic-analysis-progress.png'),fullPage:true});
 current={...current,progress_stage:'publishing',completed_pages:10};await expect(page.getByRole('progressbar',{name:'整體流程進度（估計） 95%',exact:true})).toBeVisible();await expect(page.getByRole('progressbar',{name:/本階段進度/})).toHaveCount(0);
 current={...runFixture,total_pages:10,completed_pages:10,output_binding:{...runFixture.output_binding,page_count:10}};await expect(page.getByRole('button',{name:'開啟知識地圖',exact:true})).toBeVisible();await expect(page.getByRole('progressbar')).toHaveCount(0);await expect(page.locator('.learning-steps li.is-complete')).toHaveCount(4);
});

for(const status of ['failed','cancelled','partial'] as const) test(`topic material run ${status} retains canonical outcome`,async({page})=>{
 await mockKnowledgeMapApi(page,structureView());
 const current={...runFixture,status,cancel_requested_at:status==='cancelled'?runFixture.updated_at:null,progress_stage:status==='partial'?'completed':'semantics',completed_pages:status==='partial'?2:1,analysis_saved:status==='failed',output_binding:status==='partial'?runFixture.output_binding:null,error_code:status==='failed'?'MATERIAL_ANALYSIS_FAILED':null};
 const topic={topic_id:id,request:'TCP',proposal:plan,version:3,status:'approved',research_id:rid,error_code:null,research:{research_id:rid,material_id:materialId,status:'submitted',candidates:[candidate],selection:['source'],cursor:null,error_code:null,run_id:runId,run:{status:'running',error_code:null}}};
 await page.route('**/v1/topics',r=>json(r,{topics:[topic]}));await page.route(`**/v1/topics/${id}`,r=>json(r,topic));await page.route(`**/v1/material-processing-runs/${runId}`,r=>json(r,current));
 await page.goto(`/topics/${id}`);await expect(page.getByRole('heading',{name:status==='failed'?'教材處理失敗':status==='cancelled'?'已取消教材處理':'教材與知識地圖已建立',exact:true})).toBeVisible();
 await expect(page.getByRole('progressbar')).toHaveCount(0);
 if(status==='partial'){await expect(page.locator('.topic-analysis-result').getByRole('status')).toContainText('部分結果可用');await expect(page.getByRole('button',{name:'開啟知識地圖',exact:true})).toBeVisible();}
 else {await expect(page.getByRole('button',{name:'開啟知識地圖',exact:true})).toHaveCount(0);await expect(page.locator('.topic-analysis-result')).toContainText('已處理 1 / 2 頁');}
 if(status==='failed')await expect(page.getByText('已完成的處理進度已保存；可前往教材建立結果重試，接續未完成的部分。')).toBeVisible();
 await page.getByRole('button',{name:'查看教材建立結果',exact:true}).click();await expect(page).toHaveURL(new RegExp(`/materials/${materialId}/runs/${runId}`));
});

test('topic run loading failures retry and unknown progress remains indeterminate',async({page})=>{
 await mockKnowledgeMapApi(page,structureView());
 const topic={topic_id:id,request:'TCP',proposal:plan,version:3,status:'approved',research_id:rid,error_code:null,research:{research_id:rid,material_id:materialId,status:'submitted',candidates:[candidate],selection:['source'],cursor:null,error_code:null,run_id:runId,run:{status:'running',error_code:null}}};
 await page.route('**/v1/topics',r=>json(r,{topics:[topic]}));await page.route(`**/v1/topics/${id}`,r=>json(r,topic));let fail=true;
 await page.route(`**/v1/material-processing-runs/${runId}`,r=>fail?json(r,{schema:'api-error/v1',request_id:id,reason_code:'INTERNAL_ERROR',retryable:true,message:'Retry'},500):json(r,{...runFixture,status:'running',progress_stage:'evidence',total_pages:null,completed_pages:0,completed_at:null,output_binding:null}));
 await page.goto(`/topics/${id}`);await expect(page.getByRole('alert')).toContainText('無法讀取處理狀態');await expect(page.getByRole('button',{name:'開啟知識地圖',exact:true})).toHaveCount(0);
 fail=false;await page.getByRole('button',{name:'重新讀取進度'}).click();await expect(page.getByRole('progressbar',{name:'整體流程進度（估計），尚無可估計資料'})).not.toHaveAttribute('value');await expect(page.getByRole('progressbar',{name:/本階段進度/})).toHaveCount(0);
});


test('topic rejects a foreign run and ignores late responses after switching topics',async({page})=>{
 await mockKnowledgeMapApi(page,structureView());
 const topic={topic_id:id,request:'TCP',proposal:plan,version:3,status:'approved',research_id:rid,error_code:null,research:{research_id:rid,material_id:materialId,status:'submitted',candidates:[candidate],selection:['source'],cursor:null,error_code:null,run_id:runId,run:{status:'running',error_code:null}}};
 await page.route('**/v1/topics',r=>json(r,{topics:[topic]}));await page.route(`**/v1/topics/${id}`,r=>json(r,topic));
 let release!:()=>void;const held=new Promise<void>(resolve=>{release=resolve;});let retry=false,waiting=false;
 await page.route(`**/v1/material-processing-runs/${runId}`,async r=>{if(!retry)return json(r,{...runFixture,material_id:id});waiting=true;await held;return json(r,runFixture);});
 await page.goto(`/topics/${id}`);await expect(page.getByRole('alert')).toContainText('無法讀取處理狀態');await expect(page.getByRole('button',{name:'開啟知識地圖',exact:true})).toHaveCount(0);
 retry=true;await page.getByRole('button',{name:'重新讀取進度'}).click();await expect.poll(()=>waiting).toBe(true);await page.getByRole('button',{name:'新主題',exact:true}).click();release();
 await expect(page.getByRole('heading',{name:'你想學什麼？',exact:true})).toBeVisible();await expect(page.getByRole('button',{name:'開啟知識地圖',exact:true})).toHaveCount(0);await expect(page.getByRole('progressbar')).toHaveCount(0);
});

test('source search provenance and partial service failures remain clear',async({page})=>{
 await mockKnowledgeMapApi(page,structureView());
 const source={...candidate,state:'failed',error_code:'RESEARCH_SOURCE_BLOCKED',discovered_by:['OpenAlex','Crossref']};
 const topic={topic_id:id,request:'TCP',proposal:plan,version:3,status:'approved',research_id:rid,error_code:null,research:{research_id:rid,material_id:null,status:'selecting',query:'TCP',mode:'self-study',candidates:[source],selection:[],cursor:'more',error_code:'RESEARCH_PARTIAL_SEARCH',run_id:null}};
 await page.route('**/v1/topics',r=>json(r,{topics:[topic]}));await page.route(`**/v1/topics/${id}`,r=>json(r,topic));
 await page.goto(`/topics/${id}`);await expect(page.getByText('部分搜尋服務暫時不可用，已保留其他來源的結果；可稍後載入更多。')).toBeVisible();
 await expect(page.getByText('來源網站拒絕自動下載；可自行下載 PDF 後上傳，或改選其他來源。')).toBeVisible();
 await page.getByText('授權與取得資訊',{exact:true}).click();await expect(page.getByText('搜尋來源：OpenAlex、Crossref（同篇文件合併顯示）')).toBeVisible();
 await page.getByRole('checkbox',{name:'選取 TCP handshake'}).check();await expect(page.getByRole('button',{name:'建立教材與知識地圖'})).toBeEnabled();
});
