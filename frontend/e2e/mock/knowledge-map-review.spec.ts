import {expect,test,type Page} from '@playwright/test';
import {materialId,runId,sessionId,artifactId,structureRevision,structureView,run,session,progress,json,mockKnowledgeMapApi,workspaceView} from '../fixtures/knowledge-map';
const mapPath=`/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}`;
const progressPath=`/v1/study-sessions/${sessionId}/progress`;
async function mockOwnedReview(
  page: Page,
  view: ReturnType<typeof structureView>,
  readProgress: () => typeof progress,
) {
  await mockKnowledgeMapApi(page, view, readProgress);
  await page.route(`**/v1/materials/${materialId}`, (route) => {
    expect(route.request().method()).toBe("GET");
    return json(route, {
      schema: "material-library-item/v1",
      material_id: materialId,
      source_artifact_id: artifactId,
      display_name: "Data structures.pdf",
      size_bytes: 100,
      created_at: run.created_at,
      latest_attempt: run,
      available_structures: [
        {
          run_id: runId,
          knowledge_structure_revision: structureRevision,
          created_at: run.created_at,
          status: "succeeded",
        },
      ],
      study_sessions: [
        { ...session(), run_id: runId, current_concept_id: readProgress().current_concept_id },
      ],
    });
  });
}


for (const width of [1536,1100,390]) {
 test(`assessment list separates unfinished concepts and optional practice at ${width}px`,async({page})=>{
  await page.setViewportSize({width,height:900});const view=structureView(),state=structuredClone(progress);
  state.concept_states.forEach(s => s.attempts = 1);
  state.concept_states[0].status='needs_review';state.concept_states[0].weak_claim_ids=[view.concepts[0].claims[0].claim_id];
  state.concept_states[1].status='completed';state.concept_states[1].completed_claim_ids=[view.concepts[1].claims[0].claim_id];
  await mockOwnedReview(page,view,()=>state);await page.goto(mapPath);await page.getByRole('tab',{name:'測驗',exact:true}).click();
  const section=page.getByRole('region',{name:'測驗紀錄'}),pending=section.locator(':scope > .assessment-concept-list');
  await expect(pending.locator('li')).toHaveCount(1);await expect(pending).toContainText('Stack');await expect(pending).toContainText('需要加強');await expect(pending).not.toContainText('Array');
  await expect(section.getByRole('button',{name:'再次練習',exact:true})).not.toBeVisible();
  await section.locator('summary').click();await expect(section.getByRole('button',{name:'再次練習',exact:true})).toBeVisible();await expect(section.locator('details')).toContainText('Array');
  await expect(section).not.toContainText(/mastered|assisted|本輪檢測通過|掌握度/);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  await page.reload();await expect(page.getByRole('tab',{name:'測驗',exact:true})).toHaveAttribute('aria-selected','true');
 });
}

for (const completed of [0,32]) {
 test(`assessment list preserves ${32-completed} unfinished memberships`,async({page})=>{
  const view=workspaceView(32,true),state=structuredClone(progress);
  state.concept_states=view.concepts.map(c=>({...progress.concept_states[0],concept_id:c.concept_id,label:c.label,attempts:1,status:completed?'completed':'in_progress',assessable_claim_ids:c.claims.map(a=>a.claim_id),completed_claim_ids:completed?c.claims.map(a=>a.claim_id):[]}));
  state.current_concept_id=view.concepts[0].concept_id;
  await mockOwnedReview(page,view,()=>state);await page.goto(mapPath);await page.getByRole('tab',{name:'測驗',exact:true}).click();
  const section=page.getByRole('region',{name:'測驗紀錄'});
  if(completed){await expect(section).toContainText('已有作答紀錄的概念都已完成');await expect(section.getByRole('button',{name:'繼續測驗',exact:true})).toHaveCount(0);}
  else {await expect(section.locator(':scope > ul li strong')).toHaveText(view.concepts.map(c=>c.label));await section.locator(':scope > ul li').last().scrollIntoViewIfNeeded();await expect(section.locator(':scope > ul li').last()).toBeInViewport();}
 });
}

test('zero assessable concept is not put into completed practice',async({page})=>{
 const state=structuredClone(progress);state.concept_states[0].assessable_claim_ids=[];
 await mockOwnedReview(page,structureView(),()=>state);await page.goto(mapPath);await page.getByRole('tab',{name:'測驗',exact:true}).click();
 const section=page.getByRole('region',{name:'測驗紀錄'});await expect(section).toContainText('目前還沒有測驗紀錄');await expect(section.locator('li')).toHaveCount(0);await expect(section.locator('details')).toHaveCount(0);
});

test('loading progress does not show a false completed or empty list',async({page})=>{
 await mockOwnedReview(page,structureView(),()=>progress);let release!:()=>void;const gate=new Promise<void>(r=>{release=r;});let reads=0;
 await page.route(`**${progressPath}`,async route=>{reads++;await gate;await json(route,progress);});
 await page.goto(mapPath);const tab=page.getByRole('tab',{name:'測驗',exact:true});
 await expect.poll(()=>reads).toBe(1);await expect(tab).toBeDisabled();
 await expect(page.getByText('已有作答紀錄的概念都已完成。',{exact:true})).toHaveCount(0);
 release();await tab.click();await expect(page.getByRole('region',{name:'測驗紀錄'}).getByRole('button',{name:'繼續測驗',exact:true})).toHaveCount(0);
});

for (const status of ["in_progress", "needs_review", "completed"]) {
  test(`only submitted concept is listed using backend ${status}, including reload`, async ({ page }) => {
    const state = structuredClone(progress);
    Object.assign(state.concept_states[0], {
      attempts: 3, correct_answers: 1, status,
      latest_is_correct: status === "completed" ? false : true,
    });
    await mockOwnedReview(page, structureView(), () => state);
    await page.goto(mapPath);
    await page.getByRole("tab", { name: "測驗", exact: true }).click();
    const section = page.getByRole("region", { name: "測驗紀錄" });
    for (const reload of [false, true]) {
      if (reload) await page.reload();
      await expect(section.locator("li")).toHaveCount(1);
      await expect(section).not.toContainText("Array");
      await expect(section).not.toContainText("尚未開始");
      if (status === "completed") {
        await section.locator("summary").click();
        await expect(section.locator("li")).toContainText("已完成");
        await expect(section.getByRole("button", { name: "再次練習", exact: true })).toBeVisible();
        await expect(section.getByRole("button", { name: "繼續測驗", exact: true })).toHaveCount(0);
      } else {
        await expect(section).toContainText(status === "needs_review" ? "需要加強" : "進行中");
        await expect(section.getByRole("button", { name: "繼續測驗", exact: true })).toBeVisible();
      }
    }
  });
}

test("no saved session has an empty record list and a map entry", async ({ page }) => {
  await mockKnowledgeMapApi(page);
  await page.goto(mapPath);
  await page.getByRole("tab", { name: "測驗", exact: true }).click();
  const section = page.getByRole("region", { name: "測驗紀錄" });
  await expect(section).toContainText("目前還沒有測驗紀錄，可以從概念地圖選擇概念開始學習。");
  await expect(section.locator("li")).toHaveCount(0);
  await section.getByRole("button", { name: "返回概念地圖" }).click();
  await expect(page.getByRole("tab", { name: "概念地圖", exact: true })).toHaveAttribute("aria-selected", "true");
});

test("switching materials never reuses the previous material's answers", async ({ page }) => {
  const state = structuredClone(progress);
  Object.assign(state.concept_states[0], { attempts: 1, status: "in_progress" });
  await mockOwnedReview(page, structureView(), () => state);
  const otherId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
  const otherRunId = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
  const otherRun = { ...run, material_id: otherId, run_id: otherRunId };
  const otherView = structureView();
  otherView.source_resolver = otherView.source_resolver.replace(materialId, otherId);
  await page.route(`**/v1/materials/${otherId}`, route => json(route, {
    schema: "material-library-item/v1", material_id: otherId, source_artifact_id: artifactId,
    display_name: "Other.pdf", size_bytes: 100, created_at: run.created_at,
    latest_attempt: otherRun,
    available_structures: [{ run_id: otherRunId, knowledge_structure_revision: structureRevision,
      created_at: run.created_at, status: "succeeded" }],
    study_sessions: [],
  }));
  await page.route(`**/v1/material-processing-runs/${otherRunId}`, route => json(route, otherRun));
  await page.route(url => decodeURIComponent(url.pathname) === `/v1/materials/${otherId}/knowledge-structures/${structureRevision}`,
    route => json(route, otherView));
  await page.goto(mapPath);
  await page.getByRole("tab", { name: "測驗", exact: true }).click();
  await expect(page.locator(".assessment-concept-list li")).toHaveCount(1);
  await page.evaluate(path => {
    window.history.pushState(null, "", path);
    window.dispatchEvent(new PopStateEvent("popstate"));
  }, mapPath.replace(materialId, otherId).replace(runId, otherRunId));
  await page.getByRole("tab", { name: "測驗", exact: true }).click();
  await expect(page.getByRole("region", { name: "測驗紀錄" })).toContainText("目前還沒有測驗紀錄");
  await expect(page.locator(".assessment-concept-list li")).toHaveCount(0);
});

test("a different login does not restore the previous user's record list", async ({ page }) => {
  const state = structuredClone(progress);
  Object.assign(state.concept_states[0], { attempts: 1, status: "in_progress" });
  await mockOwnedReview(page, structureView(), () => state);
  await page.goto(mapPath);
  await page.getByRole("tab", { name: "測驗", exact: true }).click();
  await expect(page.locator(".assessment-concept-list li")).toHaveCount(1);
  await page.getByRole("button", { name: "登出", exact: true }).click();
  await expect(page.locator("form.auth-form")).toBeVisible();
  await mockKnowledgeMapApi(page);
  const identity = { schema: "learner-identity/v1", learner_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa" };
  await page.route("**/v1/session/login", route => json(route, identity));
  await page.route("**/v1/session/refresh", route => json(route, identity));
  await page.route("**/v1/materials", route => json(route, { schema: "material-library/v1", materials: [] }));
  await page.getByLabel("Email", { exact: true }).fill("other@example.com");
  await page.getByLabel("密碼", { exact: true }).fill("Synthetic password 42");
  await page.getByRole("button", { name: "登入", exact: true }).click();
  await expect(page.locator("form.auth-form")).toHaveCount(0);
  await page.evaluate(path => {
    window.history.pushState(null, "", path);
    window.dispatchEvent(new PopStateEvent("popstate"));
  }, mapPath);
  await page.getByRole("tab", { name: "測驗", exact: true }).click();
  await expect(page.getByRole("region", { name: "測驗紀錄" })).toContainText("目前還沒有測驗紀錄");
  await expect(page.locator(".assessment-concept-list li")).toHaveCount(0);
  await page.reload();
  await expect(page.getByRole("region", { name: "測驗紀錄" })).toContainText("目前還沒有測驗紀錄");
});
