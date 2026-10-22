import { readFileSync } from 'node:fs';
import { expect, test, type Page } from '@playwright/test';
import type { VoiceTurn, VoiceView } from '../../src/features/material-tools/types';
import { materialId, runId, structureRevision, structureView, mockKnowledgeMapApi, json } from '../fixtures/knowledge-map';

const mapPath=`/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}`;
function wav() {
 const data=Buffer.alloc(44+24000*8*2);data.write('RIFF');data.writeUInt32LE(data.length-8,4);data.write('WAVEfmt ',8);data.writeUInt32LE(16,16);data.writeUInt16LE(1,20);data.writeUInt16LE(1,22);data.writeUInt32LE(24000,24);data.writeUInt32LE(48000,28);data.writeUInt16LE(2,32);data.writeUInt16LE(16,34);data.write('data',36);data.writeUInt32LE(data.length-44,40);return data;
}
async function mockVoice(page:Page) {
 const structure=structureView();await mockKnowledgeMapApi(page,structure);
 let counter=1;const next=()=>`77777777-7777-4777-8777-${String(counter++).padStart(12,'0')}`;
 const conversations:VoiceView[]=[],actions:{action:string;question?:string;turnId:string}[]=[],questions:string[]=[];
 const recorded:VoiceTurn[]=[];let deletes=0;
 const behavior={audio:false,status:'ready'};
 const answer:NonNullable<VoiceTurn['answer']>={schema:'material-answer/v2',text:'堆疊採用後進先出。最後放入的資料會最先取出。',supported:true,citations:[{passage_id:"passage:sha256:"+"a".repeat(64),text:structure.concepts[0].claims[0].text,evidence:structure.concepts[0].claims[0].evidence}]};
 const finishAudio=(c:VoiceView,t:VoiceTurn)=>{t.status='ready';t.error_code=null;t.audio_url=`/v1/voice-conversations/${c.conversation_id}/turns/${t.turn_id}/audio`;};
 const reply=(c:VoiceView,t:VoiceTurn)=>{t.answer={...answer};t.status=t.mode==='text'?'ready':behavior.status;t.error_code=t.status==='failed'?'VOICE_PROVIDER_FAILED':null;if(t.mode!=='text'&&behavior.audio&&t.status==='ready')finishAudio(c,t);};
 const add=()=>{const c:VoiceView={conversation_id:next(),material_id:materialId,knowledge_structure_revision:structureRevision,title:`已保存的對話 ${counter}`,created_at:'2026-10-06T00:00:00Z',is_current_revision:true,source_resolver:structure.source_resolver,turns:[]};conversations.push(c);return c;};
 const turn=(c:VoiceView,question:string,status='ready',audio=false,mode:VoiceTurn['mode']=null)=>{const id=next();const t:VoiceTurn={turn_id:id,mode,question,status,answer:status==='ready'?{...answer}:null,error_code:null,audio_url:audio?`/v1/voice-conversations/${c.conversation_id}/turns/${id}/audio`:null};c.turns.push(t);return t;};
 await page.route(`**/v1/materials/${materialId}/voice-conversations`,r=>{
  if(r.request().method()==='POST'){expect(r.request().headers()['idempotency-key']).toBeTruthy();return json(r,add(),201);}
  return json(r,{conversations:conversations.map(({turns,is_current_revision,source_resolver,...c})=>c)});
 });
 await page.route('**/v1/voice-conversations/**',r=>{
  const parts=new URL(r.request().url()).pathname.split('/').filter(Boolean),c=conversations.find(c=>c.conversation_id===parts[2]);
  if(!c)return json(r,{schema:'api-error/v1',request_id:materialId,reason_code:'RESOURCE_NOT_FOUND',retryable:false,message:'Not found'},404);
  if(parts.length===3){if(r.request().method()==='DELETE'){deletes++;conversations.splice(conversations.indexOf(c),1);return json(r,{ok:true});}return json(r,c);}
  if(parts[3]==='recordings'){expect(r.request().headers()['idempotency-key']).toBeTruthy();expect(r.request().postDataBuffer()!.length).toBeGreaterThan(0);const t=turn(c,'','transcribing',false,'voice');recorded.push(t);return json(r,{turn_id:t.turn_id},202);}
  if(parts.length===4&&parts[3]==='turns'){const body=r.request().postDataJSON();questions.push(body.question);expect(r.request().headers()['idempotency-key']).toBeTruthy();const t=turn(c,body.question,'ready',false,body.mode??'text');reply(c,t);c.title=body.question;return json(r,{turn_id:t.turn_id},202);}
  if(parts.at(-1)==='actions'){
   const t=c.turns.find(t=>t.turn_id===parts[4])!,body=r.request().postDataJSON();actions.push({...body,turnId:t.turn_id});
   if(body.action==='cancel')t.status='cancelled';
   else if(body.action==='retry')t.status=t.answer?'speaking':'pending';
   return json(r,{ok:true,status:t.status});
  }
  return json(r,{ok:true});
 });
 await page.route('**/v1/voice-conversations/*/turns/*/audio',r=>r.fulfill({status:200,contentType:'audio/wav',body:wav()}));
 return {conversations,add,turn,answer,actions,questions,recorded,behavior,reply,finishAudio,deletes:()=>deletes};
}
async function open(page:Page) {await page.getByRole('button',{name:'教材問答',exact:true}).click();await expect(page.getByRole('dialog',{name:'教材問答'})).toBeVisible();}
async function choose(page:Page,id:string) {await page.getByRole('button',{name:'對話紀錄',exact:true}).click();await page.locator(`[data-conversation-id="${id}"]`).click();}
async function microphone(page:Page) {
 // UI lifecycle tests inject speech events; real model classification is tested separately.
 await page.route('**/assets/voice-vad-*.js', route => route.fulfill({contentType:'text/javascript',body:`
  export async function createVoiceVad(stream, context, callbacks) {
   let paused=false, speaking=false;
   const timer=setInterval(()=>{
    if(paused)return;
    if(window.speechActive&&!speaking){speaking=true;callbacks.onStart();}
    if(!window.speechActive&&speaking){speaking=false;callbacks.onEnd(new Blob(['synthetic recording'],{type:'audio/wav'}));}
   },20);
   return {pause:async()=>{paused=true;speaking=false;},start:async()=>{paused=false;},destroy:async()=>clearInterval(timer)};
  }
 `}));
 await page.addInitScript(()=>{
  (window as any).stoppedTracks=0;
  (window as any).speechActive=false;
  (window as any).AudioContext=class {
   state='running'; resume(){return Promise.resolve();} close(){this.state='closed';return Promise.resolve();}
  };
  Object.defineProperty(navigator.mediaDevices,'getUserMedia',{value:async()=>({getTracks:()=>[{stop:()=>{(window as any).stoppedTracks++;}}]})});
 });
}

async function speak(page:Page) {
 await expect(page.locator('.voice-composer-status')).toContainText('正在聆聽');
 await page.evaluate(()=>(window as any).speechActive=true);
 await expect(page.locator('.voice-composer-status')).toContainText('說完稍停');
 await page.evaluate(()=>(window as any).speechActive=false);
}

test('compact widget has one entry, chat bubbles, secondary sources and a growing single composer',async({page})=>{
 await page.setViewportSize({width:1440,height:900});const mock=await mockVoice(page);await page.goto(mapPath);await open(page);
 const panel=page.getByRole('dialog',{name:'教材問答'}),input=page.getByLabel('問題／辨識文字');
 await expect(page.getByRole('button',{name:'教材問答',exact:true})).toHaveAttribute('aria-expanded','true');await expect(panel.getByRole('heading',{name:'教材問答',exact:true})).toBeVisible();await expect(input).toBeFocused();
 await expect(page.getByLabel('已保存的對話')).not.toBeVisible();
 const box=(await panel.boundingBox())!,header=(await page.locator('.voice-header').boundingBox())!,composer=(await page.locator('.voice-composer').boundingBox())!;
 // 桌機改為頁首下方的右側抽屜。
 expect(box.width).toBeLessThanOrEqual(420);expect(box.y).toBeGreaterThanOrEqual((await page.locator('.app-header').boundingBox())!.height);expect(box.y+box.height).toBeLessThanOrEqual(900);expect(header.height).toBeLessThanOrEqual(56);expect(composer.height).toBeLessThan(110);
 expect((await page.locator('.voice-turns').boundingBox())!.height).toBeGreaterThan(box.height*.6);
 await page.getByRole('button',{name:'主要觀念是什麼？',exact:true}).click();await expect(input).toHaveValue('主要觀念是什麼？');await expect(input).toBeFocused();
 await input.fill('較長的輸入\n'.repeat(30));expect((await input.boundingBox())!.height).toBeLessThanOrEqual(112);expect(await input.evaluate(el=>el.scrollHeight>el.clientHeight)).toBe(true);
 await input.fill('堆疊是什麼？');await page.getByRole('button',{name:'送出問題',exact:true}).click();await expect(page.locator('.voice-answer')).toContainText(mock.answer.text);await expect(input).toHaveValue('');expect(mock.questions).toEqual(['堆疊是什麼？']);
 const user=(await page.locator('.voice-question').boundingBox())!,assistant=(await page.locator('.voice-response').boundingBox())!;expect(user.x).toBeGreaterThan(assistant.x);
 await expect(page.locator('.voice-question > span')).toHaveCount(0);await expect(page.getByRole('button',{name:/來源 1.*PDF 第 1 頁/})).toHaveCount(0);
 await page.getByText(/^查看來源 ·/).click();await page.getByRole('button',{name:/來源 1.*PDF 第 1 頁/}).click();await expect(page.getByRole('dialog',{name:'教材來源'})).toBeVisible();await page.keyboard.press('Escape');await expect(panel).toBeVisible();
 await input.fill('換行');await input.press('Shift+Enter');await input.pressSequentially('補充');await input.dispatchEvent('keydown',{key:'Enter',isComposing:true});expect(mock.questions).toHaveLength(1);await input.press('Enter');await expect.poll(()=>mock.questions.length).toBe(2);expect(mock.questions.at(-1)).toBe('換行\n補充');await expect(input).toHaveValue('');
 for(let i=0;i<3;i++){await page.getByRole('button',{name:'關閉',exact:true}).click();await expect(page.getByRole('button',{name:'教材問答',exact:true})).toBeFocused();await open(page);}
 await page.getByRole('button',{name:'對話紀錄',exact:true}).click();await page.keyboard.press('Escape');await expect(panel).toBeVisible();await expect(page.getByRole('button',{name:'對話紀錄',exact:true})).toBeFocused();await page.keyboard.press('Escape');await expect(panel).toHaveCount(0);
});

test('voice mode automatically moves through STT answer and audio with no second send',async({page})=>{
 const mock=await mockVoice(page);await microphone(page);await page.goto(mapPath);await open(page);
 await page.getByRole('switch',{name:'語音模式',exact:true}).click();
 await expect(page.locator('.voice-panel textarea')).toHaveCount(1);
 await expect(page.locator('.voice-composer-status')).toContainText('正在聆聽');
 await page.evaluate(()=>(window as any).speechActive=true);
 await expect(page.locator('.voice-composer-status')).toContainText('說完稍停');
 await page.getByRole('switch',{name:'語音模式',exact:true}).click();expect(mock.recorded).toHaveLength(0);
 await page.evaluate(()=>(window as any).speechActive=false);
 await page.getByRole('switch',{name:'語音模式',exact:true}).click();
 await speak(page);
 await expect.poll(()=>mock.recorded.length).toBe(1);await expect(page.getByText('正在辨識你的問題…',{exact:true})).toBeVisible();
 const turn=mock.recorded[0],conversation=mock.conversations[0];turn.question='辨識出的教材問題';turn.status='pending';
 await expect(page.getByText('等待教材回答…',{exact:true})).toBeVisible();await expect(page.locator('.voice-question p')).toHaveText(turn.question);
 turn.status='answering';await expect(page.getByText('正在依教材整理回答…',{exact:true})).toBeVisible();
 mock.behavior.status='speaking';mock.reply(conversation,turn);
 await expect(page.getByText('正在產生語音…',{exact:true})).toBeVisible();await expect(page.locator('.voice-answer')).toContainText(mock.answer.text);
 mock.finishAudio(conversation,turn);
 const audio=page.locator('.answer-audio audio');await expect.poll(()=>audio.evaluate((a:HTMLAudioElement)=>a.paused)).toBe(false);
 await expect(page.getByText('播放回答中',{exact:true})).toBeVisible();
 expect(mock.actions.filter(a=>a.action==='send')).toHaveLength(0);expect(mock.questions).toHaveLength(0);
 await expect(page.getByRole('button',{name:'送出問題',exact:true})).toHaveCount(1);
 await expect(page.locator('.voice-panel textarea')).toHaveCount(1);
});

test('audio retry preserves unsent text and switching conversations clears the composer',async({page})=>{
 const mock=await mockVoice(page),first=mock.add(),second=mock.add();first.is_current_revision=false;
 const answer=mock.turn(first,'原本問題','ready',false,'voice');answer.status='failed';mock.turn(second,'另一段對話');
 await page.goto(mapPath);await open(page);await choose(page,first.conversation_id);
 const input=page.getByLabel('問題／辨識文字');await expect(input).toHaveValue('');await expect(page.locator('.voice-revision-note')).toContainText('先前的教材版本');
 await input.fill('下一個尚未送出的問題');await page.getByRole('button',{name:'重試語音',exact:true}).click();await expect(page.getByText('正在產生語音…',{exact:true})).toBeVisible();await expect(input).toHaveValue('下一個尚未送出的問題');expect(mock.actions.at(-1)).toMatchObject({action:'retry',turnId:answer.turn_id});expect(mock.actions.at(-1)).not.toHaveProperty('question');
 answer.status='ready';await expect(input).toBeEnabled();await choose(page,second.conversation_id);await expect(input).toHaveValue('');
 await page.getByRole('button',{name:'對話紀錄',exact:true}).click();page.once('dialog',dialog=>dialog.dismiss());await page.getByRole('button',{name:'刪除對話',exact:true}).click();expect(mock.deletes()).toBe(0);
 page.once('dialog',dialog=>dialog.accept());await page.getByRole('button',{name:'刪除對話',exact:true}).click();await expect(input).toBeFocused();await expect(page.locator('.voice-turn')).toHaveCount(0);expect(mock.deletes()).toBe(1);
});

test('long conversations scroll independently without stealing reading position and audio excludes other media',async({page})=>{
 const mock=await mockVoice(page),conversation=mock.add();
 for(let i=0;i<24;i++){const t=mock.turn(conversation,`問題 ${i+1}`,'ready',i===0);t.answer!.text=`回答 ${i+1}：`+'完整教材說明。'.repeat(35);}
 const pending=mock.turn(conversation,'最後的提問','answering');
 await page.goto(mapPath);await open(page);await choose(page,conversation.conversation_id);
 const stream=page.locator('.voice-turns');await expect(page.locator('.voice-turn')).toHaveCount(25);
 await expect.poll(()=>stream.evaluate(el=>el.scrollHeight-el.scrollTop-el.clientHeight)).toBeLessThan(2);
 const header=(await page.locator('.voice-header').boundingBox())!,composer=(await page.locator('.voice-composer').boundingBox())!;
 await stream.evaluate(el=>{el.scrollTop=0;});await expect.poll(()=>stream.evaluate(el=>el.scrollTop)).toBe(0);pending.status='ready';pending.answer={...mock.answer,text:'最後一則完成的回答'};
 await expect(page.locator('.voice-answer').last()).toContainText('最後一則完成的回答');expect(await stream.evaluate(el=>el.scrollTop)).toBe(0);
 expect((await page.locator('.voice-header').boundingBox())!.y).toBeCloseTo(header.y,0);expect((await page.locator('.voice-composer').boundingBox())!.y).toBeCloseTo(composer.y,0);
 await page.getByRole('button',{name:'播放回答語音',exact:true}).click();const audio=page.locator('.answer-audio audio').first();await expect.poll(()=>audio.evaluate((el:HTMLAudioElement)=>el.paused)).toBe(false);
 await page.getByRole('slider',{name:'回答語音進度',exact:true}).fill('2');await expect.poll(()=>audio.evaluate((el:HTMLAudioElement)=>el.currentTime)).toBeGreaterThanOrEqual(2);
 await page.route('**/test-podcast-audio.wav',r=>r.fulfill({contentType:'audio/wav',body:wav()}));
 await page.evaluate(async()=>{const outside=document.createElement('audio');outside.id='test-podcast';outside.src='/test-podcast-audio.wav';document.body.append(outside);await outside.play();});await expect.poll(()=>audio.evaluate((el:HTMLAudioElement)=>el.paused)).toBe(true);
 await page.getByRole('button',{name:'播放回答語音',exact:true}).click();await expect.poll(()=>page.locator('#test-podcast').evaluate((el:HTMLAudioElement)=>el.paused)).toBe(true);
 await page.getByLabel('問題／辨識文字').fill('新的追問');await page.getByRole('button',{name:'送出問題',exact:true}).click();await expect(page.locator('.voice-question p').last()).toHaveText('新的追問');await expect.poll(()=>stream.evaluate(el=>el.scrollHeight-el.scrollTop-el.clientHeight)).toBeLessThan(2);
});

test('390px chat uses the same compact header, fixed composer and keyboard focus',async({page})=>{
 await page.setViewportSize({width:390,height:844});const mock=await mockVoice(page),conversation=mock.add();for(let i=0;i<10;i++)mock.turn(conversation,`問題 ${i}`);
 await page.goto(mapPath);await open(page);await choose(page,conversation.conversation_id);
 const panel=page.getByRole('dialog',{name:'教材問答'}),box=(await panel.boundingBox())!,appHeader=(await page.locator('.app-header').boundingBox())!;
 expect(box.width).toBe(390);expect(box.x).toBeGreaterThanOrEqual(0);expect(box.y).toBeGreaterThanOrEqual(appHeader.height);expect(box.y+box.height).toBeLessThanOrEqual(844);
 await expect(page.getByRole('button',{name:'教材問答',exact:true})).toHaveAttribute('aria-expanded','true');await expect(page.locator('.voice-panel textarea')).toHaveCount(1);
 await page.getByLabel('問題／辨識文字').fill('多行草稿\n'.repeat(25));const composer=(await page.locator('.voice-composer').boundingBox())!,header=(await page.locator('.voice-header').boundingBox())!;
 await page.locator('.voice-turns').evaluate(el=>{el.scrollTop=0;});expect((await page.locator('.voice-composer').boundingBox())!.y).toBeCloseTo(composer.y,0);expect((await page.locator('.voice-header').boundingBox())!.y).toBeCloseTo(header.y,0);expect(composer.y+composer.height).toBeLessThanOrEqual(box.y+box.height);
 expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBe(390);
 await page.getByLabel('問題／辨識文字').focus();await page.keyboard.press('Tab');await expect(page.getByRole('button',{name:'送出問題',exact:true})).toBeFocused();
 await page.keyboard.press('Escape');await expect(panel).toHaveCount(0);await expect(page.getByRole('button',{name:'教材問答',exact:true})).toBeFocused();
});

test('text and voice share a conversation but only the current voice reply autoplays',async({page})=>{
 const mock=await mockVoice(page);mock.behavior.audio=true;const conversation=mock.add();mock.turn(conversation,'歷史回答','ready',true);
 await microphone(page);await page.goto(mapPath);await open(page);await choose(page,conversation.conversation_id);
 const input=page.getByLabel('問題／辨識文字');await input.fill('文字問題');await page.getByRole('button',{name:'送出問題',exact:true}).click();
 await expect(page.locator('.voice-answer')).toHaveCount(2);await expect(page.locator('.answer-audio audio')).toHaveCount(1);
 expect(conversation.turns.at(-1)?.mode).toBe('text');expect(conversation.turns.at(-1)?.audio_url).toBeNull();
 expect(await page.locator('.answer-audio audio').evaluate((a:HTMLAudioElement)=>a.paused)).toBe(true);
 await page.getByRole('switch',{name:'語音模式',exact:true}).click();await speak(page);
 await expect.poll(()=>mock.recorded.length).toBe(1);const turn=mock.recorded[0];turn.question='錄音辨識內容';mock.behavior.status='speaking';mock.reply(conversation,turn);
 await expect(page.getByText('正在產生語音…',{exact:true})).toBeVisible();
 await page.evaluate(()=>{const audio=document.createElement('audio');audio.id='other-media';audio.src=document.querySelector('audio')!.src;document.body.append(audio);return audio.play();});
 mock.finishAudio(conversation,turn);const spoken=page.locator('.answer-audio audio').last();
 await expect.poll(()=>spoken.evaluate((a:HTMLAudioElement)=>a.paused)).toBe(false);
 await expect.poll(()=>page.locator('#other-media').evaluate((a:HTMLAudioElement)=>a.paused)).toBe(true);
 expect(await page.locator('.answer-audio audio').first().evaluate((a:HTMLAudioElement)=>a.paused)).toBe(true);
 await spoken.evaluate((a:HTMLAudioElement)=>{a.pause();a.dispatchEvent(new Event('ended'));});
 await expect(page.locator('.voice-composer-status')).toContainText('正在聆聽');expect(mock.recorded).toHaveLength(1);
});

test('voice audio failure keeps text and sources and retries only audio with autoplay',async({page})=>{
 const mock=await mockVoice(page);mock.behavior.audio=true;mock.behavior.status='failed';await microphone(page);await page.goto(mapPath);await open(page);
 await page.getByRole('switch',{name:'語音模式',exact:true}).click();await speak(page);
 await expect.poll(()=>mock.recorded.length).toBe(1);const turn=mock.recorded[0];turn.question='語音問題';mock.reply(mock.conversations[0],turn);
 await expect(page.locator('.voice-answer')).toContainText(mock.answer.text);await expect(page.getByText('文字回答已完成',{exact:true})).toBeVisible();
 await expect(page.getByText('本次處理未完成',{exact:true})).toHaveCount(0);await expect(page.locator('.voice-sources summary')).toBeVisible();
 await page.getByRole('button',{name:'重試語音',exact:true}).click();await expect(page.getByText('正在產生語音…',{exact:true})).toBeVisible();mock.finishAudio(mock.conversations[0],turn);
 await expect.poll(()=>page.locator('.answer-audio audio').evaluate((a:HTMLAudioElement)=>a.paused)).toBe(false);
 expect(mock.actions.at(-1)).toMatchObject({action:'retry',turnId:turn.turn_id});expect(mock.actions.at(-1)).not.toHaveProperty('question');expect(mock.recorded).toHaveLength(1);
});

test('blocked autoplay preserves ready state and offers explicit click-to-play',async({page})=>{
 const mock=await mockVoice(page);mock.behavior.audio=true;await microphone(page);
 await page.addInitScript(()=>{const play=HTMLMediaElement.prototype.play;(window as any).playCalls=0;(window as any).allowPlay=false;HTMLMediaElement.prototype.play=function(){(window as any).playCalls++;return (window as any).allowPlay?play.call(this):Promise.reject(new DOMException('Blocked by test policy','NotAllowedError'));};});
 await page.goto(mapPath);await open(page);await page.getByRole('switch',{name:'語音模式',exact:true}).click();
 await speak(page);await expect.poll(()=>mock.recorded.length).toBe(1);
 const turn=mock.recorded[0];turn.question='請用語音回答';mock.reply(mock.conversations[0],turn);
 await expect(page.getByText('語音回答已完成，點擊播放。',{exact:true})).toBeVisible();await expect(page.locator('.voice-answer')).toContainText(mock.answer.text);
 expect(turn.status).toBe('ready');await expect(page.getByText('本次處理未完成',{exact:true})).toHaveCount(0);
 await expect(page.getByRole('dialog',{name:'教材問答'}).getByRole('alert')).toHaveCount(0);
 expect(await page.evaluate(()=>(window as any).playCalls)).toBe(1);
 await page.evaluate(()=>(window as any).allowPlay=true);await page.getByRole('button',{name:'播放回答語音',exact:true}).click();
 await expect.poll(()=>page.locator('.answer-audio audio').evaluate((a:HTMLAudioElement)=>a.paused)).toBe(false);
 await expect(page.getByText('語音回答已完成，點擊播放。',{exact:true})).toHaveCount(0);
 expect(mock.actions.filter(a=>a.action==='retry'||a.action==='send')).toHaveLength(0);
});


test('switching to text preserves its draft and prevents a late voice answer from autoplaying',async({page})=>{
 const mock=await mockVoice(page);mock.behavior.audio=true;await microphone(page);await page.goto(mapPath);await open(page);
 await page.getByLabel('問題／辨識文字').fill('稍後再送出的文字問題');
 await page.getByRole('switch',{name:'語音模式',exact:true}).click();await speak(page);await expect.poll(()=>mock.recorded.length).toBe(1);
 await page.getByRole('switch',{name:'語音模式',exact:true}).click();await expect(page.getByLabel('問題／辨識文字')).toHaveValue('稍後再送出的文字問題');
 const turn=mock.recorded[0];turn.question='辨識的語音問題';mock.reply(mock.conversations[0],turn);
 await expect(page.locator('.voice-question p')).toHaveText(turn.question);await expect(page.locator('.answer-audio audio')).toHaveCount(1);
 expect(await page.locator('.answer-audio audio').evaluate((a:HTMLAudioElement)=>a.paused)).toBe(true);
 await page.getByRole('switch',{name:'語音模式',exact:true}).click();expect(await page.locator('.answer-audio audio').evaluate((a:HTMLAudioElement)=>a.paused)).toBe(true);
 await page.getByRole('switch',{name:'語音模式',exact:true}).click();await expect(page.getByLabel('問題／辨識文字')).toHaveValue('稍後再送出的文字問題');
 await page.getByRole('button',{name:'送出問題',exact:true}).click();await expect(page.locator('.voice-question p').last()).toHaveText('稍後再送出的文字問題');
 expect(mock.conversations[0].turns.at(-1)?.mode).toBe('text');expect(mock.conversations[0].turns.at(-1)?.audio_url).toBeNull();
});


test('reload and reopen do not replay an old voice answer, and mobile voice controls stay readable',async({page})=>{
 await page.setViewportSize({width:390,height:844});const mock=await mockVoice(page);mock.behavior.audio=true;await microphone(page);await page.goto(mapPath);await open(page);
 await page.getByRole('switch',{name:'語音模式',exact:true}).click();await speak(page);await expect.poll(()=>mock.recorded.length).toBe(1);
 const turn=mock.recorded[0];turn.question='手機上的語音問題';mock.reply(mock.conversations[0],turn);
 await expect.poll(()=>page.locator('.answer-audio audio').evaluate((a:HTMLAudioElement)=>a.paused)).toBe(false);
 const panel=page.getByRole('dialog',{name:'教材問答'});
 const box=(await panel.boundingBox())!;expect(box.x).toBeGreaterThanOrEqual(0);expect(box.x+box.width).toBeLessThanOrEqual(390);expect(box.y+box.height).toBeLessThanOrEqual(844);expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBe(390);
 const cid=mock.conversations[0].conversation_id;await page.reload();await open(page);await choose(page,cid);
 await expect(page.getByRole('switch',{name:'語音模式',exact:true})).toHaveAttribute('aria-checked','false');await expect(page.locator('.answer-audio audio')).toHaveCount(1);
 expect(await page.locator('.answer-audio audio').evaluate((a:HTMLAudioElement)=>a.paused)).toBe(true);
 await expect(page.getByRole('button',{name:'播放回答語音',exact:true})).toBeVisible();
});


test('a pending autoplay attempt cannot resume after switching modes or closing the panel',async({page})=>{
 const mock=await mockVoice(page);mock.behavior.audio=true;await microphone(page);
 await page.addInitScript(()=>{const play=HTMLMediaElement.prototype.play;(window as any).pendingPlay=null;HTMLMediaElement.prototype.play=function(){return new Promise<void>((resolve,reject)=>{(window as any).pendingPlay=()=>play.call(this).then(resolve,reject);});};});
 await page.goto(mapPath);await open(page);await page.getByRole('switch',{name:'語音模式',exact:true}).click();await speak(page);await expect.poll(()=>mock.recorded.length).toBe(1);
 const turn=mock.recorded[0];turn.question='遲到播放測試';mock.reply(mock.conversations[0],turn);await expect.poll(()=>page.evaluate(()=>!!(window as any).pendingPlay)).toBe(true);
 const audio=page.locator('.answer-audio audio');await page.getByRole('switch',{name:'語音模式',exact:true}).click();await page.evaluate(()=>(window as any).pendingPlay().catch(()=>{}));
 await expect.poll(()=>audio.evaluate((a:HTMLAudioElement)=>a.paused)).toBe(true);
 await page.getByRole('button',{name:'關閉',exact:true}).click();await expect(page.locator('.answer-audio audio')).toHaveCount(0);
});


test('a conversation created for a different revision is rejected before sending the question',async({page})=>{
 const mock=await mockVoice(page);
 await page.route(`**/v1/materials/${materialId}/voice-conversations`,r=>{
  if(r.request().method()==='POST')return json(r,{...mock.add(),knowledge_structure_revision:`knowledge-structure:sha256:${'f'.repeat(64)}`},201);
  return json(r,{conversations:[]});
 });
 await page.goto(mapPath);await open(page);await page.getByLabel('問題／辨識文字').fill('目前版本的問題');await page.getByRole('button',{name:'送出問題',exact:true}).click();
 await expect(page.getByRole('alert')).toContainText('這段對話不屬於目前的教材版本');expect(mock.questions).toHaveLength(0);
});


test('cancel intent prevents autoplay even if the answer completes before cancel returns',async({page})=>{
 const mock=await mockVoice(page);mock.behavior.audio=true;await microphone(page);await page.goto(mapPath);await open(page);
 await page.getByRole('switch',{name:'語音模式',exact:true}).click();await speak(page);await expect.poll(()=>mock.recorded.length).toBe(1);
 await expect(page.getByText('正在辨識你的問題…',{exact:true})).toBeVisible();
 let release!:()=>void;const gate=new Promise<void>(resolve=>{release=resolve;});
 await page.route('**/v1/voice-conversations/*/turns/*/actions',async route=>{
  expect(route.request().postDataJSON().action).toBe('cancel');const turn=mock.recorded[0];turn.question='剛完成的問題';mock.reply(mock.conversations[0],turn);await gate;await json(route,{ok:true});
 });
 await page.getByRole('button',{name:'取消這次提問',exact:true}).click();
 await expect(page.locator('.answer-audio audio')).toHaveCount(1);expect(await page.locator('.answer-audio audio').evaluate((a:HTMLAudioElement)=>a.paused)).toBe(true);
 release();await expect(page.locator('.voice-composer-status')).toContainText('正在聆聽');
 expect(await page.locator('.answer-audio audio').evaluate((a:HTMLAudioElement)=>a.paused)).toBe(true);
});


test('route change stops voice audio and cancels its pending playback intent',async({page})=>{
 const mock=await mockVoice(page);mock.behavior.audio=true;await microphone(page);await page.goto(mapPath);await open(page);
 await page.getByRole('switch',{name:'語音模式',exact:true}).click();await speak(page);await expect.poll(()=>mock.recorded.length).toBe(1);
 const turn=mock.recorded[0];turn.question='路由切換的問題';mock.reply(mock.conversations[0],turn);
 const audio=page.locator('.answer-audio audio');await expect.poll(()=>audio.evaluate((a:HTMLAudioElement)=>a.paused)).toBe(false);
 await audio.evaluate(a=>{(window as any).previousVoiceAudio=a;});
 await page.getByRole('tab',{name:'教材來源',exact:true}).click();
 await expect.poll(()=>page.evaluate(()=>(window as any).previousVoiceAudio.paused)).toBe(true);
});


test('switching account releases private voice audio and never carries it into the next account',async({page})=>{
 const mock=await mockVoice(page);mock.behavior.audio=true;await microphone(page);await page.goto(mapPath);await open(page);
 await page.getByRole('switch',{name:'語音模式',exact:true}).click();await speak(page);await expect.poll(()=>mock.recorded.length).toBe(1);
 const turn=mock.recorded[0];turn.question='第一個帳號的問題';mock.reply(mock.conversations[0],turn);
 const audio=page.locator('.answer-audio audio');await expect.poll(()=>audio.evaluate((a:HTMLAudioElement)=>a.paused)).toBe(false);
 await audio.evaluate(a=>{(window as any).previousVoiceAudio=a;});await page.getByRole('button',{name:'登出',exact:true}).click();
 await expect(page.getByRole('heading',{name:'登入您的帳戶'})).toBeVisible();
 expect(await page.evaluate(()=>({paused:(window as any).previousVoiceAudio.paused,src:(window as any).previousVoiceAudio.getAttribute('src')}))).toEqual({paused:true,src:null});
 const stranger={schema:'learner-identity/v1',learner_id:'88888888-8888-4888-8888-888888888888'};
 await page.route('**/v1/session/login',r=>json(r,stranger));await page.route('**/v1/session/refresh',r=>json(r,stranger));
 await page.route('**/v1/materials/*/voice-conversations',r=>json(r,{conversations:[]}));
 await page.getByLabel('Email',{exact:true}).fill('second@example.test');await page.getByLabel('密碼',{exact:true}).fill('Synthetic password 42');await page.getByRole('button',{name:'登入',exact:true}).click();
 await expect(page.getByRole('button',{name:'登出',exact:true})).toBeVisible();
 await expect(page.locator('.voice-answer')).toHaveCount(0);await expect(page.locator('.answer-audio audio')).toHaveCount(0);
 expect(await page.evaluate(()=>(window as any).previousVoiceAudio.paused)).toBe(true);
});


test('opening voice questions pauses existing podcast media',async({page})=>{
 const mock=await mockVoice(page),conversation=mock.add(),turn=mock.turn(conversation,'合成音訊','ready',true);
 await page.goto(mapPath);await page.getByRole('tab',{name:'知識地圖',exact:true}).click();
 await page.evaluate(async src=>{const audio=document.createElement('audio');audio.id='podcast-fixture';audio.src=src!;document.body.append(audio);await audio.play();},turn.audio_url);
 await expect.poll(()=>page.locator('#podcast-fixture').evaluate((a:HTMLAudioElement)=>a.paused)).toBe(false);
 await open(page);await expect.poll(()=>page.locator('#podcast-fixture').evaluate((a:HTMLAudioElement)=>a.paused)).toBe(true);
});


test('older conversations remain selectable while new conversations bind to the displayed revision',async({page})=>{
 const mock=await mockVoice(page),old=mock.add();const oldRevision=`knowledge-structure:sha256:${'f'.repeat(64)}`;
 old.knowledge_structure_revision=oldRevision;old.is_current_revision=false;old.source_resolver=old.source_resolver.replace(structureRevision,oldRevision);mock.turn(old,'舊版本問題');
 await page.goto(mapPath);await open(page);await choose(page,old.conversation_id);await expect(page.locator('.voice-question p')).toHaveText('舊版本問題');await expect(page.locator('.voice-revision-note')).toBeVisible();
 await page.getByRole('button',{name:'新對話',exact:true}).click();await page.getByLabel('問題／辨識文字').fill('目前版本問題');
 const request=page.waitForRequest(r=>r.method()==='POST'&&r.url().endsWith(`/v1/materials/${materialId}/voice-conversations`));
 await page.getByRole('button',{name:'送出問題',exact:true}).click();expect((await request).postDataJSON()).toEqual({knowledge_structure_revision:structureRevision});
 await expect(page.locator('.voice-question p')).toHaveText('目前版本問題');
});


test('continuous voice supports typed questions, barge-in and microphone cleanup',async({page})=>{
 const mock=await mockVoice(page);mock.behavior.audio=true;await microphone(page);await page.goto(mapPath);await open(page);
 await page.getByRole('switch',{name:'語音模式',exact:true}).click();
 await page.getByLabel('問題／辨識文字').fill('開啟語音後的文字問題');await page.getByRole('button',{name:'送出問題',exact:true}).click();
 const audio=page.locator('.answer-audio audio');await expect.poll(()=>audio.evaluate((a:HTMLAudioElement)=>a.paused)).toBe(false);
 await speak(page);await expect.poll(()=>audio.evaluate((a:HTMLAudioElement)=>a.paused)).toBe(true);
 await expect.poll(()=>mock.recorded.length).toBe(1);
 expect(mock.conversations).toHaveLength(1);expect(mock.conversations[0].turns[0].mode).toBe('voice');
 await page.getByRole('switch',{name:'語音模式',exact:true}).click();
 await expect.poll(()=>page.evaluate(()=>(window as any).stoppedTracks)).toBeGreaterThan(0);
});

test('sources use structured references without rewriting numeric literals and history truncates long titles',async({page})=>{
 const mock=await mockVoice(page),conversation=mock.add();conversation.title='非常長的教材問題'.repeat(20);
 const t=mock.turn(conversation,'來源測試'),citation=t.answer!.citations[0];
 t.answer!.text='保留向量 [0,3] 與 values[0]。';
 t.answer!.citations.push(citation);
 await page.goto(mapPath);await open(page);await choose(page,conversation.conversation_id);
 await expect(page.locator('.voice-answer')).toHaveText(t.answer!.text);
 await page.locator('.voice-sources summary').click();await expect(page.locator('.voice-source-list button')).toHaveCount(1);
 await page.getByRole('button',{name:'對話紀錄',exact:true}).click();
 const entry=page.locator('.voice-history-list button');expect(await entry.evaluate(el=>getComputedStyle(el).textOverflow)).toBe('ellipsis');
 await expect(page.locator('.voice-history-menu select')).toHaveCount(0);
});

test('microphone permission denial turns voice mode off and leaves text usable',async({page})=>{
 await mockVoice(page);
 await page.addInitScript(()=>Object.defineProperty(navigator.mediaDevices,'getUserMedia',{value:()=>Promise.reject(new DOMException('Permission denied','NotAllowedError'))}));
 await page.goto(mapPath);await open(page);await page.getByRole('switch',{name:'語音模式'}).click();
 await expect(page.getByRole('alert')).toContainText('Permission denied');
 await expect(page.getByRole('switch',{name:'語音模式'})).toHaveAttribute('aria-checked','false');
 await expect(page.getByLabel('問題／辨識文字')).toBeEnabled();
});

test('closing during microphone permission releases a late stream without sending audio',async({page})=>{
 const mock=await mockVoice(page);
 await page.addInitScript(()=>{
  (window as any).stoppedTracks=0;
  Object.defineProperty(navigator.mediaDevices,'getUserMedia',{value:()=>new Promise(resolve=>{
   (window as any).grantMicrophone=()=>resolve({getTracks:()=>[{stop:()=>{(window as any).stoppedTracks++;}}]});
  })});
 });
 await page.goto(mapPath);await open(page);await page.getByRole('switch',{name:'語音模式'}).click();
 await expect(page.locator('.voice-composer-status')).toContainText('等待麥克風權限');
 await page.getByRole('button',{name:'關閉',exact:true}).click();
 await page.evaluate(()=>(window as any).grantMicrophone());
 await expect.poll(()=>page.evaluate(()=>(window as any).stoppedTracks)).toBe(1);
 expect(mock.recorded).toHaveLength(0);
});

test('saved conversation recovers from a transient read failure without resending a question',async({page})=>{
 const mock=await mockVoice(page),conversation=mock.add();mock.turn(conversation,'已完成的問題');
 let reads=0;
 await page.route(`**/v1/voice-conversations/${conversation.conversation_id}`,async route=>{
  reads++;
  if(reads===1)return route.fulfill({status:502,contentType:'text/plain',body:'temporary gateway failure'});
  return route.fallback();
 });
 await page.goto(mapPath);await open(page);await choose(page,conversation.conversation_id);
 await expect(page.locator('.voice-answer')).toContainText(mock.answer.text,{timeout:10000});
 await expect(page.locator('.voice-composer [role="alert"]')).toHaveCount(0);
 await choose(page,conversation.conversation_id);
 await expect(page.locator('.voice-answer')).toContainText(mock.answer.text);
 expect(mock.questions).toEqual([]);
 expect(reads).toBeGreaterThanOrEqual(3);
});

test('real local VAD rejects non-speech and preserves quiet speech with a sentence pause',async({page},testInfo)=>{
 test.setTimeout(90000);
 const mock=await mockVoice(page), uploads:Buffer[]=[], external:string[]=[], assets:string[]=[];
 page.on('request',request=>{
  if(request.url().includes('/voice-assets/'))assets.push(request.url());
  if(request.url().includes('/recordings'))uploads.push(request.postDataBuffer()!);
  if(!request.url().startsWith('http://127.0.0.1:')&&!request.url().startsWith('blob:')&&!request.url().startsWith('data:'))external.push(request.url());
 });
 const speech=readFileSync(new URL('../fixtures/voice/speech.wav',import.meta.url)).toString('base64');
 await page.addInitScript(({speech})=>{
  const w=window as any;
  Object.defineProperty(navigator.mediaDevices,'getUserMedia',{value:async()=>{
   const context=new AudioContext({sampleRate:16000}),destination=context.createMediaStreamDestination();
   await context.resume();
   w.voiceTracks=destination.stream.getTracks();
   w.playSample=async(kind:string)=>{
    const decoded=await context.decodeAudioData(Uint8Array.from(atob(speech),c=>c.charCodeAt(0)).buffer);
    const original=decoded.getChannelData(0),rate=context.sampleRate;
    const duration=kind==='noise'?4:original.length/rate*2+0.8;
    const buffer=context.createBuffer(1,Math.ceil((duration+2)*rate),rate),samples=buffer.getChannelData(0);
    const rms=Math.sqrt(original.reduce((sum,x)=>sum+x*x,0)/original.length),scale=0.008/rms;
    let seed=17;
    for(let i=0;i<duration*rate;i++){
     seed=(Math.imul(seed,1664525)+1013904223)|0;
     const random=(seed/2147483648);
     if(kind==='noise')samples[i]=random*.08+Math.sin(i/rate*2*Math.PI*60)*.1+(i%8000<32?.3:0);
     else {
      const offset=i<original.length?i:i-original.length-Math.round(.8*rate);
      const value=(offset>=0&&offset<original.length?original[offset]*scale:0);
      samples[i]=value+(kind==='mixed'?random*.003:0);
     }
    }
    const source=context.createBufferSource();source.buffer=buffer;source.connect(destination);
    source.start();await new Promise(resolve=>{source.onended=resolve;});
    return {inputSeconds:duration, rms:0.008};
   };
   return destination.stream;
  }});
 },{speech});
 await page.goto(mapPath);await open(page);await page.getByRole('switch',{name:'語音模式'}).click();
 await expect(page.locator('.voice-composer-status')).toContainText('正在聆聽',{timeout:30000});
 await page.evaluate(()=>(window as any).playSample('noise'));
 expect(uploads).toHaveLength(0);expect(mock.recorded).toHaveLength(0);
 for(const kind of ['quiet','mixed']) {
  const before=uploads.length;
  const sample=await page.evaluate(kind=>(window as any).playSample(kind),kind);
  await expect.poll(()=>uploads.length).toBe(before+1);
  const wav=uploads.at(-1)!;
  expect(wav.subarray(0,4).toString()).toBe('RIFF');expect(wav.readUInt32LE(24)).toBe(16000);
  const seconds=(wav.length-44)/32000;
  // 兩句之間的 800ms 停頓不得分成兩次上傳，也不得丟掉第一句。
  expect(seconds).toBeGreaterThan(sample.inputSeconds-.5);
  // 800ms 前置緩衝 + 1400ms 收尾，另容許三個 32ms 模型 frame。
  expect(seconds).toBeLessThan(sample.inputSeconds+2.3);
  await testInfo.attach(`${kind}.wav`,{body:wav,contentType:'audio/wav'});
  mock.recorded.at(-1)!.status='cancelled';
  await expect(page.locator('.voice-composer-status')).toHaveText('正在聆聽');
 }
 await page.getByRole('switch',{name:'語音模式'}).click();
 await expect.poll(()=>page.evaluate(()=>(window as any).voiceTracks.every((track:MediaStreamTrack)=>track.readyState==='ended'))).toBe(true);
 expect(external).toEqual([]);
 for(const file of ['silero_vad_v6.onnx','ort-wasm-simd-threaded.wasm'])expect(assets.filter(url=>url.endsWith(file))).toHaveLength(1);
});

test('closing while the voice model loads stops microphone and prevents late initialization',async({page})=>{
 const mock=await mockVoice(page);await microphone(page);
 let release:()=>void=()=>{};
 const delayed=new Promise<void>(resolve=>{release=resolve;});
 let requested=false;
 await page.route('**/assets/voice-vad-*.js',async route=>{
  requested=true;await delayed;
  await route.fulfill({contentType:'text/javascript',body:'export async function createVoiceVad(){window.lateVoiceInit=true;throw Error("Should not initialize after close");}'});
 });
 await page.goto(mapPath);await open(page);
 // 只有移到「語音模式」開關時才預載語音模型（只用文字問答時不下載）。
 await page.getByRole('switch',{name:'語音模式'}).hover();
 await expect.poll(()=>requested).toBe(true);
 await expect(page.getByRole('switch',{name:'語音模式'})).toHaveAttribute('aria-checked','false');
 await expect(page.getByLabel('問題／辨識文字')).toBeEnabled();
 await page.getByRole('switch',{name:'語音模式'}).click();
 await expect(page.locator('.voice-composer-status')).toContainText('正在載入人聲辨識');
 await page.getByRole('button',{name:'關閉',exact:true}).click();
 await expect.poll(()=>page.evaluate(()=>(window as any).stoppedTracks)).toBe(1);
 release();
 await page.waitForLoadState('networkidle');
 expect(await page.evaluate(()=>(window as any).lateVoiceInit)).toBeUndefined();
 expect(mock.recorded).toHaveLength(0);
});
