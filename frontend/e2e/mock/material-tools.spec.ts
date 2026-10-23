import { expect,test,type Page } from '@playwright/test';
import {artifactId,materialId,runId,structureRevision,structureView,mockKnowledgeMapApi,json} from '../fixtures/knowledge-map';
const mapPath=`/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}`;
const cid='55555555-5555-4555-8555-555555555555';
const tid='66666666-6666-4666-8666-666666666666';
const conversation={conversation_id:cid,material_id:materialId,knowledge_structure_revision:structureRevision,title:'測試對話',created_at:'2026-10-04T00:00:00Z'};
const researchSummary = (work: { research_id:string; query:string; status:string; selection:string[]; run_id:string|null; candidates?:unknown[]; run?:{status:string}; created_at?:string }) => ({
 research_id:work.research_id,query:work.query,status:work.status,selection:work.selection,run_id:work.run_id,
 candidate_count:work.candidates?.length??0,run_status:work.run?.status??null,created_at:work.created_at??'2026-10-06T00:00:00Z',
});

test('material voice text flow saves, cites, reopens and closes',async({page})=>{
 const structure=structureView();await mockKnowledgeMapApi(page,structure);
 let saved=false;let turns:unknown[]=[];
 await page.route('**/v1/materials/*/voice-conversations',r=>{if(r.request().method()==='POST'){saved=true;return json(r,conversation,201);}return json(r,{conversations:saved?[conversation]:[]});});
 await page.route(`**/v1/voice-conversations/${cid}`,r=>json(r,{...conversation,is_current_revision:true,source_resolver:structure.source_resolver,turns}));
 await page.route(`**/v1/voice-conversations/${cid}/turns`,r=>{turns=[{turn_id:tid,question:r.request().postDataJSON().question,answer:{text:'堆疊遵循後進先出。',supported:true,citations:[structure.concepts[0].claims[0]]},status:'ready',audio_url:null,error_code:null}];return json(r,{turn_id:tid},202);});
 await page.goto(mapPath);await page.getByRole('button',{name:'教材問答',exact:true}).click();
 await page.getByLabel('問題／辨識文字').fill('堆疊是什麼？');await page.getByRole('button',{name:'送出問題',exact:true}).click();
 await expect(page.locator('.voice-answer')).toHaveText('堆疊遵循後進先出。');
 await expect(page.locator('.voice-source-chips').getByRole('button',{name:'第 1 頁',exact:true})).toHaveAttribute('title',/PDF 第 1 頁/);
 await page.locator('.voice-source-chips').getByRole('button',{name:'第 1 頁',exact:true}).click();await expect(page.getByRole('dialog',{name:'教材來源'})).toBeVisible();await page.keyboard.press('Escape');await expect(page.getByRole('dialog',{name:'教材來源'})).toHaveCount(0);await expect(page.getByRole('dialog',{name:'教材問答'})).toBeVisible();
 await page.getByRole('button',{name:'關閉',exact:true}).click();await expect(page.getByLabel('問題／辨識文字')).toHaveCount(0);await expect(page.getByRole('button',{name:'教材問答',exact:true})).toBeFocused();
 await page.getByRole('button',{name:'教材問答',exact:true}).click();await page.getByRole('button',{name:'對話紀錄',exact:true}).click();await page.locator(`[data-conversation-id="${cid}"]`).click();await expect(page.locator('.voice-answer')).toHaveText('堆疊遵循後進先出。');
 await page.keyboard.press('Escape');await expect(page.getByRole('dialog',{name:'教材問答'})).toHaveCount(0);await expect(page.getByRole('button',{name:'教材問答',exact:true})).toBeFocused();
});

test('late microphone permission after cancel stops tracks and sends nothing',async({page})=>{
 await mockKnowledgeMapApi(page,structureView());await page.route('**/v1/materials/*/voice-conversations',r=>json(r,{conversations:[]}));
 await page.addInitScript(()=>{let resolve:(value:MediaStream)=>void;Object.assign(window,{stopped:false,allowMic:()=>resolve({getTracks:()=>[{stop:()=>{(window as any).stopped=true;}}]} as unknown as MediaStream)});Object.defineProperty(navigator.mediaDevices,'getUserMedia',{value:()=>new Promise(r=>resolve=r)});});
 await page.goto(mapPath);await page.getByRole('button',{name:'教材問答',exact:true}).click();await page.getByRole('switch',{name:'語音模式',exact:true}).click();await expect(page.locator('.voice-composer-status')).toContainText('等待麥克風權限');await page.getByRole('switch',{name:'語音模式',exact:true}).click();await page.evaluate(()=>(window as any).allowMic());await expect.poll(()=>page.evaluate(()=>(window as any).stopped)).toBe(true);
 await expect(page.getByRole('switch',{name:'語音模式',exact:true})).toHaveAttribute('aria-checked','false');
});

test('research preserves arbitrary source selection and requires irreversible confirmation',async({page})=>{
 await mockKnowledgeMapApi(page,structureView());
 const candidates=Array.from({length:5},(_,i)=>({id:`source-${i}`,kind:i===0?'wikipedia':'paper',title:`官方教材 ${i+1}`,authors:'Fixture',year:2026,url:'https://docs.python.org/3/',doi:null,license:'PSF-2.0',license_url:'https://docs.python.org/3/license.html',version:'3',eligible:true,reason:'可使用',state:'candidate'}));
 Object.assign(candidates[0],{title:'維基百科教材 1',url:'https://zh.wikipedia.org/wiki/Internet',license:'CC-BY-4.0',license_url:'https://creativecommons.org/licenses/by/4.0/',version:'取得時的網頁版本'});
 let value:any={research_id:cid,material_id:materialId,query:'網路通訊',mode:'review',status:'selecting',candidates,selection:[],cursor:null,error_code:null,run_id:null,is_current_revision:true};
 await page.route('**/v1/materials/*/research',r=>r.request().method()==='POST'?json(r,value,202):json(r,{researches:[researchSummary(value)]}));
 await page.route(`**/v1/research/${cid}`,r=>json(r,value));
 await page.route(`**/v1/research/${cid}/actions`,r=>{const b=r.request().postDataJSON();expect(b.selected).toHaveLength(5);value={...value,status:'ready',selection:b.selected,candidates:candidates.map(c=>({...c,state:'ready'}))};return json(r,value);});
 let submits=0;await page.route(`**/v1/research/${cid}/submit`,r=>{expect(r.request().postDataJSON()).toEqual({confirmed:true});submits++;value={...value,status:'submitted',run_id:runId};return json(r,value,202);});
 await page.goto(mapPath);await page.getByRole('tab',{name:'補充學習',exact:true}).click();await expect(page).toHaveURL(new RegExp(`/materials/${materialId}/research$`));await expect(page.getByRole('dialog',{name:'補充學習'})).toHaveCount(0);await page.locator('.research-card-link').click();
 await page.getByLabel('來源類型',{exact:true}).selectOption('wikipedia');await expect(page.locator('.research-candidates article')).toHaveCount(1);await expect(page.locator('.source-byline')).toContainText('Wikipedia');await page.getByLabel('來源類型',{exact:true}).selectOption('all');
 await page.getByRole('checkbox',{name:'全選可用來源'}).check();
 await page.getByRole('tablist',{name:'教材學習內容'}).getByRole('tab',{name:'知識地圖',exact:true}).click();await expect(page.locator('.research-panel')).toHaveCount(0);
 await page.getByRole('tab',{name:'補充學習',exact:true}).click();await expect(page.locator('.research-candidates')).toHaveCount(0);await page.locator('.research-card-link').click();await expect(page.locator('.research-candidates input:checked')).toHaveCount(5);
 await page.getByLabel('篩選來源',{exact:true}).fill('官方教材 3');await expect(page.locator('.research-candidates article')).toHaveCount(1);await expect(page.locator('.tool-stage-footer').getByText('已選 5 份來源',{exact:true})).toBeVisible();await page.getByLabel('篩選來源',{exact:true}).clear();await expect(page.getByLabel('想多了解什麼？',{exact:true})).toHaveCount(0);
 await page.getByRole('button',{name:'取得選取的 5 份來源'}).click();
 await expect(page.getByRole('button',{name:'確認加入教材'})).toBeDisabled();await expect(page.getByText('無法撤回新增內容')).toBeVisible();expect(submits).toBe(0);
 await page.getByLabel('我了解加入後不可逆').check();await page.getByRole('button',{name:'確認加入教材'}).click();await expect(page.getByRole('button',{name:'查看教材分析'})).toBeVisible();await expect(page.getByRole('button',{name:'返回補充學習'})).toBeVisible();await expect(page.getByRole('checkbox',{name:'全選可用來源'})).toHaveCount(0);expect(submits).toBe(1);await page.getByRole('button',{name:'查看教材分析'}).click();await expect(page).toHaveURL(new RegExp(`/runs/${runId}$`));
});

test('closing a conversation releases every private audio element',async({page})=>{
 const structure=structureView();await mockKnowledgeMapApi(page,structure);
 const wav=Buffer.alloc(44+24000*2*4);wav.write('RIFF',0);wav.writeUInt32LE(wav.length-8,4);wav.write('WAVEfmt ',8);wav.writeUInt32LE(16,16);wav.writeUInt16LE(1,20);wav.writeUInt16LE(1,22);wav.writeUInt32LE(24000,24);wav.writeUInt32LE(48000,28);wav.writeUInt16LE(2,32);wav.writeUInt16LE(16,34);wav.write('data',36);wav.writeUInt32LE(wav.length-44,40);
 await page.route('**/v1/materials/*/voice-conversations',r=>json(r,{conversations:[conversation]}));
 await page.route(`**/v1/voice-conversations/${cid}`,r=>json(r,{...conversation,is_current_revision:true,source_resolver:structure.source_resolver,turns:[0,1].map(i=>({turn_id:`turn-${i}`,question:'合成問題',answer:{text:'合成回答',supported:false,citations:[]},status:'ready',error_code:null,audio_url:`/v1/voice-conversations/${cid}/turns/${i}/audio`}))}));
 await page.route(`**/v1/voice-conversations/${cid}/turns/*/audio`,r=>r.fulfill({status:200,contentType:'audio/wav',body:wav}));
 await page.goto(mapPath);await page.getByRole('button',{name:'教材問答',exact:true}).click();await page.getByRole('button',{name:'對話紀錄',exact:true}).click();await page.locator(`[data-conversation-id="${cid}"]`).click();await expect(page.locator('.voice-turn audio')).toHaveCount(2);
 await page.evaluate(async()=>{const clips=Array.from(document.querySelectorAll<HTMLAudioElement>('.voice-turn audio'));(window as any).__clips=clips;for(const a of clips)await a.play();});
 expect(await page.evaluate(()=>(window as any).__clips[0].paused)).toBe(true);
 await page.getByRole('button',{name:'關閉',exact:true}).click();
 expect(await page.evaluate(()=>(window as any).__clips.every((a:HTMLAudioElement)=>a.paused&&!a.getAttribute('src')))).toBe(true);
});

test('late permission failure cannot stop a newer voice session',async({page})=>{
 await page.route('**/assets/voice-vad-*.js',route=>route.fulfill({contentType:'text/javascript',body:'export async function createVoiceVad(){return {pause:async()=>{},start:async()=>{},destroy:async()=>{}};}'}));
 await mockKnowledgeMapApi(page,structureView());await page.route('**/v1/materials/*/voice-conversations',r=>json(r,{conversations:[]}));
 await page.addInitScript(()=>{
   let count=0;const state:any={stopped:false};(window as any).micRace=state;
   Object.defineProperty(navigator.mediaDevices,'getUserMedia',{value:()=>new Promise((resolve,reject)=>{count++;if(count===1)state.rejectOld=()=>reject(new Error('old request rejected'));else state.allowNew=()=>resolve({getTracks:()=>[{stop:()=>{state.stopped=true;}}]});})});
   (window as any).AudioContext=class{state='running';resume(){return Promise.resolve();}close(){this.state='closed';return Promise.resolve();}};
 });
 await page.goto(mapPath);await page.getByRole('button',{name:'教材問答',exact:true}).click();await page.getByRole('switch',{name:'語音模式',exact:true}).click();await expect(page.locator('.voice-composer-status')).toContainText('等待麥克風權限');await page.getByRole('switch',{name:'語音模式',exact:true}).click();await page.getByRole('switch',{name:'語音模式',exact:true}).click();await expect.poll(()=>page.evaluate(()=>typeof (window as any).micRace.allowNew)).toBe('function');await page.evaluate(()=>(window as any).micRace.allowNew());await expect(page.locator('.voice-composer-status')).toContainText('正在聆聽');
 await page.evaluate(()=>(window as any).micRace.rejectOld());expect(await page.evaluate(()=>(window as any).micRace.stopped)).toBe(false);
 await page.getByRole('button',{name:'關閉',exact:true}).click();expect(await page.evaluate(()=>(window as any).micRace.stopped)).toBe(true);
});


test('supplementary input survives tab switches without submitting a search',async({page})=>{
 await mockKnowledgeMapApi(page,structureView());let searches=0;
 await page.route('**/v1/materials/*/research',r=>{if(r.request().method()==='POST')searches++;return json(r,{researches:[]});});
 await page.goto(mapPath);await page.getByRole('tab',{name:'補充學習',exact:true}).click();await page.getByLabel('想多了解什麼？',{exact:true}).fill('保留尚未送出的需求');
 await page.getByRole('tablist',{name:'教材學習內容'}).getByRole('tab',{name:'知識地圖',exact:true}).click();await page.getByRole('tab',{name:'補充學習',exact:true}).click();
 await expect(page.getByLabel('想多了解什麼？',{exact:true})).toHaveValue('保留尚未送出的需求');expect(searches).toBe(0);
 await page.setViewportSize({width:390,height:844});expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
 await expect(page.getByRole('tab',{name:'補充學習',exact:true})).toHaveAttribute('aria-selected','true');await expect(page.getByRole('button',{name:'教材問答',exact:true})).toBeVisible();
 await expect.poll(async()=>{const nav=await page.getByRole('tablist',{name:'教材學習內容'}).boundingBox(),selected=await page.getByRole('tab',{name:'補充學習',exact:true}).boundingBox();return !!nav&&!!selected&&selected.x>=nav.x-1&&selected.x+selected.width<=nav.x+nav.width+1;}).toBe(true);
});


test('completed supplementation refreshes the map tab to the current material head',async({page})=>{
 await mockKnowledgeMapApi(page,structureView());
 const nextRun='88888888-8888-4888-8888-888888888888',nextRevision=`knowledge-structure:sha256:${'b'.repeat(64)}`;let reads=0;
 await page.route(`**/v1/materials/${materialId}`,r=>{reads++;return json(r,{schema:'material-library-item/v1',material_id:materialId,source_artifact_id:artifactId,display_name:'測試教材',size_bytes:100,created_at:'2026-10-04T00:00:00Z',head_revision:reads>1?nextRevision:structureRevision,latest_attempt:null,study_sessions:[],available_structures:[{run_id:runId,knowledge_structure_revision:structureRevision,created_at:'2026-10-04T00:00:00Z',status:'succeeded'},{run_id:nextRun,knowledge_structure_revision:nextRevision,created_at:'2026-10-04T00:00:00Z',status:'partial'}]});});
 const work={research_id:cid,material_id:materialId,query:'已完成補充',mode:'review',status:'submitted',candidates:[],selection:[],cursor:null,error_code:null,run_id:nextRun,run:{status:'partial',error_code:null}};
 await page.route('**/v1/materials/*/research',r=>json(r,{researches:[researchSummary(work)]}));await page.route(`**/v1/research/${cid}`,r=>json(r,work));
 await page.goto(`/materials/${materialId}/research`);await page.locator('.research-card-link').click();await expect.poll(()=>reads).toBeGreaterThanOrEqual(2);
 await page.getByRole('tablist',{name:'教材學習內容'}).getByRole('tab',{name:'知識地圖',exact:true}).click();await expect(page).toHaveURL(new RegExp(`/runs/${nextRun}/knowledge-structures/${encodeURIComponent(nextRevision)}$`));
});


test('one underline tab row opens review and preserves keyboard focus across material pages',async({page})=>{
 await mockKnowledgeMapApi(page,structureView());
 await page.route('**/v1/materials/*/research',r=>json(r,{researches:[]}));
 await page.route('**/v1/card-sets',r=>json(r,{schema:'card-set-list/v1',card_sets:[]}));
 await page.route(`**/v1/materials/${materialId}/sources`,r=>json(r,{schema:'material-sources/v1',material_id:materialId,sources:[],discard_requested:false}));
 await page.goto(mapPath);const tabs=page.getByRole('tablist',{name:'教材學習內容'});await expect(tabs).toHaveCount(1);await expect(tabs.getByRole('tab')).toHaveText(['知識地圖','學習與測驗','概念卡','Podcast','補充學習','教材來源']);
 await page.getByRole('tab',{name:'補充學習',exact:true}).click();await page.getByRole('tab',{name:'學習與測驗',exact:true}).click();await expect(page.locator('#map-panel-review')).toBeVisible();await expect(page.getByRole('tab',{name:'學習與測驗',exact:true})).toHaveAttribute('aria-selected','true');
 await page.getByRole('tab',{name:'學習與測驗',exact:true}).focus();await page.keyboard.press('ArrowRight');await expect(page.getByRole('tab',{name:'概念卡',exact:true})).toBeFocused();await expect(page).toHaveURL(new RegExp(`/materials/${materialId}/concept-cards$`));
 await page.keyboard.press('End');await expect(page.getByRole('tab',{name:'教材來源',exact:true})).toBeFocused();await expect(page).toHaveURL(new RegExp(`/materials/${materialId}/sources$`));
 await page.keyboard.press('ArrowRight');await expect(page.getByRole('tab',{name:'知識地圖',exact:true})).toBeFocused();await expect(page.locator('#map-panel-focus')).toBeVisible();
 // 目前分頁是深色膠囊，其餘分頁透明。
 const styles=await page.getByRole('tab').evaluateAll(tabs=>tabs.map(e=>getComputedStyle(e).backgroundColor));expect(styles[0]).toBe('rgb(29, 39, 66)');expect(styles.slice(1).every(color=>color!=='rgb(29, 39, 66)')).toBe(true);
});


test('one material header row; map search floats on the map and only content tabs with their own lists keep a search field',async({page})=>{
 await mockKnowledgeMapApi(page,structureView());
 await page.route('**/v1/card-sets',r=>json(r,{schema:'card-set-list/v1',card_sets:[]}));
 await page.route('**/v1/podcasts',r=>json(r,{schema:'podcast-list/v1',podcasts:[]}));
 await page.route('**/v1/materials/*/research',r=>json(r,{researches:[]}));
 await page.route(`**/v1/materials/${materialId}/sources`,r=>json(r,{schema:'material-sources/v1',material_id:materialId,sources:[],discard_requested:false}));
 for(const width of [1440,390]){
  await page.setViewportSize({width,height:844});await page.goto(mapPath);
  const nav=(await page.getByRole('tablist',{name:'教材學習內容'}).boundingBox())!;
  // 教材內只有一列教材頁首；全站頁首隱藏。
  await expect(page.locator('.app-header')).toBeHidden();
  // 地圖搜尋與摘要浮在地圖上（地圖滿版），不另佔一列。
  const anchor=(await page.getByLabel('搜尋概念或關鍵字',{exact:true}).boundingBox())!;const graph=(await page.locator('.focus-graph').boundingBox())!;
  expect(anchor.y).toBeGreaterThanOrEqual(graph.y);expect(anchor.x).toBeGreaterThanOrEqual(graph.x);expect(anchor.y+anchor.height).toBeLessThanOrEqual(graph.y+graph.height);
  expect(graph.y-nav.y-nav.height).toBeLessThanOrEqual(20);
  for(const [tab,label] of [['學習與測驗',null],['概念卡',null],['Podcast',null],['補充學習','想多了解什麼？'],['教材來源','搜尋教材來源']] as const){
   await page.getByRole('tab',{name:tab,exact:true}).click();
   await expect(page.getByRole('tab',{name:tab,exact:true})).toHaveAttribute('aria-selected','true');
   const currentNav=(await page.getByRole('tablist',{name:'教材學習內容'}).boundingBox())!;expect(Math.abs(currentNav.y-nav.y)).toBeLessThanOrEqual(1);
   if(label){
    await page.getByLabel(label,{exact:true}).waitFor();const box=(await page.getByLabel(label,{exact:true}).boundingBox())!;
    expect(box.y).toBeGreaterThanOrEqual(currentNav.y+currentNav.height);
   } else await expect(page.getByLabel('搜尋概念或關鍵字',{exact:true})).toHaveCount(0);
   // 教材問答在教材頁首右端，緊鄰頭像選單。
   const voice=(await page.getByRole('button',{name:'教材問答',exact:true}).boundingBox())!;const toolbar=(await page.locator('.material-learning-toolbar').boundingBox())!;const account=(await page.locator('.material-learning-toolbar .account-menu').boundingBox())!;
   expect(voice.y).toBeGreaterThanOrEqual(toolbar.y);expect(voice.y+voice.height).toBeLessThanOrEqual(toolbar.y+toolbar.height);expect(account.x-voice.x-voice.width).toBeLessThanOrEqual(12);expect(toolbar.x+toolbar.width-account.x-account.width).toBeLessThanOrEqual(12);
   if(tab==='概念卡'||tab==='Podcast'){
    await expect(page.locator('.material-search-row').getByRole('searchbox')).toHaveCount(0);
    const create=(await page.locator('.material-search-row').getByRole('button',{name:tab==='概念卡'?'建立卡組':'建立 Podcast',exact:true}).boundingBox())!;expect(create.y).toBeGreaterThanOrEqual(currentNav.y+currentNav.height);
   }
  }
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
 }
});

test('published sources filter files without idle update steps and keep upload errors visible',async({page})=>{
 await mockKnowledgeMapApi(page,structureView());
 const names=['甲教材.pdf','乙教材.pdf'];
 await page.route(`**/v1/materials/${materialId}/sources`,r=>json(r,{schema:'material-sources/v1',material_id:materialId,discard_requested:false,sources:names.map((name,i)=>({source_id:`${i+1}1111111-1111-4111-8111-111111111111`,normalization_id:`${i+1}2222222-2222-4222-8222-222222222222`,original_artifact_id:artifactId,original_name:name,origin:i?'research':'upload',media_type:'application/pdf',status:'ready',attempt:1,page_count:2,included:true,error_code:null,normalized_artifact_id:artifactId}))}));
 await page.goto(`/materials/${materialId}/sources`);await expect(page.locator('.material-context:visible')).toHaveCount(1);await expect(page.locator('.source-guide')).toHaveCount(0);await expect(page.locator('.source-list-footer')).toHaveCount(0);
 await expect(page.getByRole('region',{name:'使用者上傳',exact:true})).toContainText('甲教材.pdf');
 await expect(page.getByRole('region',{name:'網路補充學習資源',exact:true})).toContainText('乙教材.pdf');
 await expect(page.getByRole('button',{name:'開啟目前地圖',exact:true})).toHaveCount(0);
 await page.getByLabel('搜尋教材來源',{exact:true}).fill('乙');await expect(page.locator('.source-row')).toHaveCount(1);await expect(page.locator('.source-row .source-number')).toHaveText('2');
 await page.getByLabel('選擇新增教材',{exact:true}).setInputFiles({name:'unsupported.exe',mimeType:'application/octet-stream',buffer:Buffer.from('fixture')});await expect(page.locator('#source-selection-error')).toBeVisible();await expect(page.locator('.source-row')).toHaveCount(1);
});


test('fast reads do not flash a loading message; slow reads and failures remain visible',async({page})=>{
 await mockKnowledgeMapApi(page,structureView());await page.clock.install();
 await page.route('**/v1/card-sets',r=>json(r,{schema:'card-set-list/v1',card_sets:[]}));
 const material={schema:'material-library-item/v1',material_id:materialId,source_artifact_id:artifactId,display_name:'測試教材',size_bytes:100,created_at:'2026-10-04T00:00:00Z',head_revision:structureRevision,latest_attempt:null,study_sessions:[],available_structures:[{run_id:runId,knowledge_structure_revision:structureRevision,created_at:'2026-10-04T00:00:00Z',status:'succeeded'}]};
 let release!:()=>void;let held=new Promise<void>(resolve=>release=resolve);let fail=false;
 await page.route(`**/v1/materials/${materialId}`,async r=>{await held;return fail?json(r,{schema:'api-error/v1',request_id:materialId,reason_code:'RESOURCE_NOT_FOUND',retryable:false,message:'Unavailable'},404):json(r,material);});
 await page.goto(`/materials/${materialId}/concept-cards`);await expect(page.locator('.quiet-loading')).toHaveCount(1);await page.clock.runFor(100);await expect(page.getByText('正在讀取教材',{exact:true})).toHaveCount(0);release();
 await expect(page.getByRole('tab',{name:'概念卡',exact:true})).toBeVisible();await expect(page.locator('.quiet-loading')).toHaveCount(0);await page.clock.runFor(500);await expect(page.getByText('正在讀取教材',{exact:true})).toHaveCount(0);
 held=new Promise<void>(resolve=>release=resolve);fail=true;await page.reload();await expect(page.locator('.quiet-loading')).toHaveCount(1);await page.clock.runFor(400);await expect(page.getByText('正在讀取教材',{exact:true})).toBeVisible();await expect(page.locator('.quiet-loading .loading-ring')).toHaveCount(0);release();await expect(page.getByRole('heading',{name:'無法讀取教材',exact:true})).toBeVisible();
});

test('switching tabs reuses the material index while still fetching a fresh copy',async({page})=>{
 await mockKnowledgeMapApi(page,structureView());await page.route('**/v1/card-sets',r=>json(r,{schema:'card-set-list/v1',card_sets:[]}));
 await page.goto(mapPath);await expect(page.locator('.react-flow')).toBeVisible();let reads=0;let release!:()=>void;const held=new Promise<void>(resolve=>release=resolve);
 await page.route(`**/v1/materials/${materialId}`,async r=>{reads++;await held;return r.fulfill({status:500,json:{schema:'api-error/v1',request_id:materialId,reason_code:'INTERNAL_ERROR',retryable:true,message:'Temporary failure'}});});
 await page.getByRole('tab',{name:'概念卡',exact:true}).click();await expect(page.getByRole('tab',{name:'概念卡',exact:true})).toHaveAttribute('aria-selected','true');await expect.poll(()=>reads).toBe(1);await expect(page.locator('.quiet-loading')).toHaveCount(0);release();await expect(page.getByRole('heading',{name:'無法讀取教材',exact:true})).toBeVisible();
});

test('Voice bubble keeps the page reachable and restores focus when dismissed',async({page})=>{
 await mockKnowledgeMapApi(page,structureView());
 await page.route('**/v1/materials/*/voice-conversations',r=>json(r,{conversations:[]}));
 await page.goto(mapPath);
 for(const width of [1440,390]) {
  await page.setViewportSize({width,height:844});const opener=page.getByRole('button',{name:'教材問答',exact:true});await opener.click();
  const bubble=page.getByRole('dialog',{name:'教材問答'});await expect(bubble).toHaveAttribute('aria-modal','false');
  // 桌機從教材頁首下緣展開；手機是上緣留 8px 的全高面板。
  const box=(await bubble.boundingBox())!,header=(await page.locator('.material-learning-toolbar').boundingBox())!;
  expect(box.width).toBeLessThanOrEqual(420);expect(box.x).toBeGreaterThanOrEqual(0);expect(box.x+box.width).toBeLessThanOrEqual(width);expect(box.y).toBeGreaterThanOrEqual(width===390?8:header.y+header.height);
  await bubble.getByRole('textbox',{name:'問題／辨識文字'}).fill('尚未送出的測試');await page.keyboard.press('Escape');await expect(bubble).toHaveCount(0);await expect(opener).toBeFocused();
 }
});


test('research collection cards open scoped detail pages and retain task drafts through navigation',async({page})=>{
 await page.setViewportSize({width:1440,height:1000});await mockKnowledgeMapApi(page,structureView());
 const candidates=Array.from({length:4},(_,i)=>({id:`source-${i}`,kind:'official',title:`補充來源 ${i+1}`,authors:'Fixture',year:2026,url:'https://docs.python.org/3/',doi:null,license:'PSF-2.0',license_url:null,version:'3',eligible:true,reason:'fixture',state:'candidate'}));
 const records=[
  {research_id:cid,material_id:materialId,query:'TCP 重傳機制',mode:'review',status:'selecting',candidates,selection:[] as string[],cursor:null,error_code:null,run_id:null as string|null,created_at:'2026-10-06T04:00:00Z',is_current_revision:true,run:undefined as {status:string;error_code:null}|undefined},
  {research_id:tid,material_id:materialId,query:'DNS 查詢流程',mode:'review',status:'submitted',candidates,selection:['source-0'],cursor:null,error_code:null,run_id:runId,created_at:'2026-10-06T03:00:00Z',is_current_revision:true,run:{status:'running',error_code:null}},
  {research_id:'77777777-7777-4777-8777-777777777777',material_id:materialId,query:'HTTP 快取策略',mode:'review',status:'submitted',candidates,selection:['source-1'],cursor:null,error_code:null,run_id:runId,created_at:'2026-10-06T02:00:00Z',is_current_revision:true,run:{status:'succeeded',error_code:null}},
 ];
 let detailReads=0;
 await page.route(`**/v1/materials/${materialId}/research`,r=>json(r,{researches:records.map(researchSummary)}));
 await page.route('**/v1/research/*',r=>{detailReads++;return json(r,records.find(item=>item.research_id===new URL(r.request().url()).pathname.split('/').at(-1)));});
 await page.goto(`/materials/${materialId}/research`);
 const first=page.getByRole('article',{name:records[0].query,exact:true}),progress=page.getByRole('article',{name:records[1].query,exact:true});
 await expect(page.locator('.research-record')).toHaveCount(3);expect(detailReads).toBe(0);
 await expect(page.locator('.research-candidates')).toHaveCount(0);
 // 列表卡只說明選取狀態，不列搜尋找到的候選總數。
 await expect(first).toContainText('已選 0 份');await expect(first).not.toContainText('候選');await expect(progress).toContainText('已送出 1 份');
 const a=(await first.boundingBox())!,b=(await progress.boundingBox())!;expect(b.x).toBeGreaterThan(a.x+a.width);expect(b.y).toBeCloseTo(a.y,0);
 const last=(await page.locator('.research-record').last().boundingBox())!,grid=(await page.locator('.research-records').boundingBox())!;
 const gutter=await page.locator('.research-records').evaluate(el=>el.offsetWidth-el.clientWidth+parseFloat(getComputedStyle(el).paddingRight));
 expect(last.x+last.width).toBeCloseTo(grid.x+grid.width-gutter,0);
 records[1].run!.status='succeeded';await expect(progress.locator('.research-status')).toHaveText('已完成');
 await page.getByLabel('想多了解什麼？',{exact:true}).fill('下一個搜尋草稿');
 await first.getByRole('link',{name:records[0].query,exact:true}).click();
 await expect(page).toHaveURL(new RegExp(`/materials/${materialId}/research/${cid}$`));
 await expect(page.getByRole('tab',{name:'補充學習',exact:true})).toHaveAttribute('aria-selected','true');
 await expect(page.locator('.research-records')).toHaveCount(0);await expect(page.locator('.research-candidates article')).toHaveCount(4);
 expect((await page.locator('.research-workspace').boundingBox())!.width).toBeGreaterThan(1300);
 expect((await page.locator('.research-detail-body').boundingBox())!.width).toBeGreaterThan(1000);
 await page.getByRole('checkbox',{name:'選取 補充來源 1',exact:true}).check();await page.getByLabel('篩選來源',{exact:true}).fill('來源 1');
 await page.getByRole('button',{name:'返回補充學習',exact:true}).click();
 await expect(page).toHaveURL(new RegExp(`/materials/${materialId}/research$`));await expect(page.getByLabel('想多了解什麼？',{exact:true})).toHaveValue('下一個搜尋草稿');
 await expect(first).toContainText('已選 1 份');await expect(page.locator('.research-candidates')).toHaveCount(0);
 await page.goBack();await expect(page).toHaveURL(new RegExp(`/research/${cid}$`));
 await expect(page.getByRole('checkbox',{name:'選取 補充來源 1',exact:true})).toBeChecked();await expect(page.getByLabel('篩選來源',{exact:true})).toHaveValue('來源 1');
 await page.getByRole('button',{name:'返回補充學習',exact:true}).click();
 await page.getByRole('article',{name:records[2].query,exact:true}).getByRole('button',{name:'查看完成結果',exact:true}).click();
 await expect(page).toHaveURL(new RegExp(`/research/${records[2].research_id}$`));
 await expect(page.getByRole('heading',{name:'補充教材已建立',exact:true})).toBeVisible();
 await page.reload();await expect(page.getByRole('heading',{name:records[2].query,exact:true})).toBeVisible();
 await page.getByRole('button',{name:'返回補充學習',exact:true}).click();
 await page.setViewportSize({width:390,height:844});
 const mobileA=(await first.boundingBox())!,mobileB=(await progress.boundingBox())!;expect(mobileB.x).toBeCloseTo(mobileA.x,0);expect(mobileB.y).toBeGreaterThanOrEqual(mobileA.y+mobileA.height);
 await first.getByRole('link').click();await expect(page.locator('.research-candidates article')).toHaveCount(4);
 expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBe(390);
});

test('research deletion follows the shared menu pattern, cancels safely and cannot be resurrected by a late list',async({page})=>{
 await mockKnowledgeMapApi(page,structureView());
 const work={research_id:cid,material_id:materialId,query:'待刪除查詢',status:'searching',selection:[] as string[],run_id:null,candidates:[],mode:'review',created_at:'2026-10-06T00:00:00Z'};
 let deleted=false,deletes=0,holdNext=false,held=false,release!:()=>void;const late=new Promise<void>(resolve=>release=resolve);
 await page.route(`**/v1/materials/${materialId}/research`,async r=>{const snapshot={researches:deleted?[]:[researchSummary(work)]};if(holdNext&&!held){held=true;await late;}return json(r,snapshot);});
 await page.route(`**/v1/research/${cid}`,r=>{
  if(r.request().method()==='DELETE'){deletes++;if(deletes===1)return json(r,{schema:'api-error/v1',request_id:materialId,reason_code:'INTERNAL_ERROR',retryable:true,message:'Try again'},500);deleted=true;return json(r,{research_id:cid,status:'deleted'});}
  return deleted?json(r,{schema:'api-error/v1',request_id:materialId,reason_code:'RESOURCE_NOT_FOUND',retryable:false,message:'Not found'},404):json(r,work);
 });
 await page.goto(`/materials/${materialId}/research`);
 const management=page.getByRole('button',{name:'管理查詢「待刪除查詢」',exact:true});
 await management.click();await page.keyboard.press('Escape');await expect(management).toBeFocused();
 await management.click();await page.getByRole('button',{name:'刪除查詢',exact:true}).click();
 const confirmation=page.getByRole('group',{name:'刪除查詢「待刪除查詢」確認'});
 await expect(confirmation.getByRole('button',{name:'取消',exact:true})).toBeFocused();
 await expect(confirmation).toContainText('來源檔案');await confirmation.getByRole('button',{name:'取消',exact:true}).click();expect(deletes).toBe(0);
 await management.click();await page.getByRole('button',{name:'刪除查詢',exact:true}).click();
 await confirmation.getByRole('button',{name:'確認刪除查詢',exact:true}).click();await expect(confirmation.getByRole('alert')).toBeVisible();
 holdNext=true;await expect.poll(()=>held).toBe(true);
 await confirmation.getByRole('button',{name:'確認刪除查詢',exact:true}).dblclick();await expect(page.locator('.research-record')).toHaveCount(0);expect(deletes).toBe(2);
 release();await page.waitForTimeout(300);await expect(page.locator('.research-record')).toHaveCount(0);await expect(page.locator('.collection-summary')).toBeFocused();
 await page.goto(`/materials/${materialId}/research/${cid}`);await expect(page.getByRole('heading',{name:'無法讀取查詢',exact:true})).toBeVisible();await expect(page.locator('.research-candidates')).toHaveCount(0);
 await page.getByRole('button',{name:'返回補充學習',exact:true}).click();await expect(page.locator('.research-record')).toHaveCount(0);
});

test('research detail rejects a foreign material and late reads cannot replace another detail',async({page})=>{
 await mockKnowledgeMapApi(page,structureView());
 const work={research_id:cid,material_id:tid,query:'其他教材的查詢',mode:'review',status:'selecting',selection:[],candidates:[],cursor:null,error_code:null,run_id:null,created_at:'2026-10-06T00:00:00Z'};
 await page.route(`**/v1/materials/${materialId}/research`,r=>json(r,{researches:[]}));
 await page.route(`**/v1/research/${cid}`,r=>json(r,work));
 await page.goto(`/materials/${materialId}/research/${cid}`);await expect(page.getByRole('heading',{name:'找不到這筆補充學習',exact:true})).toBeVisible();await expect(page.getByRole('heading',{name:work.query,exact:true})).toHaveCount(0);
 let release!:()=>void;const delayed=new Promise<void>(resolve=>release=resolve);
 await page.route(`**/v1/research/${tid}`,async r=>{await delayed;return json(r,{...work,material_id:materialId,research_id:tid,query:'晚到的查詢'});});
 await page.goto(`/materials/${materialId}/research/${tid}`);await page.getByRole('button',{name:'返回補充學習',exact:true}).click();
 release();await page.waitForTimeout(200);await expect(page.getByRole('heading',{name:'晚到的查詢',exact:true})).toHaveCount(0);await expect(page.locator('.research-detail')).toHaveCount(0);
});

test('supplement requires editable scope confirmation before creating research',async({page})=>{
 await mockKnowledgeMapApi(page,structureView());let searches=0,plans=0;
 const proposal={title:'停止與求助',goals:['了解何時求助'],topics:['信心：如何判斷不確定性','停止：何時結束重試']};
 let scope:any={topic_id:tid,material_id:materialId,request:'代理何時停止並詢問人類？',proposal,version:2,status:'ready',research_id:null,error_code:null};
 const work={research_id:cid,material_id:materialId,query:scope.request,mode:'review',status:'searching',selection:[],candidates:[],cursor:null,error_code:null,run_id:null,created_at:'2026-10-06T00:00:00Z'};
 await page.route(`**/v1/materials/${materialId}/research`,r=>{expect(r.request().method()).toBe('GET');return json(r,{researches:searches?[researchSummary(work)]:[]});});
 await page.route('**/v1/topics',r=>{if(r.request().method()==='POST'){plans++;expect(r.request().postDataJSON()).toEqual({request:scope.request,material_id:materialId});expect(r.request().headers()['idempotency-key']).toBeTruthy();return json(r,scope,202);}return json(r,{topics:[]});});
 await page.route(`**/v1/topics?material_id=${materialId}`,r=>json(r,{topics:plans?[scope]:[]}));
 await page.route(`**/v1/topics/${tid}`,r=>json(r,scope));
 await page.route(`**/v1/topics/${tid}/approve`,r=>{searches++;expect(r.request().postDataJSON().proposal.topics[1]).toBe('停止：重試沒有進展時交由人類判斷');scope={...scope,status:'approved',research_id:cid,version:3};return json(r,scope);});
 await page.route(`**/v1/research/${cid}`,r=>json(r,work));
 await page.goto(`/materials/${materialId}/research`);await page.getByLabel('想多了解什麼？',{exact:true}).fill(scope.request);await page.getByRole('button',{name:'產生搜尋範圍',exact:true}).click();
 await expect(page).toHaveURL(new RegExp(`/topics/${tid}$`));await expect(page.getByRole('heading',{name:'確認學習範圍',exact:true})).toBeVisible();expect(plans).toBe(1);expect(searches).toBe(0);
 await page.reload();await expect(page.getByLabel('主題 2',{exact:true})).toHaveValue('停止：何時結束重試');expect(searches).toBe(0);
 await page.getByLabel('主題 2',{exact:true}).fill('停止：重試沒有進展時交由人類判斷');
 await page.getByRole('button',{name:'確認範圍並搜尋',exact:true}).click();await expect(page).toHaveURL(new RegExp(`/research/${cid}$`));expect(searches).toBe(1);
 await page.getByRole('button',{name:'返回補充學習',exact:true}).click();await expect(page.locator('.research-card-link')).toHaveCount(1);
 await expect(page.getByRole('button',{name:'查看搜尋範圍',exact:true})).toHaveCount(0);
});

test('compact research panels keep desktop actions on screen and scroll only history or sources',async({page})=>{
 await mockKnowledgeMapApi(page,structureView());
 const candidates=Array.from({length:24},(_,i)=>({id:`source-${i}`,kind:'official',title:`補充來源 ${i+1}`,authors:'Fixture',year:2026,url:'https://docs.python.org/3/',doi:null,license:'PSF-2.0',license_url:null,version:'3',eligible:true,reason:'fixture',state:'ready'}));
 let status='selecting';
 const detail=()=>({research_id:cid,material_id:materialId,query:'TCP 補充查詢',mode:'review',status,candidates,selection:status==='selecting'?[]:candidates.map(c=>c.id),cursor:null,error_code:null,run_id:status==='submitted'?runId:null,run:status==='submitted'?{status:'succeeded',error_code:null}:undefined,created_at:'2026-10-06T00:00:00Z',is_current_revision:true});
 const records=Array.from({length:36},(_,i)=>({...researchSummary(detail()),research_id:i?`${String(i).padStart(8,'0')}-1111-4111-8111-111111111111`:cid,query:`補充查詢 ${i+1}`}));
 await page.route(`**/v1/materials/${materialId}/research`,r=>json(r,{researches:records}));
 await page.route(`**/v1/research/${cid}`,r=>json(r,detail()));
 for(const [width,height] of [[1280,720],[1366,768],[1920,1080]]) {
  await page.setViewportSize({width,height});await page.goto(`/materials/${materialId}/research`);
  await expect(page.locator('.research-record')).toHaveCount(36);
  const grid=page.getByRole('region',{name:'補充搜尋紀錄',exact:true});
  expect(await page.evaluate(()=>document.documentElement.scrollHeight)).toBeLessThanOrEqual(height);
  expect((await page.locator('.research-record').first().boundingBox())!.height).toBeLessThan(230);
  await grid.evaluate(el=>{el.scrollTop=el.scrollHeight;});
  expect(await grid.evaluate(el=>el.scrollTop)).toBeGreaterThan(0);expect(await page.evaluate(()=>scrollY)).toBe(0);
  for(const next of ['selecting','ready','normalizing','submitted']) {
   status=next;await page.goto(`/materials/${materialId}/research/${cid}`);
   await expect(page.locator('.research-candidates article')).toHaveCount(10);
   const body=(await page.locator('.research-detail-body').boundingBox())!;
   expect(body.y+body.height).toBeLessThanOrEqual(height-72);
   expect(await page.evaluate(()=>document.documentElement.scrollHeight)).toBeLessThanOrEqual(height);
   const sources=page.locator('.research-candidates');
   expect(await sources.evaluate(el=>el.clientHeight)).toBeGreaterThan(80);
   const overflow=await sources.evaluate(el=>el.scrollHeight-el.clientHeight);
   await sources.evaluate(el=>{el.scrollTop=el.scrollHeight;});
   expect(await sources.evaluate(el=>el.scrollTop)).toBeCloseTo(Math.max(0,overflow),0);
   expect(await page.evaluate(()=>scrollY)).toBe(0);
   const action=page.getByRole('button',{name:next==='ready'?'確認加入教材':next==='submitted'?'查看教材分析':next==='normalizing'?'停止處理':'取得選取的 0 份來源',exact:true});
   const button=(await action.boundingBox())!;
   expect(button.y+button.height).toBeLessThanOrEqual(height-72);
   if(next==='ready') await expect(page.getByLabel('我了解加入後不可逆')).toBeVisible();
  }
 }
 await page.setViewportSize({width:390,height:844});status='selecting';await page.goto(`/materials/${materialId}/research/${cid}`);
 await expect(page.locator('.research-candidates article')).toHaveCount(10);expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBe(390);
});

test('compact research workflow navigates available stages without changing the server workflow',async({page})=>{
 await page.setViewportSize({width:1366,height:768});await mockKnowledgeMapApi(page,structureView());
 const candidate=(i:number)=>({id:`s${i}`,kind:'official',title:`來源 ${i}`,authors:'Fixture',year:2026,url:'https://docs.python.org/3/',doi:null,license:'PSF-2.0',license_url:null,version:'3',eligible:true,reason:'fixture',state:'candidate'});
 let work:any={research_id:cid,material_id:materialId,query:'流程導覽測試',mode:'review',status:'selecting',candidates:[0,1,2,3].map(candidate),selection:[],cursor:'next',error_code:null,run_id:null,is_current_revision:true,created_at:'2026-10-06T00:00:00Z'};
 const actions:any[]=[];let submits=0;
 await page.route(`**/v1/research/${cid}`,r=>json(r,work));
 await page.route(`**/v1/materials/${materialId}/research`,r=>json(r,{researches:[researchSummary(work)]}));
 await page.route(`**/v1/research/${cid}/actions`,r=>{const body=r.request().postDataJSON();actions.push(body);if(body.action==='more')work={...work,status:'searching'};else if(body.action==='acquire')work={...work,status:'acquiring',selection:body.selected};return json(r,work);});
 await page.route(`**/v1/research/${cid}/submit`,r=>{expect(r.request().postDataJSON()).toEqual({confirmed:true});submits++;work={...work,status:'submitted',run_id:runId,run:{status:'pending',error_code:null}};return json(r,work,202);});
 await page.goto(`/materials/${materialId}/research/${cid}`);
 const nav=page.getByRole('navigation',{name:'補充學習流程'}),search=nav.getByRole('button',{name:/搜尋資料/}),selection=nav.getByRole('button',{name:/選取來源/}),add=nav.getByRole('button',{name:/加入教材/});
 await expect(selection).toHaveAttribute('aria-current','step');await expect(add).toBeDisabled();await expect(page.locator('.research-detail .learning-steps')).toHaveCount(0);
 expect(await page.locator('.research-candidates').evaluate(el=>getComputedStyle(el).gridTemplateColumns.split(' ').length)).toBe(3);
 await expect(page.locator('.source-picker-filters')).not.toContainText('已選 0');
 await expect(page.getByText('可加入',{exact:true})).toHaveCount(0);
 await expect(page.getByRole('button',{name:'取消全選',exact:true})).toHaveCount(0);
 await expect(page.locator('.source-pagination')).toHaveCount(0);
 const sidebar=(await nav.boundingBox())!,body=(await page.locator('.research-detail-body').boundingBox())!;
 expect(sidebar.y+sidebar.height).toBeLessThanOrEqual(body.y);expect(sidebar.height).toBeLessThan(50);
 await page.getByRole('checkbox',{name:'選取 來源 0',exact:true}).check();await page.getByLabel('只看已選',{exact:true}).check();await expect(page.locator('.research-candidates article')).toHaveCount(1);
 await search.click();await expect(search).toHaveAttribute('aria-pressed','true');await expect(page.locator('.research-candidates article')).toHaveCount(4);await expect(page.getByRole('checkbox',{name:/選取 來源/})).toHaveCount(0);expect(actions).toHaveLength(0);
 await page.getByRole('button',{name:'前往選取來源',exact:true}).click();await expect(page.getByRole('checkbox',{name:'選取 來源 0',exact:true})).toBeChecked();await expect(page.locator('.research-candidates article')).toHaveCount(1);await page.getByLabel('只看已選',{exact:true}).uncheck();
 await page.getByLabel('篩選來源',{exact:true}).fill('來源 0');await expect(page.locator('.research-candidates article')).toHaveCount(1);expect(actions).toHaveLength(0);
 await expect(selection).toHaveAttribute('aria-current','step');await expect(page.getByLabel('篩選來源',{exact:true})).toHaveValue('來源 0');
 await page.getByRole('button',{name:'取得選取的 1 份來源',exact:true}).click();await expect(search).toBeDisabled();await expect(add).toBeDisabled();await expect(page.getByRole('button',{name:'停止處理',exact:true})).toBeVisible();expect(actions[0]).toEqual({action:'acquire',selected:['s0']});
 work={...work,status:'ready',candidates:work.candidates.map((c:any)=>c.id==='s0'?{...c,state:'ready'}:c)};
 await expect(page.getByRole('button',{name:'確認加入教材',exact:true})).toBeVisible();await expect(add).toHaveAttribute('aria-current','step');await expect(page.getByLabel('我了解加入後不可逆')).toBeVisible();
 await page.getByRole('button',{name:'調整來源',exact:true}).click();await page.getByLabel('篩選來源',{exact:true}).clear();await page.getByRole('checkbox',{name:'選取 來源 1',exact:true}).check();await expect(add).toBeDisabled();expect(actions).toHaveLength(1);
 await page.getByRole('button',{name:'取消調整',exact:true}).click();await expect(page.getByRole('button',{name:'確認加入教材',exact:true})).toBeDisabled();await page.getByLabel('我了解加入後不可逆').check();await page.getByRole('button',{name:'確認加入教材',exact:true}).click();
 await expect(page.getByRole('heading',{name:'正在建立補充教材',exact:true})).toBeVisible();await expect(search).toBeDisabled();await expect(selection).toBeDisabled();await expect(add).toBeDisabled();await expect(nav.locator('li.is-complete')).toHaveCount(2);expect(submits).toBe(1);
 await expect(page.getByRole('button',{name:'調整來源',exact:true})).toHaveCount(0);await expect(page.getByRole('checkbox',{name:/選取 來源/})).toHaveCount(0);
 work.run.status='succeeded';await expect(nav.locator('li.is-complete')).toHaveCount(3);
 await page.setViewportSize({width:390,height:844});await expect(nav).toBeVisible();await expect(add).toHaveAttribute('aria-current','step');
 expect(await page.locator('.research-candidates').evaluate(el=>getComputedStyle(el).gridTemplateColumns.split(' ').length)).toBe(1);expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBe(390);
});

test('research source comparison has a single toolbar, tri-state select-all and only exception badges',async({page})=>{
 await page.setViewportSize({width:1440,height:900});await mockKnowledgeMapApi(page,structureView());
 const candidates=Array.from({length:4},(_,i)=>({id:`s${i}`,kind:i===0?'official':'paper',title:`來源 ${i}：網路協定的重要條件與設計取捨`,authors:'完整作者與出版資訊'.repeat(12),year:2026,url:'https://docs.python.org/3/',doi:null,license:'PSF-2.0',license_url:null,version:'3',eligible:i!==2,reason:'fixture',state:i===3?'failed':'candidate',...(i===3?{error_code:'RESEARCH_DOWNLOAD_FAILED'}:{})}));
 let work:any={research_id:cid,material_id:materialId,query:'來源比較',mode:'review',status:'selecting',candidates,selection:[],cursor:null,error_code:null,run_id:null,is_current_revision:true,created_at:'2026-10-06T00:00:00Z'};
 const actions:any[]=[];await page.route(`**/v1/research/${cid}`,r=>json(r,work));
 await page.route(`**/v1/research/${cid}/actions`,r=>{const body=r.request().postDataJSON();actions.push(body);work={...work,status:'acquiring',selection:body.selected};return json(r,work);});
 await page.goto(`/materials/${materialId}/research/${cid}`);
 const toolbar=page.locator('.source-picker-filters'),all=toolbar.getByRole('checkbox',{name:'全選可用來源',exact:true}),only=toolbar.getByRole('checkbox',{name:'只看已選',exact:true});
 await expect(all).toBeVisible();await expect(toolbar.getByLabel('來源類型')).toBeVisible();await expect(toolbar.getByLabel('篩選來源',{exact:true})).toBeVisible();
 await expect(page.getByText('可加入',{exact:true})).toHaveCount(0);await expect(page.getByText('取得失敗',{exact:true})).toBeVisible();
 await expect(page.getByRole('checkbox',{name:`選取 ${candidates[2].title}`,exact:true})).toHaveCount(0);
 const input=(await toolbar.getByLabel('篩選來源',{exact:true}).boundingBox())!;
 for(const field of [all,only,toolbar.getByLabel('來源類型')]){const box=(await field.boundingBox())!;expect(box.y).toBeGreaterThanOrEqual(input.y);expect(box.y+box.height).toBeLessThanOrEqual(input.y+input.height+1);}
 await toolbar.getByLabel('篩選來源',{exact:true}).fill('來源 0');await all.check();await expect(page.locator('.tool-stage-footer')).toContainText('已選 3 份來源');
 await expect(toolbar).not.toContainText('已選 3');
 await toolbar.getByLabel('篩選來源',{exact:true}).clear();await toolbar.getByLabel('來源類型').selectOption('paper');await only.check();await expect(page.locator('.research-candidates article')).toHaveCount(2);
 await all.uncheck();await expect(page.locator('.tool-stage-footer')).toContainText('已選 0 份來源');await expect(page.locator('.research-candidates article')).toHaveCount(0);
 await only.uncheck();await toolbar.getByLabel('來源類型').selectOption('all');await page.getByRole('checkbox',{name:`選取 ${candidates[0].title}`,exact:true}).check();expect(await all.evaluate((el:HTMLInputElement)=>el.indeterminate)).toBe(true);
 await all.check();expect(await all.evaluate((el:HTMLInputElement)=>el.indeterminate)).toBe(false);await expect(all).toBeChecked();
 for(const [width,columns] of [[1920,3],[1366,3],[390,1]]) {
  await page.setViewportSize({width,height:900});
  expect(await page.locator('.research-candidates').evaluate(el=>getComputedStyle(el).gridTemplateColumns.split(' ').length)).toBe(columns);
  const metadata=page.locator('.source-byline').first();expect((await metadata.boundingBox())!.height).toBeLessThan(25);await expect(metadata).toHaveAttribute('title',`官方教學 · ${candidates[0].authors} · 2026`);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBe(width);
 }
 await page.getByRole('button',{name:'取得選取的 3 份來源',exact:true}).click();expect(actions).toEqual([{action:'acquire',selected:['s0','s1','s3']}]);
});
