import { expect, test, type Page } from "@playwright/test";

test.skip(process.env.STUDYDY_E2E_REMEDIATION !== "true", "Requires isolated remediation API/DB fixture");
const data = JSON.parse(process.env.STUDYDY_E2E_REMEDIATION_DATA ?? "{}");
const study = `/materials/${data.material}/runs/${data.run}/knowledge-structures/${encodeURIComponent(data.revision)}/study-sessions/${data.session}`;
async function login(page: Page) {
  await page.goto("/");
  await page.getByLabel("Email", { exact: true }).fill("learner_test@example.com");
  await page.getByLabel("密碼", { exact: true }).fill("Synthetic test password 42");
  await page.getByRole("button", { name: "登入", exact: true }).click();
  await expect(page.getByRole("heading", { name: "歡迎回來！", exact: true, level: 1 })).toBeVisible();
}
const card = (page: Page, n: number) => page.getByRole("article", { name: `第 ${n} 題`, exact: true });

test("wrong points directly form groups and survive lost create responses", async ({ page, browser }, info) => {
  await page.setViewportSize({ width: data.width, height: 900 });
  const errors: string[] = []; page.on("pageerror", e => errors.push(e.message));
  await login(page); await page.goto(study);
  await page.getByRole("button", { name: "開始本輪 3 題", exact: true }).click();
  await expect(page.locator(".assessment-set-item")).toHaveCount(3);
  const rootUrl = page.url();
  for (let n=1; n<=3; n++) {
    await card(page,n).getByRole("radio", { name: n===1 ? /\bcode0\b/ : new RegExp(`wrong${n-1}a`) }).check();

  }
  await page.getByRole("button", { name: "交卷並查看結果", exact: true }).click();
  const reviews = page.getByRole("article", { name: "待補強重點", exact: true });
  await expect(reviews).toHaveCount(2);
  await expect(reviews.nth(0)).toContainText("Signal 1 uses code1.");
  await expect(reviews.nth(1)).toContainText("Signal 2 uses code2.");
  await expect(reviews.first().getByRole("button", { name: /PDF 第 1 頁/ })).toBeVisible();
  await expect(page.getByRole('button',{name:/已複習此重點|這個重點稍後處理|重新檢測整個觀念|結束本輪|取消本組測驗/})).toHaveCount(0);
  await expect(page.getByText('其他檢測操作',{exact:true})).toHaveCount(0);
  await expect(page.getByRole('button',{name:'開始補強 2 題',exact:true})).toBeVisible();
  await page.reload();
  await expect(page.getByRole('button',{name:'開始補強 2 題',exact:true})).toBeVisible();
  await page.screenshot({path:`/tmp/studydy-b05r-direct/${data.width}-result.png`,fullPage:true});
  let lostCreate=false;
  await page.route("**/assessment-sets/*/remediation", async route => {
    if (lostCreate) { await route.continue(); return; }
    lostCreate=true; const response=await route.fetch(); expect(response.status()).toBe(202); await route.abort();
  });
  await page.getByRole("button", { name: "開始補強 2 題", exact: true }).click();
  await expect(page.getByRole("button", { name: "重新讀取題組", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "重新讀取題組", exact: true }).click();
  await page.getByRole("button", { name: "接續補強", exact: true }).click();
  await expect(page.locator(".assessment-set-item")).toHaveCount(2);
  let childUrl=page.url(); expect(childUrl).not.toBe(rootUrl);
  await expect(page.getByText("錯題重點補強", { exact: true })).toBeVisible();
  await card(page,1).getByRole("radio", { name: /\bcode1\b/ }).check();
  await card(page,2).getByRole("radio", { name: /wrong2a/ }).check();
  await expect(card(page,1).getByRole("radio", { name: /\bcode1\b/ })).toBeChecked();
  await expect(card(page,2).getByRole("radio", { name: /wrong2a/ })).toBeChecked();
  await page.getByRole("button", { name: "交卷並查看結果", exact: true }).click();
  await expect(page.getByRole('button',{name:'開始補強 1 題',exact:true})).toBeVisible();
  await page.reload();
  await expect(page.getByRole('button',{name:'開始補強 1 題',exact:true})).toBeVisible();
  await page.getByRole('button',{name:'開始補強 1 題',exact:true}).click();
  await expect(page.locator('.assessment-set-item')).toHaveCount(1);
  childUrl=page.url();
  await card(page,1).getByRole('radio',{name:/\bcode2\b/}).check();
  await page.getByRole('button',{name:'交卷並查看結果',exact:true}).click();
  await expect(page.getByText("本輪檢測通過，僅代表這次檢測範圍的結果。", { exact: true })).toBeVisible();
  await expect(page.locator(".learning-insights")).toHaveCount(0);
  await expect(page.locator(".assessment-set-history")).toContainText("補強");
  await expect(page.locator(".assessment-set-history")).toContainText("初篩");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);

  await page.evaluate(()=>window.scrollTo(0,0));
  await page.screenshot({ path: `/tmp/studydy-b05r-direct/${data.width}-passed.png`, fullPage: true });
  const context=await browser.newContext({ viewport: { width: data.width, height: 900 } }); const fresh=await context.newPage();
  await login(fresh); await fresh.goto(childUrl);
  await expect(fresh.getByText("本輪檢測通過，僅代表這次檢測範圍的結果。", { exact: true })).toBeVisible();
  const progress=await (await fresh.request.get(`/v1/study-sessions/${data.session}/progress`)).json();
  expect(progress.assessment_cycles[0].outcome).toBe("passed"); expect(progress.event_watermark).toBe(6);
  expect(progress.concept_states.find((c: {concept_id: string})=>c.concept_id===data.concept).qualified_correct_items).toBe(1);
  await expect(fresh.getByRole('button',{name:'查看原始初篩題組',exact:true})).toHaveCount(0);
  await fresh.locator('.assessment-set-history').getByRole('button').filter({hasText:'初篩'}).click();
  await expect(fresh).toHaveURL(rootUrl); await expect(fresh.locator(".assessment-set-item")).toHaveCount(3);
  await fresh.getByRole('button',{name:'下一個觀念：Other topic',exact:true}).click();
  await expect(fresh).toHaveURL(study);
  await expect(fresh.getByRole('heading',{name:'Other topic',level:1,exact:true})).toBeVisible();
  await expect(fresh.getByRole('button',{name:'開始本輪 1 題',exact:true})).toBeVisible();
  expect(errors).toEqual([]); await context.close();
});
