import { expect, test } from '@playwright/test';
import { studyLayoutFixture } from './fixtures/study-layout.mjs';

for(const width of [1536,1366,390]) {
  test(`advance uses guidance and stays in the session at ${width}px`,async({page},info)=>{
    await page.setViewportSize({width,height:width===390?844:width===1366?768:1024});
    const label='使用者端與多階段網路服務中的請求及回應角色';
    const fixture=await studyLayoutFixture(page,'submitted',{navigation:true,kind:'remediation',wrong:[],nextLabel:label});
    await fixture.open();
    const next=page.getByRole('button',{name:`下一個觀念：${label}`,exact:true});
    await expect(next).toBeVisible();
    await expect(page.getByRole('button',{name:/查看原始初篩題組|回到地圖繼續學習/})).toHaveCount(0);
    await expect(page.locator('.assessment-cycle .primary-button')).toHaveCount(1);
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
    await page.screenshot({path:info.outputPath('next-step.png'),fullPage:true});
    let release!:()=>void;const gate=new Promise<void>(resolve=>{release=resolve;});
    await page.route('**/guidance/apply',async route=>{await gate;await route.fallback();});
    const before=page.url();await next.click();
    await expect(page.getByRole('button',{name:'正在前往下一個觀念…',exact:true})).toBeDisabled();
    await expect(page).toHaveURL(before);
    release();
    await expect(page).toHaveURL(new RegExp(fixture.path+'$'));
    await expect(page.getByRole('heading',{name:label,level:1,exact:true})).toBeVisible();
    await expect(page.locator('.current-concept-card')).toContainText(label);
    await expect(page.getByRole('button',{name:'開始本輪 6 題',exact:true})).toBeVisible();
    const requests=fixture.requests.filter(r=>r.path.endsWith('/guidance/apply'));
    expect(requests).toHaveLength(1);
    expect(JSON.parse(requests[0].body!)).toEqual({schema:'guidance-apply/v2',guidance_revision:'learner-guidance:sha256:'+ '1'.padStart(64,'0')});
    expect(fixture.requests.some(r=>r.path.endsWith('/focus'))).toBe(false);
  });

  test(`complete applies authority before showing completion at ${width}px`,async({page})=>{
    await page.setViewportSize({width,height:844});
    const fixture=await studyLayoutFixture(page,'submitted',{navigation:true,nextAction:'complete',wrong:[]});await fixture.open();
    let release!:()=>void;const gate=new Promise<void>(resolve=>{release=resolve;});
    await page.route('**/guidance/apply',async route=>{await gate;await route.fallback();});
    await page.getByRole('button',{name:'完成本次學習',exact:true}).click();
    await expect(page.getByRole('button',{name:'正在完成…',exact:true})).toBeDisabled();
    await expect(page.getByRole('heading',{name:'學習已完成',exact:true})).toHaveCount(0);
    release();
    await expect(page.getByRole('heading',{name:'學習已完成',exact:true})).toBeVisible();
    await expect(page).toHaveURL(new RegExp(fixture.path+'$'));
    await expect(page.getByRole('button',{name:/下一個觀念|完成本次學習/})).toHaveCount(0);
  });
}

for(const [name,stage,scenario] of [
  ['wrong points','submitted',{wrong:[2]}],
  ['active remediation','ready',{kind:'remediation'}],
  ['preparing remediation','preparing',{kind:'remediation'}],
  ['another active set','submitted',{wrong:[],preparingHistory:true}],
] as const) test(`${name} does not offer advance`,async({page})=>{
  const f=await studyLayoutFixture(page,stage,{navigation:true,...scenario});await f.open();
  await expect(page.getByRole('button',{name:/下一個觀念|完成本次學習/})).toHaveCount(0);
  if(name==='wrong points')await expect(page.getByRole('button',{name:'開始補強 1 題',exact:true})).toBeVisible();
});

test('failed apply keeps result and allows retry with the same guidance',async({page})=>{
  const f=await studyLayoutFixture(page,'submitted',{navigation:true,wrong:[]});await f.open();
  const before=page.url();let attempts=0;const revisions:string[]=[];
  await page.route('**/guidance/apply',async route=>{
    revisions.push(route.request().postDataJSON().guidance_revision);
    if(++attempts===1)return route.fulfill({status:503,json:{schema:'api-error/v1',request_id:'00000000-0000-4000-8000-000000000099',reason_code:'STORAGE_UNAVAILABLE',retryable:true,message:'Request could not be completed.'}});
    await route.fallback();
  });
  await page.getByRole('button',{name:'下一個觀念：使用者端',exact:true}).click();
  await expect(page.getByRole('alert')).toBeVisible();await expect(page).toHaveURL(before);
  await page.getByRole('button',{name:'下一個觀念：使用者端',exact:true}).click();
  await expect(page.getByRole('heading',{name:'使用者端',level:1,exact:true})).toBeVisible();
  expect(revisions[1]).toBe(revisions[0]);
});

test('stale guidance refreshes the decision without automatic apply',async({page})=>{
  const f=await studyLayoutFixture(page,'submitted',{navigation:true,wrong:[]});await f.open();
  const before=page.url();let applies=0;const revisions:string[]=[];
  await page.route('**/guidance/apply',async route=>{
    revisions.push(route.request().postDataJSON().guidance_revision);
    if(++applies===1){f.setGuidance('complete',2);return route.fulfill({status:409,json:{schema:'api-error/v1',request_id:'00000000-0000-4000-8000-000000000099',reason_code:'LEARNER_GUIDANCE_STALE',retryable:true,message:'Request could not be completed.'}});}
    await route.fallback();
  });
  await page.getByRole('button',{name:'下一個觀念：使用者端',exact:true}).click();
  await expect(page.getByRole('button',{name:'完成本次學習',exact:true})).toBeVisible();
  await expect(page).toHaveURL(before);expect(applies).toBe(1);
  await page.getByRole('button',{name:'完成本次學習',exact:true}).click();
  await expect(page.getByRole('heading',{name:'學習已完成',exact:true})).toBeVisible();
  expect(revisions[1]).toBe('learner-guidance:sha256:'+'2'.padStart(64,'0'));
});


test('incomplete cycle follows the backend next action without claiming a pass',async({page})=>{
  const f=await studyLayoutFixture(page,'submitted',{navigation:true,wrong:[],unavailable:1});await f.open();
  await expect(page.getByText('本輪有未作答或未檢測的重點。',{exact:true})).toBeVisible();
  await page.getByRole('button',{name:'下一個觀念：使用者端',exact:true}).click();
  await expect(page.getByRole('heading',{name:'使用者端',level:1,exact:true})).toBeVisible();
});
