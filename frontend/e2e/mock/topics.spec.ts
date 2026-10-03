import {expect,test} from '@playwright/test';
import {materialId,runId,structureRevision,structureView,mockKnowledgeMapApi,json} from '../fixtures/knowledge-map';
const id='77777777-7777-4777-8777-777777777777';
const rid='88888888-8888-4888-8888-888888888888';
const plan={title:'TCP 入門',level:'入門',goals:['理解握手'],topics:['SYN、SYN-ACK、ACK'],exclude:['進階壅塞演算法']};
const candidate={id:'source',kind:'official',title:'TCP handshake',authors:'Mozilla Contributors',year:null,url:'https://developer.mozilla.org/en-US/docs/Glossary/TCP_handshake',doi:null,license:'CC-BY-SA',license_url:null,version:'current',eligible:true,reason:'來源可使用',state:'candidate'};

test('scope can be edited and no material is created until source selection',async({page})=>{
 await mockKnowledgeMapApi(page,structureView());let approved=0,created=0;let topic:any={topic_id:id,request:'TCP',proposal:plan,version:2,status:'ready',research_id:null,error_code:null};
 await page.route('**/v1/topics',r=>r.request().method()==='POST'?json(r,topic,202):json(r,{topics:[topic]}));
 await page.route(`**/v1/topics/${id}`,r=>json(r,topic));
 await page.route(`**/v1/topics/${id}/approve`,r=>{const body=r.request().postDataJSON();expect(body.expected_version).toBe(2);expect(body.proposal.level).toBe('已學過 IP');approved++;topic={...topic,proposal:body.proposal,version:3,status:'approved',research_id:rid,research:{research_id:rid,material_id:null,query:'TCP',mode:'self-study',status:'selecting',candidates:[candidate],selection:[],cursor:null,error_code:null,run_id:null}};return json(r,topic);});
 await page.route(`**/v1/topics/${id}/material`,r=>{expect(r.request().postDataJSON()).toEqual({selected:['source']});created++;topic.research={...topic.research,material_id:materialId,selection:['source'],status:'submitted',run_id:runId,run:{status:'succeeded',error_code:null,output_binding:{knowledge_structure_revision:structureRevision}}};return json(r,{material_id:materialId,research_id:rid},202);});
 await page.goto('/topics');await page.getByLabel('想學的主題').fill('TCP');await page.getByRole('button',{name:'產生學習範圍'}).click();await expect(page.getByRole('heading',{name:'2. 確認學習範圍'})).toBeVisible();expect(approved).toBe(0);expect(created).toBe(0);
 await page.getByLabel('學習程度',{exact:true}).fill('已學過 IP');await page.getByRole('button',{name:'確認範圍並搜尋'}).click();await expect(page.getByRole('checkbox',{name:'選取 TCP handshake'})).toBeVisible();expect(approved).toBe(1);expect(created).toBe(0);
 await page.getByRole('checkbox',{name:'選取 TCP handshake'}).check();await page.getByRole('button',{name:'建立教材與知識地圖'}).click();await expect(page.getByRole('button',{name:'開啟知識地圖',exact:true})).toBeVisible();expect(created).toBe(1);
 await page.reload();await expect(page.getByRole('button',{name:'開啟知識地圖',exact:true})).toBeVisible();expect(created).toBe(1);expect(approved).toBe(1);
 await page.getByRole('button',{name:'開啟知識地圖',exact:true}).click();await expect(page).toHaveURL(new RegExp(`/runs/${runId}/knowledge-structures/`));
});

test('empty source search offers no fake map or enabled create action',async({page})=>{
 await mockKnowledgeMapApi(page,structureView());const topic={topic_id:id,request:'空結果',proposal:plan,version:3,status:'approved',research_id:rid,error_code:null,research:{research_id:rid,material_id:null,query:'空結果',mode:'self-study',status:'selecting',candidates:[],selection:[],cursor:null,error_code:null,run_id:null}};
 await page.route('**/v1/topics',r=>json(r,{topics:[topic]}));await page.route(`**/v1/topics/${id}`,r=>json(r,topic));
 await page.goto(`/topics/${id}`);await expect(page.getByText('沒有找到來源，請調整主題重新規劃。')).toBeVisible();await expect(page.getByRole('button',{name:'建立教材與知識地圖'})).toBeDisabled();await expect(page.getByRole('button',{name:'開啟知識地圖',exact:true})).toHaveCount(0);
});
