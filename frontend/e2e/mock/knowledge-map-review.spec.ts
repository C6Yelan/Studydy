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
  state.concept_states[0].status='needs_review';state.concept_states[0].weak_claim_ids=[view.concepts[0].claims[0].claim_id];
  state.concept_states[1].status='completed';state.concept_states[1].completed_claim_ids=[view.concepts[1].claims[0].claim_id];
  await mockOwnedReview(page,view,()=>state);await page.goto(mapPath);await page.getByRole('tab',{name:'測驗',exact:true}).click();
  const section=page.getByRole('region',{name:'待完成的概念'}),pending=section.locator(':scope > .assessment-concept-list');
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
  state.concept_states=view.concepts.map(c=>({...progress.concept_states[0],concept_id:c.concept_id,label:c.label,status:completed?'completed':'in_progress',assessable_claim_ids:c.claims.map(a=>a.claim_id),completed_claim_ids:completed?c.claims.map(a=>a.claim_id):[]}));
  state.current_concept_id=view.concepts[0].concept_id;
  await mockOwnedReview(page,view,()=>state);await page.goto(mapPath);await page.getByRole('tab',{name:'測驗',exact:true}).click();
  const section=page.getByRole('region',{name:'待完成的概念'});
  if(completed){await expect(section).toContainText('所有可檢測的概念都已完成');await expect(section.getByRole('button',{name:'檢測這個概念',exact:true})).toHaveCount(0);}
  else {await expect(section.locator(':scope > ul li strong')).toHaveText(view.concepts.map(c=>c.label));await section.locator(':scope > ul li').last().scrollIntoViewIfNeeded();await expect(section.locator(':scope > ul li').last()).toBeInViewport();}
 });
}

test('zero assessable concept is not put into completed practice',async({page})=>{
 const state=structuredClone(progress);state.concept_states[0].assessable_claim_ids=[];
 await mockOwnedReview(page,structureView(),()=>state);await page.goto(mapPath);await page.getByRole('tab',{name:'測驗',exact:true}).click();
 const section=page.getByRole('region',{name:'待完成的概念'});await expect(section).toContainText('此概念目前無法提供可靠檢測');await expect(section.locator('details')).toHaveCount(0);
});

test('loading progress does not show a false completed or empty list',async({page})=>{
 await mockOwnedReview(page,structureView(),()=>progress);let release!:()=>void;const gate=new Promise<void>(r=>{release=r;});let reads=0;
 await page.route(`**${progressPath}`,async route=>{reads++;await gate;await json(route,progress);});
 await page.goto(mapPath);const tab=page.getByRole('tab',{name:'測驗',exact:true});
 await expect.poll(()=>reads).toBe(1);await expect(tab).toBeDisabled();
 await expect(page.getByText('所有可檢測的概念都已完成。',{exact:true})).toHaveCount(0);
 release();await tab.click();await expect(page.getByRole('region',{name:'待完成的概念'}).getByRole('button',{name:'檢測這個概念',exact:true})).toHaveCount(2);
});
