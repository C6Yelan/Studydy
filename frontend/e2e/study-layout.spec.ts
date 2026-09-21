import { expect, test, type Page } from "@playwright/test";
import { studyLayoutFixture } from "./fixtures/study-layout.mjs";

async function preparationLayout(page: Page, width: number) {
  await expect(page.locator('.study-learning-grid')).toHaveClass(/is-preparation-mode/);
  await expect(page.locator('.study-material-summary')).toHaveCount(0);
  const material = (await page.locator('.current-concept-card').boundingBox())!;
  const action = (await page.locator('.study-current-action').boundingBox())!;
  const rail = (await page.locator('.study-rail').boundingBox())!;
  if (width > 900) {
    expect(Math.abs(material.y - action.y)).toBeLessThan(1);
    expect(action.x).toBeGreaterThanOrEqual(material.x + material.width);
    expect(rail.x).toBeGreaterThanOrEqual(action.x + action.width);
    expect(rail.width).toBeGreaterThanOrEqual(300);
    expect(rail.width).toBeLessThanOrEqual(320);
  } else {
    expect(action.y).toBeGreaterThanOrEqual(material.y + material.height);
    expect(rail.y).toBeGreaterThanOrEqual(action.y + action.height);
  }
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
}

for (const viewport of [{width:1536,height:1024},{width:1366,height:768},{width:390,height:844}]) {
  test(`study preparation, six questions, results and history at ${viewport.width}px`, async ({page}, info) => {
    await page.setViewportSize(viewport);
    const fixture = await studyLayoutFixture(page);
    await fixture.open();
    const start = page.getByRole('button', {name:'開始本輪 6 題',exact:true});
    await expect(start).toBeEnabled();
    await preparationLayout(page, viewport.width);
    if (viewport.width > 900) await expect(start).toBeInViewport();
    await expect(page.locator('.assessment-set-history')).not.toHaveAttribute('open','');
    await expect(page.locator('.study-record-picker')).not.toHaveAttribute('open','');
    const sources = page.getByRole('region',{name:'教材來源',exact:true}).getByRole('button');
    await expect(sources).toHaveCount(1);
    await sources.click();
    const source = page.getByRole('dialog',{name:'教材來源',exact:true});
    await expect(source.getByRole('link',{name:'開啟 PDF 來源頁'})).toHaveAttribute('href', /#page=1$/);
    await page.keyboard.press('Escape');
    await expect(sources).toBeFocused();
    await page.screenshot({path:info.outputPath('preparation.png'),fullPage:true});
    await start.click();
    await expect(page.getByText('已備妥 2／6 題',{exact:true})).toBeVisible();
    await preparationLayout(page, viewport.width);
    fixture.setStage('ready');
    await expect(page.locator('.assessment-set-item')).toHaveCount(6);
    await expect(page.locator('.study-learning-grid')).toHaveClass(/is-question-mode/);
    await expect(page.locator('.current-concept-card')).toHaveCount(0);
    const main = (await page.locator('.study-main').boundingBox())!;
    const question = (await page.locator('.assessment-set-item').first().boundingBox())!;
    expect(Math.abs(main.width-question.width)).toBeLessThan(1);
    if (viewport.width > 900) expect(question.width).toBeGreaterThan(900);
    else {
      const options=await page.locator('.assessment-options').first().locator('label').evaluateAll(nodes=>nodes.map(n=>n.getBoundingClientRect().x));
      expect(new Set(options).size).toBe(1);
    }
    await page.reload();
    await expect(page.locator('.assessment-set-item')).toHaveCount(6);
    for(let i=0;i<6;i++) await page.locator('.assessment-set-item').nth(i).getByRole('radio').nth(i===5?1:0).check();
    await page.getByRole('button',{name:'交卷並查看結果',exact:true}).click();
    await expect(page.locator('.study-learning-grid')).toHaveClass(/is-result-mode/);
    await expect(page.locator('.study-material-summary')).not.toHaveAttribute('open','');
    await expect(page.locator('.current-concept-card')).not.toBeVisible();
    await expect(page.locator('.feedback-card')).toHaveCount(6);
    await expect(page.getByRole('article',{name:'待補強重點',exact:true})).toHaveCount(1);
    expect((await page.locator('.assessment-cycle').boundingBox())!.width).toBeCloseTo(main.width,0);
    await page.locator('.assessment-set-history summary').click();
    await page.locator('.assessment-set-history button').last().click();
    await expect(page).toHaveURL(new RegExp(fixture.historyPath.split('/').at(-1)!+'$'));
    await expect(page.locator('.study-learning-grid')).toHaveClass(/is-result-mode/);
    await page.locator('.study-record-picker summary').click();
    await page.locator('.study-history-row').click();
    await expect(page.getByRole('region',{name:'歷史作答',exact:true})).toBeVisible();
    expect(fixture.requests.filter(r=>r.path.endsWith('/assessment-sets'))).toHaveLength(1);
    expect(fixture.requests.filter(r=>r.path.endsWith('/submissions'))).toHaveLength(1);
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  });

  test(`study retry and partial publish keep preparation layout at ${viewport.width}px`, async ({page}) => {
    await page.setViewportSize(viewport);
    const fixture=await studyLayoutFixture(page,'partial_ready');
    await fixture.open();
    const publish=page.getByRole('button',{name:'先做已備妥的 4 題',exact:true});
    await expect(publish).toBeEnabled();
    await preparationLayout(page,viewport.width);
    await page.getByRole('button',{name:'重試未備妥的題目',exact:true}).click();
    await expect(page.getByText('已備妥 2／6 題',{exact:true})).toBeVisible();
    await preparationLayout(page,viewport.width);
    fixture.setStage('partial_ready');
    await expect(publish).toBeVisible();
    await publish.click();
    await expect(page.locator('.study-learning-grid')).toHaveClass(/is-question-mode/);
    await expect(page.locator('.assessment-set-item')).toHaveCount(4);
    expect(fixture.requests.filter(r=>r.path.endsWith('/retry'))).toHaveLength(1);
    expect(fixture.requests.filter(r=>r.path.endsWith('/publish-partial'))).toHaveLength(1);
  });
}

test('plan loading, generation failure and no-safe remain preparation',async({page})=>{
  await page.setViewportSize({width:1366,height:768});
  const fixture=await studyLayoutFixture(page,'failed');
  await fixture.open();
  await expect(page.getByRole('button',{name:'重試未備妥的題目',exact:true})).toBeVisible();
  await preparationLayout(page,1366);
  fixture.setStage('no-safe');
  let release!:()=>void;
  const gate=new Promise<void>(resolve=>{release=resolve;});
  await page.route('**/assessment-plan?*',async route=>{await gate;await route.fallback();});
  await fixture.open();
  await expect(page.getByText('正在讀取這個觀念的檢測範圍…',{exact:true})).toBeVisible();
  await preparationLayout(page,1366);
  release();
  await expect(page.getByRole('button',{name:'開始本輪 0 題',exact:true})).toBeDisabled();
  await preparationLayout(page,1366);
});
