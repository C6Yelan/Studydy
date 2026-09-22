import { expect, test } from '@playwright/test';
import { studyLayoutFixture } from './fixtures/study-layout.mjs';

for(const width of [1536,1366,390]) {
  for(const [name,stage,scenario] of [
    ['entry','preparation',null],
    ['wrong','submitted',{}],
    ['remediation','ready',{kind:'remediation'}],
    ['passed','submitted',{kind:'remediation',wrong:[],navigation:true}],
  ] as const) test(`history-only rail ${name} at ${width}px`,async({page})=>{
    await page.setViewportSize({width,height:width===390?844:1024});
    const f=await studyLayoutFixture(page,stage,scenario);await f.open();
    await expect(page.locator('.learning-insights,.learning-status,.mastery-explanation,.cycle-insight')).toHaveCount(0);
    const rail=page.getByRole('complementary',{name:'學習紀錄'});
    await expect(rail).toBeVisible();
    await expect(rail.locator(':scope > *')).toHaveCount(1);
    await expect(rail.locator('.assessment-set-history')).toHaveAttribute('open','');
    await expect(rail).not.toContainText(/學習中|尚未開始|已掌握|已練習|本輪檢測通過|作答 \d+ 次|答對 \d+ 次/);
    if(name==='wrong') {
      await expect(page.locator('.assessment-set-summary')).toContainText('答對2 / 4 題');
      await expect(page.locator('.assessment-set-summary')).toContainText('待補強2 個重點');
    }
    if(name==='passed')await expect(page.getByRole('button',{name:'下一個觀念：使用者端',exact:true})).toBeVisible();
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
    await rail.locator('summary').click();
    await expect(rail.locator('.assessment-set-history')).not.toHaveAttribute('open','');
    await rail.locator('summary').click();
    await rail.getByRole('button').last().click();
    await expect(page.locator('.assessment-cycle')).toBeVisible();
    await page.locator('.assessment-answer-review > summary').click();
    await expect(page.locator('.feedback-card').first()).toBeVisible();
    await expect(page.locator('.learning-insights')).toHaveCount(0);
  });

  test(`empty history removes rail and frees main width at ${width}px`,async({page},info)=>{
    await page.setViewportSize({width,height:width===390?844:1024});
    const f=await studyLayoutFixture(page,'preparation',{noHistory:true});await f.open();
    await expect(page.locator('.study-rail')).toHaveCount(0);
    const workspace=(await page.locator('.study-workspace').boundingBox())!,main=(await page.locator('.study-main').boundingBox())!;
    expect(main.width).toBeCloseTo(workspace.width,0);
    await expect(page.getByRole('button',{name:'開始本輪 6 題',exact:true})).toBeVisible();
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
    await page.screenshot({path:info.outputPath('without-rail.png'),fullPage:true});
  });
}
