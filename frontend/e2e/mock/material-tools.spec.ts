import { expect,test,type Page } from '@playwright/test';
import {materialId,runId,structureRevision,structureView,mockKnowledgeMapApi,json} from '../fixtures/knowledge-map';
const mapPath=`/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}`;
const cid='55555555-5555-4555-8555-555555555555';
const tid='66666666-6666-4666-8666-666666666666';
const conversation={conversation_id:cid,material_id:materialId,knowledge_structure_revision:structureRevision,title:'測試對話',created_at:'2026-10-04T00:00:00Z'};

test('material voice text flow saves, cites, reopens and closes',async({page})=>{
 const structure=structureView();await mockKnowledgeMapApi(page,structure);
 let saved=false;let turns:unknown[]=[];
 await page.route('**/v1/materials/*/voice-conversations',r=>{if(r.request().method()==='POST'){saved=true;return json(r,conversation,201);}return json(r,{conversations:saved?[conversation]:[]});});
 await page.route(`**/v1/voice-conversations/${cid}`,r=>json(r,{...conversation,is_current_revision:true,source_resolver:structure.source_resolver,turns}));
 await page.route(`**/v1/voice-conversations/${cid}/turns`,r=>{turns=[{turn_id:tid,question:r.request().postDataJSON().question,answer:{text:'堆疊遵循後進先出。',supported:true,citations:[structure.concepts[0].claims[0]]},status:'ready',audio_url:null,error_code:null}];return json(r,{turn_id:tid},202);});
 await page.goto(mapPath);await page.getByRole('button',{name:'語音問答',exact:true}).click();
 await page.getByLabel('問題／辨識文字').fill('堆疊是什麼？');await page.getByRole('button',{name:'送出問題',exact:true}).click();
 await expect(page.locator('.voice-answer')).toHaveText('堆疊遵循後進先出。');
 await page.getByText('查看回答來源',{exact:true}).click();await expect(page.getByRole('button',{name:'查看第 1 頁來源'})).toBeVisible();
 await page.getByRole('button',{name:'關閉',exact:true}).click();await expect(page.getByLabel('問題／辨識文字')).toHaveCount(0);
 await page.getByRole('button',{name:'語音問答',exact:true}).click();await page.getByLabel('已保存的對話').selectOption(cid);await expect(page.locator('.voice-answer')).toHaveText('堆疊遵循後進先出。');
});

test('late microphone permission after cancel stops tracks and sends nothing',async({page})=>{
 await mockKnowledgeMapApi(page,structureView());await page.route('**/v1/materials/*/voice-conversations',r=>json(r,{conversations:[]}));
 await page.addInitScript(()=>{let resolve:(value:MediaStream)=>void;Object.assign(window,{stopped:false,allowMic:()=>resolve({getTracks:()=>[{stop:()=>{(window as any).stopped=true;}}]} as unknown as MediaStream)});Object.defineProperty(navigator.mediaDevices,'getUserMedia',{value:()=>new Promise(r=>resolve=r)});});
 await page.goto(mapPath);await page.getByRole('button',{name:'語音問答',exact:true}).click();await page.getByRole('button',{name:'開始錄音'}).click();await page.getByRole('button',{name:'取消等待麥克風'}).click();await page.evaluate(()=>(window as any).allowMic());await expect.poll(()=>page.evaluate(()=>(window as any).stopped)).toBe(true);
 await expect(page.getByRole('button',{name:'開始錄音'})).toBeEnabled();
});

test('research preserves arbitrary source selection and requires irreversible confirmation',async({page})=>{
 await mockKnowledgeMapApi(page,structureView());
 const candidates=Array.from({length:5},(_,i)=>({id:`source-${i}`,kind:'official',title:`官方教材 ${i+1}`,authors:'Fixture',year:2026,url:'https://docs.python.org/3/',doi:null,license:'PSF-2.0',license_url:'https://docs.python.org/3/license.html',version:'3',eligible:true,reason:'可使用',state:'candidate'}));
 let value:any={research_id:cid,query:'網路通訊',mode:'review',status:'selecting',candidates,selection:[],cursor:null,error_code:null,run_id:null,is_current_revision:true};
 await page.route('**/v1/materials/*/research',r=>r.request().method()==='POST'?json(r,value,202):json(r,{researches:[value]}));
 await page.route(`**/v1/research/${cid}`,r=>json(r,value));
 await page.route(`**/v1/research/${cid}/actions`,r=>{const b=r.request().postDataJSON();expect(b.selected).toHaveLength(5);value={...value,status:'ready',selection:b.selected,candidates:candidates.map(c=>({...c,state:'ready'}))};return json(r,value);});
 let submits=0;await page.route(`**/v1/research/${cid}/submit`,r=>{expect(r.request().postDataJSON()).toEqual({confirmed:true});submits++;value={...value,status:'submitted',run_id:runId};return json(r,value,202);});
 await page.goto(mapPath);await page.getByRole('button',{name:'補充學習',exact:true}).click();await page.getByLabel('已保存的搜尋').selectOption(cid);
 await page.getByRole('button',{name:'全選可用來源'}).click();await page.getByRole('button',{name:'取得選取的 5 份來源'}).click();
 await expect(page.getByRole('button',{name:'確認加入教材'})).toBeDisabled();await expect(page.getByText('無法撤回新增內容')).toBeVisible();expect(submits).toBe(0);
 await page.getByLabel('我了解加入後不可逆').check();await page.getByRole('button',{name:'確認加入教材'}).click();await expect(page.getByRole('button',{name:'查看教材分析'})).toBeVisible();expect(submits).toBe(1);await page.getByRole('button',{name:'查看教材分析'}).click();await expect(page.locator('.material-tool-panel')).toHaveCount(0);
});

test('closing a conversation releases every private audio element',async({page})=>{
 const structure=structureView();await mockKnowledgeMapApi(page,structure);
 const wav=Buffer.alloc(44+24000*2*4);wav.write('RIFF',0);wav.writeUInt32LE(wav.length-8,4);wav.write('WAVEfmt ',8);wav.writeUInt32LE(16,16);wav.writeUInt16LE(1,20);wav.writeUInt16LE(1,22);wav.writeUInt32LE(24000,24);wav.writeUInt32LE(48000,28);wav.writeUInt16LE(2,32);wav.writeUInt16LE(16,34);wav.write('data',36);wav.writeUInt32LE(wav.length-44,40);
 await page.route('**/v1/materials/*/voice-conversations',r=>json(r,{conversations:[conversation]}));
 await page.route(`**/v1/voice-conversations/${cid}`,r=>json(r,{...conversation,is_current_revision:true,source_resolver:structure.source_resolver,turns:[0,1].map(i=>({turn_id:`turn-${i}`,question:'合成問題',answer:{text:'合成回答',supported:false,citations:[]},status:'ready',error_code:null,audio_url:`/v1/voice-conversations/${cid}/turns/${i}/audio`}))}));
 await page.route(`**/v1/voice-conversations/${cid}/turns/*/audio`,r=>r.fulfill({status:200,contentType:'audio/wav',body:wav}));
 await page.goto(mapPath);await page.getByRole('button',{name:'語音問答',exact:true}).click();await page.getByLabel('已保存的對話').selectOption(cid);await expect(page.locator('.voice-turn audio')).toHaveCount(2);
 await page.evaluate(async()=>{const clips=Array.from(document.querySelectorAll<HTMLAudioElement>('.voice-turn audio'));(window as any).__clips=clips;await Promise.all(clips.map(a=>a.play()));});
 await page.getByRole('button',{name:'關閉',exact:true}).click();
 expect(await page.evaluate(()=>(window as any).__clips.every((a:HTMLAudioElement)=>a.paused&&!a.getAttribute('src')))).toBe(true);
});

test('late permission failure cannot stop a newer recording',async({page})=>{
 await mockKnowledgeMapApi(page,structureView());await page.route('**/v1/materials/*/voice-conversations',r=>json(r,{conversations:[]}));
 await page.addInitScript(()=>{
   let count=0;const state:any={stopped:false};(window as any).micRace=state;
   Object.defineProperty(navigator.mediaDevices,'getUserMedia',{value:()=>new Promise((resolve,reject)=>{count++;if(count===1)state.rejectOld=()=>reject(new Error('old request rejected'));else state.allowNew=()=>resolve({getTracks:()=>[{stop:()=>{state.stopped=true;}}]});})});
   (window as any).MediaRecorder=class{state='inactive';onstop?:()=>void;start(){this.state='recording';}stop(){this.state='inactive';queueMicrotask(()=>this.onstop?.());}};
 });
 await page.goto(mapPath);await page.getByRole('button',{name:'語音問答',exact:true}).click();await page.getByRole('button',{name:'開始錄音',exact:true}).click();await page.getByRole('button',{name:'取消等待麥克風'}).click();await page.getByRole('button',{name:'開始錄音',exact:true}).click();await page.evaluate(()=>(window as any).micRace.allowNew());await expect(page.getByRole('button',{name:'停止錄音並辨識'})).toBeVisible();
 await page.evaluate(()=>(window as any).micRace.rejectOld());expect(await page.evaluate(()=>(window as any).micRace.stopped)).toBe(false);
 await page.getByRole('button',{name:'關閉',exact:true}).click();expect(await page.evaluate(()=>(window as any).micRace.stopped)).toBe(true);
});
