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

for (const stage of ["preparing", "ready", "failed", "no-safe", "completed", "remediation"] as const) {
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
    if (stage === "completed") await expect(page.getByRole("dialog", { name: "概念詳情" })).toContainText("本輪檢測通過");
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
    if (stage === "no-safe") await expect(page.getByRole("button", { name: "開始本輪 0 題", exact: true })).toBeDisabled();
    if (stage === "completed") await expect(page.locator(".assessment-set-summary")).toContainText("答對4 / 4 題");
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
    if (stage === "ready") await expect(page.getByRole("dialog", { name: "概念詳情" })).toContainText("本輪檢測通過");
  });
}

for (const width of [1366, 390]) {
  test(`complete sidebar content scrolls and enters assessment at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 844 });
    const view = structureView();
    const original = view.concepts[0].claims[0];
    view.concepts[0].claims = Array.from({ length: 24 }, (_, i) => ({
      ...original,
      claim_id: `claim:sha256:${(i + 20).toString(16).padStart(64, "0")}`,
      text: `完整教材重點 ${i + 1}：` + "依教材說明這個概念的用途與限制。".repeat(8),
    }));
    await mockKnowledgeMapApi(page, view);
    const writes = trackStudyWrites(page);
    await page.goto(mapPath);
    await openMapConcept(page);
    const detail = page.getByRole("dialog", { name: "概念詳情" });
    await expect(detail).toContainText("尚未練習");
    await expect(detail.locator(".concept-claim")).toHaveCount(24);
    const cta = detail.getByRole("button", { name: "檢測這個概念", exact: true });
    await expect(cta).toBeInViewport();
    await detail.locator(".concept-claim").last().scrollIntoViewIfNeeded();
    await expect(detail.locator(".concept-claim").last()).toContainText("完整教材重點 24");
    await expect.poll(() => detail.evaluate((el) => el.scrollTop)).toBeGreaterThan(0);
    await detail.getByRole("region", { name: "教材來源" }).scrollIntoViewIfNeeded();
    await expect(detail.getByRole("region", { name: "教材來源" }).getByRole("button").first()).toBeVisible();
    await expect(cta).toBeInViewport();
    await page.reload();
    await expect(detail.locator(".concept-claim")).toHaveCount(24);
    await page.keyboard.press("Escape");
    await openMapConcept(page, "Array");
    await expect(detail.locator(".concept-claim")).toHaveCount(1);
    await expect(detail).not.toContainText("完整教材重點 24");
    await cta.click();
    await expect(page).toHaveURL(new RegExp(`/study-sessions/${sessionId}$`));
    await expect(page.getByRole("button", { name: "開始本輪 1 題" })).toBeEnabled();
    await expect(page.locator(".current-concept-card")).not.toBeVisible();
    expect(writes.filter((r) => r.path === "/v1/study-sessions")).toHaveLength(1);
    await page.getByRole("button", { name: "返回概念地圖", exact: true }).click();
    await expect(detail.getByRole("heading", { name: "Array", exact: true })).toBeVisible();
  });
}

test("assessment tab uses selected concept and other material tabs reuse session entry", async ({ page }) => {
  const view = structureView();
  let saved = session();
  await mockKnowledgeMapApi(page, view);
  await mockStudyReads(page, view, () => saved);
  await page.route(`**/v1/materials/${materialId}`, route => json(route, materialWithHistory([{ ...saved, run_id: runId }])));
  await page.route(`**/v1/study-sessions/${sessionId}/focus`, route => {
    saved = { ...saved, current_concept_id: secondConcept };
    return json(route, saved);
  });
  const writes = trackStudyWrites(page);
  await page.goto(mapPath);
  await expect(page.getByRole("tab", { name: "複習重點", exact: true })).toHaveCount(0);
  await openMapConcept(page, "Array");
  await page.keyboard.press("Escape");
  await page.getByRole("tab", { name: "測驗", exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`/study-sessions/${sessionId}$`));
  await expect(page.getByRole("heading", { name: "Array", level: 1 })).toBeVisible();
  expect(writes.map(r => r.path)).toEqual([`/v1/study-sessions/${sessionId}/focus`]);
  await page.route("**/v1/card-sets", route => json(route, { schema: "card-set-list/v1", card_sets: [] }));
  await page.goto(`/materials/${materialId}/concept-cards`);
  await page.getByRole("tab", { name: "測驗", exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`/study-sessions/${sessionId}$`));
  await page.reload();
  await expect(page.getByRole("heading", { name: "Array", level: 1 })).toBeVisible();
  expect(writes).toHaveLength(1);
});

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

// 各 session／run 的回應須綁定所請求的紀錄。
async function mockStudyReads(
  page: Page,
  view: ReturnType<typeof structureView>,
  readState: () => ReturnType<typeof session>,
  publishedRunId = runId,
) {
  const map = `/v1/materials/${materialId}/knowledge-structures/${view.knowledge_structure_revision}`;
  const reject = (route: Parameters<typeof json>[0]) =>
    json(
      route,
      {
        schema: "api-error/v1",
        request_id: materialId,
        reason_code: "RESOURCE_NOT_FOUND",
        retryable: false,
        message: "Request could not be completed.",
      },
      404,
    );
  const snapshot = () => {
    const state = readState();
    const concept = view.concepts.find((item) => item.concept_id === state.current_concept_id)!;
    return {
      ...progress,
      study_session_id: state.study_session_id,
      knowledge_structure_revision: state.knowledge_structure_revision,
      current_concept_id: state.current_concept_id,
      event_watermark: state.event_watermark,
      concept_states: view.concepts.map((item, index) => ({
        ...progress.concept_states[index],
        concept_id: item.concept_id,
        label: item.label,
      })),
      next_action:
        state.status === "completed"
          ? {
              ...progress.next_action,
              action: "complete",
              target_concept_id: null,
              target_claim_id: null,
              reason: "all_mastered",
            }
          : {
              ...progress.next_action,
              target_concept_id: concept.concept_id,
              target_claim_id: concept.claims[0].claim_id,
            },
    };
  };
  await page.route(
    /\/v1\/study-sessions\/[^/]+\/(?:progress|assessment-plan)(?:\?.*)?$/,
    (route) => {
      expect(route.request().method()).toBe("GET");
      const address = new URL(route.request().url());
      const state = readState();
      if (address.pathname.split("/").at(-2) !== state.study_session_id) return reject(route);
      if (address.pathname.endsWith("/progress")) return json(route, snapshot());
      const concept = view.concepts.find(
        (item) => item.concept_id === address.searchParams.get("concept_id"),
      );
      if (!concept) return reject(route);
      return json(route, {
        schema: "assessment-plan/v1",
        study_session_id: state.study_session_id,
        knowledge_structure_revision: state.knowledge_structure_revision,
        policy: "single-concept-grounded-points/v1",
        concept_id: concept.concept_id,
        point_count: concept.claims.length,
        requested_count: concept.claims.length,
        targets: concept.claims.map((claim) => ({
          claim_id: claim.claim_id,
          covered_claim_ids: [claim.claim_id],
          reason: "distinct_grounded_point",
        })),
        excluded: [],
      });
    },
  );
  await page.route(
    (url) =>
      decodeURIComponent(url.pathname).startsWith(`${map}/study-sessions/`) &&
      url.pathname.endsWith("/resume"),
    (route) => {
      expect(route.request().method()).toBe("GET");
      const address = new URL(route.request().url());
      const state = readState();
      if (
        decodeURIComponent(address.pathname) !==
          `${map}/study-sessions/${state.study_session_id}/resume` ||
        address.searchParams.get("run_id") !== publishedRunId
      )
        return reject(route);
      return json(route, {
        schema: "study-resume/v1",
        assessment_sets: [],
        selected_set_id: null,
        session: state,
        run_id: publishedRunId,
        source_artifact_id: artifactId,
        knowledge_structure: view,
        progress: snapshot(),
      });
    },
  );
}

test("recovered map ignores stale bindings and resumes without study writes", async ({ page }) => {
  await page.setViewportSize({ width: 1366, height: 768 });
  const saved = session();
  await mockKnowledgeMapApi(page);
  await mockStudyReads(page, structureView(), () => saved);
  await page.route(`**/v1/materials/${materialId}`, (route) =>
    json(
      route,
      materialWithHistory([
        {
          ...saved,
          study_session_id: "77777777-7777-4777-8777-777777777777",
          run_id: runId,
          knowledge_structure_revision: `knowledge-structure:sha256:${"9".repeat(64)}`,
          started_at: "2026-09-06T00:00:00Z",
        },
        {
          ...saved,
          study_session_id: "88888888-8888-4888-8888-888888888888",
          run_id: "99999999-9999-4999-8999-999999999999",
        },
        { ...saved, run_id: runId },
      ]),
    ),
  );
  const writes = trackStudyWrites(page);
  await page.goto(mapPath);
  await openMapConcept(page);
  const resume = page.getByRole("button", { name: "檢測這個概念", exact: true });
  await expect(resume).toBeEnabled();
  await expect(resume).toBeInViewport();
  await page.reload();
  await openMapConcept(page);
  await expect(resume).toBeEnabled();
  await openMapConcept(page, "Array");
  await expect(
    page
      .getByRole("region", { name: "學習入口" })
      .getByRole("button", { name: "檢測這個概念", exact: true }),
  ).toBeEnabled();
  await expect(page.locator(".map-study-bar")).toHaveCount(0);
  await page.keyboard.press("Escape");
  await openMapConcept(page, "Stack");
  await resume.click();
  await expect(page).toHaveURL(new RegExp(`/study-sessions/${sessionId}$`));
  await expect(page.getByRole("button", { name: "開始本輪 1 題", exact: true })).toBeEnabled();
  expect(writes).toEqual([]);
});

test("learning entry gates loading and creation, then opens completed history without writes", async ({
  page,
}) => {
  const view = structureView();
  let saved = session();
  let history: SavedSession[] = [];
  await mockKnowledgeMapApi(page, view);
  await mockStudyReads(page, view, () => saved);
  let releaseMaterial!: () => void;
  let releaseCreation!: () => void;
  const readingMaterial = new Promise<void>((resolve) => {
    releaseMaterial = resolve;
  });
  const startingStudy = new Promise<void>((resolve) => {
    releaseCreation = resolve;
  });
  await page.route(`**/v1/materials/${materialId}`, async (route) => {
    await readingMaterial;
    return json(route, materialWithHistory(history));
  });
  const creation = {
    schema: "study-session-create/v1",
    material_id: materialId,
    knowledge_structure_revision: structureRevision,
    current_concept_id: firstConcept,
  };
  await page.route("**/v1/study-sessions", async (route) => {
    expect(route.request().postDataJSON()).toEqual(creation);
    await startingStudy;
    return route.fallback();
  });
  const writes = trackStudyWrites(page);
  await page.goto(mapPath);
  await openMapConcept(page);
  const entry = page.getByRole("region", { name: "學習入口" });
  await expect(entry.getByRole("button", { name: "讀取學習進度…", exact: true })).toBeDisabled();
  expect(writes).toEqual([]);
  releaseMaterial();
  await expect(entry.getByRole("button", { name: "檢測這個概念", exact: true })).toBeEnabled();
  await entry.getByRole("button", { name: "檢測這個概念", exact: true }).click();
  await expect(entry.getByRole("button", { name: "正在開始…", exact: true })).toBeDisabled();
  await expect.poll(() => writes.length).toBe(1);
  releaseCreation();
  await expect(page).toHaveURL(new RegExp(`/study-sessions/${sessionId}$`));
  await expect(page.getByRole("button", { name: "開始本輪 1 題", exact: true })).toBeEnabled();
  saved = session("completed");
  history = [{ ...saved, run_id: runId }];
  await page.goto(mapPath);
  // 完成紀錄即使選取另一個概念，也只查看成果，不呼叫 focus。
  await openMapConcept(page, "Array");
  await expect(entry.getByRole("button", { name: "查看測驗結果", exact: true })).toBeEnabled();
  await expect(page.locator(".map-study-bar")).toHaveCount(0);
  await page.keyboard.press("Escape");
  await openMapConcept(page, "Array");
  await entry.getByRole("button", { name: "查看測驗結果", exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`/study-sessions/${sessionId}$`));
  await expect(page.getByRole("heading", { name: "學習已完成", exact: true })).toBeVisible();
  expect(writes).toEqual([{ method: "POST", path: "/v1/study-sessions", body: creation }]);
});

// 兩個桌機尺寸使用相同入口版面，保留桌機與手機的三種紀錄情境。
for (const viewport of [
  { width: 1536, height: 1024 },
  { width: 390, height: 844 },
]) {
  for (const history of ["none", "same", "different"] as const) {
    test(`selected concept ${history} history chooses the correct study request at ${viewport.width}px`, async ({
      page,
    }) => {
      await page.setViewportSize(viewport);
      const view = structureView();
      view.concepts[0].label = "指標";
      view.concepts[1].label = "陣列";
      view.concepts[0].claims[0].text = view.concepts[0].claims[0].evidence[0].quote =
        "指標保存記憶體位址。";
      const saved = {
        ...session(),
        current_concept_id: history === "same" ? secondConcept : firstConcept,
      };
      const created = {
        ...saved,
        study_session_id: history === "none" ? "55555555-5555-4555-8555-555555555555" : sessionId,
        current_concept_id: secondConcept,
      };
      let live = saved;
      await mockKnowledgeMapApi(page, view);
      await mockStudyReads(page, view, () => live);
      await page.route(`**/v1/materials/${materialId}`, (route) =>
        json(route, materialWithHistory(history === "none" ? [] : [{ ...saved, run_id: runId }])),
      );
      await page.route("**/v1/study-sessions", (route) => {
        expect(route.request().method()).toBe("POST");
        live = created;
        return json(route, created, 201);
      });
      await page.route(`**/v1/study-sessions/${sessionId}/focus`, (route) => {
        expect(route.request().method()).toBe("POST");
        live = created;
        return json(route, created);
      });
      const writes = trackStudyWrites(page);
      await page.goto(mapPath);
      await openMapConcept(page, "陣列");
      const entry = page.getByRole("region", { name: "學習入口" });
      const label =
        "檢測這個概念";
      const action = entry.getByRole("button", { name: label, exact: true });
      await expect(action).toBeEnabled();
      await expect(
        page
          .getByRole("dialog", { name: "概念詳情" })
          .getByRole("heading", { name: "陣列", exact: true }),
      ).toBeVisible();
      await expect(page.locator(".focus-study-action")).toHaveCount(0);
      expect(writes).toEqual([]);
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(viewport.width);
      await action.click();
      await expect(page).toHaveURL(new RegExp(`/study-sessions/${created.study_session_id}$`));
      await expect(
        page.getByRole("heading", { name: "陣列", level: 1, exact: true }),
      ).toBeVisible();
      await expect(page.getByRole("button", { name: "開始本輪 1 題", exact: true })).toBeEnabled();
      expect(writes).toEqual(
        history === "same"
          ? []
          : [
              {
                method: "POST",
                path:
                  history === "different"
                    ? `/v1/study-sessions/${sessionId}/focus`
                    : "/v1/study-sessions",
                body:
                  history === "different"
                    ? { schema: "study-session-focus/v1", current_concept_id: secondConcept }
                    : {
                        schema: "study-session-create/v1",
                        material_id: materialId,
                        knowledge_structure_revision: structureRevision,
                        current_concept_id: secondConcept,
                      },
              },
            ],
      );
    });
  }
}

test("new head creates a distinct study session and never focuses the old revision", async ({
  page,
}) => {
  const nextRevision = `knowledge-structure:sha256:${"9".repeat(64)}`;
  const nextRunId = "55555555-5555-4555-8555-555555555555";
  const nextStateId = "66666666-6666-4666-8666-666666666666";
  const view = structureView(nextRevision);
  const nextRun = {
    ...run,
    run_id: nextRunId,
    output_binding: { ...run.output_binding, knowledge_structure_revision: nextRevision },
  };
  const saved = {
    ...session(),
    study_session_id: nextStateId,
    knowledge_structure_revision: nextRevision,
  };
  await mockKnowledgeMapApi(page, view);
  await mockStudyReads(page, view, () => saved, nextRunId);
  await page.route(`**/v1/material-processing-runs/${nextRunId}`, (route) => json(route, nextRun));
  await page.route(`**/v1/materials/${materialId}`, (route) =>
    json(route, materialWithHistory([{ ...session(), run_id: runId }], nextRun)),
  );
  await page.route("**/v1/study-sessions", (route) => {
    expect(route.request().method()).toBe("POST");
    return json(route, saved, 201);
  });
  const writes = trackStudyWrites(page);
  await page.goto(
    `/materials/${materialId}/runs/${nextRunId}/knowledge-structures/${encodeURIComponent(nextRevision)}`,
  );
  await openMapConcept(page);
  await page.getByRole("button", { name: "檢測這個概念", exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`/study-sessions/${nextStateId}$`));
  await expect(page.getByRole("heading", { name: "Stack", level: 1, exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "開始本輪 1 題", exact: true })).toBeEnabled();
  expect(writes).toEqual([
    {
      method: "POST",
      path: "/v1/study-sessions",
      body: {
        schema: "study-session-create/v1",
        material_id: materialId,
        knowledge_structure_revision: nextRevision,
        current_concept_id: firstConcept,
      },
    },
  ]);
});

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
