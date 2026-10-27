import { answerPaper } from "../fixtures/answer-paper";
import {expect,test} from "@playwright/test";
import {studyLayoutFixture} from "../fixtures/study-layout.mjs";

for(const kind of ["diagnostic","material_diagnostic"])test(`${kind} reports every twenty seconds and unlocks questions after submission`,async({page},testInfo)=>{
 await page.clock.install();
 const fixture=await studyLayoutFixture(page,"ready",{startingLevel:kind==="material_diagnostic"?"familiar":"some_knowledge",kind,count:2,navigation:true});
 await fixture.open();await expect.poll(()=>fixture.presence.length).toBe(1);
 expect(fixture.presence[0].body).toEqual({schema:"assessment-presence/v1",assessment_set_id:"00000000-0000-4000-8000-000000000005",active:true});
 expect(fixture.presence[0].browserId).toMatch(/^[0-9a-f-]{36}$/);
 await expect(page.getByRole("button",{name:"教材問答",exact:true})).toHaveCount(0);
 await page.clock.fastForward(20000);await expect.poll(()=>fixture.presence.filter(p=>p.body.active).length).toBe(2);
 for(const [width,height] of [[1440,900],[1300,900],[1200,900],[1024,768],[390,844]]){
  await page.setViewportSize({width,height});await page.screenshot({path:testInfo.outputPath(`presence-${kind}-${width}.png`)});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
 }
 await answerPaper(page, [0, 0]);
 await page.getByRole('button',{name:'交卷',exact:true}).click();
 await expect.poll(()=>fixture.presence.some(p=>p.body.active===false)).toBe(true);
 await expect(page.getByRole('button',{name:'教材問答',exact:true})).toBeVisible();
 const count=fixture.presence.length;await page.clock.fastForward(40000);expect(fixture.presence).toHaveLength(count);
});

test("leaving the answer page stops heartbeats without clearing another tab's guard",async({page})=>{
 await page.clock.install();const fixture=await studyLayoutFixture(page,"ready",{startingLevel:"some_knowledge",navigation:true});
 await fixture.open();await expect.poll(()=>fixture.presence.length).toBe(1);
 await page.goto(fixture.historyPath);await expect(page.locator('.assessment-cycle')).toBeVisible();
 const count=fixture.presence.length;await page.clock.fastForward(60000);expect(fixture.presence).toHaveLength(count);
 expect(fixture.presence.every(p=>p.body.active)).toBe(true);
});
