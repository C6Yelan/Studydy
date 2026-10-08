import { expect, test, type Page } from "@playwright/test";
import type { StudyResumeView } from "../../src/api/contracts";
import { studyLayoutFixture } from "../fixtures/study-layout.mjs";
import {
  materialId,
  runId,
  sessionId,
  artifactId,
  structureRevision,
  firstConcept,
  secondConcept,
  structureView,
  run,
  session,
  progress,
  json,
  mockKnowledgeMapApi,
  openMapConcept,
} from "../fixtures/knowledge-map";

const mapPath = `/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}`;
type SavedSession = ReturnType<typeof session> & { run_id: string };

for (const stage of ["preparing", "ready", "failed", "no-safe", "remediation"] as const) {
  test(`map entry resumes existing ${stage} state without recreating assessment`, async ({ page }) => {
    const fixture = await studyLayoutFixture(page, stage === "remediation" ? "ready" : stage,
      { wrong: [], kind: stage === "remediation" ? "remediation" : "diagnostic" });
    let snapshot: StudyResumeView;
    page.on("response", async response => {
      if (response.url().includes("/resume?") && response.ok()) {
        // 刻意 reload 可能中止前一次 resume；保留最近完整收到的 fixture snapshot。
        try { snapshot = await response.json(); } catch { /* navigation cancelled this read */ }
      }
    });
    await fixture.open();
    await expect.poll(() => !!snapshot).toBe(true);
    const sid = snapshot!.session.study_session_id;
    const mid = snapshot!.session.material_id;
    const rid = snapshot!.run_id;
    const revision = snapshot!.knowledge_structure.knowledge_structure_revision;
    const published = { ...run, run_id: rid, material_id: mid,
      output_binding: { ...run.output_binding, knowledge_structure_revision: revision } };
    await page.route(`**/v1/materials/${mid}`, route => json(route, {
      ...materialWithHistory([]), material_id: mid, source_artifact_id: snapshot.source_artifact_id,
      head_revision: revision, latest_attempt: published,
      available_structures: [{ run_id: rid, knowledge_structure_revision: revision, created_at: run.created_at, status: "succeeded" }],
      study_sessions: [{ ...snapshot.session, run_id: rid }],
    }));
    await page.route(`**/v1/material-processing-runs/${rid}`, route => json(route, published));
    await page.route(url => decodeURIComponent(url.pathname) === `/v1/materials/${mid}/knowledge-structures/${revision}`,
      route => json(route, snapshot.knowledge_structure));
    await page.route(`**/v1/study-sessions/${sid}/progress`, route => json(route, snapshot.progress));
    const map = fixture.path.split("/study-sessions/")[0];
    await page.goto(map);
    await openMapConcept(page, "伺服器");
    await page.getByRole("button", { name: "檢測這個概念", exact: true }).click();
    await expect(page).toHaveURL(new RegExp(`/study-sessions/${sid}`));
    await page.reload();
    if (stage === "preparing") await expect(page.getByRole("progressbar", { name: "準備進度" })).toBeVisible();
    if (stage === "ready") await expect(page.locator(".assessment-set-item")).toHaveCount(4);
    if (stage === "remediation") {
      await expect(page.locator(".assessment-set-item")).toHaveCount(4);
      await expect(page.getByText("錯題重點補強", { exact: true })).toBeVisible();
    }
    if (stage === "failed") await expect(page.getByRole("button", { name: "再試一次", exact: true })).toBeEnabled();
    if (stage === "no-safe") await expect(page.getByText("此概念目前無法提供可靠檢測", { exact: true })).toBeVisible();
    expect(fixture.requests.filter((r: { path: string }) => /\/study-sessions/.test(r.path))).toEqual([]);
    if (stage === "ready") {
      for (const question of await page.locator(".assessment-set-item").all()) await question.getByRole("radio").first().check();
      await page.getByRole("button", { name: "交卷並查看結果", exact: true }).click();
      await expect(page.locator(".assessment-set-summary")).toContainText("答對4 / 4 題");
    }
    if (stage === "failed") {
      await page.getByRole("button", { name: "再試一次", exact: true }).click();
      await expect(page.getByRole("progressbar", { name: "準備進度" })).toBeVisible();
      expect(fixture.requests.filter((r: { path: string }) => r.path.endsWith("/retry"))).toHaveLength(1);
    }
    await page.getByRole("button", { name: "返回概念地圖", exact: true }).click();
    await expect(page.getByRole("dialog", { name: "概念詳情" }).getByRole("heading", { name: "伺服器", exact: true })).toBeVisible();
    if (stage === "ready") await expect(page.getByRole("dialog", { name: "概念詳情" })).toContainText("已完成");
  });
}

function materialWithHistory(studies: SavedSession[], publishedRun = run) {
  const revision = publishedRun.output_binding.knowledge_structure_revision;
  return {
    schema: "material-library-item/v1",
    material_id: materialId,
    source_artifact_id: artifactId,
    display_name: "Data structures.pdf",
    size_bytes: 100,
    created_at: publishedRun.created_at,
    latest_attempt: publishedRun,
    head_revision: revision,
    available_structures: [
      {
        run_id: publishedRun.run_id,
        knowledge_structure_revision: revision,
        created_at: publishedRun.created_at,
        status: "succeeded",
      },
    ],
    study_sessions: studies,
  };
}

function trackStudyWrites(page: Page) {
  const writes: { method: string; path: string; body: unknown }[] = [];
  page.on("request", (request) => {
    const path = new URL(request.url()).pathname;
    if (path.startsWith("/v1/study-sessions") && request.method() !== "GET")
      writes.push({ method: request.method(), path, body: request.postDataJSON() });
  });
  return writes;
}


for (const stage of ["preparation", "completed"] as const) {
 test(`direct assessment entry creates ${stage === "completed" ? "practice" : "first assessment"} without rereading`,async({page})=>{
  const fixture=await studyLayoutFixture(page,stage,{wrong:[]});
  let snapshot:StudyResumeView;
  page.on('response',async response=>{if(response.url().includes('/resume?')&&response.ok()){try{snapshot=await response.json();}catch{}}});
  await fixture.open();await expect.poll(()=>!!snapshot).toBe(true);
  const {session:sessionView,knowledge_structure:view}=snapshot!;
  const mid=sessionView.material_id,sid=sessionView.study_session_id,rid=snapshot!.run_id,revision=view.knowledge_structure_revision;
  const published={...run,run_id:rid,material_id:mid,output_binding:{...run.output_binding,knowledge_structure_revision:revision}};
  await page.route(`**/v1/materials/${mid}`,route=>json(route,{...materialWithHistory([]),material_id:mid,source_artifact_id:snapshot.source_artifact_id,head_revision:revision,latest_attempt:published,available_structures:[{run_id:rid,knowledge_structure_revision:revision,created_at:run.created_at,status:'succeeded'}],study_sessions:[{...sessionView,run_id:rid}]}));
  await page.route(`**/v1/material-processing-runs/${rid}`,route=>json(route,published));
  await page.route(url=>decodeURIComponent(url.pathname)===`/v1/materials/${mid}/knowledge-structures/${revision}`,route=>json(route,view));
  await page.route(`**/v1/study-sessions/${sid}/progress`,route=>json(route,snapshot.progress));
  await page.goto(fixture.path.split('/study-sessions/')[0]);
  await page.getByRole('tab',{name:'測驗',exact:true}).click();
  await expect(page).not.toHaveURL(/study-sessions/);
  if(stage==='completed')await page.getByText(/已完成的概念 · 再次練習/).click();
  await page.getByRole('button',{name:stage==='completed'?'再次練習':'檢測這個概念',exact:true}).click();
  await expect(page).toHaveURL(/assessment-sets/);
  await expect(page.getByRole('progressbar',{name:'準備進度'})).toBeVisible();
  await expect(page.locator('.current-concept-card')).not.toBeVisible();
  expect(fixture.requests.filter(r=>r.path.endsWith('/assessment-sets'))).toHaveLength(1);
 });
}
for (const pending of ["create", "saved-focus", "second-focus"] as const) {
  for (const destination of ["leave", "material", "run", "structure"] as const) {
    test(`stale ${pending} cannot navigate after ${destination} changes`, async ({ page }) => {
      await mockKnowledgeMapApi(page);
      if (pending === "saved-focus") {
        await page.route(`**/v1/materials/${materialId}`, (route) =>
          json(route, materialWithHistory([{ ...session(), run_id: runId }])),
        );
      }
      // 在 response.json 的 continuation 完成後才斷言，避免只測到 response 尚未消化。
      await page.addInitScript((waitingForCreate) => {
        const readJson = Response.prototype.json;
        Response.prototype.json = async function () {
          const value = await readJson.call(this);
          if (value.schema === "study-session/v1" && this.url.endsWith(waitingForCreate ? "/study-sessions" : "/focus")) {
            setTimeout(() => document.documentElement.dataset.studyResponseRead = "true", 0);
          }
          return value;
        };
      }, pending === "create");
      let release!: () => void;
      const blocked = new Promise<void>((resolve) => { release = resolve; });
      const selected = pending === "saved-focus" ? secondConcept : firstConcept;
      const aligned = { ...session(), current_concept_id: selected };
      await page.route("**/v1/study-sessions", async (route) => {
        if (pending === "create") await blocked;
        // 故意回傳不同觀念；失效 create 不得再送 focus。
        return json(route, { ...session(), current_concept_id: secondConcept }, 201);
      });
      await page.route(`**/v1/study-sessions/${sessionId}/focus`, async (route) => {
        await blocked;
        return json(route, aligned);
      });
      const writes = trackStudyWrites(page);
      await page.goto(mapPath);
      await openMapConcept(page, pending === "saved-focus" ? "Array" : "Stack");
      await page.getByRole("button", {
        name: "檢測這個概念", exact: true,
      }).click();
      await expect.poll(() => writes.length).toBe(pending === "second-focus" ? 2 : 1);
      const nextPath = destination === "leave" ? "/materials"
        : destination === "material" ? mapPath.replace(materialId, "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
        : destination === "run" ? mapPath.replace(runId, "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
        : mapPath.replace(encodeURIComponent(structureRevision), encodeURIComponent(`knowledge-structure:sha256:${"9".repeat(64)}`));
      await page.evaluate((path) => {
        window.history.pushState(null, "", path);
        window.dispatchEvent(new PopStateEvent("popstate"));
        delete document.documentElement.dataset.studyResponseRead;
      }, nextPath);
      await expect(page.getByRole("region", { name: "學習入口" })).toHaveCount(0);
      release();
      await expect(page.locator("html")).toHaveAttribute("data-study-response-read", "true");
      expect(new URL(page.url()).pathname).toBe(nextPath);
      expect(writes).toHaveLength(pending === "second-focus" ? 2 : 1);
    });
  }
}
