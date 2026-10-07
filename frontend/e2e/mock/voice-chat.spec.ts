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
 const answer={text:'堆疊採用後進先出。最後放入的資料會最先取出。',supported:true,citations:[structure.concepts[0].claims[0]]};
 const finishAudio=(c:VoiceView,t:VoiceTurn)=>{t.status='ready';t.error_code=null;t.audio_url=`/v1/voice-conversations/${c.conversation_id}/turns/${t.turn_id}/audio`;};
 const reply=(c:VoiceView,t:VoiceTurn)=>{t.answer={...answer};t.status=behavior.status;t.error_code=t.status==='failed'?'VOICE_PROVIDER_FAILED':null;if(behavior.audio&&t.status==='ready')finishAudio(c,t);};
 const add=()=>{const c:VoiceView={conversation_id:next(),material_id:materialId,knowledge_structure_revision:structureRevision,title:`已保存的對話 ${counter}`,created_at:'2026-10-06T00:00:00Z',is_current_revision:true,source_resolver:structure.source_resolver,turns:[]};conversations.push(c);return c;};
 const turn=(c:VoiceView,question:string,status='ready',audio=false)=>{const id=next();const t:VoiceTurn={turn_id:id,question,status,answer:status==='ready'?{...answer}:null,error_code:null,audio_url:audio?`/v1/voice-conversations/${c.conversation_id}/turns/${id}/audio`:null};c.turns.push(t);return t;};
 await page.route(`**/v1/materials/${materialId}/voice-conversations`,r=>{
  if(r.request().method()==='POST'){expect(r.request().headers()['idempotency-key']).toBeTruthy();return json(r,add(),201);}
  return json(r,{conversations:conversations.map(({turns,is_current_revision,source_resolver,...c})=>c)});
 });
 await page.route('**/v1/voice-conversations/**',r=>{
  const parts=new URL(r.request().url()).pathname.split('/').filter(Boolean),c=conversations.find(c=>c.conversation_id===parts[2]);
  if(!c)return json(r,{schema:'api-error/v1',request_id:materialId,reason_code:'RESOURCE_NOT_FOUND',retryable:false,message:'Not found'},404);
  if(parts.length===3){if(r.request().method()==='DELETE'){deletes++;conversations.splice(conversations.indexOf(c),1);return json(r,{ok:true});}return json(r,c);}
  if(parts[3]==='recordings'){expect(r.request().headers()['idempotency-key']).toBeTruthy();expect(r.request().postDataBuffer()!.length).toBeGreaterThan(0);const t=turn(c,'','transcribing');recorded.push(t);return json(r,{turn_id:t.turn_id},202);}
  if(parts.length===4&&parts[3]==='turns'){const body=r.request().postDataJSON();questions.push(body.question);expect(r.request().headers()['idempotency-key']).toBeTruthy();const t=turn(c,body.question);reply(c,t);c.title=body.question;return json(r,{turn_id:t.turn_id},202);}
  if(parts.at(-1)==='actions'){
   const t=c.turns.find(t=>t.turn_id===parts[4])!,body=r.request().postDataJSON();actions.push({...body,turnId:t.turn_id});
   if(body.action==='cancel')t.status='cancelled';
   else if(body.action==='send'){expect(t.status).toBe('draft');t.question=body.question;reply(c,t);c.title=body.question;}
   else if(body.action==='retry')t.status=t.answer?'speaking':'pending';
   return json(r,{ok:true});
  }
  return json(r,{ok:true});
 });
 await page.route('**/v1/voice-conversations/*/turns/*/audio',r=>r.fulfill({status:200,contentType:'audio/wav',body:wav()}));
 return {conversations,add,turn,answer,actions,questions,recorded,behavior,finishAudio,deletes:()=>deletes};
}
async function open(page:Page) {await page.getByRole('button',{name:'教材問答',exact:true}).click();await expect(page.getByRole('dialog',{name:'教材問答'})).toBeVisible();}
async function choose(page:Page,id:string) {await page.getByRole('button',{name:'對話紀錄',exact:true}).click();await page.getByLabel('已保存的對話').selectOption(id);}
async function microphone(page:Page) {
 await page.addInitScript(()=>{
  (window as any).stoppedTracks=0;
  Object.defineProperty(navigator.mediaDevices,'getUserMedia',{value:async()=>({getTracks:()=>[{stop:()=>{(window as any).stoppedTracks++;}}]})});
  (window as any).MediaRecorder=class {
   state='inactive';mimeType='audio/webm';ondataavailable?: (e:{data:Blob})=>void;onstop?:()=>void;
   start(){this.state='recording';}
   stop(){this.state='inactive';queueMicrotask(()=>{this.ondataavailable?.({data:new Blob(['synthetic recording'],{type:this.mimeType})});this.onstop?.();});}
  };
 });
}

test('compact widget has one entry, chat bubbles, secondary sources and a growing single composer',async({page},testInfo)=>{
 await page.setViewportSize({width:1440,height:900});const mock=await mockVoice(page);await page.goto(mapPath);await open(page);
 const panel=page.getByRole('dialog',{name:'教材問答'}),input=page.getByLabel('問題／辨識文字');
 await expect(page.getByRole('button',{name:'教材問答',exact:true})).toHaveCount(0);await expect(panel.getByRole('heading',{name:'教材問答',exact:true})).toBeVisible();await expect(input).toBeFocused();
 await expect(page.getByLabel('已保存的對話')).not.toBeVisible();
 const box=(await panel.boundingBox())!,header=(await page.locator('.voice-header').boundingBox())!,composer=(await page.locator('.voice-composer').boundingBox())!;
 expect(box.width).toBeLessThanOrEqual(380);expect(box.height).toBeLessThanOrEqual(520);expect(header.height).toBeLessThanOrEqual(56);expect(composer.height).toBeLessThan(110);
 expect((await page.locator('.voice-turns').boundingBox())!.height).toBeGreaterThan(box.height*.6);
 await panel.screenshot({path:testInfo.outputPath('voice-empty-desktop.png')});
 await page.getByRole('button',{name:'主要觀念是什麼？',exact:true}).click();await expect(input).toHaveValue('主要觀念是什麼？');await expect(input).toBeFocused();
 await input.fill('較長的輸入\n'.repeat(30));expect((await input.boundingBox())!.height).toBeLessThanOrEqual(112);expect(await input.evaluate(el=>el.scrollHeight>el.clientHeight)).toBe(true);
 await input.fill('堆疊是什麼？');await page.getByRole('button',{name:'送出問題',exact:true}).click();await expect(page.locator('.voice-answer')).toHaveText(mock.answer.text);await expect(input).toHaveValue('');expect(mock.questions).toEqual(['堆疊是什麼？']);
 const user=(await page.locator('.voice-question').boundingBox())!,assistant=(await page.locator('.voice-response').boundingBox())!;expect(user.x).toBeGreaterThan(assistant.x);
 await expect(page.locator('.voice-question > span')).toHaveCount(0);await expect(page.getByRole('button',{name:'查看第 1 頁來源'})).toHaveCount(0);
 await panel.screenshot({path:testInfo.outputPath('voice-messages-desktop.png')});
 await page.getByText(/^查看來源 ·/).click();await page.getByRole('button',{name:'查看第 1 頁來源'}).click();await expect(page.getByRole('dialog',{name:'教材來源'})).toBeVisible();await page.keyboard.press('Escape');await expect(panel).toBeVisible();
 await input.fill('換行');await input.press('Shift+Enter');await input.pressSequentially('補充');await input.dispatchEvent('keydown',{key:'Enter',isComposing:true});expect(mock.questions).toHaveLength(1);await input.press('Enter');await expect.poll(()=>mock.questions.length).toBe(2);expect(mock.questions.at(-1)).toBe('換行\n補充');await expect(input).toHaveValue('');
 for(let i=0;i<3;i++){await page.getByRole('button',{name:'關閉',exact:true}).click();await expect(page.getByRole('button',{name:'教材問答',exact:true})).toBeFocused();await open(page);}
 await page.getByRole('button',{name:'對話紀錄',exact:true}).click();await page.keyboard.press('Escape');await expect(panel).toBeVisible();await expect(page.getByRole('button',{name:'對話紀錄',exact:true})).toBeFocused();await page.keyboard.press('Escape');await expect(panel).toHaveCount(0);
});

test('recording and STT use one editable composer and send or cancel clears the draft',async({page},testInfo)=>{
 const mock=await mockVoice(page);await microphone(page);await page.goto(mapPath);await open(page);
 const input=page.getByLabel('問題／辨識文字');
 await page.getByRole('button',{name:'開始錄音',exact:true}).click();await expect(page.getByRole('button',{name:'停止錄音並辨識',exact:true})).toBeVisible();await page.getByRole('button',{name:'取消錄音',exact:true}).click();expect(mock.recorded).toHaveLength(0);expect(await page.evaluate(()=>(window as any).stoppedTracks)).toBeGreaterThan(0);
 const record=async()=>{await page.getByRole('button',{name:'開始錄音',exact:true}).click();await page.getByRole('button',{name:'停止錄音並辨識',exact:true}).click();await expect(page.getByText('正在辨識錄音…',{exact:true})).toBeVisible();};
 await record();mock.recorded[0].status='draft';mock.recorded[0].question='辨識出來的問題';await expect(input).toHaveValue('辨識出來的問題');await expect(input).toBeFocused();
 await expect(page.locator('.voice-panel textarea')).toHaveCount(1);await expect(page.locator('.voice-turn')).toHaveCount(0);await expect(page.getByText('辨識完成，可修改後送出',{exact:true})).toBeVisible();
 await page.getByRole('dialog',{name:'教材問答'}).screenshot({path:testInfo.outputPath('voice-stt-composer.png')});
 await input.fill('修改後的提問');await page.getByRole('button',{name:'送出問題',exact:true}).click();await expect(page.locator('.voice-question p')).toHaveText('修改後的提問');await expect(page.locator('.voice-answer')).toHaveText(mock.answer.text);await expect(input).toHaveValue('');await expect(page.locator('.voice-draft-hint')).toHaveCount(0);expect(mock.actions[0]).toMatchObject({action:'send',question:'修改後的提問'});
 await record();mock.recorded[1].status='draft';mock.recorded[1].question='這筆要取消';await expect(input).toHaveValue('這筆要取消');await page.getByRole('button',{name:'取消這次提問',exact:true}).click();await expect(input).toHaveValue('');await expect(page.locator('.voice-draft-hint')).toHaveCount(0);await expect(page.locator('.voice-question')).toHaveCount(1);
 await record();await page.getByRole('button',{name:'取消這次提問',exact:true}).click();await expect(page.getByText('正在辨識錄音…',{exact:true})).toHaveCount(0);await expect(input).toBeEnabled();expect(mock.recorded[2].status).toBe('cancelled');
});

test('persisted draft stays with its conversation and audio retry never becomes composer text',async({page})=>{
 const mock=await mockVoice(page),first=mock.add(),second=mock.add();first.is_current_revision=false;
 const answer=mock.turn(first,'原本問題','ready');answer.status='failed';const draft=mock.turn(first,'辨識草稿','draft');mock.turn(second,'另一段對話');
 await page.goto(mapPath);await open(page);await choose(page,first.conversation_id);
 const input=page.getByLabel('問題／辨識文字');await expect(input).toHaveValue('辨識草稿');await expect(page.locator('.voice-revision-note')).toContainText('先前的教材版本');
 await input.fill('尚未送出的修改');await choose(page,second.conversation_id);await expect(input).toHaveValue('');await expect(page.locator('.voice-draft-hint')).toHaveCount(0);await expect(page.locator('.voice-question p')).toHaveText('另一段對話');
 await page.reload();await open(page);await choose(page,first.conversation_id);await expect(input).toHaveValue('辨識草稿');
 await page.getByRole('button',{name:'取消這次提問',exact:true}).click();await expect(input).toHaveValue('');expect(draft.status).toBe('cancelled');
 await input.fill('下一個尚未送出的問題');await page.getByRole('button',{name:'重試語音',exact:true}).click();await expect(page.getByText('正在製作語音…',{exact:true})).toBeVisible();await expect(input).toHaveValue('下一個尚未送出的問題');await expect(page.locator('.voice-draft-hint')).toHaveCount(0);expect(mock.actions.at(-1)).toMatchObject({action:'retry',turnId:answer.turn_id});expect(mock.actions.at(-1)).not.toHaveProperty('question');
 answer.status='ready';await expect(input).toBeEnabled();await page.getByRole('button',{name:'新對話',exact:true}).click();await expect(input).toHaveValue('');await expect(input).toBeFocused();await expect(page.locator('.voice-revision-note')).toHaveCount(0);
 await choose(page,second.conversation_id);await page.getByRole('button',{name:'對話紀錄',exact:true}).click();page.once('dialog',dialog=>dialog.dismiss());await page.getByRole('button',{name:'刪除對話',exact:true}).click();expect(mock.deletes()).toBe(0);
 page.once('dialog',dialog=>dialog.accept());await page.getByRole('button',{name:'刪除對話',exact:true}).click();await expect(input).toBeFocused();await expect(page.locator('.voice-turn')).toHaveCount(0);expect(mock.deletes()).toBe(1);expect(mock.conversations).toHaveLength(1);
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
 await expect(page.locator('.voice-answer').last()).toHaveText('最後一則完成的回答');expect(await stream.evaluate(el=>el.scrollTop)).toBe(0);
 expect((await page.locator('.voice-header').boundingBox())!.y).toBeCloseTo(header.y,0);expect((await page.locator('.voice-composer').boundingBox())!.y).toBeCloseTo(composer.y,0);
 await page.getByRole('button',{name:'播放回答語音',exact:true}).click();const audio=page.locator('.answer-audio audio').first();await expect.poll(()=>audio.evaluate((el:HTMLAudioElement)=>el.paused)).toBe(false);
 await page.getByRole('slider',{name:'回答語音進度',exact:true}).fill('2');await expect.poll(()=>audio.evaluate((el:HTMLAudioElement)=>el.currentTime)).toBeGreaterThanOrEqual(2);
 await page.route('**/test-podcast-audio.wav',r=>r.fulfill({contentType:'audio/wav',body:wav()}));
 await page.evaluate(async()=>{const outside=document.createElement('audio');outside.id='test-podcast';outside.src='/test-podcast-audio.wav';document.body.append(outside);await outside.play();});await expect.poll(()=>audio.evaluate((el:HTMLAudioElement)=>el.paused)).toBe(true);
 await page.getByRole('button',{name:'播放回答語音',exact:true}).click();await expect.poll(()=>page.locator('#test-podcast').evaluate((el:HTMLAudioElement)=>el.paused)).toBe(true);
 await page.getByLabel('問題／辨識文字').fill('新的追問');await page.getByRole('button',{name:'送出問題',exact:true}).click();await expect(page.locator('.voice-question p').last()).toHaveText('新的追問');await expect.poll(()=>stream.evaluate(el=>el.scrollHeight-el.scrollTop-el.clientHeight)).toBeLessThan(2);
});

test('390px chat uses the same compact header, fixed composer and keyboard focus',async({page},testInfo)=>{
 await page.setViewportSize({width:390,height:844});const mock=await mockVoice(page),conversation=mock.add();for(let i=0;i<10;i++)mock.turn(conversation,`問題 ${i}`);
 await page.goto(mapPath);await open(page);await choose(page,conversation.conversation_id);
 const panel=page.getByRole('dialog',{name:'教材問答'}),box=(await panel.boundingBox())!,appHeader=(await page.locator('.app-header').boundingBox())!;
 expect(box.width).toBe(374);expect(box.x).toBeGreaterThanOrEqual(0);expect(box.y).toBeGreaterThanOrEqual(appHeader.height);expect(box.y+box.height).toBeLessThanOrEqual(844);
 await expect(page.getByRole('button',{name:'教材問答',exact:true})).toHaveCount(0);await expect(page.locator('.voice-panel textarea')).toHaveCount(1);
 await page.getByLabel('問題／辨識文字').fill('多行草稿\n'.repeat(25));const composer=(await page.locator('.voice-composer').boundingBox())!,header=(await page.locator('.voice-header').boundingBox())!;
 await page.locator('.voice-turns').evaluate(el=>{el.scrollTop=0;});expect((await page.locator('.voice-composer').boundingBox())!.y).toBeCloseTo(composer.y,0);expect((await page.locator('.voice-header').boundingBox())!.y).toBeCloseTo(header.y,0);expect(composer.y+composer.height).toBeLessThanOrEqual(box.y+box.height);
 expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBe(390);await panel.screenshot({path:testInfo.outputPath('voice-mobile.png')});
 await page.getByLabel('問題／辨識文字').focus();await page.keyboard.press('Tab');await expect(page.getByRole('button',{name:'送出問題',exact:true})).toBeFocused();
 await page.keyboard.press('Escape');await expect(panel).toHaveCount(0);await expect(page.getByRole('button',{name:'教材問答',exact:true})).toBeFocused();
});

test('text and confirmed recording turns share a conversation but only the recording reply autoplays',async({page})=>{
 const mock=await mockVoice(page);mock.behavior.audio=true;const conversation=mock.add();mock.turn(conversation,'歷史回答','ready',true);
 await microphone(page);await page.goto(mapPath);await open(page);await choose(page,conversation.conversation_id);
 await expect(page.locator('.answer-audio audio')).toHaveCount(1);expect(await page.locator('.answer-audio audio').first().evaluate((a:HTMLAudioElement)=>a.paused)).toBe(true);
 const input=page.getByLabel('問題／辨識文字');await input.fill('文字問題');await page.getByRole('button',{name:'送出問題',exact:true}).click();await expect(page.locator('.answer-audio audio')).toHaveCount(2);expect(await page.locator('.answer-audio audio').last().evaluate((a:HTMLAudioElement)=>a.paused)).toBe(true);
 await page.getByRole('button',{name:'開始錄音',exact:true}).click();await page.getByRole('button',{name:'停止錄音並辨識',exact:true}).click();await expect.poll(()=>mock.recorded.length).toBe(1);
 const voice=mock.recorded[0];voice.status='draft';voice.question='錄音辨識內容';await expect(input).toHaveValue(voice.question);await expect(page.locator('.voice-question')).toHaveCount(2);
 mock.behavior.status='speaking';await input.fill('確認後的語音問題');await page.getByRole('button',{name:'送出問題',exact:true}).click();await expect(page.locator('.voice-question p').last()).toHaveText('確認後的語音問題');await expect(page.locator('.voice-answer').last()).toHaveText(mock.answer.text);
 await page.route('**/test-podcast-audio.wav',r=>r.fulfill({contentType:'audio/wav',body:wav()}));await page.evaluate(async()=>{const other=document.createElement('audio');other.id='other-media';other.src='/test-podcast-audio.wav';document.body.append(other);await other.play();});
 mock.finishAudio(conversation,voice);await expect(page.locator('.answer-audio audio')).toHaveCount(3);const spoken=page.locator('.answer-audio audio').last();await expect.poll(()=>spoken.evaluate((a:HTMLAudioElement)=>a.paused)).toBe(false);await expect.poll(()=>page.locator('#other-media').evaluate((a:HTMLAudioElement)=>a.paused)).toBe(true);
 await spoken.evaluate((a:HTMLAudioElement)=>{a.pause();a.dispatchEvent(new Event('ended'));});await expect(page.getByRole('button',{name:'開始錄音',exact:true})).toBeVisible();await expect(page.getByRole('button',{name:'停止錄音並辨識',exact:true})).toHaveCount(0);expect(mock.recorded).toHaveLength(1);
 mock.behavior.status='ready';await input.fill('再用文字提問');await page.getByRole('button',{name:'送出問題',exact:true}).click();await expect(page.locator('.answer-audio audio')).toHaveCount(4);expect(await page.locator('.answer-audio audio').last().evaluate((a:HTMLAudioElement)=>a.paused)).toBe(true);expect(mock.conversations).toHaveLength(1);
 await page.getByRole('button',{name:'關閉',exact:true}).click();await open(page);await choose(page,conversation.conversation_id);await expect(page.locator('.answer-audio audio')).toHaveCount(4);expect(await page.locator('.answer-audio audio').evaluateAll(elements=>elements.every(a=>(a as HTMLAudioElement).paused))).toBe(true);
});

test('a voice TTS failure keeps the text complete and an explicit voice retry can autoplay',async({page})=>{
 const mock=await mockVoice(page);mock.behavior.audio=true;mock.behavior.status='failed';await microphone(page);await page.goto(mapPath);await open(page);
 await page.getByRole('button',{name:'開始錄音',exact:true}).click();await page.getByRole('button',{name:'停止錄音並辨識',exact:true}).click();await expect.poll(()=>mock.recorded.length).toBe(1);
 const voice=mock.recorded[0];voice.status='draft';voice.question='語音問題';await expect(page.getByLabel('問題／辨識文字')).toHaveValue(voice.question);await page.getByRole('button',{name:'送出問題',exact:true}).click();
 await expect(page.locator('.voice-answer')).toHaveText(mock.answer.text);await expect(page.getByText('文字回答已完成',{exact:true})).toBeVisible();await expect(page.getByText('本次處理未完成',{exact:true})).toHaveCount(0);await expect(page.locator('.voice-sources summary')).toBeVisible();await expect(page.locator('.voice-draft-hint')).toHaveCount(0);
 await page.getByRole('button',{name:'重試語音',exact:true}).click();await expect(page.getByText('正在製作語音…',{exact:true})).toBeVisible();mock.finishAudio(mock.conversations[0],voice);
 await expect(page.locator('.answer-audio audio')).toHaveCount(1);await expect.poll(()=>page.locator('.answer-audio audio').evaluate((a:HTMLAudioElement)=>a.paused)).toBe(false);expect(mock.actions.at(-1)).toMatchObject({action:'retry',turnId:voice.turn_id});expect(mock.recorded).toHaveLength(1);
});

test('blocked automatic speech offers manual playback without repeating the attempt',async({page})=>{
 await page.addInitScript(()=>{const original=HTMLMediaElement.prototype.play;(window as any).blockAutomatic=true;(window as any).automaticAttempts=0;HTMLMediaElement.prototype.play=function(){if(this.closest('.answer-audio')&&(window as any).blockAutomatic){(window as any).automaticAttempts++;return Promise.reject(new DOMException('blocked','NotAllowedError'));}return original.call(this);};});
 const mock=await mockVoice(page);mock.behavior.audio=true;await microphone(page);await page.goto(mapPath);await open(page);
 await page.getByRole('button',{name:'開始錄音',exact:true}).click();await page.getByRole('button',{name:'停止錄音並辨識',exact:true}).click();await expect.poll(()=>mock.recorded.length).toBe(1);mock.recorded[0].status='draft';mock.recorded[0].question='請用語音回答';
 await expect(page.getByLabel('問題／辨識文字')).toHaveValue('請用語音回答');await page.getByRole('button',{name:'送出問題',exact:true}).click();await expect(page.getByText('未能自動播放，請按「聽回答」。',{exact:true})).toBeVisible();await expect(page.locator('.voice-answer')).toHaveText(mock.answer.text);
 await page.getByLabel('問題／辨識文字').fill('尚未送出的下一題');expect(await page.evaluate(()=>(window as any).automaticAttempts)).toBe(1);
 await page.evaluate(()=>(window as any).blockAutomatic=false);await page.getByRole('button',{name:'播放回答語音',exact:true}).click();await expect.poll(()=>page.locator('.answer-audio audio').evaluate((a:HTMLAudioElement)=>a.paused)).toBe(false);await expect(page.getByText('未能自動播放，請按「聽回答」。',{exact:true})).toHaveCount(0);
});
