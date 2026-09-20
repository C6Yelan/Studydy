import { expect, test, type Page } from "@playwright/test";

test.skip(process.env.STUDYDY_E2E_ASSESSMENT_SET !== "true", "Requires isolated assessment set API/DB fixture");
const data = JSON.parse(process.env.STUDYDY_E2E_SET_DATA ?? "{}");
const origin = process.env.STUDYDY_E2E_BASE_URL ?? "http://127.0.0.1:4173";
const studyPath = `/materials/${data.material}/runs/${data.run}/knowledge-structures/${encodeURIComponent(data.revision)}/study-sessions/${data.session}`;
async function login(page: Page) {
  await page.goto("/");
  await page.getByLabel("Email", { exact: true }).fill("learner_test@example.com");
  await page.getByLabel("密碼", { exact: true }).fill("Synthetic test password 42");
  await page.getByRole("button", { name: "登入", exact: true }).click();
  await expect(page.getByRole("heading", { name: "歡迎回來！", level: 1, exact: true })).toBeVisible();
}
const card = (page: Page, n: number) => page.getByRole("article", { name: `第 ${n} 題`, exact: true });

test("whole paper submits once, restores all results and keeps aligned cards", async ({ page, browser }, info) => {
  await page.setViewportSize({ width: data.width, height: 900 });
  const errors: string[] = []; page.on("pageerror", e => errors.push(e.message));
  await login(page); await page.goto(studyPath);
  await page.getByRole("button", { name: "開始本輪 3 題", exact: true }).click();
  await expect(page).toHaveURL(/assessment-sets\/[0-9a-f-]+$/);
  const roundPath = new URL(page.url()).pathname, setId = roundPath.split("/").at(-1)!;
  await expect(page.getByText("已備妥 0／3 題", { exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByText("已備妥 0／3 題", { exact: true })).toBeVisible();
  await page.request.post("/v1/__test/sets/release", { headers: { Origin: origin } });
  await expect(page.locator(".assessment-set-item")).toHaveCount(3);
  const endpoint=`/v1/study-sessions/${data.session}/assessment-sets/${setId}`;
  const snapshot=await (await page.request.get(endpoint)).json();
  expect(snapshot.target_concept_id).toBe(data.concept);
  expect(JSON.stringify(snapshot)).not.toMatch(/"correct_option_id"|"prepared_document"|"private_answer_document"|"generation_provenance"/);
  await expect(page.getByRole("button", { name: "送出答案", exact: true })).toHaveCount(0);
  await expect(page.locator(".assessment-cycle,.feedback-card")).toHaveCount(0);
  const submit=page.getByRole("button", { name: "交卷並查看結果", exact: true });
  await expect(submit).toBeDisabled();
  await card(page,1).getByRole("radio", { name: /\bcode0\b/ }).check();
  await card(page,2).getByRole("radio", { name: /\bcode1\b/ }).check();
  await expect(submit).toBeDisabled();
  await expect(card(page,1).getByRole("radio", { name: /\bcode0\b/ })).toBeChecked();
  expect((await (await page.request.get(endpoint)).json()).answered_count).toBe(0);
  await page.reload();
  await expect(page.locator(".assessment-set-item")).toHaveCount(3);
  await expect(page.locator('.assessment-paper input:checked')).toHaveCount(0);
  const restored=await (await page.request.get(endpoint)).json();
  expect(restored.items.map((i: {assessment: unknown})=>i.assessment)).toEqual(snapshot.items.map((i: {assessment: unknown})=>i.assessment));
  await card(page,1).getByRole("radio", { name: /wrong0a/ }).check();
  await card(page,1).getByRole("radio", { name: /\bcode0\b/ }).check();
  await card(page,2).getByRole("radio", { name: /\bcode1\b/ }).check();
  await card(page,3).getByRole("radio", { name: /wrong2a/ }).check();
  await expect(submit).toBeEnabled();
  const bounds=await page.locator('.assessment-set-item').evaluateAll(elements=>elements.map(e=>{const b=e.getBoundingClientRect();return {x:b.x,y:b.y,width:b.width,bottom:b.bottom};}));
  expect(Math.max(...bounds.map(b=>b.width))-Math.min(...bounds.map(b=>b.width))).toBeLessThan(1);
  for(let i=1;i<bounds.length;i++){expect(Math.abs(bounds[i].x-bounds[0].x)).toBeLessThan(1);expect(bounds[i].y-bounds[i-1].bottom).toBeGreaterThanOrEqual(16);}
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  if (data.width >= 1280) await expect(page.locator('.study-rail')).toBeInViewport();
  await page.evaluate(()=>window.scrollTo(0,0));
  await page.screenshot({ path: `/tmp/studydy-b05-batch-ui/${data.width}-paper.png`, fullPage: true });
  let posts=0, release!:()=>void;
  const submissions: { version:number; key:string }[]=[];
  const conflict = data.width === 1536;
  const gate=new Promise<void>(resolve=>{release=resolve;});
  await page.route('**/assessment-sets/*/submissions',async route=>{
    posts++;const body=route.request().postDataJSON();expect(body.answers).toHaveLength(3);
    submissions.push({version:body.expected_set_version,key:route.request().headers()['idempotency-key']});
    await gate;
    if(conflict && posts===1){
      const changed=await page.request.post(`/v1/__test/sets/${setId}/version`,{headers:{Origin:origin}});expect(changed.ok()).toBe(true);
      await route.continue();return;
    }
    const response=await route.fetch();expect(response.status()).toBe(200);await route.abort('failed');
  });
  await submit.click();
  await expect(page.getByRole('button',{name:'正在交卷…',exact:true})).toBeDisabled();
  await expect(card(page,1).getByRole('radio').first()).toBeDisabled();
  await expect(page.locator('.feedback-card')).toHaveCount(0);
  release();
  if(conflict){
    await expect(page.getByText('題組已同步，答案選取已保留。確認後可再次交卷。',{exact:true})).toBeVisible();
    await expect(card(page,1).getByRole('radio',{name:/\bcode0\b/})).toBeChecked();
    await expect(card(page,1).getByRole('radio',{name:/\bcode0\b/})).toBeEnabled();
    await page.getByRole('button',{name:'重新交卷',exact:true}).click();
    await expect.poll(()=>submissions.length).toBe(2);
    expect(submissions[1].version).toBeGreaterThan(submissions[0].version);
    expect(submissions[1].key).not.toBe(submissions[0].key);
  }
  await page.getByRole('button',{name:'查回交卷結果',exact:true}).click();
  await expect(page.locator('.feedback-card')).toHaveCount(3);
  await expect(page.getByRole('button',{name:'交卷並查看結果',exact:true})).toHaveCount(0);
  await expect(page.getByRole('button',{name:'結束初篩，查看結果',exact:true})).toHaveCount(0);
  const done=await (await page.request.get(endpoint)).json();
  expect(done.status).toBe('completed');expect(done.answered_count).toBe(3);expect(done.passed_count).toBe(2);expect(posts).toBe(conflict ? 2 : 1);
  await page.evaluate(()=>window.scrollTo(0,0));
  await page.screenshot({path:`/tmp/studydy-b05-batch-ui/${data.width}-results.png`,fullPage:true});
  expect(errors).toEqual([]);
  const context=await browser.newContext({viewport:{width:data.width,height:900}});const fresh=await context.newPage();
  await login(fresh);await fresh.goto(roundPath);
  await expect(fresh.locator('.feedback-card')).toHaveCount(3);
  expect((await (await fresh.request.get(endpoint)).json()).items.map((i:{feedback:{answer_event_id:string}})=>i.feedback.answer_event_id))
    .toEqual(done.items.map((i:{feedback:{answer_event_id:string}})=>i.feedback.answer_event_id));
  const progress=await (await fresh.request.get(`/v1/study-sessions/${data.session}/progress`)).json();
  expect(progress.concept_states.find((i:{concept_id:string})=>i.concept_id===data.concept).status).not.toBe('mastered');
  await context.close();
});
