import {expect,test,type Page} from '@playwright/test';
import {materialId,structureView,mockKnowledgeMapApi,json} from '../fixtures/knowledge-map';

const id='77777777-7777-4777-8777-777777777777';
const proposal={title:'停止與求助',goals:['理解停止條件'],topics:['有限重試與求助']};

async function setup(page:Page,supplement=true,fail=false){
 await mockKnowledgeMapApi(page,structureView());
 const scope={topic_id:id,material_id:supplement?materialId:null,request:'尚未搜尋的 Agent 規劃',proposal,version:2,status:'ready',research_id:null,error_code:null};
 let actions=0;
 await page.route('**/v1/topics',r=>json(r,{topics:scope.status==='cancelled'?[]:[scope]}));
 await page.route(`**/v1/topics?material_id=${materialId}`,r=>json(r,{topics:scope.status==='cancelled'?[]:[scope]}));
 await page.route(`**/v1/topics/${id}`,r=>json(r,scope));
 await page.route(`**/v1/materials/${materialId}/research`,r=>json(r,{researches:[]}));
 await page.route(`**/v1/topics/${id}/actions`,r=>{
  expect(r.request().postDataJSON()).toEqual({action:'cancel',expected_version:2});actions++;
  if(fail)return json(r,{schema:'api-error/v1',reason_code:'TOPIC_CONFLICT',retryable:false,message:'Conflict'},409);
  scope.status='cancelled';scope.version++;
  return json(r,scope);
 });
 return {scope,actions:()=>actions};
}

test('unconfirmed supplement scope can be cancelled from the list after confirmation',async({page})=>{
 const state=await setup(page);
 await page.goto(`/materials/${materialId}/research`);
 const card=page.locator('article').filter({hasText:state.scope.request});
 await expect(card.getByRole('button',{name:'查看搜尋範圍',exact:true})).toBeVisible();
 page.once('dialog',dialog=>dialog.dismiss());
 await card.getByRole('button',{name:'取消這個規劃',exact:true}).click();
 expect(state.actions()).toBe(0);await expect(card).toBeVisible();
 page.once('dialog',dialog=>dialog.accept());
 await card.getByRole('button',{name:'取消這個規劃',exact:true}).click();
 await expect(card).toHaveCount(0);expect(state.actions()).toBe(1);
 await page.reload();await expect(page.getByRole('button',{name:'查看搜尋範圍',exact:true})).toHaveCount(0);
 await expect(page.getByRole('button',{name:'產生搜尋範圍',exact:true})).toBeVisible();
});

test('failed scope cancellation keeps the plan visible and permits retry',async({page})=>{
 const state=await setup(page,true,true);
 await page.goto(`/materials/${materialId}/research`);
 const card=page.locator('article').filter({hasText:state.scope.request});
 page.once('dialog',dialog=>dialog.accept());
 await card.getByRole('button',{name:'取消這個規劃',exact:true}).click();
 await expect(page.getByRole('alert')).toBeVisible();await expect(card).toBeVisible();
 await expect(card.getByRole('button',{name:'取消這個規劃',exact:true})).toBeEnabled();
 expect(state.actions()).toBe(1);expect(state.scope.status).toBe('ready');
});
