import {expect,test,type Page} from '@playwright/test';
import {artifactId,materialId,runId,sessionId,structureRevision,structureView,mockKnowledgeMapApi,json,run} from '../fixtures/knowledge-map';
const mapPath=`/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}`;
const opener=(page:Page)=>page.getByRole('button',{name:'教材問答',exact:true});

for(const width of [1920,1440,390]) test(`processing pages never mount learning chat at ${width}px`,async({page})=>{
 await page.setViewportSize({width,height:900});await mockKnowledgeMapApi(page,structureView());
 await page.route(`**/v1/material-processing-runs/${runId}`,r=>json(r,{...run,status:'running',progress_stage:'semantics',completed_pages:1,total_pages:2,completed_at:null,output_binding:null}));
 await page.goto(`/materials/${materialId}/runs/${runId}`);await expect(page.getByRole('heading',{name:'正在分析教材',exact:true})).toBeVisible();
 await expect(opener(page)).toHaveCount(0);
 await page.route(`**/v1/material-processing-runs/${runId}`,r=>json(r,run));await page.reload();await expect(page.getByRole('heading',{name:'教材整理完成',exact:true})).toBeVisible();await expect(opener(page)).toHaveCount(0);
 await page.getByRole('button',{name:'開啟知識地圖',exact:true}).click();await expect(opener(page)).toBeVisible();
 expect(await opener(page).evaluate(el=>!!el.closest('.material-learning-toolbar'))).toBe(true);
});


async function mockWorkspaces(page:Page){
 await mockKnowledgeMapApi(page,structureView());
 await page.route('**/v1/card-sets',r=>json(r,{schema:'card-set-list/v1',card_sets:[]}));
 await page.route('**/v1/podcasts',r=>json(r,{schema:'podcast-list/v1',podcasts:[]}));
 await page.route(`**/v1/materials/${materialId}/research`,r=>json(r,{researches:[]}));
 await page.route(`**/v1/materials/${materialId}/voice-conversations`,r=>json(r,{conversations:[]}));
 await page.route(`**/v1/materials/${materialId}/sources`,r=>json(r,{schema:'material-sources/v1',material_id:materialId,sources:[]}));
}

// 入口固定在分頁列右端；抽屜屬於 viewport，從分頁列下緣展開，不受內容區的 transform、overflow 或捲動影響。
for(const width of [1920,1440,390]) test(`published workspace chat drawer stays viewport anchored at ${width}px`,async({page})=>{
 await page.setViewportSize({width,height:900});await mockWorkspaces(page);
 const inset=width===390?0:12; // 手機為貼齊左右與底部的全高面板
 const paths=[mapPath,`/materials/${materialId}/podcasts`,`/materials/${materialId}/concept-cards`,`/materials/${materialId}/research`,`/materials/${materialId}/sources`,`${mapPath}/study-sessions/${sessionId}`];
 for(const [index,path] of paths.entries()){
  await page.goto(path);const trigger=opener(page);await expect(trigger).toBeVisible();await expect(trigger).toHaveCount(1);
  expect(await trigger.evaluate(el=>!!el.closest('.material-learning-toolbar'))).toBe(true);
  const closed=(await trigger.boundingBox())!;
  await page.locator('.app-main').evaluate(el=>Object.assign((el as HTMLElement).style,{transform:'translate(31px, 17px)',contain:'paint',isolation:'isolate',overflow:'hidden',height:'240px',maxWidth:'360px'}));
  await page.evaluate(()=>{const spacer=document.createElement('div');spacer.style.height='2000px';spacer.dataset.testScroll='true';document.body.append(spacer);window.scrollTo(0,300);});
  await expect.poll(()=>page.evaluate(()=>window.scrollY)).toBeGreaterThan(0);
  await trigger.click();const panel=page.getByRole('dialog',{name:'教材問答',exact:true});await expect(panel).toBeVisible();await expect(page.getByRole('textbox',{name:'問題／辨識文字'})).toBeFocused();
  expect(await panel.evaluate(el=>el.parentElement===document.body)).toBe(true);
  // 等面板的出現動畫結束再量位置。
  await panel.evaluate(el=>Promise.all(el.getAnimations().map(a=>a.finished)));
  const opened=(await panel.boundingBox())!;expect(width-opened.x-opened.width).toBeCloseTo(inset,0);expect(opened.y).toBeGreaterThanOrEqual(8);expect(opened.y).toBeLessThanOrEqual(width===390?(await page.locator('.app-header').boundingBox())!.height+1:450);expect(900-opened.y-opened.height).toBeCloseTo(inset,0);expect(opened.x).toBeGreaterThanOrEqual(0);
  await page.evaluate(()=>window.scrollTo(0,600));await expect.poll(()=>page.evaluate(()=>window.scrollY)).toBeGreaterThan(300);const moved=(await panel.boundingBox())!;expect(moved.x).toBeCloseTo(opened.x,0);expect(moved.y).toBeCloseTo(opened.y,0);
  await page.keyboard.press('Escape');await expect(panel).toHaveCount(0);await expect(trigger).toBeFocused();
  if(index===0){
   await page.locator('.app-main').evaluate(el=>(el as HTMLElement).removeAttribute('style'));await page.evaluate(()=>{document.querySelector('[data-test-scroll]')?.remove();window.scrollTo(0,0);});
   await page.getByRole('tab',{name:'學習與測驗',exact:true}).click();await expect(trigger).toBeVisible();const review=(await trigger.boundingBox())!;expect(review.x).toBeCloseTo(closed.x,0);expect(review.y).toBeCloseTo(closed.y,0);
  }
 }
});

test('setup and unpublished material workspaces never mount learning chat',async({page})=>{
 await mockWorkspaces(page);
 const material={schema:'material-library-item/v1',material_id:materialId,source_artifact_id:artifactId,display_name:'新教材',size_bytes:100,created_at:run.created_at,latest_attempt:null,available_structures:[],study_sessions:[],head_revision:null};
 await page.route(`**/v1/materials/${materialId}`,r=>json(r,material));
 for(const path of ['/upload',`/materials/${materialId}/sources`,`/materials/${materialId}/podcasts`,`/materials/${materialId}/concept-cards`,`/materials/${materialId}/research`]){
  await page.goto(path);await expect(page.locator('.app-main')).toBeVisible();
  if(path!=='/upload')await expect(page.locator('main')).toContainText(path.endsWith('/sources')?'教材來源':'新教材');
  await expect(opener(page)).toHaveCount(0);
 }
});

// 寬螢幕：問答是右側面板，主內容讓出空間不被蓋住；再按一次入口即可收起。
test('wide screens dock the chat panel beside the content instead of covering it',async({page})=>{
 await page.setViewportSize({width:1440,height:900});await mockWorkspaces(page);
 await page.goto(mapPath);const trigger=opener(page);await trigger.click();
 const panel=page.getByRole('dialog',{name:'教材問答',exact:true});await expect(panel).toBeVisible();
 await expect(trigger).toHaveAttribute('aria-expanded','true');
 const box=(await panel.boundingBox())!;
 await expect.poll(async()=>{const main=(await page.locator('.app-main > *').first().boundingBox())!;return main.x+main.width;}).toBeLessThanOrEqual(box.x);
 await trigger.click();await expect(panel).toHaveCount(0);await expect(trigger).toBeFocused();
 expect(await page.evaluate(()=>document.documentElement.classList.contains('qa-panel-open'))).toBe(false);
});
