import {expect,test,type Page} from '@playwright/test';
import { diagnosisView, difficultyView } from "../fixtures/learning-diagnosis";
import { outlineView } from '../fixtures/knowledge-read-views.mjs';
import {materialId,runId,sessionId,artifactId,structureRevision,structureView,run,session,progress,json,mockKnowledgeMapApi,workspaceView} from '../fixtures/knowledge-map';
const mapPath=`/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}`;
const progressPath=`/v1/study-sessions/${sessionId}/map-progress`;
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


for (const width of [1440, 1300, 1200, 1024, 390]) {
 test(`next step and open records at ${width}px`,async({page}, info)=>{
  await page.setViewportSize({width,height:900}); const view=structureView(), state=structuredClone(progress);
  state.concept_states.forEach(s => s.attempts = 1);
  state.concept_states[0].status='needs_review'; state.concept_states[1].status='completed';
  await mockOwnedReview(page,view,()=>state); await page.goto(mapPath); await page.getByRole('tab',{name:'學習與測驗',exact:true}).click();
  const next = page.getByRole('region',{name:'學習進度'});
  await expect(next).toContainText('已完成 1／2 個概念');
  await expect(next.getByRole('button',{name:'繼續學習',exact:true})).toBeInViewport();
  const diagnosis = page.getByRole('region',{name:'測驗紀錄'});
  await expect(diagnosis.locator('.diagnosis-point-list > li')).toHaveCount(1);
  await expect(diagnosis.locator('.diagnosis-point-list > li')).toContainText('Stack');
  const records=page.locator('.learning-diagnosis');
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  await page.screenshot({path:info.outputPath(`review-${width}.png`),fullPage:true});
  await records.getByRole('button', {name:/^全部 /}).click();
  await expect(records.locator('li')).toHaveCount(2);
  await expect(records.locator('li').last()).toContainText('已完成');
  await page.reload(); await expect(page.getByRole('tab',{name:'學習與測驗',exact:true})).toHaveAttribute('aria-selected','true');
 });
}

for (const completed of [0,32]) {
 test(`assessment records preserve ${32-completed} unfinished memberships`,async({page})=>{
  const view=workspaceView(32,true),state=structuredClone(progress);
  state.concept_states=view.concepts.map(c=>({...progress.concept_states[0],concept_id:c.concept_id,label:c.label,attempts:1,status:completed?'completed':'in_progress',assessable_claim_ids:c.claims.map(a=>a.claim_id),completed_claim_ids:completed?c.claims.map(a=>a.claim_id):[]}));
  state.current_concept_id=view.concepts[0].concept_id;
  await mockOwnedReview(page,view,()=>state); await page.goto(mapPath); await page.getByRole('tab',{name:'學習與測驗',exact:true}).click();
  const records=page.locator('.learning-diagnosis'); await records.getByRole('button', {name:/^全部 /}).click();
  await expect(records.locator('li strong')).toHaveText(view.concepts.map(c=>c.label));
  await records.locator('li').last().scrollIntoViewIfNeeded(); await expect(records.locator('li').last()).toBeInViewport();
  await records.getByRole('button', {name:/^待加強 /}).click(); await expect(records.locator('li')).toHaveCount(completed?0:32);
 });
}

test('zero assessable concept has no submitted record',async({page})=>{
 const state=structuredClone(progress);state.concept_states[0].assessable_claim_ids=[];
 await mockOwnedReview(page,structureView(),()=>state);await page.goto(mapPath);await page.getByRole('tab',{name:'學習與測驗',exact:true}).click();
 const section=page.locator('.learning-diagnosis'); await section.getByRole('button', {name:/^全部 /}).click(); await expect(section).toContainText('還沒有測驗紀錄'); await expect(section.locator('li')).toHaveCount(0);
});

test('loading progress does not show a false completed or empty list',async({page})=>{
 await mockOwnedReview(page,structureView(),()=>progress);let release!:()=>void;const gate=new Promise<void>(r=>{release=r;});let reads=0;
 await page.route(`**${progressPath}`,async route=>{reads++;await gate;await json(route,progress);});
 await page.goto(mapPath);const tab=page.getByRole('tab',{name:'學習與測驗',exact:true});
 await expect.poll(()=>reads).toBe(1);await expect(tab).toBeDisabled();
 await expect(page.getByText('已有作答紀錄的概念都已完成。',{exact:true})).toHaveCount(0);
 release();await tab.click();await expect(page.locator('.learning-diagnosis').getByRole('button',{name:'繼續測驗',exact:true})).toHaveCount(0);
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
    await page.getByRole("tab", { name: "學習與測驗", exact: true }).click();
    const section = page.locator(".learning-diagnosis");
    for (const reload of [false, true]) {
      if (reload) await page.reload();
      await expect(section.locator("li")).toHaveCount(1);
      await expect(section).not.toContainText("Array");
      await expect(section).not.toContainText("尚未開始");
      await section.getByRole("button", {name:/^全部 /}).click();
      await expect(section.locator("li")).toContainText(status === "completed" ? "已完成" : "尚未完成");
      await expect(section.getByRole("button", { name: "查看與練習", exact: true })).toBeVisible();
    }
  });
}

test("no saved session offers next step and an empty record list", async ({ page }) => {
  await mockKnowledgeMapApi(page); await page.goto(mapPath); await page.getByRole("tab", { name: "學習與測驗", exact: true }).click();
  await expect(page.getByRole("region", { name: "學習進度" }).getByRole("button", { name: "繼續學習", exact: true })).toBeVisible();
  const section = page.locator(".learning-diagnosis"); await section.getByRole("button", {name:/^全部 /}).click();
  await expect(section).toContainText("還沒有測驗紀錄"); await expect(section.locator("li")).toHaveCount(0);
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
  await page.route(url => decodeURIComponent(url.pathname) === `/v1/materials/${otherId}/knowledge-structures/${structureRevision}/outline`,
    route => json(route, outlineView(otherView, otherId, otherRunId)));
  await page.route(`**/v1/materials/${otherId}/knowledge-structures/*/learning-diagnosis`, route => json(route, diagnosisView(otherId, structureRevision)));
  await page.route(`**/v1/materials/${otherId}/knowledge-structures/*/concept-difficulty`, route => json(route, difficultyView(otherId, structureRevision)));
  await page.goto(mapPath);
  await page.getByRole("tab", { name: "學習與測驗", exact: true }).click();
  await expect(page.locator(".learning-diagnosis li")).toHaveCount(1);
  await page.evaluate(path => {
    window.history.pushState(null, "", path);
    window.dispatchEvent(new PopStateEvent("popstate"));
  }, mapPath.replace(materialId, otherId).replace(runId, otherRunId));
  await page.getByRole("tab", { name: "學習與測驗", exact: true }).click();
  await expect(page.locator(".learning-diagnosis")).toContainText("還沒有測驗紀錄");
  await expect(page.locator(".learning-diagnosis li")).toHaveCount(0);
});

test("a different login does not restore the previous user's record list", async ({ page }) => {
  const state = structuredClone(progress);
  Object.assign(state.concept_states[0], { attempts: 1, status: "in_progress" });
  await mockOwnedReview(page, structureView(), () => state);
  await page.goto(mapPath);
  await page.getByRole("tab", { name: "學習與測驗", exact: true }).click();
  await expect(page.locator(".learning-diagnosis li")).toHaveCount(1);
  // 另一分頁切換帳號時，立即清除私人內容。
  await page.evaluate(() => { const channel = new BroadcastChannel("studydy-account"); channel.postMessage("identity-changed"); channel.close(); });
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
  await page.getByRole("tab", { name: "學習與測驗", exact: true }).click();
  await expect(page.locator(".learning-diagnosis")).toContainText("還沒有測驗紀錄");
  await expect(page.locator(".learning-diagnosis li")).toHaveCount(0);
  await page.reload();
  await expect(page.locator(".learning-diagnosis")).toContainText("還沒有測驗紀錄");
});
