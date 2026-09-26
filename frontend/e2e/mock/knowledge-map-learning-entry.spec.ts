import { expect, test, type Page } from "@playwright/test";
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

// 此檔需要舊／新 session ID 與不同 run 的讀取；回應必須綁定目前的紀錄。
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
  const resume = page.getByRole("button", { name: "繼續學習", exact: true });
  await expect(resume).toBeEnabled();
  await expect(resume).toBeInViewport();
  await page.reload();
  await openMapConcept(page);
  await expect(resume).toBeEnabled();
  await openMapConcept(page, "Array");
  await expect(
    page
      .getByRole("region", { name: "學習入口" })
      .getByRole("button", { name: "從這個概念繼續", exact: true }),
  ).toBeEnabled();
  await expect(page.locator(".map-study-bar")).toHaveCount(0);
  await page.keyboard.press("Escape");
  await page.getByRole("tab", { name: "複習重點", exact: true }).click();
  await page.getByRole("button", { name: "查看學習導覽", exact: true }).click();
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
  await expect(entry.getByRole("button", { name: "開始學習", exact: true })).toBeEnabled();
  await entry.getByRole("button", { name: "開始學習", exact: true }).click();
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
  await expect(entry.getByRole("button", { name: "查看學習成果", exact: true })).toBeEnabled();
  await expect(page.locator(".map-study-bar")).toHaveCount(0);
  await page.keyboard.press("Escape");
  await page.getByRole("tab", { name: "複習重點", exact: true }).click();
  await expect(page.getByRole("complementary", { name: "Studydy 學習引導" })).toHaveCount(0);
  await expect(entry).toHaveCount(0);
  await page.getByRole("button", { name: "查看學習導覽", exact: true }).click();
  await openMapConcept(page, "Array");
  await entry.getByRole("button", { name: "查看學習成果", exact: true }).click();
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
        history === "same" ? "繼續學習" : history === "different" ? "從這個概念繼續" : "開始學習";
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
  await page.getByRole("button", { name: "開始學習", exact: true }).click();
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
