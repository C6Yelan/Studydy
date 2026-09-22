import { expect, test } from '@playwright/test';
import { studyLayoutFixture } from './fixtures/study-layout.mjs';

for (const width of [1536, 1366, 390]) {
  test(`result hierarchy and complete answer review at ${width}px`, async ({page}, info) => {
    await page.setViewportSize({width, height:width===390?844:1024});
    const fixture=await studyLayoutFixture(page,'submitted',{preparingHistory:true});
    await fixture.open();
    const summary=page.locator('.assessment-set-summary');
    await expect(summary).toHaveCount(1);
    await expect(summary).toContainText('答對2 / 4 題');
    await expect(summary).toContainText('待補強2 個重點');
    await expect(summary).toContainText('已完成4 / 4 題');
    await expect(summary).not.toContainText(/未作答|未檢測|其中補強通過/);
    await expect(page.locator('.cycle-insight')).toHaveCount(0);
    await expect(page.locator('.learning-insights, .mastery-explanation')).toHaveCount(0);
    await expect(page.locator('.study-rail')).not.toContainText(/已掌握|已練習|作答 \d+ 次/);
    const history=page.locator('.assessment-set-history');
    await expect(history).toHaveAttribute('open','');
    await expect(history.locator('summary')).toHaveText('觀念題組紀錄（2）');
    await expect(history).not.toContainText('0/0');
    const cards=page.locator('.assessment-review-point');
    await expect(cards).toHaveCount(2);
    const first=(await cards.nth(0).boundingBox())!, second=(await cards.nth(1).boundingBox())!;
    if(width>=1280){expect(first.y).toBeCloseTo(second.y,0);expect(second.x).toBeGreaterThan(first.x+first.width);}
    else expect(second.y).toBeGreaterThanOrEqual(first.y+first.height);
    const review=page.locator('.assessment-answer-review');
    await expect(review).not.toHaveAttribute('open','');
    await expect(review.locator('.feedback-card').first()).not.toBeVisible();
    await page.screenshot({path:info.outputPath('result.png'),fullPage:true});
    await review.locator('summary').click();
    await expect(review.locator('.feedback-card')).toHaveCount(4);
    for(const card of await review.locator('.feedback-card').all()) {
      await expect(card).toBeVisible();
      for(const label of ['題目','你的答案','為什麼？','教材依據']) await expect(card).toContainText(label);
    }
    await review.getByRole('button',{name:/PDF 第 1 頁/}).first().click();
    await expect(page.getByRole('dialog',{name:'教材來源'})).toBeVisible();
    await page.keyboard.press('Escape');
    await review.locator('summary').click();
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  });
}

for(const [name,scenario,expected] of [
  ['all correct',{wrong:[]},['答對4 / 4 題','已完成4 / 4 題']],
  ['unanswered',{wrong:[1],unanswered:1},['未作答1 個重點']],
  ['unavailable',{wrong:[1],unavailable:1},['未檢測1 個重點']],
  ['remediation',{kind:'remediation',wrong:[3]},['本次補強通過3 / 4 題','仍待補強1 個重點','已完成補強4 / 4 題']],
] as const) {
  test(`result metrics: ${name}`,async({page})=>{
    const fixture=await studyLayoutFixture(page,'submitted',scenario);await fixture.open();
    const summary=page.locator('.assessment-set-summary');await expect(summary).toHaveCount(1);
    for(const text of expected)await expect(summary).toContainText(text);
    if(name==='all correct'){
      await expect(summary.locator('span')).toHaveCount(2);
      await expect(page.locator('.assessment-review-section')).toHaveCount(0);
    }
    if(name!=='unanswered')await expect(summary).not.toContainText('未作答');
    if(name!=='unavailable')await expect(summary).not.toContainText('未檢測');
    await expect(summary).not.toContainText('其中補強通過');
  });
}

test('history excludes empty preparing sets and keeps published sets',async({page})=>{
  const fixture=await studyLayoutFixture(page,'preparing',{});
  await page.goto(fixture.historyPath);
  const history=page.locator('.assessment-set-history');
  await expect(history.locator('summary')).toHaveText('觀念題組紀錄（1）');
  await expect(history.getByRole('button')).toHaveCount(1);
  await expect(history).not.toContainText('0/0');
  await history.getByRole('button').click();
  await expect(page.locator('.assessment-cycle')).toBeVisible();
  fixture.setStage('ready');await page.goto(fixture.path);
  await expect(history.locator('summary')).toHaveText('觀念題組紀錄（2）');
  await expect(history).toContainText('尚未作答');
  await expect(history).not.toContainText('已答 0/4');
});
