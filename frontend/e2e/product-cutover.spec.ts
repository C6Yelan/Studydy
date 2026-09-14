import { expect, test, type Page, type Route } from "@playwright/test";
import type { AssessmentRecordView } from "../src/api/contracts";

const materialId = "11111111-1111-4111-8111-111111111111";
const runId = "22222222-2222-4222-8222-222222222222";
const sessionId = "33333333-3333-4333-8333-333333333333";
const artifactId = "44444444-4444-4444-8444-444444444444";
const structureRevision = `knowledge-structure:sha256:${"a".repeat(64)}`;
const firstConcept = `concept:sha256:${"b".repeat(64)}`;
const secondConcept = `concept:sha256:${"c".repeat(64)}`;
const firstClaim = `claim:sha256:${"d".repeat(64)}`;
const secondClaim = `claim:sha256:${"e".repeat(64)}`;
const evidenceId = `evidence:sha256:${"1".repeat(64)}`;

function structureView() {
  const concept = (conceptId: string, claimId: string, label: string, page: number) => ({
    concept_id: conceptId,
    label,
    aliases: [],
    section_ids: [`section:sha256:${"2".repeat(64)}`],
    source_pages: [page],
    claims: [{
      claim_id: claimId,
      text: label === "Stack" ? "A stack follows LIFO order." : "An array stores contiguous values.",
      evidence: [{
        evidence_id: label === "Stack" ? evidenceId : `evidence:sha256:${"6".repeat(64)}`,
        page_ref: `page:sha256:${String(page).repeat(64)}`,
        page,
        block_order: 0,
        kind: "paragraph",
        source: "native_text",
        source_locator: { page, block_id: `block:sha256:${String(page).repeat(64)}`, region: [1, 2, 30, 40] },
        quote: label === "Stack" ? "A stack follows LIFO order." : "An array stores contiguous values.",
      }],
    }],
  });
  return {
    schema: "knowledge-structure-view/v2",
    material_id: `material:sha256:${"7".repeat(64)}`,
    knowledge_structure_revision: structureRevision,
    status: { processing: "succeeded", quality: "accepted", decision: "retain", reason_codes: [] },
    document_tree: {
      material_id: `material:sha256:${"7".repeat(64)}`,
      sections: [{ section_id: `section:sha256:${"2".repeat(64)}`, title: "Data structures", order: 0, heading_evidence_id: null, concept_ids: [firstConcept, secondConcept] }],
    },
    concepts: [concept(firstConcept, firstClaim, "Stack", 1), concept(secondConcept, secondClaim, "Array", 2)],
    relations: [{
      relation_id: `relation:sha256:${"8".repeat(64)}`,
      source_concept_id: firstConcept,
      target_concept_id: secondConcept,
      type: "prerequisite",
      learner_reason: "Stack must be learned before Array traversal.",
      evidence_refs: [evidenceId],
      context_refs: [`section:sha256:${"2".repeat(64)}`],
      inference_basis: "dependency",
      confidence: 0.9,
    }],
    initial_learning_path: [
      { position: 1, concept_id: firstConcept, reason: "document_order" },
      { position: 2, concept_id: secondConcept, reason: "prerequisite" },
    ],
    excluded_pages: [],
  };
}

const run = {
  schema: "material-processing-run/v5", cancel_requested_at: null, run_id: runId, material_id: materialId,
  source_artifact_id: artifactId, status: "succeeded", progress_stage: "completed",
  completed_pages: 2, total_pages: 2, error_code: null,
  created_at: "2026-09-05T00:00:00Z", updated_at: "2026-09-05T00:01:00Z", completed_at: "2026-09-05T00:01:00Z",
  output_binding: {
    schema: "material-run-output-binding/v4", knowledge_structure_revision: structureRevision,
    runtime_lock_sha256: "9".repeat(64), page_count: 2, processing: "succeeded",
    quality: "accepted", decision: "retain", reason_codes: [], ocr_calls: 0, semantic_calls: 1,
  },
};

function session(status = "active") {
  return {
    schema: "study-session/v2", study_session_id: sessionId, material_id: materialId,
    knowledge_structure_revision: structureRevision, current_concept_id: firstConcept,
    deferred_concept_ids: [], no_safe_claim_ids: [], status, started_at: "2026-09-05T00:01:00Z",
    completed_at: status === "completed" ? "2026-09-05T00:02:00Z" : null, event_watermark: 0,
  };
}

const progress = {
  schema: "learner-progress/v2", study_session_id: sessionId,
  knowledge_structure_revision: structureRevision, event_watermark: 0,
  current_concept_id: firstConcept, deferred_concept_ids: [],
  concept_states: [
    { concept_id: firstConcept, label: "Stack", status: "not_started", attempts: 0, correct_answers: 0, qualified_correct_items: 0, covered_claim_ids: [], mastered_claim_ids: [], weak_claim_ids: [], latest_is_correct: null },
    { concept_id: secondConcept, label: "Array", status: "not_started", attempts: 0, correct_answers: 0, qualified_correct_items: 0, covered_claim_ids: [], mastered_claim_ids: [], weak_claim_ids: [], latest_is_correct: null },
  ],
  weaknesses: [], next_action: { action: "assess", target_concept_id: firstConcept, target_claim_id: firstClaim, prerequisite_concept_ids: [], reason: "current_concept" },
  guidance_revision: `learner-guidance:sha256:${"f".repeat(64)}`,
};

async function json(route: Route, body: unknown, status = 200) {
  await route.fulfill({ status, json: body });
}

async function routes(page: Page, view = structureView(), readProgress = () => progress, readRecords: () => AssessmentRecordView[] = () => []) {
  await page.route("**/v1/session", (route) => route.request().method() === "GET" ? json(route, { schema: "learner-identity/v1", learner_id: sessionId }) : route.fulfill({ status: 204 }));
  await page.route("**/v1/session/refresh", (route) => route.fulfill({ status: 204 }));
  await page.route(`**/v1/materials/${materialId}`, route => json(route, {
    schema: "material-library-item/v2", material_id: materialId, source_artifact_id: artifactId,
    display_name: "Data structures.pdf", size_bytes: 100, created_at: run.created_at,
    latest_attempt: run, available_structures: [{ run_id: runId, knowledge_structure_revision: structureRevision,
      created_at: run.created_at, status: "succeeded" }], study_sessions: [],
  }));
  await page.route(`**/v1/material-processing-runs/${runId}`, (route) => json(route, run));
  await page.route("**/v1/materials/*/knowledge-structures/**", (route) => json(route, view));
  await page.route("**/v1/study-sessions", (route) => json(route, session(), 201));
  await page.route("**/v1/materials/*/knowledge-structures/*/study-sessions/*/resume?*", route => {
    const currentProgress = readProgress();
    const records = readRecords();
    const selected = new URL(route.request().url()).searchParams.get("assessment_revision") ?? records[0]?.assessment.assessment_revision ?? null;
    return json(route, { schema: "study-resume/v1", session: { ...session(), event_watermark: currentProgress.event_watermark },
      run_id: runId, source_artifact_id: artifactId, knowledge_structure: view, progress: currentProgress,
      assessments: records, selected_assessment_revision: selected });
  });
}

function navigationConcept(page: Page, label: string) {
  return page.getByRole("navigation", { name: "學習導覽" }).getByRole("button").filter({ has: page.getByText(label, { exact: true }) });
}

async function openFocusRelation(page: Page, mobile: boolean) {
  if (mobile) {
    const fallback = page.locator(".focus-relations");
    if (await fallback.getAttribute("open") === null) await fallback.locator("summary").click();
    const button = fallback.getByRole("list", { name: "直接概念關係" }).getByRole("button").first();
    await button.click();
    return button;
  }
  await page.getByRole("button", { name: "顯示完整關係圖", exact: true }).click();
  const edge = page.locator(".focus-graph .concept-flow-edge").first();
  await edge.locator(".react-flow__edge-textbg").click();
  return edge;
}

test("learning navigation and chapter views lead to source-backed learning", async ({ page }) => {
  await routes(page);
  await page.route("**/v1/study-sessions", route => {
    expect(route.request().postDataJSON()).toEqual({ schema: "study-session-create/v2", material_id: materialId,
      knowledge_structure_revision: structureRevision, current_concept_id: firstConcept });
    return json(route, session(), 201);
  });
  await page.context().route(`**/v1/artifacts/${artifactId}`, route => route.fulfill({ contentType: "text/plain", body: "Synthetic source document" }));
  await page.goto(`/materials/${materialId}/runs/${runId}`);
  await page.getByRole("button", { name: "開啟知識地圖", exact: true }).click();
  await expect(page).toHaveURL(`http://127.0.0.1:4173/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}`);
  await expect(page.getByRole("heading", { name: "知識地圖", exact: true })).toBeVisible();
  await expect(page.getByRole("tab", { name: "概念地圖" })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("region", { name: "學習入口" })).toContainText("準備開始學習「Stack」？");
  await expect(page.locator(".react-flow__node")).toHaveCount(2);
  const canvas = await page.locator(".focus-graph").boundingBox();
  const navigator = await page.getByRole("navigation", { name: "學習導覽" }).boundingBox();
  const context = await page.getByRole("complementary", { name: "目前焦點資訊" }).boundingBox();
  expect(canvas!.x).toBeGreaterThan(navigator!.x + navigator!.width);
  expect(canvas!.x + canvas!.width).toBeLessThan(context!.x);
  expect(canvas!.width).toBeGreaterThan(context!.width);
  await expect(page.getByRole("button", { name: "開始學習", exact: true })).toBeInViewport();
  const edge = page.locator(".concept-flow-edge.is-prerequisite");
  await edge.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("dialog", { name: "關係詳情" })).toContainText("Stack must be learned before Array traversal.");
  await expect(page.getByRole("dialog", { name: "關係詳情" }).getByRole("button", { name: /原始教材第 1 頁/ })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(edge).toBeFocused();
  await expect(page.locator(".navigator-position")).toHaveText(["1", "2"]);
  await navigationConcept(page, "Array").click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await page.getByRole("button", { name: "教材概念：Array", exact: true }).click();
  await expect(page.getByRole("dialog", { name: "概念詳情" })).toContainText("An array stores contiguous values.");
  await page.getByRole("button", { name: "關閉概念詳情" }).click();
  await page.getByRole("tab", { name: "總覽", exact: true }).click();
  await page.locator(".overview-index-disclosure summary").click();
  await page.getByRole("button", { name: "在概念地圖中查看：Stack", exact: true }).click();
  await expect(page.getByRole("tab", { name: "概念地圖", exact: true })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await page.getByRole("button", { name: "教材概念：Stack", exact: true }).click();
  await expect(page.getByRole("button", { name: /原始教材第 1 頁/ })).toBeVisible();
  const popup = page.waitForEvent("popup");
  await page.getByRole("button", { name: /原始教材第 1 頁/ }).click();
  const source = await popup;
  await expect(source).toHaveURL(`http://127.0.0.1:4173/v1/artifacts/${artifactId}#page=1`);
  await source.close();
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "開始學習", exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`/study-sessions/${sessionId}$`));
});

test("large maps show a readable focus and keep it in view after resizing", async ({ page }) => {
  const view = structureView();
  view.concepts = Array.from({ length: 30 }, (_, index) => ({
    ...view.concepts[0], concept_id: `concept:sha256:${(index + 10).toString(16).padStart(64, "0")}`,
    label: `Concept ${index + 1}`, section_ids: [`section:sha256:${(index + 10).toString(16).padStart(64, "0")}`],
  }));
  view.document_tree.sections = view.concepts.map((concept, index) => ({
    section_id: concept.section_ids[0], title: `Section ${index + 1}`, order: index,
    heading_evidence_id: null, concept_ids: [concept.concept_id],
  }));
  view.relations = [];
  view.initial_learning_path = view.concepts.map((concept, index) => ({ position: index + 1, concept_id: concept.concept_id, reason: "document_order" }));
  await routes(page, view);
  await page.setViewportSize({ width: 1280, height: 720 });
  await page.goto(`/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}`);
  await page.getByRole("tab", { name: "概念地圖" }).click();
  const graph = page.locator(".focus-graph");
  await expect.poll(async () => (await graph.boundingBox())?.height ?? 0).toBeGreaterThanOrEqual(300);
  const viewport = page.locator(".react-flow__viewport");
  await expect(page.locator(".react-flow__node.is-focus")).toBeVisible();
  const transform = await viewport.getAttribute("style");
  const graphBox = (await graph.boundingBox())!;
  await page.mouse.move(graphBox.x + graphBox.width / 2, graphBox.y + graphBox.height / 2);
  await page.mouse.wheel(0, 250);
  await expect(viewport).not.toHaveAttribute("style", transform!);
  await expect.poll(() => page.evaluate(() => window.scrollY)).toBe(0);
  const rail = page.locator(".navigator-list");
  const railBox = (await rail.boundingBox())!;
  await page.mouse.move(railBox.x + 50, railBox.y + 100);
  await page.mouse.wheel(0, 250);
  await expect.poll(() => rail.evaluate(element => element.scrollTop)).toBeGreaterThan(100);
  expect(await page.evaluate(() => window.scrollY)).toBe(0);
  await navigationConcept(page, "Concept 30").click();
  await expect(page.getByRole("button", { name: "教材概念：Concept 30", exact: true })).toHaveClass(/is-focus/);
  await expect.poll(async () => {
    const node = await page.locator(".react-flow__node.is-focus").boundingBox();
    const graph = await page.locator(".focus-graph").boundingBox();
    return !!node && !!graph && node.x >= graph.x && node.x + node.width <= graph.x + graph.width
      && node.y >= graph.y && node.y + node.height <= graph.y + graph.height;
  }).toBe(true);
  await expect(page.locator(".react-flow__node")).toHaveCount(1);
  await page.getByRole("button", { name: "教材概念：Concept 30", exact: true }).click();
  await expect(page.getByRole("dialog", { name: "概念詳情" })).toBeInViewport();
  await expect.poll(async () => {
    const close = await page.getByRole("button", { name: "關閉概念詳情", exact: true }).boundingBox();
    const tabs = await page.getByRole("tablist", { name: "知識地圖檢視" }).boundingBox();
    return !!close && !!tabs && close.y >= tabs.y + tabs.height;
  }).toBe(true);
  await expect.poll(async () => (await graph.boundingBox())?.height ?? 0).toBeGreaterThanOrEqual(300);
  await expect(page.locator(".map-view")).toHaveCSS("overflow-y", "visible");
  await page.setViewportSize({ width: 1100, height: 720 });
  await expect.poll(async () => {
    const node = await page.locator(".react-flow__node.is-focus").boundingBox();
    const graph = await page.locator(".focus-graph").boundingBox();
    return !!node && !!graph && node.width >= 150 && node.x >= graph.x && node.x + node.width <= graph.x + graph.width;
  }).toBe(true);
});

test("StudySession uses source-bound assessment and server feedback", async ({ page }) => {
  const sharedEvidenceView = structureView();
  sharedEvidenceView.concepts[0].claims.push({
    ...sharedEvidenceView.concepts[0].claims[0],
    claim_id: `claim:sha256:${"f".repeat(64)}`,
    text: "Another learning point with the same source page.",
  });
  let guidedClaim = firstClaim;
  const records: AssessmentRecordView[] = [];
  await routes(page, sharedEvidenceView, () => ({
    ...progress, event_watermark: records.filter(record => record.feedback).length,
    next_action: { ...progress.next_action, target_claim_id: guidedClaim },
  }), () => records);
  const requestedClaims: string[] = [];
  const assessmentRevision = `assessment:sha256:${"3".repeat(64)}`;
  const questionId = `question:sha256:${"4".repeat(64)}`;
  const options = ["LIFO", "FIFO", "RANDOM", "PRIORITY"].map((text, index) => ({ option_id: `option:sha256:${String(index + 1).repeat(64)}`, text }));
  await page.route(`**/v1/study-sessions/${sessionId}/assessments`, (route) => {
    requestedClaims.push(route.request().postDataJSON().target_claim_id);
    const assessment = {
    schema: "single-choice-assessment/v2" as const, assessment_revision: requestedClaims.length === 1 ? assessmentRevision : `assessment:sha256:${"6".repeat(64)}`,
    study_session_id: sessionId, knowledge_structure_revision: structureRevision,
    question_id: questionId, target_concept_id: firstConcept, target_claim_id: firstClaim,
    source_evidence_ids: [evidenceId], question_type: "single_choice" as const,
    prompt: "根據教材，Stack 使用哪種順序？", options,
  };
    records.unshift({ assessment, feedback: null, created_at: "2026-09-05T00:02:00Z", can_submit: true });
    return json(route, assessment, 201);
  });
  await page.route(`**/v1/study-sessions/${sessionId}/assessments/${encodeURIComponent(assessmentRevision)}/submissions`, (route) => {
    guidedClaim = sharedEvidenceView.concepts[0].claims[1].claim_id;
    const feedback = {
    schema: "answer-feedback/v2" as const, answer_event_id: "55555555-5555-4555-8555-555555555555",
    study_session_id: sessionId, assessment_revision: assessmentRevision,
    question_id: questionId, selected_option_id: options[0].option_id, is_correct: true,
    rationale: "A stack follows LIFO order.", source_evidence_ids: [evidenceId], event_number: 1,
    created_at: "2026-09-05T00:02:00Z",
  };
    records[0].feedback = feedback;
    records[0].can_submit = false;
    return json(route, feedback, 201);
  });
  await page.goto(`/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}/study-sessions/${sessionId}`);
  await page.getByRole("button", { name: "開始練習" }).click();
  await expect(page.getByRole("heading", { name: "根據教材，Stack 使用哪種順序？" })).toBeVisible();
  await page.getByLabel(/LIFO/).check();
  await page.getByRole("button", { name: "送出答案" }).click();
  await expect(page.getByRole("heading", { name: "答對了" })).toBeVisible();
  await expect(page.locator(".feedback-rationale")).toHaveText("A stack follows LIFO order.");
  await expect(page.locator(".feedback-evidence button")).toHaveCount(1);
  await expect.poll(() => guidedClaim).toBe(sharedEvidenceView.concepts[0].claims[1].claim_id);
  await page.getByRole("button", { name: "繼續練習" }).click();
  await expect.poll(() => requestedClaims).toEqual([firstClaim, guidedClaim]);
});

test("mobile map has a modal detail drawer with keyboard focus and source links", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await routes(page);
  await page.goto(`/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}`);
  await page.getByRole("tab", { name: "概念地圖" }).click();
  await page.getByRole("button", { name: "查看概念與來源" }).click();
  const dialog = page.getByRole("dialog", { name: "概念詳情" });
  await expect(dialog).toBeInViewport();
  await expect(dialog.getByRole("button", { name: /原始教材第 1 頁/ })).toBeVisible();
  await expect.poll(() => dialog.evaluate(element => element.matches(":modal"))).toBe(true);
  await page.keyboard.press("Tab");
  await expect.poll(() => page.evaluate(() => !!document.activeElement?.closest(".detail-panel"))).toBe(true);
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  await expect(page.getByRole("button", { name: "查看概念與來源" })).toBeFocused();
  await page.locator(".focus-relations summary").click();
  await page.getByRole("list", { name: "直接概念關係", exact: true }).getByRole("button").click();
  await expect(page.getByRole("dialog", { name: "關係詳情" })).toContainText("Stack must be learned before Array traversal.");
});

test("assessment follows the guided Claim when reopening a multi-Claim concept", async ({ page }) => {
  const view = structureView();
  view.concepts[0].claims.push({ ...view.concepts[0].claims[0], claim_id: secondClaim, text: "Second learning point." });
  await routes(page, view, () => ({
    ...progress, next_action: { ...progress.next_action, target_claim_id: secondClaim },
  }));
  await page.goto(`/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}/study-sessions/${sessionId}`);
  await expect(page.locator(".claim-picker, .assessment-claim-picker")).toHaveCount(0);
  await page.reload();
  await expect(page.locator(".claim-picker, .assessment-claim-picker")).toHaveCount(0);
  let requested: string | null = null;
  await page.route(`**/v1/study-sessions/${sessionId}/assessments`, route => {
    requested = route.request().postDataJSON().target_claim_id;
    return json(route, { schema: "api-error/v1", request_id: sessionId, reason_code: "NO_SAFE_ASSESSMENT", retryable: false, message: "Request could not be completed." }, 422);
  });
  await page.getByRole("button", { name: "開始練習", exact: true }).click();
  await expect.poll(() => requested).toBe(secondClaim);
});

test("a safe retry replaces old no-safe guidance before answering", async ({ page }) => {
  let unavailable = false;
  let requests = 0;
  const records: AssessmentRecordView[] = [];
  await routes(page, structureView(), () => ({
    ...progress, next_action: unavailable
      ? { ...progress.next_action, action: "defer", target_concept_id: secondConcept, reason: "no_safe_assessment" }
      : progress.next_action,
  }), () => records);
  await page.route(`**/v1/study-sessions/${sessionId}/assessments`, route => {
    requests += 1;
    unavailable = requests === 1;
    if (unavailable) return json(route, {
      schema: "api-error/v1", request_id: materialId, reason_code: "NO_SAFE_ASSESSMENT", retryable: false, message: "Request could not be completed.",
    }, 422);
    const assessment = {
      schema: "single-choice-assessment/v2" as const, assessment_revision: `assessment:sha256:${"3".repeat(64)}`,
      study_session_id: sessionId, knowledge_structure_revision: structureRevision,
      question_id: `question:sha256:${"4".repeat(64)}`, target_concept_id: firstConcept, target_claim_id: firstClaim,
      source_evidence_ids: [evidenceId], question_type: "single_choice" as const, prompt: "Safe retry question",
      options: ["LIFO", "FIFO", "RANDOM", "PRIORITY"].map((text, i) => ({ option_id: `option:sha256:${String(i + 1).repeat(64)}`, text })),
    };
    records.unshift({ assessment, feedback: null, created_at: "2026-09-05T00:02:00Z", can_submit: true });
    return json(route, assessment, 201);
  });
  await page.goto(`/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}/study-sessions/${sessionId}`);
  await page.getByRole("button", { name: "開始練習" }).click();
  await expect(page.getByRole("heading", { name: "改用教材回顧" })).toBeVisible();
  await expect(page.getByRole("button", { name: "暫緩並繼續" })).toHaveCount(0);
  await page.getByRole("button", { name: "完成本次回顧" }).click();
  await expect(page.getByRole("button", { name: "暫緩並繼續" })).toBeVisible();
  await expect(page.getByRole("button", { name: "開始練習" })).toHaveCount(0);
  expect(requests).toBe(1);
  unavailable = false;
  await page.reload();
  await page.getByRole("button", { name: "開始練習" }).click();
  await expect(page.getByRole("heading", { name: "Safe retry question" })).toBeVisible();
  await expect(page.locator(".adaptive-card")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "暫緩並繼續" })).toHaveCount(0);
});

test("stale guidance can reload current progress without applying the old target", async ({ page }) => {
  let outdated = true;
  await routes(page, structureView(), () => ({
    ...progress, next_action: outdated
      ? { ...progress.next_action, action: "advance", target_concept_id: secondConcept, target_claim_id: null }
      : progress.next_action,
  }));
  await page.route(`**/v1/study-sessions/${sessionId}/guidance/apply`, route => {
    outdated = false;
    return json(route, {
      schema: "api-error/v1", request_id: materialId, reason_code: "IDEMPOTENCY_CONFLICT", retryable: false, message: "Request could not be completed.",
    }, 409);
  });
  await page.goto(`/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}/study-sessions/${sessionId}`);
  await page.getByRole("button", { name: "繼續學習", exact: true }).click();
  await expect(page.getByRole("heading", { name: "無法開啟本次學習", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "重新讀取", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Stack", exact: true, level: 1 })).toBeVisible();
  await expect(page.locator(".adaptive-card")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "繼續學習", exact: true })).toHaveCount(0);
});

test("library loading, read failure and empty state retain usable actions", async ({ page }) => {
  await routes(page);
  let release: (() => void) | undefined;
  const ready = new Promise<void>(resolve => { release = resolve; });
  let failRead = true;
  await page.route("**/v1/materials", async route => {
    if (failRead) {
      await ready;
      return json(route, { schema: "api-error/v1", request_id: sessionId, reason_code: "STORAGE_UNAVAILABLE", retryable: true, message: "Request could not be completed." }, 503);
    }
    return json(route, { schema: "material-library/v2", materials: [] });
  });
  await page.goto("/materials");
  await expect(page.getByRole("heading", { name: "正在讀取教材庫", exact: true })).toBeVisible();
  release!();
  await expect(page.getByRole("heading", { name: "無法讀取教材", exact: true })).toBeVisible();
  failRead = false;
  await page.getByRole("button", { name: "重新讀取", exact: true }).click();
  await expect(page.getByRole("heading", { name: "尚未有學習教材", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "上傳第一份教材", exact: true }).click();
  await expect(page).toHaveURL(/\/upload$/);
  await expect(page.locator('input[type="file"]')).toHaveCount(1);
});

test("reopen rejects a run from a different Knowledge Structure revision", async ({ page }) => {
  await routes(page);
  await page.route(`**/v1/materials/${materialId}`, route => json(route, {
    schema: "material-library-item/v2", material_id: materialId, source_artifact_id: artifactId,
    display_name: "Data structures.pdf", size_bytes: 100, created_at: run.created_at,
    latest_attempt: run, available_structures: [{ run_id: runId, knowledge_structure_revision: structureRevision,
      created_at: run.created_at, status: "succeeded" }], study_sessions: [],
  }));
  await page.route(`**/v1/material-processing-runs/${runId}`, route => json(route, {
    ...run, output_binding: { ...run.output_binding, knowledge_structure_revision: `knowledge-structure:sha256:${"7".repeat(64)}` },
  }));
  await page.route("**/v1/materials", route => json(route, { schema: "material-library/v2", materials: [] }));
  await page.goto(`/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}`);
  await expect(page.getByRole("heading", { name: "無法讀取知識地圖", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "開始新的學習", exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "返回教材庫", exact: true }).click();
  await expect(page.getByRole("heading", { name: "尚未有學習教材", exact: true })).toBeVisible();
});

test("dashboard shows unavailable counts truthfully and retries its server-backed summary", async ({ page }) => {
  await routes(page);
  let unavailable = true;
  await page.route('**/v1/materials', route => unavailable
    ? json(route, { schema: 'api-error/v1', request_id: sessionId, reason_code: 'STORAGE_UNAVAILABLE', retryable: true, message: 'Request could not be completed.' }, 503)
    : json(route, { schema: 'material-library/v2', materials: [] }));
  await page.goto('/');
  await expect(page.getByRole('heading', { name: '歡迎回來！', exact: true })).toBeVisible();
  await expect(page.getByRole('alert')).toContainText('資料服務暫時無法使用');
  await expect(page.locator('.dashboard-stat strong')).toHaveText(['—', '—', '—', '—']);
  unavailable = false;
  await page.getByRole('button', { name: '重新讀取', exact: true }).click();
  await expect(page.locator('.dashboard-stat strong')).toHaveText(['0', '0', '0', '0']);
  await page.getByRole('button', { name: '前往我的教材', exact: true }).click();
  await expect(page).toHaveURL(/\/materials$/);
  await expect(page.getByRole('region', { name: '空教材引導' })).toBeVisible();
});

test("map errors can be retried and an empty map has an actionable explanation", async ({ page }) => {
  await routes(page);
  let failed = true;
  const empty = structureView();
  empty.concepts = [];
  empty.relations = [];
  empty.initial_learning_path = [];
  empty.document_tree.sections = [];
  await page.route("**/v1/materials/*/knowledge-structures/**", route => failed
    ? json(route, { schema: "api-error/v1", request_id: materialId, reason_code: "STORAGE_UNAVAILABLE", retryable: true, message: "Request could not be completed." }, 503)
    : json(route, empty));
  await page.goto(`/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}`);
  await expect(page.getByRole("heading", { name: "無法讀取知識地圖", exact: true })).toBeVisible();
  failed = false;
  await page.getByRole("button", { name: "重新讀取", exact: true }).click();
  await expect(page.getByRole("heading", { name: "知識地圖目前是空的", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "查看處理狀態", exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`/runs/${runId}$`));
});

test("parallel relations retain separate labels, paths and details in both directions", async ({ page }) => {
  const view = structureView();
  view.relations = ["prerequisite", "part_of", "application", "example", "contrast"].map((type, index) => ({
    ...view.relations[0], type,
    relation_id: `relation:sha256:${(index + 20).toString(16).padStart(64, "0")}`,
    source_concept_id: index % 2 ? secondConcept : firstConcept,
    target_concept_id: index % 2 ? firstConcept : secondConcept,
    learner_reason: `Distinct grounded explanation for ${type}.`,
  }));
  await routes(page, view);
  await page.goto(`/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}`);
  const assertSeparate = async () => {
    await expect(page.locator(".concept-flow-edge")).toHaveCount(5);
    await expect.poll(() => page.locator(".concept-flow-edge .react-flow__edge-path").evaluateAll((paths) => new Set(paths.map((path) => path.getAttribute("d"))).size)).toBe(5);
    await expect.poll(() => page.locator(".concept-flow-edge .react-flow__edge-textbg").evaluateAll((labels) => {
      const boxes = labels.map((label) => label.getBoundingClientRect());
      return boxes.length === 5 && boxes.every((a, i) => boxes.every((b, j) => i === j || a.right <= b.left || b.right <= a.left || a.bottom <= b.top || b.bottom <= a.top));
    })).toBe(true);
  };
  await assertSeparate();
  await expect(page.locator(".focus-context-content .relation-list")).toHaveCount(0);
  await expect(page.locator(".focus-relation-summary")).toContainText("5 個直接關係");
  for (const relation of view.relations) {
    await page.locator(`.concept-flow-edge.is-${relation.type} .react-flow__edge-textbg`).click();
    await expect(page.getByRole("dialog", { name: "關係詳情" })).toContainText(relation.learner_reason);
    await expect(page.getByRole("dialog").locator(".relation-direction strong")).toHaveText([
      view.concepts.find(concept => concept.concept_id === relation.source_concept_id)!.label,
      view.concepts.find(concept => concept.concept_id === relation.target_concept_id)!.label,
    ]);
    await assertSeparate();
    if (relation.type === "prerequisite") {
      await page.getByRole("dialog").getByRole("button", { name: /目標概念/ }).click();
      await expect(page.getByRole("dialog", { name: "概念詳情" }).getByRole("heading", { name: "Array", exact: true })).toBeVisible();
    }
    await page.keyboard.press("Escape");
    await expect(page.locator(`.concept-flow-edge.is-${relation.type}`)).toBeFocused();
  }
  await navigationConcept(page, "Array").click();
  await assertSeparate();
  await page.setViewportSize({ width: 900, height: 800 });
  await assertSeparate();
  await page.locator(".concept-flow-edge.is-application").focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("dialog", { name: "關係詳情" })).toContainText("Distinct grounded explanation for application.");
});

test("dense focus fans avoid crossing edges and unrelated cards", async ({ page }) => {
  const view = structureView();
  const focal = view.concepts[0];
  const neighbours = Array.from({ length: 8 }, (_, i) => ({ ...view.concepts[1], concept_id: `concept:sha256:${(i + 40).toString(16).padStart(64, "0")}`, label: `Neighbour ${i + 1}` }));
  view.concepts = [focal, ...neighbours];
  view.document_tree.sections[0].concept_ids = view.concepts.map(node => node.concept_id);
  view.initial_learning_path = view.concepts.map((node, index) => ({ position: index + 1, concept_id: node.concept_id, reason: "document_order" }));
  view.relations = neighbours.flatMap((node, i) => ["example", "application", "part_of"].map((type, j) => ({
    ...view.relations[0], type,
    relation_id: `relation:sha256:${(100 + i * 3 + j).toString(16).padStart(64, "0")}`,
    source_concept_id: i < 4 && j % 2 === 0 ? node.concept_id : focal.concept_id,
    target_concept_id: i < 4 && j % 2 === 0 ? focal.concept_id : node.concept_id,
  })));
  await routes(page, view);
  await page.goto(`/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}`);
  await expect(page.locator(".react-flow__edge-path")).toHaveCount(24);
  await expect.poll(() => page.evaluate(() => {
    const paths = [...document.querySelectorAll<SVGPathElement>(".react-flow__edge-path")];
    const samples = paths.map(path => Array.from({ length: 81 }, (_, i) => {
      const point = path.getPointAtLength(path.getTotalLength() * i / 80);
      return { x: point.x, y: point.y };
    }));
    const cross = (a: { x: number; y: number }, b: { x: number; y: number }, c: { x: number; y: number }) => (b.x - a.x) * (c.y - a.y) - (b.y - a.y) * (c.x - a.x);
    for (let i = 0; i < samples.length; i++) for (let j = i + 1; j < samples.length; j++) {
      for (let a = 1; a < samples[i].length; a++) for (let b = 1; b < samples[j].length; b++) {
        const p = samples[i][a - 1], q = samples[i][a], r = samples[j][b - 1], s = samples[j][b];
        if (cross(p, q, r) * cross(p, q, s) < -0.0001 && cross(r, s, p) * cross(r, s, q) < -0.0001) return false;
      }
    }
    const cards = [...document.querySelectorAll(".react-flow__node")].map(node => node.getBoundingClientRect());
    return paths.every((path, i) => samples[i].slice(1, -1).every(point => {
      const screen = new DOMPoint(point.x, point.y).matrixTransform(path.getScreenCTM()!);
      return cards.every(card => screen.x <= card.left + 1 || screen.x >= card.right - 1 || screen.y <= card.top + 1 || screen.y >= card.bottom - 1);
    }));
  })).toBe(true);
});

test("recovered map reads owned progress and continues the same session without creating learning", async ({ page }) => {
  await page.setViewportSize({ width: 1366, height: 768 });
  await routes(page);
  await page.route(`**/v1/materials/${materialId}`, route => json(route, {
    schema: "material-library-item/v2", material_id: materialId, source_artifact_id: artifactId,
    display_name: "Data structures.pdf", size_bytes: 100, created_at: run.created_at,
    latest_attempt: run, available_structures: [{ run_id: runId, knowledge_structure_revision: structureRevision,
      created_at: run.created_at, status: "succeeded" }], study_sessions: [
      { ...session(), run_id: runId, knowledge_structure_revision: `knowledge-structure:sha256:${"9".repeat(64)}`, started_at: "2026-09-06T00:00:00Z" },
      { ...session(), run_id: runId },
    ],
  }));
  let creates = 0;
  await page.route("**/v1/study-sessions", route => { creates += 1; return json(route, session(), 201); });
  const path = `/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}`;
  await page.goto(path);
  await expect(page.getByRole("button", { name: "繼續本次學習", exact: true })).toBeEnabled();
  await expect(page.getByRole("button", { name: "繼續本次學習", exact: true })).toBeInViewport();
  await page.screenshot({ path: "/tmp/studydy-map-workspace/1366-resumed.png", fullPage: true });
  await page.reload();
  await expect(page.getByRole("button", { name: "繼續本次學習", exact: true })).toBeEnabled();
  await navigationConcept(page, "Array").click();
  await expect(page.getByRole("region", { name: "學習入口" })).toContainText("從「Array」開始新的學習？");
  await page.getByRole("tab", { name: "總覽", exact: true }).click();
  await expect(page.locator(".map-study-bar")).toHaveCount(0);
  await page.getByRole("tab", { name: "複習重點", exact: true }).click();
  await page.getByRole("button", { name: "查看學習導覽", exact: true }).click();
  await navigationConcept(page, "Stack").click();
  await page.getByRole("button", { name: "繼續本次學習", exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`/study-sessions/${sessionId}$`));
  await expect(page.getByRole("button", { name: "開始練習", exact: true })).toBeVisible();
  expect(creates).toBe(0);
});

test("shared shell density keeps standard pages and map workspace bounded", async ({ page }) => {
  await routes(page);
  await page.route("**/v1/materials", route => json(route, { schema: "material-library/v2", materials: [] }));
  const map = `/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}`;
  for (const viewport of [{ width: 1536, height: 1024 }, { width: 390, height: 844 }]) {
    await page.setViewportSize(viewport);
    for (const [name, path, heading] of [
      ["home", "/", "歡迎回來！"], ["materials", "/materials", "我的教材"],
      ["upload", "/upload", "上傳教材"], ["study", `${map}/study-sessions/${sessionId}`, "Stack"],
      ["maps", "/knowledge-maps", "知識地圖"],
      ["detail", `/materials/${materialId}`, "教材詳情"],
      ["processing", `/materials/${materialId}/runs/${runId}`, "教材整理完成"],
      ["map", map, "知識地圖"],
    ]) {
      await page.goto(path);
      await expect(page.locator(".app-header")).toBeVisible();
      if (name === "study") await expect(page.locator(".study-session-page")).toBeVisible();
      else await expect(page.getByRole("heading", { name: heading, exact: true }).first()).toBeVisible();
      expect((await page.locator(".app-header").boundingBox())!.height).toBe(["map", "study"].includes(name) ? 56 : viewport.width > 900 ? 74 : 72);
      if (["map", "study"].includes(name)) await expect(page.locator(".app-sidebar")).toHaveCount(0);
      else {
        await expect(page.locator(".app-sidebar")).toHaveCount(1);
        await expect(page.locator(".brand small")).toHaveText("AI 智慧學習平台");
        await expect(page.locator(".account-avatar")).toHaveCount(1);
      }
      if (!["map", "home", "materials", "maps", "upload", "processing", "study"].includes(name) && viewport.width > 900) await expect(page.locator(".sidebar-helper")).toBeVisible();
      if (["home", "materials", "maps", "upload", "processing", "study"].includes(name)) await expect(page.locator(".sidebar-helper")).toHaveCount(0);
      const widths: Record<string, string> = { home: "1260px", materials: "1260px", maps: "1260px", detail: "1018px", processing: "1180px", upload: "1180px" };
      if (widths[name]) expect(await page.locator(".app-main > *").first().evaluate(element => getComputedStyle(element).maxWidth)).toBe(widths[name]);
      await expect(page.locator(".task-page")).toHaveCount(["upload", "processing"].includes(name) ? 1 : 0);
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(viewport.width);
      await page.screenshot({ path: `/tmp/studydy-dashboard/shell-${name}-${viewport.width}.png`, fullPage: true });
    }
  }
});

function workspaceView(count = 52, longNames = false) {
  const view = structureView();
  const seed = view.concepts[0];
  view.concepts = Array.from({ length: count }, (_, index) => ({ ...seed,
    concept_id: `concept:sha256:${(index + 10).toString(16).padStart(64, "0")}`,
    label: `Concept ${index + 1}${longNames && index % 3 === 0 ? " — 時間與資料結構的跨章節概念_" + "LongTechnicalConceptName".repeat(3) : ""}`,
    aliases: index === 29 ? ["target-alias"] : [],
    section_ids: [`section:sha256:${(Math.floor(index / 6) + 10).toString(16).padStart(64, "0")}`],
    claims: [{ ...seed.claims[0], claim_id: `claim:sha256:${(index + 100).toString(16).padStart(64, "0")}`,
      text: index === 29 ? "A searchable unique learning point." : `Learning point ${index + 1}: read the source and explore how these ideas connect.` }],
  }));
  view.document_tree.sections = Array.from({ length: Math.ceil(count / 6) }, (_, index) => ({
    section_id: view.concepts[index * 6].section_ids[0], title: `Section ${index + 1}`, order: index,
    heading_evidence_id: null, concept_ids: view.concepts.slice(index * 6, index * 6 + 6).map(concept => concept.concept_id),
  }));
  view.initial_learning_path = view.concepts.map((concept, index) => ({ position: index + 1, concept_id: concept.concept_id, reason: "document_order" }));
  view.relations = Array.from({ length: count === 52 ? 47 : count - 1 }, (_, index) => ({ ...view.relations[0],
    relation_id: `relation:sha256:${(index + 100).toString(16).padStart(64, "0")}`,
    source_concept_id: view.concepts[index < 8 ? 0 : index].concept_id,
    target_concept_id: view.concepts[index + 1].concept_id,
    learner_reason: `Connection ${index + 1}: the source explains why these concepts belong together in this section.`,
  }));
  return view;
}

for (const viewport of [{ width: 1920, height: 1080 }, { width: 1536, height: 1024 }, { width: 1366, height: 768 }, { width: 1100, height: 800 }, { width: 390, height: 844 }]) {
  for (const kind of ["small", "large", "long-names"] as const) {
    test(`focus workspace ${kind} at ${viewport.width}px keeps navigation, context and learning accessible`, async ({ page }) => {
      await page.setViewportSize(viewport);
      const view = workspaceView(kind === "small" ? 6 : 52, kind === "long-names");
      await routes(page, view);
      await page.goto(`/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}`);
      const navigator = page.getByRole("navigation", { name: "學習導覽" });
      const context = page.getByRole("complementary", { name: "目前焦點資訊" });
      const graph = page.locator(".focus-graph");
      await expect(navigator).toBeVisible();
      await expect(page.getByText("聚焦目前概念", { exact: true })).toHaveCount(0);
      await expect(page.getByText("顯示所有直接關係", { exact: true })).toHaveCount(0);
      await expect(page.locator(".graph-actions")).toHaveCount(0);
      for (const name of ["放大地圖", "縮小地圖", "顯示完整關係圖"]) await expect(graph.getByRole("button", { name, exact: true })).toBeVisible();
      const studyEntry = page.getByRole("region", { name: "學習入口" });
      await expect(studyEntry).toContainText(`準備開始學習「${view.concepts[0].label}」？`);
      await expect(studyEntry.locator("img")).toHaveCount(0);
      await expect(page.locator(".study-guide")).toHaveCount(0);
      await expect(page.getByRole("combobox", { name: "焦點概念" })).toHaveCount(0);
      await expect(graph.locator(".react-flow__node")).toHaveCount(kind === "small" ? 6 : 9);
      await expect(graph.locator(".concept-flow-edge")).toHaveCount(kind === "small" ? 5 : 8);
      await expect(context.getByRole("heading", { name: view.concepts[0].label, exact: true })).toBeVisible();
      if (viewport.width > 900) {
        await expect(page.locator(".focus-relations")).toHaveCount(0);
        await expect(context.locator(".focus-relation-summary")).toContainText(`${kind === "small" ? 5 : 8} 個直接關係`);
        await expect(context.locator(".focus-context-content .relation-list")).toHaveCount(0);
        await expect(context.locator(".focus-context-content").getByRole("button")).toHaveCount(0);
        await expect(context).toContainText("點選地圖上的概念或連線查看詳細內容。");
        await expect(page.getByRole("button", { name: "開始學習", exact: true })).toBeInViewport();
        const graphBox = (await graph.boundingBox())!;
        const contextBox = (await context.boundingBox())!;
        const entryBox = (await studyEntry.boundingBox())!;
        expect(Math.abs(entryBox.x - graphBox.x)).toBeLessThan(3);
        expect(Math.abs(entryBox.x + entryBox.width - contextBox.x - contextBox.width)).toBeLessThan(3);
        if (kind !== "long-names") expect(entryBox.height).toBeLessThanOrEqual(64);
        expect(await studyEntry.evaluate(element => element.scrollHeight <= element.clientHeight + 1)).toBe(true);
        if (viewport.width >= 1200) expect(entryBox.x).toBeGreaterThan((await navigator.boundingBox())!.x + (await navigator.boundingBox())!.width);
        expect(graphBox.width).toBeGreaterThan(contextBox.width);
        expect(Math.abs(graphBox.y + graphBox.height - contextBox.y - contextBox.height)).toBeLessThan(3);
      } else {
        await expect(graph).toHaveCSS("height", "360px");
        await expect(page.locator(".focus-relations")).not.toHaveAttribute("open", "");
      }
      if (viewport.width < 1200) await navigator.getByRole("button", { name: /學習導覽/ }).click();
      await expect(navigator.locator('[aria-current="true"]')).toBeInViewport();
      if (kind !== "small") expect(await navigator.locator(".navigator-list").evaluate(element => element.scrollHeight > element.clientHeight)).toBe(true);
      if (viewport.width < 1200 && kind === "large") {
        await expect(navigator.locator(".navigator-list")).toBeInViewport();
        await page.screenshot({ path: `/tmp/studydy-map-workspace/${viewport.width}-navigator.png`, fullPage: true });
      }
      if (viewport.width < 1200) await navigator.getByRole("button", { name: /學習導覽/ }).click();
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(viewport.width);
      await page.screenshot({ path: `/tmp/studydy-map-workspace/${viewport.width}-${kind}-context.png`, fullPage: true });
      const pageHeight = await page.evaluate(() => document.documentElement.scrollHeight);
      const detailAction = viewport.width <= 900 ? context.getByRole("button", { name: "查看概念與來源", exact: true }) : page.locator(".concept-flow-node.is-focus");
      await detailAction.click();
      const conceptDetail = page.getByRole("dialog", { name: "概念詳情" });
      await expect(conceptDetail).toBeFocused();
      await expect(conceptDetail.locator(".primary-button, .new-study-note")).toHaveCount(0);
      await expect(page.locator(".map-workspace .primary-button")).toHaveCount(1);
      await expect(page.locator(".focus-study-action")).toBeVisible();
      if (viewport.width > 900) await expect(studyEntry.getByRole("button")).toBeInViewport();
      expect(await conceptDetail.evaluate(element => element.matches(":modal"))).toBe(viewport.width <= 900);
      if (viewport.width <= 900) expect(await page.evaluate(() => document.documentElement.scrollHeight)).toBe(pageHeight);
      expect(await conceptDetail.evaluate(element => element.scrollWidth <= element.clientWidth + 1)).toBe(true);
      await expect(page.locator(".map-content > .detail-panel")).toHaveCount(0);
      await page.screenshot({ path: `/tmp/studydy-map-workspace/${viewport.width}-${kind}-concept.png`, fullPage: true });
      if (viewport.width <= 900) await page.mouse.click(1, 1); else await page.keyboard.press("Escape");
      await expect(detailAction).toBeFocused();
      const relation = await openFocusRelation(page, viewport.width <= 900);
      const relationDetail = page.getByRole("dialog", { name: "關係詳情" });
      await expect(relationDetail).toBeFocused();
      await expect(page.locator(".focus-study-action")).toBeVisible();
      if (viewport.width > 900) await expect(studyEntry.getByRole("button")).toBeInViewport();
      await expect(relationDetail).toContainText(view.relations[0].learner_reason);
      await page.screenshot({ path: `/tmp/studydy-map-workspace/${viewport.width}-${kind}-relation.png`, fullPage: true });
      await page.keyboard.press("Escape");
      await expect(relation).toBeFocused();
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(viewport.width);
    });
  }
}

for (const width of [1920, 1536, 1366, 1100, 390]) {
  test(`52-concept navigation and search share focus without opening detail at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: width === 1366 ? 768 : width === 1920 ? 1080 : width === 1536 ? 1024 : width === 1100 ? 800 : 844 });
    const view = workspaceView();
    await routes(page, view);
    await page.goto(`/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}`);
    const navigator = page.getByRole("navigation", { name: "學習導覽" });
    if (width < 1200) await navigator.getByRole("button", { name: /學習導覽/ }).click();
    const target = navigationConcept(page, "Concept 30");
    await target.click();
    await expect(target).toHaveAttribute("aria-current", "true");
    await expect(target).toBeFocused();
    await expect(page.locator(".concept-flow-node.is-focus")).toContainText("Concept 30");
    await expect(page.locator(".focus-context-heading")).toContainText("Concept 30");
    await expect(page.getByRole("dialog")).toHaveCount(0);
    const inRail = () => target.evaluate(element => {
      const item = element.getBoundingClientRect(), rail = element.closest(".navigator-list")!.getBoundingClientRect();
      return item.top >= rail.top - 1 && item.bottom <= rail.bottom + 1;
    });
    await expect.poll(inRail).toBe(true);
    await navigationConcept(page, "Concept 52").click();
    await expect(page.locator(".react-flow__node")).toHaveCount(1);
    await expect(page.locator(".concept-flow-edge")).toHaveCount(0);
    if (width <= 900) await page.locator(".focus-relations summary").click();
    if (width <= 900) await expect(page.locator(".relation-empty")).toContainText("沒有直接連結");
    else {
      await expect(page.locator(".focus-relation-summary")).toContainText("目前沒有直接關係");
      await expect(page.locator(".focus-context-content .relation-list")).toHaveCount(0);
      await expect(page.getByRole("region", { name: "學習入口" }).getByRole("button")).toBeEnabled();
    }
    await navigationConcept(page, "Concept 52").focus();
    await page.keyboard.press("Tab");
    await expect.poll(() => page.evaluate(() => !!document.activeElement?.closest(".focus-graph"))).toBe(true);
    await page.screenshot({ path: `/tmp/studydy-map-workspace/${width}-isolated.png`, fullPage: true });
    await page.getByRole("tab", { name: "總覽", exact: true }).click();
    const search = page.getByRole("searchbox", { name: "搜尋概念或關鍵字" });
    for (const query of ["Concept 30", "target-alias", "searchable unique"]) {
      await search.fill(query); await search.press("ArrowDown"); await page.keyboard.press("Enter");
      await expect(search).toBeFocused();
      await expect(search).toHaveValue("");
      await expect(page.getByRole("tab", { name: "概念地圖", exact: true })).toHaveAttribute("aria-selected", "true");
      await expect(page.getByRole("dialog")).toHaveCount(0);
      await expect(page.locator(".concept-flow-node.is-focus")).toContainText("Concept 30");
      await expect(page.locator(".focus-context-heading")).toContainText("Concept 30");
    }
    if (width < 1200) await navigator.getByRole("button", { name: /學習導覽/ }).click();
    await expect.poll(inRail).toBe(true);
    await expect(target).toHaveAttribute("aria-current", "true");
    await search.fill("Concept"); await search.press("Escape");
    await expect(search).toHaveValue("");
    await expect(page.locator(".map-search-results")).toHaveCount(0);
  });
}

test("Focus graph utilities and graph-node detail preserve framing and opener", async ({ page }) => {
  await page.setViewportSize({ width: 1536, height: 1024 });
  const view = workspaceView();
  await routes(page, view);
  await page.goto(`/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}`);
  const focal = page.getByRole("button", { name: "教材概念：Concept 1", exact: true });
  await page.getByRole("button", { name: "放大地圖", exact: true }).click();
  const graphBox = (await page.locator(".focus-graph").boundingBox())!;
  await page.mouse.move(graphBox.x + 40, graphBox.y + 40);
  await page.mouse.down();
  await page.mouse.move(graphBox.x + 150, graphBox.y + 120, { steps: 5 });
  await page.mouse.up();
  const zoomed = (await focal.boundingBox())!;
  await page.getByRole("button", { name: "顯示完整關係圖", exact: true }).click();
  await expect.poll(async () => (await focal.boundingBox())!.width).toBeLessThan(zoomed.width);
  await expect.poll(() => page.locator(".focus-graph").evaluate(graph => {
    const bounds = graph.getBoundingClientRect();
    return [...graph.querySelectorAll(".react-flow__node")].every(node => {
      const box = node.getBoundingClientRect();
      return box.left >= bounds.left && box.right <= bounds.right && box.top >= bounds.top && box.bottom <= bounds.bottom;
    });
  })).toBe(true);

  await focal.click();
  await expect(page.getByRole("dialog", { name: "概念詳情" })).toBeFocused();
  await page.getByRole("dialog").locator(".detail-explore summary").click();
  await page.getByRole("dialog").getByRole("button", { name: /Concept 2/, exact: false }).click();
  await expect(page.getByRole("dialog").getByRole("heading", { name: "Concept 2", exact: true })).toBeVisible();
  await expect(page.locator('.navigator-list [aria-current="true"] .navigator-label')).toHaveText("Concept 2");
  await page.keyboard.press("Escape");
  await expect(focal).toBeFocused();
  await expect(page.locator(".focus-context-heading")).toContainText("Concept 2");
});

for (const viewport of [{ width: 1920, height: 1080 }, { width: 1536, height: 1024 }, { width: 1366, height: 768 }, { width: 1100, height: 800 }, { width: 390, height: 844 }]) {
  test(`map progress, keyboard tabs and weak-concept review remain available at ${viewport.width}px`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await routes(page, structureView(), () => ({ ...progress, concept_states: progress.concept_states.map((state, index) => index ? state : { ...state, status: "needs_review", weak_claim_ids: [firstClaim] }) }));
    await page.route(`**/v1/materials/${materialId}`, route => json(route, {
      schema: "material-library-item/v2", material_id: materialId, source_artifact_id: artifactId,
      display_name: "Data structures.pdf", size_bytes: 100, created_at: run.created_at, latest_attempt: run,
      available_structures: [{ run_id: runId, knowledge_structure_revision: structureRevision, created_at: run.created_at, status: "succeeded" }],
      study_sessions: [{ ...session(), run_id: runId }],
    }));
    await page.goto(`/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}`);
    await expect(page.getByRole("button", { name: "繼續本次學習", exact: true })).toBeEnabled();
    if (viewport.width > 900) await expect(page.getByRole("button", { name: "繼續本次學習", exact: true })).toBeInViewport();
    await expect(page.locator(".focus-context .map-learning-badge")).toHaveText("需要複習");
    await page.screenshot({ path: `/tmp/studydy-map-workspace/${viewport.width}-progress.png`, fullPage: true });
    await page.getByRole("tab", { name: "概念地圖", exact: true }).focus();
    await page.keyboard.press("ArrowRight");
    await expect(page.getByRole("tab", { name: "總覽", exact: true })).toBeFocused();
    await expect(page.locator(".overview-index")).toHaveCount(1);
    await expect(page.getByRole("complementary", { name: "Studydy 學習引導" })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "繼續本次學習", exact: true })).toHaveCount(0);
    await expect(page.locator(".focus-study-action")).toHaveCount(0);
    await expect(page.locator(".focus-workspace")).toHaveCount(0);
    await page.keyboard.press("End");
    await expect(page.getByRole("tab", { name: "複習重點", exact: true })).toBeFocused();
    await expect(page.getByRole("complementary", { name: "Studydy 學習引導" })).toHaveCount(0);
    await expect(page.locator(".review-points")).toContainText("A stack follows LIFO order.");
    await page.getByRole("navigation", { name: "需要複習的概念" }).getByRole("button").click();
    await expect(page.locator(".review-context")).toContainText("Stack");
    await expect(page.getByRole("button", { name: "繼續這個概念", exact: true })).toBeVisible();
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await page.getByRole("tab", { name: "複習重點", exact: true }).focus();
    await page.keyboard.press("Home");
    await expect(page.getByRole("tab", { name: "概念地圖", exact: true })).toBeFocused();
  });
}

test("map loading and excluded-page/progress notices survive the Focus layout", async ({ page }) => {
  await page.setViewportSize({ width: 1366, height: 768 });
  const view = structureView();
  view.status = { processing: "partial", quality: "needs_review", decision: "review", reason_codes: ["EXCLUDED_PAGES"] };
  view.excluded_pages = [{ page_ref: `page:sha256:${"3".repeat(64)}`, page: 3, stage: "evidence", reason_code: "NO_USABLE_EVIDENCE" }];
  await routes(page, view);
  await page.route(`**/v1/material-processing-runs/${runId}`, route => json(route, { ...run, status: "partial", completed_pages: 3, total_pages: 3,
    output_binding: { ...run.output_binding, page_count: 3, processing: "partial", quality: "needs_review", decision: "review", reason_codes: ["EXCLUDED_PAGES"] } }));
  let release!: () => void;
  const ready = new Promise<void>(resolve => { release = resolve; });
  await page.route("**/v1/materials/*/knowledge-structures/**", async route => { await ready; return json(route, view); });
  await page.route(`**/v1/materials/${materialId}`, route => json(route, { schema: "api-error/v1", request_id: materialId, reason_code: "STORAGE_UNAVAILABLE", retryable: true, message: "Request could not be completed." }, 503));
  await page.goto(`/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}`);
  await expect(page.getByRole("heading", { name: "正在讀取知識地圖", exact: true })).toBeVisible();
  release();
  await expect(page.getByRole("status").filter({ hasText: "第 3 頁未能整理" })).toBeVisible();
  await expect(page.locator(".partial-banner")).toContainText("暫時無法讀取最近的學習進度");
  await expect(page.getByRole("button", { name: "開始學習", exact: true })).toBeInViewport();
  await page.screenshot({ path: "/tmp/studydy-map-workspace/1366-notices.png", fullPage: true });
  await page.getByRole("tab", { name: "總覽", exact: true }).click();
  await expect(page.getByRole("region", { name: "未能整理的頁面" })).toContainText("第 3 頁未納入概念與練習");
});

test("compact learning entry shares loading, starting and new-study authority with reading tabs", async ({ page }) => {
  const view = structureView();
  await routes(page, view);
  let loaded!: () => void, started!: () => void;
  const readingMaterial = new Promise<void>(resolve => { loaded = resolve; });
  const startingStudy = new Promise<void>(resolve => { started = resolve; });
  let hasHistory = false, completed = false, creates = 0;
  await page.route(`**/v1/materials/${materialId}`, async route => {
    await readingMaterial;
    return json(route, { schema: "material-library-item/v2", material_id: materialId, source_artifact_id: artifactId,
      display_name: "Data structures.pdf", size_bytes: 100, created_at: run.created_at, latest_attempt: run,
      available_structures: [{ run_id: runId, knowledge_structure_revision: structureRevision, created_at: run.created_at, status: "succeeded" }],
      study_sessions: hasHistory ? [{ ...session("completed"), run_id: runId }] : [] });
  });
  await page.route("**/v1/materials/*/knowledge-structures/*/study-sessions/*/resume?*", route => json(route, {
    schema: "study-resume/v1", session: session(completed ? "completed" : "active"), run_id: runId,
    source_artifact_id: artifactId, knowledge_structure: view, progress, assessments: [], selected_assessment_revision: null,
  }));
  await page.route("**/v1/study-sessions", async route => {
    creates++;
    expect(route.request().postDataJSON()).toEqual({ schema: "study-session-create/v2", material_id: materialId,
      knowledge_structure_revision: structureRevision, current_concept_id: firstConcept });
    await startingStudy;
    completed = false;
    return json(route, session(), 201);
  });
  const mapPath = `/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}`;
  await page.goto(mapPath);
  const entry = page.getByRole("region", { name: "學習入口" });
  await expect(entry.getByRole("button", { name: "讀取學習進度…", exact: true })).toBeDisabled();
  loaded();
  await expect(entry.getByRole("button", { name: "開始學習", exact: true })).toBeEnabled();
  await entry.getByRole("button", { name: "開始學習", exact: true }).click();
  await expect(entry.getByRole("button", { name: "正在開始…", exact: true })).toBeDisabled();
  await expect.poll(() => creates).toBe(1);
  started();
  await expect(page).toHaveURL(new RegExp(`/study-sessions/${sessionId}$`));
  await expect(page.getByRole("button", { name: "開始練習", exact: true })).toBeVisible();
  hasHistory = true; completed = true;
  await page.goto(mapPath);
  await expect(entry.getByRole("button", { name: "開始新的學習", exact: true })).toBeEnabled();
  await page.getByRole("tab", { name: "總覽", exact: true }).click();
  await expect(page.locator(".map-study-bar")).toHaveCount(0);
  for (const name of ["複習重點"]) {
    await page.getByRole("tab", { name, exact: true }).click();
    await expect(page.getByRole("complementary", { name: "Studydy 學習引導" })).toHaveCount(0);
    await expect(entry).toHaveCount(0);
    await page.getByRole("button", { name: "查看學習導覽", exact: true }).click();
  }
  await page.getByRole("button", { name: "開始新的學習", exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`/study-sessions/${sessionId}$`));
  expect(creates).toBe(2);
});

for (const viewport of [{ width: 1536, height: 1024 }, { width: 1366, height: 768 }, { width: 390, height: 844 }]) {
  for (const history of ["none", "same", "different"] as const) {
    test(`Focus study targets the selected concept with ${history} session at ${viewport.width}px`, async ({ page }) => {
      await page.setViewportSize(viewport);
      const view = structureView();
      view.concepts[0].label = "指標"; view.concepts[1].label = "陣列";
      view.concepts[0].claims[0].text = view.concepts[0].claims[0].evidence[0].quote = "指標保存記憶體位址。";
      const currentId = history === "same" ? secondConcept : firstConcept;
      const saved = { ...session(), current_concept_id: currentId };
      const savedProgress = { ...progress, current_concept_id: currentId,
        next_action: { ...progress.next_action, target_concept_id: currentId, target_claim_id: history === "same" ? secondClaim : firstClaim } };
      await routes(page, view, () => savedProgress);
      await page.route(`**/v1/materials/${materialId}`, route => json(route, {
        schema: "material-library-item/v2", material_id: materialId, source_artifact_id: artifactId,
        display_name: "Data structures.pdf", size_bytes: 100, created_at: run.created_at, latest_attempt: run,
        available_structures: [{ run_id: runId, knowledge_structure_revision: structureRevision, created_at: run.created_at, status: "succeeded" }],
        study_sessions: history === "none" ? [] : [{ ...saved, run_id: runId }],
      }));
      const newSessionId = "55555555-5555-4555-8555-555555555555";
      const created = { ...session(), study_session_id: newSessionId, current_concept_id: secondConcept };
      const requests: unknown[] = [];
      const writes: string[] = [];
      page.on("request", request => { if (request.url().includes("/v1/study-sessions") && request.method() !== "GET") writes.push(request.method() + " " + new URL(request.url()).pathname); });
      await page.route("**/v1/study-sessions", route => {
        requests.push(route.request().postDataJSON());
        return json(route, created, 201);
      });
      await page.route("**/v1/materials/*/knowledge-structures/*/study-sessions/*/resume?*", route => {
        const isNew = route.request().url().includes(newSessionId);
        return json(route, { schema: "study-resume/v1", session: isNew ? created : saved, run_id: runId,
          source_artifact_id: artifactId, knowledge_structure: view,
          progress: isNew ? { ...savedProgress, study_session_id: newSessionId, current_concept_id: secondConcept,
            next_action: { ...savedProgress.next_action, target_concept_id: secondConcept, target_claim_id: secondClaim } } : savedProgress,
          assessments: [], selected_assessment_revision: null });
      });
      await page.goto(`/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}`);
      const entry = page.getByRole("region", { name: "學習入口" });
      await expect(entry.getByRole("button")).toBeEnabled();
      const navigator = page.getByRole("navigation", { name: "學習導覽" });
      if (viewport.width <= 900) await navigator.getByRole("button", { name: /學習導覽/ }).click();
      await navigationConcept(page, "陣列").click();
      if (viewport.width <= 900) await navigator.getByRole("button", { name: /學習導覽/ }).click();
      const title = history === "same" ? "接著學習「陣列」" : history === "different" ? "從「陣列」開始新的學習？" : "準備開始學習「陣列」？";
      const label = history === "same" ? "繼續本次學習" : history === "different" ? "開始新的學習" : "開始學習";
      await expect(entry).toContainText(title);
      await expect(entry.getByRole("button", { name: label, exact: true })).toBeEnabled();
      await page.screenshot({ path: `/tmp/studydy-map-study-action/${viewport.width}-${history}.png`, fullPage: true });
      await page.getByRole("button", { name: "教材概念：陣列", exact: true }).click();
      const detail = page.getByRole("dialog", { name: "概念詳情" });
      await expect(detail.getByRole("heading", { name: "陣列", exact: true })).toBeVisible();
      for (const name of ["從這個概念開始", "從這裡開始新學習", "繼續這個概念"]) await expect(detail.getByRole("button", { name, exact: true })).toHaveCount(0);
      await expect(detail.getByText("會建立新的學習紀錄，原有紀錄仍保留。", { exact: true })).toHaveCount(0);
      await expect(page.locator(".map-workspace .primary-button")).toHaveCount(1);
      await expect(page.locator(".focus-study-action")).toContainText(title);
      await page.screenshot({ path: `/tmp/studydy-map-study-action/${viewport.width}-${history}-concept.png`, fullPage: viewport.width > 900 });
      await page.keyboard.press("Escape");
      await expect(entry).toContainText(title);
      await openFocusRelation(page, viewport.width <= 900);
      await expect(page.getByRole("dialog", { name: "關係詳情" })).toBeVisible();
      await expect(page.locator(".focus-study-action")).toContainText(title);
      await page.screenshot({ path: `/tmp/studydy-map-study-action/${viewport.width}-${history}-relation.png`, fullPage: viewport.width > 900 });
      await page.keyboard.press("Escape");
      await entry.getByRole("button", { name: label, exact: true }).scrollIntoViewIfNeeded();
      await expect(entry.getByRole("button", { name: label, exact: true })).toBeInViewport();
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(viewport.width);
      await entry.getByRole("button", { name: label, exact: true }).click();
      await expect(page).toHaveURL(new RegExp(`/study-sessions/${history === "same" ? sessionId : newSessionId}$`));
      await expect(page.getByRole("heading", { name: "陣列", level: 1, exact: true })).toBeVisible();
      expect(requests).toEqual(history === "same" ? [] : [{ schema: "study-session-create/v2", material_id: materialId,
        knowledge_structure_revision: structureRevision, current_concept_id: secondConcept }]);
      expect(writes).toEqual(history === "same" ? [] : ["POST /v1/study-sessions"]);
      expect(saved.current_concept_id).toBe(currentId);
    });
  }
}

for (const viewport of [{ width: 1536, height: 1024 }, { width: 1366, height: 768 }, { width: 390, height: 844 }]) {
  for (const kind of ["many", "none", "parallel", "long"] as const) {
    test(`Concept Detail secondary exploration ${kind} at ${viewport.width}px`, async ({ page }) => {
      await page.setViewportSize(viewport);
      const view = workspaceView(kind === "none" ? 1 : kind === "parallel" ? 6 : 9, kind === "long");
      view.concepts[0].aliases = ["Document alias"];
      if (kind === "parallel") view.relations.push(
        { ...view.relations[0], relation_id: `relation:sha256:${"f".repeat(64)}`, type: "contrast", inference_basis: "comparison", learner_reason: "A different perspective on the same concept." },
        { ...view.relations[0], relation_id: `relation:sha256:${"e".repeat(64)}`, source_concept_id: view.concepts[2].concept_id, target_concept_id: view.concepts[3].concept_id },
      );
      await routes(page, view);
      await page.goto(`/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}`);
      const context = page.getByRole("complementary", { name: "目前焦點資訊" });
      if (viewport.width > 900) await expect(context.locator(".focus-relation-summary")).toContainText(kind === "none" ? "目前沒有直接關係" : `${kind === "parallel" ? 6 : 8} 個直接關係`);
      if (kind !== "none") {
        if (kind === "parallel" && viewport.width > 900) {
          await page.locator(".concept-flow-edge").first().focus();
          await page.keyboard.press("Enter");
        } else await openFocusRelation(page, viewport.width <= 900);
        const relationDetail = page.getByRole("dialog", { name: "關係詳情" });
        await expect(relationDetail).toContainText(view.relations[0].learner_reason);
        await expect(relationDetail.getByRole("button", { name: /原始教材第 1 頁/ })).toBeVisible();
        await page.keyboard.press("Escape");
      }
      if (viewport.width <= 900) await context.getByRole("button", { name: "查看概念與來源", exact: true }).click();
      else await page.locator(".concept-flow-node.is-focus").click();
      const detail = page.getByRole("dialog", { name: "概念詳情" });
      const explore = detail.locator(".detail-explore");
      await expect(detail.getByRole("heading", { name: "相關概念", exact: true })).toHaveCount(0);
      await expect(detail.getByRole("heading", { name: "教材重點", exact: true })).toBeVisible();
      await expect(detail.locator(".primary-button")).toHaveCount(0);
      if (kind === "none") await expect(explore).toHaveCount(0);
      else {
        await expect(explore.locator("summary")).toHaveText(`延伸探索${kind === "parallel" ? 5 : 8} 個相關概念`);
        await expect(explore).not.toHaveAttribute("open", "");
        expect(await detail.locator(".page-list").evaluate(element => element.parentElement?.nextElementSibling?.matches(".detail-explore"))).toBe(true);
      }
      await page.screenshot({ path: `/tmp/studydy-detail-explore/${viewport.width}-${kind}-closed.png`, fullPage: viewport.width > 900 });
      if (kind === "none") return;
      const summary = explore.locator("summary");
      await summary.focus(); await page.keyboard.press("Enter");
      await expect(explore).toHaveAttribute("open", "");
      await expect(explore).toContainText("依知識地圖中的直接關係，探索其他概念。");
      await expect(explore).not.toContainText(/連向|來自/);
      await expect(explore).not.toContainText(view.relations[0].learner_reason);
      await expect(explore.locator(".detail-explore-item strong")).toHaveText(view.concepts.slice(1).map(concept => concept.label));
      if (kind === "parallel") {
        const other = explore.getByRole("button", { name: "先備、對照：前往Concept 2", exact: true });
        await expect(other).toHaveCount(1);
        await expect(other.locator("small")).toHaveText("先備、對照");
      }
      expect(await detail.evaluate(element => element.scrollWidth <= element.clientWidth + 1)).toBe(true);
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(viewport.width);
      await page.screenshot({ path: `/tmp/studydy-detail-explore/${viewport.width}-${kind}-expanded.png`, fullPage: viewport.width > 900 });
      await summary.focus(); await page.keyboard.press("Space");
      await expect(explore).not.toHaveAttribute("open", "");
      await page.keyboard.press("Enter");
      await detail.evaluate(element => { element.scrollTop = element.scrollHeight; });
      await explore.getByRole("button").last().click();
      const target = view.concepts.at(-1)!;
      await expect(detail.getByRole("heading", { name: target.label, exact: true })).toBeVisible();
      await expect(detail).toBeFocused();
      await expect.poll(() => detail.evaluate(element => element.scrollTop)).toBe(0);
      await expect(detail.locator(".detail-explore")).not.toHaveAttribute("open", "");
      await expect(page.locator('.navigator-list [aria-current="true"] .navigator-label')).toHaveText(target.label);
      await expect(page.locator(".concept-flow-node.is-focus")).toContainText(target.label);
      await page.screenshot({ path: `/tmp/studydy-detail-explore/${viewport.width}-${kind}-navigated.png`, fullPage: viewport.width > 900 });
    });
  }
}

for (const mode of ["複習重點"] as const) {
  test(`${mode} resumes the selected review concept through the existing study action`, async ({ page }) => {
    await routes(page, structureView(), () => ({ ...progress,
      concept_states: progress.concept_states.map((state, index) => index ? state : { ...state, status: "needs_review", weak_claim_ids: [firstClaim] }),
    }));
    await page.route(`**/v1/materials/${materialId}`, route => json(route, {
      schema: "material-library-item/v2", material_id: materialId, source_artifact_id: artifactId,
      display_name: "Data structures.pdf", size_bytes: 100, created_at: run.created_at, latest_attempt: run,
      available_structures: [{ run_id: runId, knowledge_structure_revision: structureRevision, created_at: run.created_at, status: "succeeded" }],
      study_sessions: [{ ...session(), run_id: runId }],
    }));
    await page.goto(`/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}`);
    await expect(page.getByRole("button", { name: "繼續本次學習", exact: true })).toBeEnabled();
    await page.getByRole("tab", { name: mode, exact: true }).click();
    await expect(page.locator(".review-context")).toContainText("Stack");
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await page.getByRole("button", { name: "繼續這個概念", exact: true }).click();
    await expect(page).toHaveURL(new RegExp(`/study-sessions/${sessionId}$`));
  });
}

test("desktop map-first Inspector follows graph node and edge exploration with keyboard recovery", async ({ page }) => {
  await page.setViewportSize({ width: 1536, height: 1024 });
  const view = workspaceView(8);
  view.concepts[0].label = "陣列";
  view.concepts[1].label = "陣列索引值";
  await routes(page, view);
  await page.goto(`/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}`);
  const context = page.getByRole("complementary", { name: "目前焦點資訊" });
  const summary = context.locator(".focus-context-content");
  await expect(summary.getByRole("heading", { name: "目前焦點", exact: true })).toBeVisible();
  await expect(summary.getByRole("heading", { name: "陣列", exact: true })).toBeVisible();
  await expect(summary).toContainText("7 個直接關係");
  await expect(summary).toContainText("點選地圖上的概念或連線查看詳細內容。");
  await expect(summary.locator(".relation-list, details")).toHaveCount(0);
  await expect(summary.getByRole("button")).toHaveCount(0);
  await page.getByRole("button", { name: "顯示完整關係圖", exact: true }).click();
  await page.getByRole("button", { name: "教材概念：陣列索引值", exact: true }).click();
  await expect(page.getByRole("dialog", { name: "概念詳情" })).toContainText("教材重點");
  await expect(summary).toBeHidden();
  await page.keyboard.press("Escape");
  await expect(summary.getByRole("heading", { name: "陣列索引值", exact: true })).toBeVisible();
  await expect(summary).toContainText("1 個直接關係");
  const edge = await openFocusRelation(page, false);
  await expect(page.getByRole("dialog", { name: "關係詳情" })).toContainText("為什麼有這個關係？");
  await expect(page.getByRole("dialog")).toContainText("教材來源");
  await expect(edge).toHaveClass(/\bselected\b/);
  await page.keyboard.press("Escape");
  await expect(edge).not.toHaveClass(/\bselected\b/);
  await expect(edge).toBeFocused();
  for (const key of ["Enter", "Space"]) {
    const node = page.getByRole("button", { name: "教材概念：陣列索引值", exact: true });
    await node.focus(); await page.keyboard.press(key);
    await expect(page.getByRole("dialog", { name: "概念詳情" })).toBeVisible();
    await page.keyboard.press("Escape"); await expect(node).toBeFocused();
    await edge.focus(); await page.keyboard.press(key);
    await expect(page.getByRole("dialog", { name: "關係詳情" })).toContainText(view.relations[0].learner_reason);
    await page.keyboard.press("Escape"); await expect(edge).toBeFocused();
    await expect(summary.getByRole("heading", { name: "陣列索引值", exact: true })).toBeVisible();
  }
  await expect(page.getByRole("region", { name: "學習入口" })).toContainText("準備開始學習「陣列索引值」？");
});

function learningMap(count: number, reordered = false, longNames = false) {
  const view = workspaceView(count, longNames);
  if (!longNames) view.concepts.slice(0, 4).forEach((concept, index) => { concept.label = ["A", "B", "C", "D"][index]; });
  view.concepts[3].aliases = ["navigation-target"];
  const sections = [0, 1].map(index => ({ section_id: `section:sha256:${String(index + 7).repeat(64)}`,
    title: `Section ${index ? "B" : "A"}`, order: index, heading_evidence_id: null, concept_ids: [] as string[] }));
  view.concepts.forEach((concept, index) => {
    const group = index < 4 ? index % 2 : 2 + Math.floor((index - 4) / 6);
    sections[group] ??= { section_id: `section:sha256:${(group + 50).toString(16).padStart(64, "0")}`, title: `Section ${group + 1}`, order: group, heading_evidence_id: null, concept_ids: [] };
    concept.section_ids = [sections[group].section_id];
    sections[group].concept_ids.push(concept.concept_id);
  });
  view.document_tree.sections = sections;
  const order = reordered ? [2, 0, 1, ...Array.from({ length: count - 3 }, (_, index) => index + 3)] : Array.from({ length: count }, (_, index) => index);
  view.initial_learning_path = order.map((index, position) => ({ position: position + 1, concept_id: view.concepts[index].concept_id,
    reason: position === 1 || position === 2 ? "prerequisite" : "document_order" }));
  const seed = view.relations[0];
  view.relations = order.slice(1).map((target, index) => ({ ...seed,
    relation_id: `relation:sha256:${(index + 300).toString(16).padStart(64, "0")}`,
    source_concept_id: view.concepts[order[index]].concept_id, target_concept_id: view.concepts[target].concept_id,
    type: index < 2 ? "prerequisite" : "example", inference_basis: index < 2 ? "dependency" : "instantiation",
    context_refs: view.concepts[target].section_ids, learner_reason: "教材中的直接關係。",
  }));
  return view;
}

async function learningMapRoutes(page: Page, view: ReturnType<typeof structureView>, hasProgress: boolean, action = "advance", currentIndex = 2, nextIndex = 3) {
  const currentId = view.concepts[currentIndex].concept_id;
  const saved = { ...session(), current_concept_id: currentId };
  const snapshot = { ...progress, current_concept_id: currentId,
    concept_states: view.concepts.map((concept, index) => ({ ...progress.concept_states[0], concept_id: concept.concept_id, label: concept.label,
      status: index < 2 ? "mastered" : index === 2 || index === 5 ? "learning" : index === 4 ? "needs_review" : "not_started",
      mastered_claim_ids: index < 2 ? [concept.claims[0].claim_id] : [], weak_claim_ids: index === 4 ? [concept.claims[0].claim_id] : [] })),
    next_action: { ...progress.next_action, action, target_concept_id: view.concepts[nextIndex].concept_id, target_claim_id: null },
  };
  await routes(page, view, () => snapshot);
  await page.route(`**/v1/materials/${materialId}`, route => json(route, {
    schema: "material-library-item/v2", material_id: materialId, source_artifact_id: artifactId, display_name: "Navigation.pdf", size_bytes: 100,
    created_at: run.created_at, latest_attempt: run, available_structures: [{ run_id: runId, knowledge_structure_revision: structureRevision, status: "succeeded", created_at: run.created_at }],
    study_sessions: hasProgress ? [{ ...saved, run_id: runId }] : [],
  }));
  await page.route("**/v1/materials/*/knowledge-structures/*/study-sessions/*/resume?*", route => json(route, {
    schema: "study-resume/v1", session: saved, run_id: runId, source_artifact_id: artifactId, knowledge_structure: view,
    progress: snapshot, assessments: [], selected_assessment_revision: null,
  }));
}

for (const viewport of [{ width: 1920, height: 1080 }, { width: 1536, height: 1024 }, { width: 1366, height: 768 }, { width: 390, height: 844 }]) {
  for (const count of [8, 56]) for (const hasProgress of [false, true]) {
    test(`learning navigation ${count} concepts ${hasProgress ? "progress" : "new"} at ${viewport.width}px`, async ({ page }) => {
      await page.setViewportSize(viewport);
      const view = learningMap(count, !hasProgress, count === 56 && hasProgress);
      await learningMapRoutes(page, view, hasProgress);
      const writes: string[] = [];
      page.on("request", request => { if (request.url().includes("/v1/study-sessions") && request.method() !== "GET") writes.push(request.method()); });
      await page.goto(`/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}`);
      await expect(page.getByRole("region", { name: "學習入口" }).getByRole("button")).toBeEnabled();
      const navigator = page.getByRole("navigation", { name: "學習導覽" });
      if (viewport.width <= 900) await navigator.getByRole("button", { name: /學習導覽/ }).click();
      const order = hasProgress ? view.concepts : [view.concepts[2], view.concepts[0], view.concepts[1], ...view.concepts.slice(3)];
      await expect(navigator.locator(".navigator-label")).toHaveText(order.map(concept => concept.label));
      await expect(navigator.locator(".navigator-position")).toHaveText(Array.from({ length: count }, (_, index) => String(index + 1)));
      await expect(navigator).not.toContainText("依教材順序");
      await expect(navigator).not.toContainText("已完成");
      await expect(navigator).not.toContainText(/依先備關係安排|建議起點|依教材順序/);
      await expect(navigator.locator(".navigator-list h3")).toHaveCount(0);
      expect(await navigator.locator(".navigator-list button").evaluateAll(buttons => buttons.map(button => button.getAttribute("aria-label")).filter(name => /依先備關係安排|依教材順序|建議起點|prerequisite|document_order/.test(name ?? "")))).toEqual([]);
      if (!hasProgress) {
        await expect(navigator.locator(".navigator-summary")).toHaveText("建議從第 1 個概念開始");
        await expect(navigator.locator(".navigator-notes")).toHaveCount(0);
        await expect(navigator.locator(".is-learning-current, .navigator-state")).toHaveCount(0);
        await expect(navigator).not.toContainText("已掌握");
      } else {
        await expect(navigator.locator(".navigator-summary")).toHaveText(`第 3 / ${count} 個 · 已掌握 2 個`);
        await expect(navigator.locator(".navigator-summary")).toContainText("已掌握 2 個");
        await expect(navigator.locator(".is-mastered .navigator-state")).toHaveCount(2);
        await expect(navigationConcept(page, view.concepts[0].label)).toHaveAttribute("aria-label", /第 1 個，.*已掌握/);
        await expect(navigationConcept(page, view.concepts[4].label)).toHaveAttribute("aria-label", /需要複習/);
        await expect(navigationConcept(page, view.concepts[5].label).locator(".navigator-notes, .navigator-state")).toHaveCount(0);
        await expect(navigator.locator(".navigator-list")).not.toContainText(/已掌握|需要複習|學習中/);
      }
      const target = navigationConcept(page, view.concepts[3].label);
      await target.click();
      await expect(target).toHaveAttribute("aria-current", "true");
      await expect(target).toBeFocused();
      if (hasProgress) {
        await expect(navigationConcept(page, view.concepts[2].label)).toContainText("目前學習");
        await expect(navigationConcept(page, view.concepts[2].label)).not.toHaveAttribute("aria-current", "true");
        await expect(target).not.toContainText("目前學習");
        await expect(target).toContainText("下一步");
        await expect(navigator.locator(".is-learning-current")).toHaveCount(1);
      }
      await expect(page.locator(".concept-flow-node.is-focus")).toContainText(view.concepts[3].label);
      await expect(page.locator(".focus-context-heading")).toContainText(view.concepts[3].label);
      await expect(page.getByRole("region", { name: "學習入口" })).toContainText(view.concepts[3].label);
      await expect(page.getByRole("dialog")).toHaveCount(0);
      expect(writes).toEqual([]);
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(viewport.width);
      await page.screenshot({ path: `/tmp/studydy-learning-navigation/${viewport.width}-${count}-${hasProgress}.png`, fullPage: true });
      if (count === 56) {
        expect(await navigator.locator(".navigator-list").evaluate(element => element.scrollHeight > element.clientHeight)).toBe(true);
        await navigationConcept(page, view.concepts.at(-1)!.label).click();
        const search = page.getByRole("searchbox", { name: "搜尋概念或關鍵字" });
        await search.fill("navigation-target"); await search.press("Enter");
        await expect(search).toBeFocused();
        await expect(target).toHaveAttribute("aria-current", "true");
        await expect.poll(() => target.evaluate(element => {
          const row = element.getBoundingClientRect(), rail = element.closest(".navigator-list")!.getBoundingClientRect();
          return row.top >= rail.top - 1 && row.bottom <= rail.bottom + 1;
        })).toBe(true);
        await expect(target.locator(".navigator-position")).toHaveText("4");
      }
    });
  }
}

for (const action of ["advance", "review_prerequisite", "resume", "assess", "no_safe", "complete", "defer"]) {
  test(`learning navigation only marks a supplied concept-navigation next action: ${action}`, async ({ page }) => {
    const view = learningMap(8);
    await learningMapRoutes(page, view, true, action);
    await page.goto(`/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}`);
    await expect(page.getByRole("button", { name: "繼續本次學習", exact: true })).toBeEnabled();
    await navigationConcept(page, view.concepts[5].label).click();
    const nav = page.getByRole("navigation", { name: "學習導覽" });
    await expect(nav.locator(".is-next-suggested")).toHaveCount(["advance", "review_prerequisite", "resume"].includes(action) ? 1 : 0);
    if (["advance", "review_prerequisite", "resume"].includes(action)) await expect(navigationConcept(page, "D")).toContainText("下一步");
    await expect(navigationConcept(page, "C")).toContainText("目前學習");
    await expect(navigationConcept(page, view.concepts[5].label)).toHaveAttribute("aria-current", "true");
    await expect(navigationConcept(page, view.concepts[5].label)).not.toContainText("目前學習");
  });
}

test("three map tabs cycle and missing path references still fail the strict API contract", async ({ page }) => {
  const view = learningMap(8, true);
  await learningMapRoutes(page, view, false);
  const map = `/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}`;
  await page.goto(map);
  const tabs = page.getByRole("tablist", { name: "知識地圖檢視" }).getByRole("tab");
  await expect(tabs).toHaveText(["概念地圖", "總覽", "複習重點"]);
  await expect(page.getByRole("tab", { name: "學習順序", exact: true })).toHaveCount(0);
  await tabs.first().focus();
  for (const name of ["總覽", "複習重點", "概念地圖"]) {
    await page.keyboard.press("ArrowRight"); await expect(page.getByRole("tab", { name, exact: true })).toBeFocused();
    await expect(page.getByRole("tab", { name, exact: true })).toHaveAttribute("aria-selected", "true");
  }
  await page.keyboard.press("End"); await expect(tabs.last()).toBeFocused();
  await page.keyboard.press("Home"); await expect(tabs.first()).toBeFocused();
  await page.keyboard.press("ArrowLeft"); await expect(tabs.last()).toBeFocused();
  view.initial_learning_path[0].concept_id = `concept:sha256:${"f".repeat(64)}`;
  await page.goto(map);
  await expect(page.getByRole("heading", { name: "無法讀取知識地圖", exact: true })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "學習導覽" })).toHaveCount(0);
});


for (const viewport of [{ width: 1536, height: 1024 }, { width: 1366, height: 768 }, { width: 390, height: 844 }]) {
  test(`flat learning navigation keeps current 4, selected 20 and next 5 distinct at ${viewport.width}px`, async ({ page }) => {
    await page.setViewportSize(viewport);
    const view = learningMap(56);
    await learningMapRoutes(page, view, true, "advance", 3, 4);
    const pathBefore = structuredClone(view.initial_learning_path);
    const map = `/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}`;
    await page.goto(map);
    await expect(page.getByRole("button", { name: "繼續本次學習", exact: true })).toBeEnabled();
    const navigator = page.getByRole("navigation", { name: "學習導覽" });
    if (viewport.width <= 900) await navigator.getByRole("button", { name: /學習導覽/ }).click();
    await expect(navigator.locator(".navigator-list").getByRole("heading")).toHaveCount(0);
    await expect(navigator.locator(".navigator-summary")).toHaveText("第 4 / 56 個 · 已掌握 2 個");
    await expect(navigationConcept(page, view.concepts[3].label)).toContainText("目前學習");
    await expect(navigationConcept(page, view.concepts[4].label)).toContainText("下一步");
    await expect(navigator.getByText("下一步", { exact: true })).toHaveCount(1);
    await page.screenshot({ path: `/tmp/studydy-learning-density/${viewport.width}-current-next.png`, fullPage: true });
    const selected = navigationConcept(page, view.concepts[19].label);
    await selected.click();
    await expect(selected).toHaveAttribute("aria-current", "true");
    await expect(selected.locator(".navigator-position")).toHaveText("20");
    await expect(selected.locator(".navigator-notes")).toHaveCount(0);
    await expect(navigator.locator(".is-learning-current .navigator-position")).toHaveText("4");
    await expect(navigator.locator(".is-next-suggested .navigator-position")).toHaveText("5");
    await expect(navigator.locator(".navigator-summary")).toHaveText("第 4 / 56 個 · 已掌握 2 個");
    await expect(page.locator(".concept-flow-node.is-focus")).toContainText(view.concepts[19].label);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(viewport.width);
    expect(view.initial_learning_path).toEqual(pathBefore);
    expect(view.initial_learning_path[1].reason).toBe("prerequisite");
    await page.screenshot({ path: `/tmp/studydy-learning-density/${viewport.width}-selected-20.png`, fullPage: true });
    await learningMapRoutes(page, view, true, "advance", 3, 3);
    await page.goto(map);
    await expect(page.getByRole("button", { name: "繼續本次學習", exact: true })).toBeEnabled();
    if (viewport.width <= 900) await navigator.getByRole("button", { name: /學習導覽/ }).click();
    await expect(navigator.getByText("目前學習", { exact: true })).toHaveCount(1);
    await expect(navigator.getByText("下一步", { exact: true })).toHaveCount(0);
    await expect(navigator.locator(".is-learning-current")).not.toHaveAttribute("aria-label", /下一步/);
    await expect(navigator.locator(".is-next-suggested")).toHaveCount(0);
  });
}

function documentIndexView(count: number) {
  const view = workspaceView(count + 2);
  const sections = Array.from({ length: count }, (_, index) => ({
    section_id: `section:sha256:${(index + 700).toString(16).padStart(64, "0")}`,
    title: `教材来源段落 ${index + 1}`, order: index, heading_evidence_id: null,
    concept_ids: index === 0 || (count === 64 && index > 41) ? [] : [view.concepts[index].concept_id],
  }));
  sections[1].title = "本份教材大綱"; view.concepts[1].label = "陣列與字串教材";
  sections[2].title = "陣列"; view.concepts[2].label = "陣列";
  sections[3].title = "依序為陣列設值"; sections[3].concept_ids.push(view.concepts[count].concept_id);
  sections[4].title = "  ARRAY   Index  "; view.concepts[4].label = "array index";
  sections[5].title = "• 一維陣列的宣告方式：";
  sections[6].title = "C 語言的字串 (3/4)_" + "LongSourceTitle".repeat(6);
  view.concepts[6].label += "_" + "LongConceptName".repeat(5);
  view.document_tree.sections = [...sections].reverse();
  view.excluded_pages = [{ page_ref: `page:sha256:${"9".repeat(64)}`, page: 9, stage: "evidence", reason_code: "NO_USABLE_EVIDENCE" }];
  view.status = { processing: "partial", quality: "needs_review", decision: "review", reason_codes: ["EXCLUDED_PAGES"] };
  return { view, sections };
}

for (const viewport of [{ width: 1920, height: 1080 }, { width: 1536, height: 1024 }, { width: 1366, height: 768 }, { width: 1100, height: 800 }, { width: 390, height: 844 }]) {
  for (const count of [8, 64]) {
    test(`compact document index ${count} raw sections at ${viewport.width}px`, async ({ page }) => {
      await page.setViewportSize(viewport);
      const { view, sections } = documentIndexView(count);
      const original = structuredClone(view.document_tree);
      await learningMapRoutes(page, view, count === 64);
      const writes: string[] = [];
      page.on("request", request => { if (request.method() !== "GET" && request.url().includes("/v1/study-sessions")) writes.push(request.method()); });
      await page.goto(`/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}`);
      await expect(page.getByRole("region", { name: "學習入口" }).getByRole("button")).toBeEnabled();
      await page.getByRole("searchbox", { name: "搜尋概念或關鍵字" }).fill(view.concepts[6].label);
      await page.getByRole("searchbox", { name: "搜尋概念或關鍵字" }).press("Enter");
      await page.getByRole("tab", { name: "總覽", exact: true }).click();
      const overview = page.locator(".is-overview-mode");
      const index = page.getByRole("region", { name: "教材結構", exact: true });
      const list = index.locator(".overview-index-list");
      await expect(index).toHaveCount(1);
      await expect(overview.locator(".overview-master, .overview-section-detail, .overview-section-picker, .overview-browser, .chapter-card, .concept-grid, progress")).toHaveCount(0);
      await expect(overview.locator(".map-study-bar, .focus-study-action")).toHaveCount(0);
      await expect(overview.getByRole("button", { name: /^(開始本次學習|開始新的學習|繼續本次學習)$/ })).toHaveCount(0);
      await expect(index).not.toContainText(/已掌握|學習中|Learning point|查看段落概念|探索這個段落/);
      await expect(index).toContainText(`${count === 64 ? 41 : 7} 個可探索段落`);
      await expect(index.locator(".overview-index-number")).toHaveText(sections.filter(section => section.concept_ids.length).map(section => String(section.order + 1).padStart(2, "0")));
      expect(await list.evaluate(element => element.scrollTop)).toBe(0);
      const same = index.getByRole("button", { name: "教材段落 3，陣列，在概念地圖中查看", exact: true });
      await expect(same).toHaveText("03陣列");
      const normalized = index.getByRole("button", { name: /教材段落 5，/ });
      await expect(normalized).not.toContainText("array index");
      await expect(normalized).not.toHaveAttribute("aria-label", /概念：/);
      const different = index.getByRole("button", { name: "教材段落 2，本份教材大綱，概念：陣列與字串教材，在概念地圖中查看", exact: true });
      await expect(different).toContainText("本份教材大綱"); await expect(different).toContainText("陣列與字串教材");
      await expect(index.getByText("• 一維陣列的宣告方式：", { exact: true })).toBeVisible();
      const disclosure = index.locator("details");
      await expect(disclosure).not.toHaveAttribute("open", "");
      await expect(disclosure.getByRole("button")).toHaveCount(0);
      if (viewport.width > 900 && count === 64) {
        expect(await list.evaluate(element => element.scrollHeight > element.clientHeight)).toBe(true);
        expect(await list.evaluate(element => element.clientHeight / 48)).toBeGreaterThanOrEqual(10);
      }
      if (viewport.width <= 900) {
        await expect(list).toHaveCSS("overflow-y", "visible");
        expect(await list.evaluate(element => element.scrollHeight === element.clientHeight)).toBe(true);
      }
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(viewport.width);
      await page.screenshot({ path: `/tmp/studydy-overview-index/${viewport.width}-${count}-closed.png`, fullPage: true });
      await disclosure.locator("summary").focus(); await page.keyboard.press("Space");
      await expect(disclosure).toHaveAttribute("open", "");
      await expect(disclosure.getByRole("button")).toHaveCount(2);
      await page.screenshot({ path: `/tmp/studydy-overview-index/${viewport.width}-${count}-expanded.png`, fullPage: true });
      await disclosure.locator("summary").press("Enter");
      await expect(disclosure).not.toHaveAttribute("open", "");
      await same.click();
      await expect(page.getByRole("tab", { name: "概念地圖", exact: true })).toBeFocused();
      await expect(page.locator(".concept-flow-node.is-focus")).toContainText("陣列");
      await expect(page.getByRole("dialog")).toHaveCount(0);
      await page.getByRole("tab", { name: "總覽", exact: true }).click();
      await different.click();
      await expect(page.locator('.navigator-list [aria-current="true"] .navigator-label')).toHaveText("陣列與字串教材");
      await expect(page.locator(".focus-context-heading")).toContainText("陣列與字串教材");
      await expect(page.getByRole("region", { name: "學習入口" })).toContainText("陣列與字串教材");
      await page.getByRole("tab", { name: "總覽", exact: true }).click();
      await disclosure.locator("summary").click();
      await disclosure.getByRole("button").last().click();
      await expect(page.locator(".concept-flow-node.is-focus")).toContainText(view.concepts[count].label);
      await expect(page.getByRole("dialog")).toHaveCount(0);
      await page.getByRole("tab", { name: "總覽", exact: true }).click();
      if (viewport.width > 900) {
        const beforeScroll = await page.evaluate(() => window.scrollY);
        await list.evaluate(element => { element.scrollTop = element.scrollHeight; });
        expect(await page.evaluate(() => window.scrollY)).toBe(beforeScroll);
      }
      await index.getByRole("button").last().click();
      await expect(page.getByRole("tab", { name: "概念地圖", exact: true })).toHaveAttribute("aria-selected", "true");
      await page.getByRole("tab", { name: "總覽", exact: true }).click();
      await expect(page.getByRole("region", { name: "未能整理的頁面" })).toContainText("第 9 頁未納入概念與練習");
      expect(writes).toEqual([]); expect(view.document_tree).toEqual(original);
    });
  }
}

test("document index reports missing references and preserves excluded pages with no sections", async ({ page }) => {
  const { view, sections } = documentIndexView(8);
  sections[1].concept_ids = [`concept:sha256:${"f".repeat(64)}`];
  await routes(page, view);
  const map = `/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}`;
  await page.goto(map); await page.getByRole("tab", { name: "總覽", exact: true }).click();
  await expect(page.locator(".overview-index").getByRole("alert")).toContainText("概念資料不完整");
  await expect(page.locator(".overview-index-list ol > li").first().getByRole("button")).toHaveCount(0);
  sections.forEach(section => { section.concept_ids = []; });
  await page.goto(map); await page.getByRole("tab", { name: "總覽", exact: true }).click();
  await expect(page.getByRole("heading", { name: "目前沒有可探索的教材段落", exact: true })).toBeVisible();
  await expect(page.locator(".overview-index")).toHaveCount(0);
  await expect(page.getByRole("region", { name: "未能整理的頁面" })).toContainText("第 9 頁");
});

async function openStudyRecord(page: Page, record: AssessmentRecordView) {
  const history = page.locator(".study-record-picker");
  if (await history.getAttribute("open") === null) await history.locator("summary").click();
  await history.locator(".study-history-row").filter({ has: page.getByText(record.assessment.prompt, { exact: true }) }).click();
}

async function returnToCurrentStudy(page: Page) {
  await page.getByRole("button", { name: "返回最新進度", exact: true }).click();
}

async function studyWorkflowFixture(page: Page) {
  const view = workspaceView(56);
  const current = view.concepts[1];
  current.label = "陣列";
  current.claims = [0, 1, 2].map(index => ({ ...current.claims[0], claim_id: `claim:sha256:${(900 + index).toString(16).padStart(64, "0")}`,
    text: ["陣列使用連續空間存放元素。", "陣列中的元素以索引值標示位置。", "索引值從零開始，存取時需注意範圍。"][index] }));
  const state = {
    ...structuredClone(progress), current_concept_id: current.concept_id,
    concept_states: view.concepts.map(concept => ({ ...structuredClone(progress.concept_states[0]), concept_id: concept.concept_id, label: concept.label })),
    next_action: { ...progress.next_action, target_concept_id: current.concept_id, target_claim_id: current.claims[1].claim_id },
  };
  let status = "active";
  const records: AssessmentRecordView[] = [];
  const creates: { body: unknown; key: string }[] = [];
  const applies: unknown[] = [];
  const answers: unknown[] = [];
  const resumeSelections: (string | null)[] = [];
  let completions = 0;
  let waitForQuestion: Promise<void> = Promise.resolve();
  let waitForProgress: Promise<void> = Promise.resolve();
  let noSafeNext = false;
  await routes(page, view, () => state, () => records);
  await page.route("**/v1/materials/*/knowledge-structures/*/study-sessions/*/resume?*", async route => {
    await waitForProgress;
    const query = new URL(route.request().url()).searchParams;
    expect(query.get("run_id")).toBe(runId);
    const explicit = query.get("assessment_revision"); resumeSelections.push(explicit);
    const selected = explicit ?? records.find(record => status === "completed" || record.assessment.target_concept_id === state.current_concept_id)?.assessment.assessment_revision ?? null;
    return json(route, { schema: "study-resume/v1", session: { ...session(status), current_concept_id: state.current_concept_id, event_watermark: state.event_watermark, deferred_concept_ids: state.deferred_concept_ids,
      no_safe_claim_ids: status === "no_safe" ? [current.claims[1].claim_id] : [] },
      run_id: runId, source_artifact_id: artifactId, knowledge_structure: view, progress: state,
      assessments: records.map(record => ({ ...record, can_submit: !record.feedback && status !== "completed" && record.assessment.target_concept_id === state.current_concept_id })), selected_assessment_revision: selected });
  });
  await page.route(`**/v1/study-sessions/${sessionId}/assessments`, async route => {
    creates.push({ body: route.request().postDataJSON(), key: route.request().headers()["idempotency-key"] });
    await waitForQuestion;
    if (noSafeNext) {
      status = "no_safe"; state.next_action.action = "no_safe"; state.next_action.reason = "no_safe_assessment";
      return json(route, { schema: "api-error/v1", request_id: sessionId, reason_code: "NO_SAFE_ASSESSMENT", retryable: false, message: "Request could not be completed." }, 422);
    }
    const concept = view.concepts.find(item => item.concept_id === state.current_concept_id)!;
    const assessment = {
      schema: "single-choice-assessment/v2" as const, assessment_revision: `assessment:sha256:${(1000 + creates.length).toString(16).padStart(64, "0")}`,
      study_session_id: sessionId, knowledge_structure_revision: structureRevision,
      question_id: `question:sha256:${(1100 + creates.length).toString(16).padStart(64, "0")}`,
      target_concept_id: concept.concept_id, target_claim_id: route.request().postDataJSON().target_claim_id,
      source_evidence_ids: [concept.claims[0].evidence[0].evidence_id], question_type: "single_choice" as const,
      prompt: `練習 ${creates.length}：依教材內容，何者描述正確？`,
      options: ["元素依序存放在連續空間", "每個元素都不需要索引值", "陣列大小可忽略存取範圍", "任何位置都能安全存取"].map((text, i) => ({ option_id: `option:sha256:${String(i + 1).repeat(64)}`, text })),
    };
    records.unshift({ assessment, feedback: null, can_submit: true, created_at: run.created_at });
    return json(route, assessment, 201);
  });
  await page.route(`**/v1/study-sessions/${sessionId}/assessments/*/submissions`, route => {
    const body = route.request().postDataJSON(); answers.push(body);
    expect(route.request().headers()["idempotency-key"]).toBeTruthy();
    const record = records.find(record => record.assessment.question_id === body.question_id)!;
    const correct = body.selected_option_id === record.assessment.options[0].option_id;
    record.feedback = { schema: "answer-feedback/v2", answer_event_id: "55555555-5555-4555-8555-555555555555", study_session_id: sessionId,
      assessment_revision: record.assessment.assessment_revision, question_id: record.assessment.question_id, selected_option_id: body.selected_option_id,
      is_correct: correct, rationale: "教材指出元素依序存放，需使用有效索引位置。", source_evidence_ids: record.assessment.source_evidence_ids, event_number: ++state.event_watermark, created_at: run.created_at };
    record.can_submit = false;
    const conceptState = state.concept_states.find(item => item.concept_id === state.current_concept_id)!;
    Object.assign(conceptState, { status: correct ? "learning" : "needs_review", attempts: 1, correct_answers: correct ? 1 : 0, qualified_correct_items: correct ? 1 : 0,
      covered_claim_ids: [record.assessment.target_claim_id], latest_is_correct: correct });
    Object.assign(state, { weaknesses: correct ? [] : [{ concept_id: state.current_concept_id, claim_ids: [record.assessment.target_claim_id], reason: "recent_incorrect" }],
      next_action: { action: correct ? "advance" : "review_prerequisite", target_concept_id: correct ? view.concepts[2].concept_id : current.concept_id,
        target_claim_id: correct ? view.concepts[2].claims[0].claim_id : current.claims[0].claim_id, prerequisite_concept_ids: [], reason: correct ? "current_mastered" : "canonical_prerequisite_gap" } });
    return json(route, record.feedback, 201);
  });
  await page.route(`**/v1/study-sessions/${sessionId}/guidance/apply`, route => {
    applies.push(route.request().postDataJSON());
    expect(route.request().postDataJSON()).toEqual({ schema: "guidance-apply/v2", guidance_revision: state.guidance_revision });
    if (state.next_action.action === "complete") status = "completed";
    else state.current_concept_id = state.next_action.target_concept_id!;
    state.next_action = { ...progress.next_action, target_concept_id: state.current_concept_id, target_claim_id: view.concepts.find(item => item.concept_id === state.current_concept_id)!.claims[0].claim_id };
    return json(route, state);
  });
  await page.route(`**/v1/study-sessions/${sessionId}/complete`, route => { completions++; status = "completed"; return json(route, session(status)); });
  await page.context().route(`**/v1/artifacts/${artifactId}`, route => route.fulfill({ contentType: "text/plain", body: "Synthetic source" }));
  return { view, state, records, creates, answers, applies, resumeSelections, completions: () => completions,
    setStatus: (next: string) => { status = next; },
    failNextAssessment: () => { noSafeNext = true; },
    holdProgress: () => { let release!: () => void; waitForProgress = new Promise<void>(resolve => { release = resolve; }); return release; }, hold: () => { let release!: () => void; waitForQuestion = new Promise<void>(resolve => { release = resolve; }); return release; } };
}

for (const viewport of [{ width: 1920, height: 1080 }, { width: 1536, height: 1024 }, { width: 1366, height: 768 }, { width: 1100, height: 800 }, { width: 390, height: 844 }]) {
  test(`Study workflow read practice feedback guidance history and completion at ${viewport.width}px`, async ({ page }) => {
    await page.setViewportSize(viewport);
    const fixture = await studyWorkflowFixture(page);
    const map = `/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}`;
    const study = `${map}/study-sessions/${sessionId}`;
    await page.goto(study);
    const snap = async (name: string) => { expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(viewport.width); await page.screenshot({ path: `/tmp/studydy-study-workflow/${viewport.width}-${name}.png`, fullPage: true }); };
    await expect(page.locator(".study-header")).toContainText("第 2 / 56 個概念");
    await expect(page.locator(".study-header")).not.toContainText(/Session|Map|Path|prerequisite/);
    await expect(page.locator(".sidebar-helper, .session-path, .current-concept-card img, .learning-insights, .adaptive-card, .study-record-picker")).toHaveCount(0);
    await expect(page.getByRole("region", { name: "教材來源" }).getByRole("button")).toHaveCount(1);
    await expect(page.locator(".claim-picker, .assessment-claim-picker, .study-header-actions, .study-finish-confirmation")).toHaveCount(0);
    await expect(page.getByRole("radio")).toHaveCount(0);
    if (viewport.width >= 1200) {
      await expect(page.getByRole("button", { name: "開始練習", exact: true })).toBeInViewport();
      await expect(page.getByRole("region", { name: "教材來源" })).toBeInViewport();
    }
    await snap("new");
    const release = fixture.hold();
    await page.getByRole("button", { name: "開始練習", exact: true }).click();
    await expect(page.getByRole("heading", { name: "正在準備練習題" })).toBeVisible();
    await expect(page.locator(".assessment-elapsed")).toContainText("已等待");
    await snap("loading"); release();
    await expect(page.getByRole("heading", { name: /練習 1：/ })).toBeVisible();
    expect(fixture.creates[0].body).toEqual({ schema: "assessment-create/v2", target_claim_id: fixture.view.concepts[1].claims[1].claim_id });
    expect(fixture.creates[0].key).toBeTruthy();
    await expect(page).toHaveURL(study);
    await snap("question");
    await page.getByRole("radio").first().check(); await page.getByRole("button", { name: "送出答案" }).click();
    await expect(page.getByRole("heading", { name: "可以繼續下一個重點" })).toBeVisible();
    await expect(page.locator(".learning-insights")).toContainText("作答 1 次 · 答對 1 次 · 已練習 1 個重點");
    await expect(page.locator(".learning-insights")).not.toContainText("有效答對題數");
    await expect(page.getByRole("heading", { name: "可以繼續下一個重點" })).toBeVisible();
    await expect(page.getByRole("button", { name: "繼續練習" })).toHaveCount(0);
    await expect(page.locator(".study-record-picker")).not.toHaveAttribute("open", "");
    await snap("correct-advance");
    await openStudyRecord(page, fixture.records[0]);
    await expect(page.getByRole("heading", { name: "答對了", exact: true })).toBeVisible();
    await expect(page.locator(".adaptive-card")).toHaveCount(0);
    const popup = page.waitForEvent("popup"); await page.locator(".feedback-evidence button").click();
    const source = await popup; await source.waitForLoadState(); expect(source.url()).toContain(`/v1/artifacts/${artifactId}`); await source.close();
    await returnToCurrentStudy(page);
    await page.getByRole("button", { name: "繼續學習", exact: true }).click();
    await expect(page.locator(".study-header h1")).toHaveText(fixture.view.concepts[2].label);
    await expect(page).toHaveURL(study);
    await expect(page.locator(".study-header")).toContainText("第 3 / 56 個概念");
    await expect(page.locator(".adaptive-card, .learning-insights")).toHaveCount(0);
    await page.getByRole("button", { name: "開始練習", exact: true }).click();
    await expect(page.getByRole("heading", { name: /練習 2：/ })).toBeVisible();
    await page.getByRole("radio").nth(1).check(); await page.getByRole("button", { name: "送出答案" }).click();
    await expect(page.getByRole("heading", { name: "建議先補強前置概念" })).toBeVisible();
    await expect(page.locator(".learning-insights .learning-status")).toHaveText("需要複習");
    await expect(page.locator(".weakness-card")).toHaveCount(0);
    await expect(page.locator(".adaptive-card")).toContainText("建議先補強前置概念");
    await expect(page.locator(".adaptive-card")).not.toContainText(/Session|Map|Path|prerequisite/);
    await snap("incorrect-weakness");
    await page.locator(".study-record-picker summary").click();
    await openStudyRecord(page, fixture.records[1]);
    await expect(page.getByRole("heading", { name: "答對了", exact: true })).toBeVisible();
    await page.reload(); await expect(page.getByRole("heading", { name: "答對了", exact: true })).toBeVisible();
    await page.locator(".study-record-picker summary").click(); await snap("history");
    await page.getByRole("button", { name: "返回最新進度" }).click();
    await expect(page.getByRole("heading", { name: "建議先補強前置概念" })).toBeVisible();
    expect(fixture.resumeSelections).toContain(null);
    await page.getByRole("button", { name: "前往前置概念" }).click();
    await expect(page.locator(".study-header h1")).toHaveText("陣列");
    expect(fixture.applies).toHaveLength(2);
    await expect(page.getByRole("button", { name: "結束本次學習" })).toHaveCount(0);
    Object.assign(fixture.state.next_action, { action: "complete", target_concept_id: null, target_claim_id: null, reason: "all_mastered" });
    await page.goto(study);
    await expect(page.getByRole("heading", { name: "本次學習內容已完成" })).toBeVisible();
    await snap("complete-guidance");
    await page.getByRole("button", { name: "完成學習", exact: true }).click();
    await expect(page.getByRole("heading", { name: "本次學習已完成", exact: true })).toBeVisible();
    expect(fixture.completions()).toBe(0);
    await expect(page.getByRole("button", { name: "結束本次學習", exact: true })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "繼續練習" })).toHaveCount(0);
    await snap("completed");
    await page.getByRole("navigation", { name: "學習工作區導覽" }).getByRole("button", { name: "知識地圖", exact: true }).click(); await expect(page).toHaveURL(map);
  });
}

for (const viewport of [{ width: 1920, height: 1080 }, { width: 1536, height: 1024 }, { width: 1366, height: 768 }, { width: 1100, height: 800 }, { width: 390, height: 844 }]) {
  test(`Study no-safe and long material content at ${viewport.width}px`, async ({ page }) => {
    await page.setViewportSize(viewport);
    const fixture = await studyWorkflowFixture(page);
    fixture.failNextAssessment();
    fixture.state.deferred_concept_ids = [fixture.view.concepts[0].concept_id];
    fixture.view.concepts[1].claims[2].text = "很長的教材說明，保留原有文字並正常換行。".repeat(12) + "LongUnbrokenMaterialText".repeat(6);
    await page.goto(`/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}/study-sessions/${sessionId}`);
    await page.getByRole("button", { name: "開始練習", exact: true }).click();
    await expect(page.locator(".adaptive-card")).toHaveCount(0);
    await expect(page.locator(".evidence-review-activity")).toContainText("改用教材回顧");
    await expect(page.locator(".learning-insights")).toHaveCount(0);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(viewport.width);
    await page.screenshot({ path: `/tmp/studydy-study-workflow/${viewport.width}-no-safe-long.png`, fullPage: true });
    await page.reload(); await expect(page.getByRole("heading", { name: "目前沒有適合的新題目" })).toBeVisible();
    expect(fixture.answers).toHaveLength(0);
    expect(fixture.state.deferred_concept_ids).toEqual([fixture.view.concepts[0].concept_id]);
  });
}

for (const [action, title, button] of [
  ["defer", "先前往下一個可學習的重點", "暫緩並繼續"],
  ["resume", "回到先前保留的重點", "回到保留重點"],
  ["complete", "本次學習內容已完成", "完成學習"],
]) {
  test(`Study ${action} retains backend guidance authority without an assessment route`, async ({ page }) => {
    const fixture = await studyWorkflowFixture(page);
    fixture.state.next_action.action = action;
    fixture.state.next_action.target_concept_id = fixture.view.concepts[2].concept_id;
    const study = `/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}/study-sessions/${sessionId}`;
    await page.goto(study);
    await expect(page.getByRole("heading", { name: title, exact: true })).toBeVisible();
    await page.getByRole("button", { name: button, exact: true }).click();
    await expect(page.locator(".study-header h1")).toHaveText(action === "complete" ? "本次學習已完成" : fixture.view.concepts[2].label);
    expect(fixture.applies).toHaveLength(1); expect(fixture.creates).toHaveLength(0); expect(fixture.completions()).toBe(0);
    await expect(page).toHaveURL(study);
  });
}

test("Materials resumes the exact saved study and completed unanswered records stay read-only", async ({ page }) => {
  const fixture = await studyWorkflowFixture(page);
  const map = `/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}`;
  const study = `${map}/study-sessions/${sessionId}`;
  await page.route("**/v1/materials", route => json(route, { schema: "material-library/v2", materials: [{
    schema: "material-library-item/v2", material_id: materialId, source_artifact_id: artifactId, display_name: "Synthetic session material.pdf", size_bytes: 100,
    created_at: run.created_at, latest_attempt: run, available_structures: [{ run_id: runId, knowledge_structure_revision: structureRevision, created_at: run.created_at, status: "succeeded" }],
    study_sessions: [{ ...session(), current_concept_id: fixture.state.current_concept_id, run_id: runId }],
  }] }));
  await page.goto("/materials"); await page.getByRole("button", { name: "接續上次學習", exact: true }).click();
  await expect(page).toHaveURL(study);
  await expect(page.locator(".study-header h1")).toHaveText("陣列");
  await page.getByRole("button", { name: "開始練習", exact: true }).click();
  await expect(page.getByRole("heading", { name: /練習 1：/ })).toBeVisible();
  const revision = fixture.records[0].assessment.assessment_revision;
  await page.reload(); await expect(page).toHaveURL(study);
  expect(fixture.records[0].assessment.assessment_revision).toBe(revision);
  fixture.setStatus("completed"); // A saved, administratively completed record remains readable.
  await page.reload();
  expect(fixture.completions()).toBe(0);
  await expect(page.getByRole("heading", { name: "本次學習已完成", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "送出答案", exact: true })).toHaveCount(0);
  for (const radio of await page.getByRole("radio").all()) await expect(radio).toBeDisabled();
  expect(fixture.answers).toHaveLength(0);
  await page.getByRole("navigation", { name: "學習工作區導覽" }).getByRole("button", { name: "知識地圖", exact: true }).click(); await expect(page).toHaveURL(map);
  await page.route("**/v1/materials/*/knowledge-structures/*/study-sessions/*/resume?*", route => json(route, { schema: "api-error/v1", request_id: sessionId, reason_code: "RESOURCE_NOT_FOUND", retryable: false, message: "Request could not be completed." }, 404));
  await page.goto(study);
  await expect(page.getByRole("heading", { name: "無法開啟本次學習", exact: true })).toBeVisible();
  await expect(page.locator(".current-concept-card")).toHaveCount(0);
});

for (const width of [1100, 390]) {
  test(`Study long claims and options keep the newly created question reachable at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 844 });
    const fixture = await studyWorkflowFixture(page);
    fixture.view.concepts[1].claims[2].text = "教材中的長說明需保留並自然換行。".repeat(16);
    const evidence = fixture.view.concepts[1].claims[2].evidence[0];
    fixture.view.concepts[1].claims[2].evidence.push({ ...evidence, evidence_id: `evidence:sha256:${"a".repeat(64)}`, page: 5, page_ref: `page:sha256:${"5".repeat(64)}`, source_locator: { ...evidence.source_locator, page: 5 } });
    const study = `/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}/study-sessions/${sessionId}`;
    await page.goto(study);
    await expect(page.locator(".study-sources button")).toHaveText(["第 1 頁", "第 5 頁"]);
    await page.getByRole("button", { name: "開始練習", exact: true }).click();
    const question = page.getByRole("heading", { name: /練習 1：/ });
    await expect(question).toBeVisible();
    await expect(question).toBeInViewport();
    fixture.records[0].assessment.options[2].text = "很長的選項也必須完整呈現並允許閱讀，不能因為視窗寬度而截斷。".repeat(4) + "LongUnbrokenOption".repeat(6);
    await page.reload();
    await expect(question).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(width);
    await page.screenshot({ path: `/tmp/studydy-study-workflow/${width}-long-options.png`, fullPage: true });
    await page.getByRole("radio").nth(2).check(); await page.getByRole("button", { name: "送出答案" }).click();
    await expect(page.getByRole("heading", { name: "建議先補強前置概念" })).toBeVisible();
  });
}

for (const viewport of [{ width: 1536, height: 1024 }, { width: 1366, height: 768 }, { width: 390, height: 844 }]) {
  test(`learning workspace shell stays stable from Map to Study at ${viewport.width}px`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await routes(page);
    const map = `/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}`;
    const study = `${map}/study-sessions/${sessionId}`;
    const nav = page.getByRole("navigation", { name: "學習工作區導覽" });
    const shellState = async () => {
      await expect(page.locator(".app-shell")).toHaveClass("app-shell is-workspace");
      await expect(page.locator(".app-sidebar, .sidebar-helper, .brand small, .account-avatar")).toHaveCount(0);
      await expect(nav.getByRole("button")).toHaveText(["知識地圖", "教材庫", "處理狀態"]);
      await expect(page.getByRole("button", { name: "登出", exact: true })).toBeInViewport();
      await expect(nav.getByRole("button", { name: "知識地圖", exact: true })).toBeInViewport();
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(viewport.width);
      expect((await page.locator(".app-main").boundingBox())!.x).toBe(0);
      const geometry = [];
      for (const selector of [".app-header", ".brand", ".workspace-nav", ".account-controls"]) geometry.push(await page.locator(selector).boundingBox());
      expect(geometry[0]!.height).toBe(56);
      return geometry;
    };
    await page.goto(map);
    await expect(page.getByRole("button", { name: "開始學習", exact: true })).toBeEnabled();
    await expect(nav.getByRole("button", { name: "知識地圖", exact: true })).toHaveAttribute("aria-current", "page");
    const before = await shellState();
    await page.screenshot({ path: `/tmp/studydy-learning-shell/${viewport.width}-map.png`, fullPage: true });
    await page.getByRole("button", { name: "開始學習", exact: true }).click();
    await expect(page).toHaveURL(study);
    await expect(page.getByRole("button", { name: "開始練習", exact: true })).toBeVisible();
    await expect(nav.getByRole("button", { name: "知識地圖", exact: true })).not.toHaveAttribute("aria-current");
    await expect(page.locator(".study-header").getByRole("button", { name: "回到知識地圖" })).toHaveCount(0);
    await expect(page.locator(".study-header").getByRole("button", { name: "結束本次學習" })).toHaveCount(0);
    expect(await shellState()).toEqual(before);
    const content = (await page.locator(".study-session-page").boundingBox())!;
    expect(content.width).toBeLessThanOrEqual(1440);
    const card = (await page.locator(".current-concept-card").boundingBox())!;
    expect(card.x).toBeGreaterThan(content.x);
    await page.screenshot({ path: `/tmp/studydy-learning-shell/${viewport.width}-study.png`, fullPage: true });
    await nav.getByRole("button", { name: "知識地圖", exact: true }).click(); await expect(page).toHaveURL(map);
    await nav.getByRole("button", { name: "知識地圖", exact: true }).click(); await expect(page).toHaveURL(map);
    await page.goto(study); await expect(page.locator(".study-header")).toBeVisible();
    await nav.getByRole("button", { name: "處理狀態", exact: true }).click();
    await expect(page).toHaveURL(`/materials/${materialId}/runs/${runId}`);
    await expect(page.locator(".app-sidebar")).toHaveCount(1);
    await page.goto(map); await nav.getByRole("button", { name: "處理狀態", exact: true }).click();
    await expect(page).toHaveURL(`/materials/${materialId}/runs/${runId}`);
    await page.route("**/v1/materials", route => json(route, { schema: "material-library/v2", materials: [] }));
    await page.goto(study); await nav.getByRole("button", { name: "教材庫", exact: true }).click();
    await expect(page).toHaveURL("/materials"); await expect(page.locator(".app-sidebar")).toHaveCount(1);
  });
}

for (const viewport of [{ width: 1536, height: 1024 }, { width: 1366, height: 768 }, { width: 390, height: 844 }]) {
  test(`system assessment target and refreshed next claim at ${viewport.width}px`, async ({ page }) => {
    await page.setViewportSize(viewport);
    const fixture = await studyWorkflowFixture(page);
    const claims = fixture.view.concepts[1].claims;
    fixture.state.next_action.target_claim_id = claims[2].claim_id;
    const requests: { claim: string; key: string }[] = [];
    let failOnce = true;
    await page.route(`**/v1/study-sessions/${sessionId}/assessments`, route => {
      requests.push({ claim: route.request().postDataJSON().target_claim_id, key: route.request().headers()["idempotency-key"] });
      if (failOnce) { failOnce = false; return json(route, { schema: "api-error/v1", request_id: sessionId, reason_code: "STORAGE_UNAVAILABLE", retryable: true, message: "Request could not be completed." }, 503); }
      return route.fallback();
    });
    const study = `/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}/study-sessions/${sessionId}`;
    await page.goto(study);
    await expect(page.locator(".study-header-actions, .study-finish-confirmation, .assessment-claim-picker, .claim-picker")).toHaveCount(0);
    await expect(page.getByRole("radio")).toHaveCount(0);
    await expect(page.locator(".assessment-ready")).toContainText("系統會依你的學習進度與目前教材重點準備一道題目。");
    await page.screenshot({ path: `/tmp/studydy-system-flow/${viewport.width}-ready.png`, fullPage: true });
    await page.getByRole("button", { name: "開始練習", exact: true }).click();
    await page.getByRole("button", { name: "再試一次", exact: true }).click();
    await expect(page.getByRole("heading", { name: /練習 1：/ })).toBeVisible();
    expect(requests.map(request => request.claim)).toEqual([claims[2].claim_id, claims[2].claim_id]);
    expect(requests[0].key).toBeTruthy(); expect(requests[1].key).toBe(requests[0].key);
    await page.screenshot({ path: `/tmp/studydy-system-flow/${viewport.width}-question.png`, fullPage: true });
    const release = fixture.holdProgress();
    await page.getByRole("radio").first().check(); await page.getByRole("button", { name: "送出答案" }).click();
    await expect(page.getByRole("heading", { name: "答對了", exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "繼續練習", exact: true })).toHaveCount(0);
    expect(requests).toHaveLength(2);
    Object.assign(fixture.state.next_action, { action: "assess", target_concept_id: fixture.state.current_concept_id, target_claim_id: claims[1].claim_id });
    release();
    await expect(page.getByRole("button", { name: "繼續練習", exact: true })).toBeVisible();
    await expect(page.locator(".feedback-card .assessment-actions button")).toHaveText(["繼續練習"]);
    await expect(page.locator(".feedback-card")).not.toContainText("回到教材");
    await page.screenshot({ path: `/tmp/studydy-system-flow/${viewport.width}-assess-again.png`, fullPage: true });
    await page.getByRole("button", { name: "繼續練習", exact: true }).click();
    await expect(page.getByRole("heading", { name: /練習 2：/ })).toBeVisible();
    expect(requests[2].claim).toBe(claims[1].claim_id); expect(requests[2].key).not.toBe(requests[1].key);
    expect(fixture.completions()).toBe(0);
  });
  for (const invalid of ["null", "unknown", "other-concept"] as const) {
    test(`invalid system assessment target ${invalid} at ${viewport.width}px`, async ({ page }) => {
      await page.setViewportSize(viewport);
      const fixture = await studyWorkflowFixture(page);
      fixture.state.next_action.target_claim_id = invalid === "null" ? null : invalid === "unknown" ? `claim:sha256:${"f".repeat(64)}` : fixture.view.concepts[0].claims[0].claim_id;
      await page.goto(`/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}/study-sessions/${sessionId}`);
      await expect(page.getByRole("heading", { name: "暫時無法準備目前練習", exact: true })).toBeVisible();
      await expect(page.getByRole("button", { name: "開始練習" })).toHaveCount(0);
      await expect(page.getByRole("radio")).toHaveCount(0);
      expect(fixture.creates).toHaveLength(0);
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(viewport.width);
      if (invalid === "null") await page.screenshot({ path: `/tmp/studydy-system-flow/${viewport.width}-invalid.png`, fullPage: true });
      fixture.state.next_action.target_claim_id = fixture.view.concepts[1].claims[2].claim_id;
      await page.getByRole("button", { name: "重新整理本次學習", exact: true }).click();
      await page.getByRole("button", { name: "開始練習", exact: true }).click();
      await expect(page.getByRole("heading", { name: /練習 1：/ })).toBeVisible();
      expect(fixture.creates[0].body).toEqual({ schema: "assessment-create/v2", target_claim_id: fixture.view.concepts[1].claims[2].claim_id });
    });
  }
}

for (const action of ["advance", "review_prerequisite", "defer", "complete"]) {
  test(`feedback cannot bypass ${action} guidance with another question`, async ({ page }) => {
    const fixture = await studyWorkflowFixture(page);
    await page.goto(`/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}/study-sessions/${sessionId}`);
    await page.getByRole("button", { name: "開始練習", exact: true }).click();
    await page.getByRole("radio").first().check(); await page.getByRole("button", { name: "送出答案" }).click();
    await expect(page.getByRole("heading", { name: "可以繼續下一個重點" })).toBeVisible();
    Object.assign(fixture.state.next_action, { action, target_concept_id: action === "complete" ? null : fixture.view.concepts[2].concept_id,
      target_claim_id: action === "defer" ? fixture.view.concepts[1].claims[1].claim_id : null });
    await openStudyRecord(page, fixture.records[0]);
    await page.reload();
    await expect(page.getByRole("heading", { name: "答對了", exact: true })).toBeVisible();
    await expect(page.locator(".adaptive-card")).toHaveCount(0);
    await expect(page.getByRole("button", { name: /繼續練習|取得目前概念的新題目|再出一題/ })).toHaveCount(0);
    await expect(page.locator(".feedback-card .assessment-actions")).toHaveCount(0);
    await expect(page.getByRole("button", { name: "回到教材", exact: true })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "開始練習" })).toHaveCount(0);
    await returnToCurrentStudy(page);
    await expect(page.locator(".study-current-action .adaptive-card")).toBeVisible();
    await expect(page.locator(".study-rail .adaptive-card")).toHaveCount(0);
    expect(fixture.creates).toHaveLength(1); expect(fixture.completions()).toBe(0);
  });
}

test("leaving for Map Materials and Processing preserves the same active study and saved record", async ({ page }) => {
  const fixture = await studyWorkflowFixture(page);
  let newSessions = 0;
  await page.route("**/v1/study-sessions", route => { newSessions++; return json(route, session(), 201); });
  const map = `/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}`;
  const study = `${map}/study-sessions/${sessionId}`;
  const material = { schema: "material-library-item/v2", material_id: materialId, source_artifact_id: artifactId, display_name: "Synthetic resumed material.pdf", size_bytes: 100,
    created_at: run.created_at, latest_attempt: run, available_structures: [{ run_id: runId, knowledge_structure_revision: structureRevision, created_at: run.created_at, status: "succeeded" }],
    study_sessions: [{ ...session(), current_concept_id: fixture.state.current_concept_id, run_id: runId }] };
  await page.route(`**/v1/materials/${materialId}`, route => json(route, material));
  await page.route("**/v1/materials", route => json(route, { schema: "material-library/v2", materials: [material] }));
  await page.goto(study); await page.getByRole("button", { name: "開始練習", exact: true }).click();
  await expect(page.getByRole("heading", { name: /練習 1：/ })).toBeVisible();
  const record = structuredClone(fixture.records[0]);
  const savedProgress = structuredClone(fixture.state);
  const nav = page.getByRole("navigation", { name: "學習工作區導覽" });
  await nav.getByRole("button", { name: "知識地圖", exact: true }).click(); await expect(page).toHaveURL(map);
  await page.getByRole("button", { name: "繼續本次學習", exact: true }).click();
  await expect(page).toHaveURL(study);
  await expect(page.locator(".study-header h1")).toHaveText("陣列");
  await nav.getByRole("button", { name: "教材庫", exact: true }).click();
  await page.getByRole("button", { name: "接續上次學習", exact: true }).click();
  await expect(page.getByRole("heading", { name: /練習 1：/ })).toBeVisible();
  await nav.getByRole("button", { name: "處理狀態", exact: true }).click();
  await expect(page).toHaveURL(`/materials/${materialId}/runs/${runId}`);
  await page.goto(study); await page.reload();
  await expect(page.getByRole("heading", { name: /練習 1：/ })).toBeVisible();
  expect(fixture.completions()).toBe(0); expect(newSessions).toBe(0);
  expect(fixture.records[0]).toEqual(record); expect(fixture.state).toEqual(savedProgress);
  expect(material.study_sessions[0].status).toBe("active"); expect(fixture.answers).toHaveLength(0);
});

for (const viewport of [{ width: 1536, height: 1024 }, { width: 390, height: 844 }]) {
  for (const kind of ["retryable", "conflict", "non-retryable"] as const) {
    test(`assessment error actions ${kind} at ${viewport.width}px`, async ({ page }) => {
      await page.setViewportSize(viewport);
      const fixture = await studyWorkflowFixture(page);
      let reject = true;
      const attempts: string[] = [];
      await page.route(`**/v1/study-sessions/${sessionId}/assessments`, route => {
        attempts.push(route.request().headers()["idempotency-key"]);
        if (!reject) return route.fallback();
        return json(route, { schema: "api-error/v1", request_id: sessionId,
          reason_code: kind === "retryable" ? "STORAGE_UNAVAILABLE" : kind === "conflict" ? "IDEMPOTENCY_CONFLICT" : "REQUEST_INVALID",
          retryable: kind === "retryable", message: "Request could not be completed." }, kind === "retryable" ? 503 : kind === "conflict" ? 409 : 400);
      });
      const study = `/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}/study-sessions/${sessionId}`;
      await page.goto(study); await page.getByRole("button", { name: "開始練習", exact: true }).click();
      const panel = page.locator(".assessment-unavailable");
      await expect(panel.getByRole("heading", { name: "暫時無法準備練習題", exact: true })).toBeVisible();
      await expect(panel).not.toContainText("回到教材");
      await expect(panel.locator(".assessment-actions button")).toHaveText([kind === "retryable" ? "再試一次" : "重新整理本次學習"]);
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(viewport.width);
      await page.screenshot({ path: `/tmp/studydy-assessment-actions/${viewport.width}-${kind}.png`, fullPage: true });
      reject = false;
      if (kind === "retryable") await panel.getByRole("button", { name: "再試一次" }).click();
      else {
        const release = fixture.holdProgress();
        await panel.getByRole("button", { name: "重新整理本次學習" }).click();
        await expect(panel).toBeVisible();
        await expect(page.getByRole("button", { name: "開始練習", exact: true })).toHaveCount(0);
        release();
        await page.getByRole("button", { name: "開始練習", exact: true }).click();
      }
      await expect(page.getByRole("heading", { name: /練習 1：/ })).toBeVisible();
      if (kind === "retryable") expect(attempts[1]).toBe(attempts[0]);
      if (kind !== "retryable") return;
      await page.getByRole("radio").nth(1).check(); await page.getByRole("button", { name: "送出答案" }).click();
      await expect(page.getByRole("heading", { name: "建議先補強前置概念", exact: true })).toBeVisible();
      Object.assign(fixture.state.next_action, { action: "advance", target_concept_id: fixture.view.concepts[2].concept_id, target_claim_id: null });
      await page.reload();
      await expect(page.getByRole("heading", { name: "可以繼續下一個重點", exact: true })).toBeVisible();
      await openStudyRecord(page, fixture.records[0]);
      await expect(page.getByRole("heading", { name: "這題需要再想一下", exact: true })).toBeVisible();
      await expect(page.locator(".feedback-card .assessment-actions")).toHaveCount(0);
      await expect(page.locator(".feedback-card")).not.toContainText("回到教材");
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(viewport.width);
      await page.screenshot({ path: `/tmp/studydy-assessment-actions/${viewport.width}-incorrect-advance.png`, fullPage: true });
      await returnToCurrentStudy(page);
      await page.getByRole("button", { name: "繼續學習", exact: true }).click();
      await expect(page.locator(".study-header h1")).toHaveText(fixture.view.concepts[2].label);
    });
  }
  test(`no-safe review closes locally with one honest action at ${viewport.width}px`, async ({ page }) => {
    await page.setViewportSize(viewport);
    const fixture = await studyWorkflowFixture(page); fixture.failNextAssessment();
    const study = `/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}/study-sessions/${sessionId}`;
    await page.goto(study); await page.getByRole("button", { name: "開始練習", exact: true }).click();
    const panel = page.locator(".assessment-unavailable");
    await expect(panel.getByRole("heading", { name: "改用教材回顧", exact: true })).toBeVisible();
    await expect(panel).not.toContainText("回到教材");
    await expect(panel.locator(".assessment-actions button")).toHaveText(["完成本次回顧"]);
    await page.screenshot({ path: `/tmp/studydy-assessment-actions/${viewport.width}-no-safe.png`, fullPage: true });
    await panel.getByRole("button", { name: "完成本次回顧", exact: true }).click();
    await expect(page.locator(".evidence-review-activity")).toHaveCount(0);
    await expect(page).toHaveURL(study);
    await expect(page.getByRole("heading", { name: "目前沒有適合的新題目", exact: true })).toBeVisible();
    expect(fixture.completions()).toBe(0); expect(fixture.answers).toHaveLength(0);
  });
}

for (const viewport of [{ width: 1536, height: 1024 }, { width: 1366, height: 768 }, { width: 390, height: 844 }]) {
  test(`advisory middle-concept start stays on current concept at ${viewport.width}px`, async ({ page }) => {
    await page.setViewportSize(viewport);
    const fixture = await studyWorkflowFixture(page);
    const current = fixture.view.concepts[4]; current.label = "使用 sizeof 查詢陣列所佔用的記憶體空間";
    fixture.state.current_concept_id = current.concept_id;
    Object.assign(fixture.state.next_action, { action: "assess", target_concept_id: current.concept_id, target_claim_id: current.claims[0].claim_id,
      prerequisite_concept_ids: [fixture.view.concepts[1].concept_id, fixture.view.concepts[2].concept_id], reason: "canonical_prerequisite_gap" });
    let creates = 0;
    await page.route("**/v1/study-sessions", route => {
      creates++; expect(route.request().postDataJSON().current_concept_id).toBe(current.concept_id);
      return json(route, { ...session(), current_concept_id: current.concept_id }, 201);
    });
    const map = `/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}`;
    await page.goto(map);
    await page.getByRole("searchbox", { name: "搜尋概念或關鍵字" }).fill(current.label);
    await page.getByRole("searchbox", { name: "搜尋概念或關鍵字" }).press("Enter");
    await page.getByRole("button", { name: "開始學習", exact: true }).click();
    const advisory = page.getByRole("complementary", { name: "建議先了解" });
    await expect(advisory).toHaveCount(1);
    await expect(advisory.getByRole("button")).toHaveText(["查看「陣列」", "查看「Concept 3」"]);
    await expect(advisory).toContainText("如果你已經熟悉，可以直接開始練習");
    await expect(advisory.locator(".primary-button")).toHaveCount(0);
    await expect(page.locator(".study-header")).toContainText("第 5 / 56 個概念");
    await expect(page.getByRole("button", { name: "開始練習", exact: true })).toBeEnabled();
    if (viewport.width > 900) await expect(page.getByRole("button", { name: "開始練習", exact: true })).toBeInViewport();
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(viewport.width);
    await page.screenshot({ path: `/tmp/studydy-advisory-action/${viewport.width}-advisory.png`, fullPage: true });
    const advisoryIds = [...fixture.state.next_action.prerequisite_concept_ids];
    fixture.state.next_action.prerequisite_concept_ids = [];
    await page.reload(); await expect(page.getByRole("button", { name: "開始練習", exact: true })).toBeVisible();
    await expect(advisory).toHaveCount(0);
    await page.screenshot({ path: `/tmp/studydy-advisory-action/${viewport.width}-no-advisory.png`, fullPage: true });
    fixture.state.next_action.prerequisite_concept_ids = advisoryIds;
    await page.reload(); await expect(advisory).toBeVisible();
    await page.getByRole("button", { name: "開始練習", exact: true }).click();
    await expect(page.getByRole("heading", { name: /練習 1：/ })).toBeVisible();
    expect(fixture.creates[0].body).toEqual({ schema: "assessment-create/v2", target_claim_id: current.claims[0].claim_id });
    expect(fixture.state.current_concept_id).toBe(current.concept_id); expect(fixture.applies).toHaveLength(0); expect(creates).toBe(1);
    fixture.state.next_action.prerequisite_concept_ids = [];
    await page.goto(`${map}/study-sessions/${sessionId}`);
    await expect(page.getByRole("heading", { name: /練習 1：/ })).toBeVisible();
    await expect(advisory).toHaveCount(0);
  });
  for (const action of ["advance", "defer", "resume", "complete"] as const) {
    test(`right current action ${action} is above followup at ${viewport.width}px`, async ({ page }) => {
      await page.setViewportSize(viewport);
      const fixture = await studyWorkflowFixture(page);
      Object.assign(fixture.state.next_action, { action, target_concept_id: action === "complete" ? null : fixture.view.concepts[4].concept_id, target_claim_id: null });
      await page.goto(`/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}/study-sessions/${sessionId}`);
      const actionPane = page.locator(".study-current-action");
      await expect(actionPane.locator(".adaptive-card")).toBeVisible();
      await expect(page.locator(".study-rail .adaptive-card, .assessment-ready")).toHaveCount(0);
      await expect(page.locator(".study-session-page")).not.toContainText("請依下方");
      if (viewport.width > 900) {
        await expect(actionPane.getByRole("button")).toBeInViewport();
        const left = (await page.locator(".current-concept-card").boundingBox())!, right = (await actionPane.boundingBox())!;
        expect(Math.abs(left.y - right.y)).toBeLessThan(1); expect(right.x).toBeGreaterThan(left.x);
      }
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(viewport.width);
      await page.screenshot({ path: `/tmp/studydy-advisory-action/${viewport.width}-${action}.png`, fullPage: true });
      await actionPane.getByRole("button").click();
      await expect(page.locator(".study-header h1")).toHaveText(action === "complete" ? "本次學習已完成" : fixture.view.concepts[4].label);
    });
  }
}

test("unknown advisory binding fails readably without exposing raw concept ids", async ({ page }) => {
  const fixture = await studyWorkflowFixture(page);
  fixture.state.next_action.prerequisite_concept_ids = [`concept:sha256:${"f".repeat(64)}`];
  await page.goto(`/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}/study-sessions/${sessionId}`);
  await expect(page.getByRole("heading", { name: "無法開啟本次學習" })).toBeVisible();
  await expect(page.locator(".app-main")).not.toContainText("concept:sha256:");
  expect(fixture.creates).toHaveLength(0);
  fixture.state.next_action.prerequisite_concept_ids = [];
  await page.getByRole("button", { name: "重新讀取", exact: true }).click();
  await expect(page.getByRole("button", { name: "開始練習", exact: true })).toBeVisible();
});

for (const viewport of [{ width: 1536, height: 1024 }, { width: 1366, height: 768 }, { width: 390, height: 844 }]) {
  for (const action of ["no_safe", "defer"] as const) {
    test(`NO_SAFE activity owns the right pane until review ends with ${action} at ${viewport.width}px`, async ({ page }) => {
      await page.setViewportSize(viewport);
      const fixture = await studyWorkflowFixture(page);
      const study = `/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}/study-sessions/${sessionId}`;
      await page.goto(study);
      await expect(page.getByRole("button", { name: "開始練習", exact: true })).toBeEnabled();
      if (action === "defer") {
        await page.getByRole("button", { name: "開始練習", exact: true }).click();
        await expect(page.getByRole("heading", { name: /練習 1：/ })).toBeVisible();
        const releaseAnswer = fixture.holdProgress();
        await page.getByRole("radio").first().check(); await page.getByRole("button", { name: "送出答案" }).click();
        await expect(page.getByRole("heading", { name: "答對了", exact: true })).toBeVisible();
        Object.assign(fixture.state.next_action, { action: "assess", target_concept_id: fixture.state.current_concept_id, target_claim_id: fixture.view.concepts[1].claims[2].claim_id });
        releaseAnswer(); await expect(page.getByRole("button", { name: "繼續練習", exact: true })).toBeVisible();
      }
      fixture.failNextAssessment();
      const release = fixture.holdProgress();
      await page.getByRole("button", { name: action === "defer" ? "繼續練習" : "開始練習", exact: true }).click();
      await expect(page.locator(".evidence-review-activity")).toBeVisible();
      await expect(page.getByRole("button", { name: "完成本次回顧" })).toBeDisabled();
      Object.assign(fixture.state.next_action, { action, target_concept_id: action === "defer" ? fixture.view.concepts[2].concept_id : null });
      release();
      await expect(page.getByRole("button", { name: "完成本次回顧" })).toBeEnabled();
      await expect(page.locator(".study-current-action .evidence-review-activity")).toBeVisible();
      await expect(page.locator(".adaptive-card")).toHaveCount(0);
      await page.screenshot({ path: `/tmp/studydy-advisory-action/${viewport.width}-${action}-review.png`, fullPage: true });
      await page.getByRole("button", { name: "完成本次回顧" }).click();
      await expect(page).toHaveURL(study);
      await expect(page.locator(".evidence-review-activity")).toHaveCount(0);
      await expect(page.locator(".study-current-action .adaptive-card")).toHaveCount(1);
      await expect(page.locator(".study-rail .adaptive-card")).toHaveCount(0);
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(viewport.width);
      if (action === "defer") {
        await openStudyRecord(page, fixture.records[0]);
        await expect(page.getByRole("heading", { name: "答對了", exact: true })).toBeVisible();
        await expect(page.locator(".adaptive-card")).toHaveCount(0);
        await page.screenshot({ path: `/tmp/studydy-advisory-action/${viewport.width}-history.png`, fullPage: true });
        await page.reload(); await expect(page.getByRole("heading", { name: "答對了", exact: true })).toBeVisible();
        await returnToCurrentStudy(page);
        await expect(page.locator(".study-current-action .adaptive-card")).toHaveCount(1);
      }
    });
  }
}

test("late answer progress does not navigate a learner back after leaving", async ({ page }) => {
  const fixture = await studyWorkflowFixture(page);
  await page.route("**/v1/materials", route => json(route, { schema: "material-library/v2", materials: [] }));
  await page.goto(`/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}/study-sessions/${sessionId}`);
  await page.getByRole("button", { name: "開始練習", exact: true }).click();
  await expect(page.getByRole("heading", { name: /練習 1：/ })).toBeVisible();
  const release = fixture.holdProgress();
  await page.getByRole("radio").first().check(); await page.getByRole("button", { name: "送出答案" }).click();
  await expect(page.getByRole("heading", { name: "答對了", exact: true })).toBeVisible();
  await page.getByRole("navigation", { name: "學習工作區導覽" }).getByRole("button", { name: "教材庫", exact: true }).click();
  await expect(page).toHaveURL("/materials");
  const refreshed = page.waitForResponse(response => response.url().includes("/resume?") && response.ok());
  release(); await refreshed;
  await expect(page.getByRole("heading", { name: "我的教材", exact: true })).toBeVisible();
  await expect(page).toHaveURL("/materials");
});

test("failed no-safe refresh reloads current guidance without retaining an orphaned activity", async ({ page }) => {
  const fixture = await studyWorkflowFixture(page); fixture.failNextAssessment();
  let failOnce = true;
  await page.route("**/v1/materials/*/knowledge-structures/*/study-sessions/*/resume?*", route => {
    if (fixture.state.next_action.action === "no_safe" && failOnce) {
      failOnce = false;
      return json(route, { schema: "api-error/v1", request_id: sessionId, reason_code: "STORAGE_UNAVAILABLE", retryable: true, message: "Request could not be completed." }, 503);
    }
    return route.fallback();
  });
  await page.goto(`/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}/study-sessions/${sessionId}`);
  await page.getByRole("button", { name: "開始練習", exact: true }).click();
  await expect(page.getByRole("heading", { name: "無法開啟本次學習" })).toBeVisible();
  await page.getByRole("button", { name: "重新讀取", exact: true }).click();
  await expect(page.locator(".study-current-action .adaptive-card")).toContainText("目前沒有適合的新題目");
  await expect(page.locator(".evidence-review-activity, .assessment-ready")).toHaveCount(0);
});

for (const viewport of [{ width: 1536, height: 1024 }, { width: 1366, height: 768 }, { width: 390, height: 844 }]) {
  for (const count of [1, 3]) {
    test(`prerequisite preview ${count} concepts preserves study authority at ${viewport.width}px`, async ({ page }) => {
      await page.setViewportSize(viewport);
      const fixture = await studyWorkflowFixture(page);
      const current = fixture.view.concepts[1]; current.label = "使用 sizeof 計算陣列容量";
      const prerequisites = (count === 1 ? [fixture.view.concepts[0]] : [fixture.view.concepts[3], fixture.view.concepts[0], fixture.view.concepts[2]]);
      prerequisites.forEach((concept, index) => {
        concept.label = index === 0 && count === 3 ? "使用二維陣列表示資料與索引位置_" + "LongPrerequisite".repeat(4) : index === 1 || count === 1 ? "陣列索引值" : "一維陣列";
        const evidence = concept.claims[0].evidence[0];
        concept.claims = [3, 3, 5].map((sourcePage, i) => ({ claim_id: `claim:sha256:${(3000 + index * 3 + i).toString(16).padStart(64, "0")}`,
          text: `前置教材 ${index + 1} 重點 ${i + 1}：元素使用索引值定位。` + (count === 3 && index === 0 && i === 2 ? "長教材內容應完整閱讀並正常換行。".repeat(24) : ""),
          evidence: [{ ...evidence, page: sourcePage, page_ref: `page:sha256:${String(sourcePage).repeat(64)}`, source_locator: { ...evidence.source_locator, page: sourcePage } }],
        }));
      });
      fixture.state.next_action.prerequisite_concept_ids = prerequisites.map(concept => concept.concept_id);
      const snapshot = structuredClone(fixture.state);
      const targetClaim = fixture.state.next_action.target_claim_id;
      const study = `/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}/study-sessions/${sessionId}`;
      const productRequests: string[] = [];
      page.on("request", request => { if (/\/v1\/(study-sessions|materials)/.test(request.url())) productRequests.push(request.method() + request.url()); });
      await page.goto(study);
      const advisory = page.getByRole("complementary", { name: "建議先了解" });
      await expect(advisory.getByRole("button")).toHaveText(prerequisites.map(concept => `查看「${concept.label}」`));
      await expect(page.locator(".study-current-action .primary-button")).toHaveCount(1);
      await expect(page.getByRole("button", { name: "開始練習", exact: true })).toBeEnabled();
      await page.screenshot({ path: `/tmp/studydy-prerequisite-preview/${viewport.width}-${count}-ready.png`, fullPage: true });
      for (const [index, concept] of prerequisites.entries()) {
        const beforeRequests = productRequests.length;
        const opener = advisory.getByRole("button", { name: `查看「${concept.label}」`, exact: true });
        await opener.focus(); await page.keyboard.press("Enter");
        const preview = page.locator(".assessment-prerequisite-preview");
        await expect(preview.getByRole("heading", { name: concept.label, exact: true })).toBeFocused();
        await expect(preview.locator(":scope > ul > li")).toHaveText(concept.claims.map(claim => claim.text));
        await expect(preview.getByRole("region", { name: "前置概念教材來源" }).getByRole("button")).toHaveText(["第 3 頁", "第 5 頁"]);
        await expect(page.locator(".assessment-ready, .adaptive-card")).toHaveCount(0);
        await expect(preview.locator(".primary-button")).toHaveCount(0);
        await expect(page.locator(".study-header h1")).toHaveText(current.label);
        await expect(page.locator(".current-concept-card h2")).toHaveText(current.label);
        await expect(page).toHaveURL(study);
        expect(productRequests).toHaveLength(beforeRequests);
        expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(viewport.width);
        await page.screenshot({ path: `/tmp/studydy-prerequisite-preview/${viewport.width}-${count}-open-${index}.png`, fullPage: true });
        if (index === 0) {
          const opened = page.waitForEvent("popup");
          await preview.getByRole("button", { name: "第 3 頁", exact: true }).click();
          const source = await opened; await source.waitForLoadState();
          expect(source.url()).toContain(`/v1/artifacts/${artifactId}#page=3`);
          expect(await source.evaluate(() => window.opener)).toBeNull(); await source.close();
        }
        await preview.getByRole("button", { name: "回到目前練習", exact: true }).click();
        await expect(opener).toBeFocused();
        await expect(preview).toHaveCount(0);
        await expect(page.getByRole("heading", { name: `準備好練習「${current.label}」了嗎？`, exact: true })).toBeVisible();
        expect(productRequests).toHaveLength(beforeRequests);
        expect(fixture.state).toEqual(snapshot);
      }
      expect(fixture.creates).toHaveLength(0); expect(fixture.applies).toHaveLength(0); expect(fixture.completions()).toBe(0);
      await page.getByRole("button", { name: "開始練習", exact: true }).click();
      await expect(page.getByRole("heading", { name: /練習 1：/ })).toBeVisible();
      expect(fixture.creates[0].body).toEqual({ schema: "assessment-create/v2", target_claim_id: targetClaim });
      await expect(page.locator(".assessment-prerequisite-preview, .assessment-prerequisite-actions")).toHaveCount(0);
      await page.reload(); await expect(page.getByRole("heading", { name: /練習 1：/ })).toBeVisible();
      await expect(page.locator(".assessment-prerequisite-actions")).toHaveCount(0);
      fixture.setStatus("completed"); await page.reload();
      await expect(page.getByRole("heading", { name: "本次學習已完成", exact: true })).toBeVisible();
      await expect(page.locator(".assessment-prerequisite-preview, .assessment-prerequisite-actions")).toHaveCount(0);
    });
  }
  test(`preview never overlaps no-safe review at ${viewport.width}px`, async ({ page }) => {
    await page.setViewportSize(viewport);
    const fixture = await studyWorkflowFixture(page); fixture.failNextAssessment();
    fixture.state.next_action.prerequisite_concept_ids = [fixture.view.concepts[0].concept_id];
    await page.goto(`/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}/study-sessions/${sessionId}`);
    await page.getByRole("button", { name: `查看「${fixture.view.concepts[0].label}」`, exact: true }).click();
    await page.getByRole("button", { name: "回到目前練習", exact: true }).click();
    await page.getByRole("button", { name: "開始練習", exact: true }).click();
    await expect(page.locator(".evidence-review-activity")).toBeVisible();
    await expect(page.locator(".assessment-prerequisite-preview, .assessment-prerequisite-actions")).toHaveCount(0);
    await page.getByRole("button", { name: "完成本次回顧", exact: true }).click();
    await expect(page.locator(".study-current-action .adaptive-card")).toBeVisible();
    expect(fixture.applies).toHaveLength(0); expect(fixture.completions()).toBe(0);
  });
}

async function tenAssessmentHistory(page: Page) {
  const fixture = await studyWorkflowFixture(page);
  const concept = fixture.view.concepts[1];
  const records: AssessmentRecordView[] = Array.from({ length: 10 }, (_, index) => {
    const pending = [0, 3, 6].includes(index);
    const target = pending && index > 0 ? fixture.view.concepts[0] : concept;
    const assessment = {
      schema: "single-choice-assessment/v2" as const, assessment_revision: `assessment:sha256:${(5000 + index).toString(16).padStart(64, "0")}`,
      study_session_id: sessionId, knowledge_structure_revision: structureRevision,
      question_id: `question:sha256:${(5100 + index).toString(16).padStart(64, "0")}`,
      target_concept_id: target.concept_id, target_claim_id: target.claims[0].claim_id,
      source_evidence_ids: [target.claims[0].evidence[0].evidence_id], question_type: "single_choice" as const,
      prompt: `教材練習 ${10 - index}：根據 C 語言定義，陣列是由什麼組成的集合？` + (index === 1 ? "請考慮型態、排列與索引所代表的意義。".repeat(8) : ""),
      options: ["相同型態的元素", "隨機數值", "不同型態且不需要索引的元素，任何位置都可以任意存取。".repeat(5), "未定義的資料"].map((text, option) => ({ option_id: `option:sha256:${String(option + 1).repeat(64)}`, text })),
    };
    const correct = index % 2 === 0;
    return { assessment, created_at: `2026-09-14T00:${String(10 - index).padStart(2, "0")}:00Z`, can_submit: index === 0,
      feedback: pending ? null : { schema: "answer-feedback/v2" as const, answer_event_id: `55555555-5555-4555-8555-${String(index).padStart(12, "0")}`,
        study_session_id: sessionId, assessment_revision: assessment.assessment_revision, question_id: assessment.question_id,
        selected_option_id: assessment.options[correct ? 0 : 1].option_id, is_correct: correct,
        rationale: "陣列由相同型態的元素組成，並以索引定位元素。", source_evidence_ids: assessment.source_evidence_ids, event_number: 10 - index, created_at: run.created_at },
    };
  });
  fixture.records.push(...records);
  Object.assign(fixture.state.concept_states[1], { status: "needs_review", attempts: 7, correct_answers: 3, covered_claim_ids: [concept.claims[0].claim_id], weak_claim_ids: [concept.claims[0].claim_id] });
  fixture.state.next_action.target_claim_id = concept.claims[0].claim_id;
  return fixture;
}

for (const viewport of [{ width: 1536, height: 1024 }, { width: 1366, height: 768 }, { width: 390, height: 844 }]) {
  test(`ten-question history explicitly distinguishes latest progress at ${viewport.width}px`, async ({ page }) => {
    await page.setViewportSize(viewport);
    const fixture = await tenAssessmentHistory(page);
    const study = `/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}/study-sessions/${sessionId}`;
    const shot = async (state: string) => { expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(viewport.width); await page.screenshot({ path: `/tmp/studydy-assessment-history/${viewport.width}-${state}.png`, fullPage: true }); };
    await page.goto(study);
    await expect(page.getByRole("heading", { name: fixture.records[0].assessment.prompt, exact: true })).toBeVisible();
    await expect(page).toHaveURL(study);
    await expect(page.getByRole("button", { name: /返回最新進度|回到目前學習/ })).toHaveCount(0);
    await expect(page.locator(".current-concept-card")).toHaveCount(0);
    await expect(page.getByRole("button", { name: "送出答案", exact: true })).toBeDisabled();
    await expect(page.getByRole("button", { name: "送出答案", exact: true })).toHaveAttribute("aria-busy", "false");
    await shot("question");
    await page.getByRole("radio").nth(1).check();
    await expect(page.locator(".assessment-options .is-selected")).toHaveCount(1);
    await expect(page.getByRole("button", { name: "送出答案", exact: true })).toBeEnabled();
    await shot("selected");
    const history = page.locator(".study-record-picker");
    await expect(history.locator("summary")).toHaveText("題目與作答紀錄（10）");
    await expect(history).not.toHaveAttribute("open", "");
    await history.locator("summary").focus(); await page.keyboard.press("Space");
    await expect(history.getByRole("button")).toHaveCount(10);
    await expect(history.getByRole("combobox")).toHaveCount(0);
    await expect(history.locator(".study-history-number")).toHaveText(Array.from({ length: 10 }, (_, index) => `第 ${10 - index} 題`));
    await expect(history.locator(".study-history-status")).toHaveText(fixture.records.map(record => record.feedback ? record.feedback.is_correct ? "答對" : "答錯" : "未作答"));
    await expect(history.locator(".study-history-chevron svg")).toHaveCount(10);
    const firstRow = history.getByRole("button").first();
    await expect(firstRow).toHaveCSS("border-top-style", "solid");
    await expect(firstRow).not.toHaveCSS("border-top-color", "rgba(0, 0, 0, 0)");
    await expect(firstRow).toHaveCSS("cursor", "pointer");
    await shot("history-expanded");
    await history.locator("summary").focus(); await page.keyboard.press("Tab");
    await expect(firstRow).toBeFocused();
    await expect(firstRow).toHaveCSS("outline-style", "solid");
    await page.keyboard.press("Enter");
    await expect(page.getByRole("region", { name: "歷史作答", exact: true })).toContainText("第 10 題");
    await expect(page.locator(".current-concept-card")).toBeVisible();
    await expect(page.locator(".study-learning-grid")).not.toHaveClass(/is-question-mode/);
    await returnToCurrentStudy(page);
    await expect(page.getByRole("heading", { name: fixture.records[0].assessment.prompt, exact: true })).toBeVisible();
    await expect(page.locator(".current-concept-card")).toHaveCount(0);
    await openStudyRecord(page, fixture.records[1]);
    await expect(page).toHaveURL(study + "/assessments/" + encodeURIComponent(fixture.records[1].assessment.assessment_revision));
    const historical = page.getByRole("region", { name: "歷史作答", exact: true });
    await expect(historical).toBeFocused();
    await expect(historical).toContainText("第 9 題 · 已作答");
    await expect(page.locator(".current-concept-card")).toBeVisible();
    await expect(page.getByRole("heading", { name: "這題需要再想一下", exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: /^(送出答案|繼續練習)$/ })).toHaveCount(0);
    await expect(page.locator(".weakness-card")).toHaveCount(0);
    await expect(page.locator(".learning-insights")).not.toContainText("最近答案需要複習");
    await history.locator("summary").click();
    await expect(history.locator('[aria-current="true"] .study-history-number')).toHaveText("第 9 題");
    await shot("historical-wrong");
    await page.reload(); await expect(historical).toContainText("第 9 題");
    await returnToCurrentStudy(page);
    await expect(page).toHaveURL(study);
    await expect(page.getByRole("heading", { name: fixture.records[0].assessment.prompt, exact: true })).toBeVisible();
    await expect(historical).toHaveCount(0);
    await expect(page.getByRole("button", { name: "返回最新進度" })).toHaveCount(0);
    await openStudyRecord(page, fixture.records[2]);
    await expect(page.getByRole("heading", { name: "答對了", exact: true })).toBeVisible();
    await expect(historical).toContainText("第 8 題");
    await expect(page.getByRole("button", { name: "繼續練習" })).toHaveCount(0);
    await openStudyRecord(page, fixture.records[3]);
    await expect(historical).toContainText("第 7 題 · 尚未作答");
    await expect(page.locator(".current-concept-card")).toBeVisible();
    await expect(page.locator(".study-learning-grid")).not.toHaveClass(/is-question-mode/);
    await expect(page.getByText("這題目前僅供回顧", { exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "送出答案" })).toHaveCount(0);
    for (const option of await page.getByRole("radio").all()) await expect(option).toBeDisabled();
    await shot("historical-unanswered");
    await openStudyRecord(page, fixture.records[0]);
    await expect(historical).toContainText("第 10 題 · 尚未作答");
    await expect(page.getByRole("radio").first()).toBeEnabled();
    await returnToCurrentStudy(page);
    await expect(page.getByRole("button", { name: "返回最新進度" })).toHaveCount(0);
    Object.assign(fixture.state.concept_states[1], { status: "mastered", attempts: 6, correct_answers: 6, covered_claim_ids: fixture.view.concepts[1].claims.map(claim => claim.claim_id), mastered_claim_ids: fixture.view.concepts[1].claims.map(claim => claim.claim_id) });
    Object.assign(fixture.state.next_action, { action: "advance", target_concept_id: fixture.view.concepts[2].concept_id, target_claim_id: null });
    await page.reload();
    await expect(page.locator(".learning-insights .learning-status")).toHaveText("本次已掌握");
    await expect(page.locator(".learning-insights")).toContainText("已掌握 3 / 3 個教材重點");
    await expect(page.locator(".study-current-action .adaptive-card")).toBeVisible();
    await expect(page.getByRole("button", { name: "繼續練習" })).toHaveCount(0);
    await expect(page.locator(".study-current-action .primary-button")).toHaveCount(1);
    await shot("mastered-guidance");
    expect(fixture.answers).toHaveLength(0); expect(fixture.completions()).toBe(0);
  });
  for (const correct of [false, true]) {
    test(`current ${correct ? "correct" : "wrong"} feedback groups legal answer information at ${viewport.width}px`, async ({ page }) => {
      await page.setViewportSize(viewport);
      const fixture = await studyWorkflowFixture(page);
      const study = `/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}/study-sessions/${sessionId}`;
      await page.goto(study); await page.getByRole("button", { name: "開始練習", exact: true }).click();
      await expect(page.getByRole("heading", { name: /練習 1：/ })).toBeVisible();
      await expect(page.locator(".current-concept-card")).toHaveCount(0);
      const release = fixture.holdProgress();
      await page.getByRole("radio").nth(correct ? 0 : 1).check(); await page.getByRole("button", { name: "送出答案" }).click();
      await expect(page.getByRole("heading", { name: correct ? "答對了" : "這題需要再想一下", exact: true })).toBeVisible();
      Object.assign(fixture.state.next_action, { action: "assess", target_concept_id: fixture.state.current_concept_id, target_claim_id: fixture.view.concepts[1].claims[2].claim_id });
      fixture.state.concept_states[1].mastered_claim_ids = correct ? [fixture.view.concepts[1].claims[0].claim_id] : [];
      release();
      await expect(page.getByRole("button", { name: "繼續練習", exact: true })).toBeVisible();
      await expect(page).toHaveURL(study);
      const feedback = page.locator(".feedback-card");
      await expect(feedback.locator(".feedback-section h3")).toHaveText(["題目", "你的答案", "為什麼？"]);
      await expect(feedback.locator(".feedback-section p").nth(1)).toHaveText(fixture.records[0].assessment.options[correct ? 0 : 1].text);
      await expect(feedback.locator(".feedback-rationale")).toHaveText(fixture.records[0].feedback!.rationale);
      await expect(feedback.locator(".feedback-evidence button")).toHaveCount(1);
      await expect(feedback).toHaveAttribute("aria-live", "polite");
      await expect(page.locator(".assessment-history-context, .weakness-card, .adaptive-card")).toHaveCount(0);
      await expect(page.getByRole("button", { name: /返回最新進度|回到目前學習/ })).toHaveCount(0);
      await expect(page.locator(".study-current-action .primary-button")).toHaveCount(1);
      await expect(page.locator(".learning-insights .learning-status")).toHaveText(correct ? "學習中" : "需要複習");
      await expect(page.locator(".learning-insights")).toContainText(`已掌握 ${correct ? 1 : 0} / 3 個教材重點`);
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(viewport.width);
      await page.screenshot({ path: `/tmp/studydy-assessment-history/${viewport.width}-${correct ? "correct" : "wrong"}-feedback.png`, fullPage: true });
      await page.getByRole("button", { name: "繼續練習", exact: true }).click();
      await expect(page.getByRole("heading", { name: /練習 2：/ })).toBeVisible();
      expect(fixture.creates[1].body).toEqual({ schema: "assessment-create/v2", target_claim_id: fixture.view.concepts[1].claims[2].claim_id });
    });
  }
}

for (const viewport of [{ width: 1536, height: 1024 }, { width: 1366, height: 768 }, { width: 390, height: 844 }]) {
  for (const correct of [false, true]) {
    test(`active question layout reveals material after ${correct ? "correct" : "wrong"} feedback at ${viewport.width}px`, async ({ page }) => {
      await page.setViewportSize(viewport);
      const fixture = await studyWorkflowFixture(page);
      const study = `/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}/study-sessions/${sessionId}`;
      await page.goto(study);
      const material = page.locator(".current-concept-card");
      const grid = page.locator(".study-learning-grid");
      await expect(page.locator(".assessment-ready")).toBeVisible();
      await expect(material).toBeVisible();
      await expect(grid).not.toHaveClass(/is-question-mode/);
      const releaseQuestion = fixture.hold();
      await page.getByRole("button", { name: "開始練習", exact: true }).click();
      await expect(page.locator(".assessment-loading")).toBeVisible();
      expect(await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => resolve(document.querySelector(".current-concept-card") === null))))).toBe(true);
      await expect(material).toHaveCount(0);
      await expect(grid).toHaveClass(/is-question-mode/);
      await expect(page.locator(".study-header")).toContainText("第 2 / 56 個概念");
      await expect(page.locator(".study-header h1")).toHaveText("陣列");
      if (viewport.width > 900) {
        const pane = (await page.locator(".study-current-action").boundingBox())!, bounds = (await grid.boundingBox())!;
        expect(pane.width).toBeGreaterThanOrEqual(720); expect(pane.width).toBeLessThanOrEqual(820);
        expect(Math.abs(pane.x + pane.width / 2 - bounds.x - bounds.width / 2)).toBeLessThan(1);
      }
      await page.screenshot({ path: `/tmp/studydy-question-focus/${viewport.width}-${correct}-loading.png`, fullPage: true });
      releaseQuestion();
      await expect(page.getByRole("heading", { name: /練習 1：/ })).toBeVisible();
      await expect(material).toHaveCount(0);
      await page.getByRole("radio").nth(correct ? 0 : 1).check();
      await expect(material).toHaveCount(0);
      await page.screenshot({ path: `/tmp/studydy-question-focus/${viewport.width}-${correct}-question.png`, fullPage: true });
      await page.reload();
      await expect(page.getByRole("heading", { name: /練習 1：/ })).toBeVisible();
      await expect(material).toHaveCount(0);
      await expect(grid).toHaveClass(/is-question-mode/);
      await page.getByRole("radio").nth(correct ? 0 : 1).check();
      const releaseProgress = fixture.holdProgress();
      await page.getByRole("button", { name: "送出答案", exact: true }).click();
      await expect(page.getByRole("heading", { name: correct ? "答對了" : "這題需要再想一下", exact: true })).toBeVisible();
      await expect(material).toBeVisible();
      await expect(grid).not.toHaveClass(/is-question-mode/);
      Object.assign(fixture.state.next_action, { action: "assess", target_concept_id: fixture.state.current_concept_id, target_claim_id: fixture.view.concepts[1].claims[2].claim_id });
      releaseProgress();
      await expect(page.getByRole("button", { name: "繼續練習", exact: true })).toBeVisible();
      await expect(material).toBeVisible();
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(viewport.width);
      await page.screenshot({ path: `/tmp/studydy-question-focus/${viewport.width}-${correct}-feedback.png`, fullPage: true });
      await page.getByRole("button", { name: "繼續練習", exact: true }).click();
      await expect(page.getByRole("heading", { name: /練習 2：/ })).toBeVisible();
      await expect(material).toHaveCount(0);
      await expect(page).toHaveURL(study);
      expect(fixture.creates[1].body).toEqual({ schema: "assessment-create/v2", target_claim_id: fixture.view.concepts[1].claims[2].claim_id });
    });
  }
  for (const noSafe of [false, true]) {
    test(`question mode restores material for ${noSafe ? "NO_SAFE" : "request failure"} at ${viewport.width}px`, async ({ page }) => {
      await page.setViewportSize(viewport);
      const fixture = await studyWorkflowFixture(page);
      if (noSafe) fixture.failNextAssessment();
      else await page.route(`**/v1/study-sessions/${sessionId}/assessments`, route => json(route, { schema: "api-error/v1", request_id: sessionId, reason_code: "STORAGE_UNAVAILABLE", retryable: true, message: "Request could not be completed." }, 503));
      await page.goto(`/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}/study-sessions/${sessionId}`);
      await page.getByRole("button", { name: "開始練習", exact: true }).click();
      await expect(page.locator(".assessment-unavailable")).toBeVisible();
      await expect(page.locator(".current-concept-card")).toBeVisible();
      await expect(page.locator(".study-learning-grid")).not.toHaveClass(/is-question-mode/);
      if (noSafe) {
        await expect(page.locator(".evidence-review-activity")).toBeVisible();
        await page.getByRole("button", { name: "完成本次回顧", exact: true }).click();
        await expect(page.locator(".study-current-action .adaptive-card")).toBeVisible();
        await expect(page.locator(".current-concept-card")).toBeVisible();
      }
    });
  }
}

async function auditStudyComposition(page: Page, viewport: { width: number; height: number }, name: string) {
  const workspace = page.locator(".study-workspace"), main = page.locator(".study-main"), rail = page.getByRole("complementary", { name: "學習資訊", exact: true });
  await expect(workspace).toHaveCount(1); await expect(page.locator(".study-rail")).toHaveCount(1);
  await expect(page.locator(".study-followup")).toHaveCount(0);
  await expect(rail).toBeVisible();
  await expect(rail.getByRole("button", { name: "返回最新進度", exact: true })).toHaveCount(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(viewport.width);
  const mainBounds = (await main.boundingBox())!, railBounds = (await rail.boundingBox())!;
  const headerBounds = (await page.locator(".study-header").boundingBox())!, workspaceBounds = (await workspace.boundingBox())!;
  expect(Math.abs(headerBounds.x - workspaceBounds.x)).toBeLessThan(1);
  if (viewport.width >= 1280) {
    expect(railBounds.x).toBeGreaterThan(mainBounds.x + mainBounds.width);
    expect(Math.abs(railBounds.y - mainBounds.y)).toBeLessThan(1);
    expect(railBounds.width).toBeGreaterThanOrEqual(300); expect(railBounds.width).toBeLessThanOrEqual(340);
    expect(mainBounds.width).toBeGreaterThan(850);
    if (await rail.locator(".learning-insights").count()) {
      const metrics = await rail.locator(".insights-summary > span:not(.insights-separator)").all();
      expect((await metrics[1].boundingBox())!.y).toBeGreaterThan((await metrics[0].boundingBox())!.y);
      await expect(rail.locator(".insights-separator").first()).toHaveCSS("display", "none");
    }
  } else {
    expect(railBounds.y).toBeGreaterThanOrEqual(mainBounds.y + mainBounds.height);
    expect(Math.abs(railBounds.width - mainBounds.width)).toBeLessThan(1);
  }
  if (await main.locator(".is-question-mode").count()) {
    const card = (await main.locator(".assessment-card").boundingBox())!;
    expect(card.width).toBeLessThanOrEqual(780);
    expect(Math.abs(card.x + card.width / 2 - mainBounds.x - mainBounds.width / 2)).toBeLessThan(1);
    await expect(page.locator(".current-concept-card")).toHaveCount(0);
  } else if (viewport.width >= 1280) {
    expect((await page.locator(".current-concept-card").boundingBox())!.width).toBeGreaterThan(400);
    expect((await page.locator(".study-current-action").boundingBox())!.width).toBeGreaterThan(400);
  }
  await expect(rail).toHaveCSS("overflow-y", "visible");
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path: `/tmp/studydy-side-rail/${viewport.width}-${name}.png`, fullPage: true });
}

for (const viewport of [{ width: 1920, height: 1080 }, { width: 1536, height: 1024 }, { width: 1366, height: 768 }, { width: 1024, height: 768 }, { width: 390, height: 844 }]) {
  test(`study rail loading question and feedback at ${viewport.width}px`, async ({ page }) => {
    await page.setViewportSize(viewport);
    const fixture = await studyWorkflowFixture(page);
    fixture.state.next_action.prerequisite_concept_ids = [fixture.view.concepts[0].concept_id];
    const study = `/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}/study-sessions/${sessionId}`;
    await page.goto(study);
    await expect(page.locator(".assessment-ready")).toBeVisible();
    await expect(page.locator(".current-concept-card")).toBeVisible();
    await expect(page.locator(".study-rail")).not.toBeVisible();
    await page.screenshot({ path: `/tmp/studydy-side-rail/${viewport.width}-ready.png`, fullPage: true });
    const opener = page.getByRole("button", { name: `查看「${fixture.view.concepts[0].label}」`, exact: true });
    await opener.click(); await expect(page.locator(".assessment-prerequisite-preview")).toBeVisible();
    await page.screenshot({ path: `/tmp/studydy-side-rail/${viewport.width}-preview.png`, fullPage: true });
    await page.getByRole("button", { name: "回到目前練習", exact: true }).click(); await expect(opener).toBeFocused();
    await page.getByRole("button", { name: "開始練習", exact: true }).click();
    for (const correct of [true, false]) {
      await expect(page.getByRole("heading", { name: /練習 \d+：/ })).toBeVisible();
      const release = fixture.holdProgress();
      await page.getByRole("radio").nth(correct ? 0 : 1).check(); await page.getByRole("button", { name: "送出答案", exact: true }).click();
      await expect(page.getByRole("heading", { name: correct ? "答對了" : "這題需要再想一下", exact: true })).toBeVisible();
      Object.assign(fixture.state.next_action, { action: "assess", target_concept_id: fixture.state.current_concept_id, target_claim_id: fixture.view.concepts[1].claims[2].claim_id });
      release(); await expect(page.getByRole("button", { name: "繼續練習", exact: true })).toBeVisible();
      await expect(page.locator(".learning-insights")).toHaveCount(1); await expect(page.locator(".study-record-picker")).toHaveCount(1);
      await auditStudyComposition(page, viewport, correct ? "correct-feedback" : "wrong-feedback");
      if (viewport.width >= 1280) {
        await expect(page.locator(".study-rail .learning-insights")).toBeInViewport();
        await expect(page.locator(".study-record-picker summary")).toBeInViewport();
      }
      if (correct) {
        const releaseQuestion = fixture.hold();
        await page.getByRole("button", { name: "繼續練習", exact: true }).click();
        await expect(page.locator(".assessment-loading")).toBeVisible();
        await expect(page.locator(".study-record-picker")).not.toHaveAttribute("open", "");
        await auditStudyComposition(page, viewport, "loading-with-progress");
        if (viewport.width >= 1280) {
          await expect(page.locator(".study-rail .learning-insights")).toBeInViewport();
          await expect(page.locator(".study-record-picker summary")).toBeInViewport();
        }
        releaseQuestion(); await expect(page.getByRole("heading", { name: /練習 2：/ })).toBeVisible();
        await auditStudyComposition(page, viewport, "question-with-progress");
      }
    }
    fixture.setStatus("completed"); await page.reload();
    await expect(page.getByRole("heading", { name: "本次學習已完成", exact: true })).toBeVisible();
    await auditStudyComposition(page, viewport, "completed");
  });

  test(`study rail ten records historical review and long question at ${viewport.width}px`, async ({ page }) => {
    await page.setViewportSize(viewport);
    const fixture = await tenAssessmentHistory(page);
    const study = `/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}/study-sessions/${sessionId}`;
    await page.goto(study);
    await expect(page.getByRole("heading", { name: fixture.records[0].assessment.prompt, exact: true })).toBeVisible();
    await expect(page.locator(".study-record-picker")).not.toHaveAttribute("open", "");
    await auditStudyComposition(page, viewport, "long-question-history-collapsed");
    if (viewport.width >= 1280) await expect(page.locator(".study-record-picker summary")).toBeInViewport();
    const history = page.locator(".study-record-picker");
    await history.locator("summary").focus(); await page.keyboard.press("Space");
    await expect(history.locator(".study-history-row")).toHaveCount(10);
    await expect(history.locator(".study-history-chevron")).toHaveCount(10);
    await expect(history.locator(".study-history-list")).toHaveCSS("overflow-y", "visible");
    expect(await history.evaluate(element => element.scrollHeight <= element.clientHeight + 1)).toBe(true);
    if (viewport.width >= 1280 || viewport.width <= 620) {
      const row = history.locator(".study-history-row").first();
      expect((await row.locator(".study-history-prompt").boundingBox())!.y).toBeGreaterThan((await row.locator(".study-history-number").boundingBox())!.y);
    }
    await auditStudyComposition(page, viewport, "ten-history-expanded");
    await openStudyRecord(page, fixture.records[1]);
    await expect(page.getByRole("region", { name: "歷史作答", exact: true })).toBeFocused();
    await expect(page.getByRole("heading", { name: "這題需要再想一下", exact: true })).toBeVisible();
    await history.locator("summary").click();
    await expect(history.locator('[aria-current="true"] .study-history-number')).toHaveText("第 9 題");
    await auditStudyComposition(page, viewport, "historical");
    await returnToCurrentStudy(page); await expect(page).toHaveURL(study);
    await expect(page.locator(".study-learning-grid")).toHaveClass(/is-question-mode/);
    await expect(page.locator(".current-concept-card")).toHaveCount(0);
  });

  test(`study rail guidance and no-safe keep secondary information separate at ${viewport.width}px`, async ({ page }) => {
    await page.setViewportSize(viewport);
    const fixture = await tenAssessmentHistory(page);
    Object.assign(fixture.state.concept_states[1], { status: "mastered", attempts: 6, correct_answers: 6, covered_claim_ids: fixture.view.concepts[1].claims.map(claim => claim.claim_id), mastered_claim_ids: fixture.view.concepts[1].claims.map(claim => claim.claim_id) });
    Object.assign(fixture.state.next_action, { action: "advance", target_concept_id: fixture.view.concepts[2].concept_id, target_claim_id: null });
    await page.goto(`/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}/study-sessions/${sessionId}`);
    await expect(page.getByRole("heading", { name: "可以繼續下一個重點", exact: true })).toBeVisible();
    await auditStudyComposition(page, viewport, "advance");
    await page.getByRole("button", { name: "繼續學習", exact: true }).click();
    await expect(page.locator(".assessment-ready")).toBeVisible();
    await auditStudyComposition(page, viewport, "ready-with-history");
    fixture.failNextAssessment();
    await page.getByRole("button", { name: "開始練習", exact: true }).click();
    await expect(page.locator(".study-main .evidence-review-activity")).toBeVisible();
    await expect(page.locator(".study-rail .evidence-review-activity, .study-rail .adaptive-card")).toHaveCount(0);
    await expect(page.locator(".current-concept-card")).toBeVisible();
    await auditStudyComposition(page, viewport, "no-safe");
    await page.getByRole("button", { name: "完成本次回顧", exact: true }).click();
    await expect(page.locator(".study-current-action .adaptive-card")).toBeVisible();
  });
}

for (const viewport of [{ width: 1536, height: 1024 }, { width: 1100, height: 800 }, { width: 390, height: 844 }]) {
  test(`review workspace selection excerpts and study target at ${viewport.width}px`, async ({ page }) => {
    await page.setViewportSize(viewport);
    const view = structureView();
    const longText = "教材原文保留完整說明，複習入口先呈現節錄。".repeat(30);
    view.concepts[1].claims = Array.from({ length: 6 }, (_, i) => ({ ...view.concepts[1].claims[0], claim_id: `claim:sha256:${String(i + 1).repeat(64)}`, text: i === 4 ? longText : `教材重點 ${i + 1}` }));
    const state = structuredClone(progress);
    state.concept_states.forEach((item, i) => Object.assign(item, { status: "needs_review", attempts: 3, correct_answers: 1, latest_is_correct: false, weak_claim_ids: [view.concepts[i].claims[i ? 4 : 0].claim_id] }));
    await routes(page, view, () => state);
    await page.route(`**/v1/materials/${materialId}`, route => json(route, {
      schema: "material-library-item/v2", material_id: materialId, source_artifact_id: artifactId,
      display_name: "Data structures.pdf", size_bytes: 100, created_at: run.created_at, latest_attempt: run,
      available_structures: [{ run_id: runId, knowledge_structure_revision: structureRevision, created_at: run.created_at, status: "succeeded" }],
      study_sessions: [{ ...session(), run_id: runId }],
    }));
    const creates: any[] = [];
    await page.route("**/v1/study-sessions", route => { creates.push(route.request().postDataJSON()); return json(route, { ...session(), current_concept_id: secondConcept }, 201); });
    await page.goto(`/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}`);
    await page.getByRole("tab", { name: "複習重點", exact: true }).click();
    const list = page.getByRole("navigation", { name: "需要複習的概念" });
    await expect(list.getByRole("button")).toHaveCount(2);
    await expect(list).not.toContainText(longText);
    const url = page.url();
    const array = list.getByRole("button", { name: /Array/ });
    await array.focus(); await page.keyboard.press("Enter");
    await expect(array).toHaveAttribute("aria-current", "true");
    await expect(array).toBeFocused();
    expect(page.url()).toBe(url); expect(creates).toHaveLength(0);
    await expect(page.locator(".review-context h3")).toHaveText("Array");
    await expect(page.locator(".review-context")).toContainText("最近一次作答尚未答對");
    await expect(page.locator(".review-points > ol > li")).toHaveCount(4);
    await expect(page.locator(".review-points > ol > li").first()).toContainText(longText.slice(0, 96) + "…");
    await expect(page.locator(".review-full-content")).not.toHaveAttribute("open", "");
    await expect(page.locator("#map-panel-review .primary-button")).toHaveCount(1);
    await expect(page.getByRole("dialog")).toHaveCount(0);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    const boxes = await Promise.all([".review-list", ".review-context", ".review-actions", ".review-points"].map(selector => page.locator(selector).boundingBox()));
    const [l, c, a, p] = boxes.map(box => box!);
    if (viewport.width >= 1200) { expect(l.x + l.width).toBeLessThan(c.x); expect(c.x + c.width).toBeLessThan(a.x); }
    if (viewport.width <= 900) { expect(c.y).toBeLessThan(a.y); expect(a.y).toBeLessThan(p.y); expect(p.y).toBeLessThan(l.y); }
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({ path: `/tmp/studydy-review-${viewport.width}.png`, fullPage: true });
    await page.getByText("查看完整教材重點", { exact: true }).click();
    await expect(page.locator(".review-full-content .concept-claim")).toHaveCount(6);
    await expect(page.locator(".review-full-content")).toContainText(longText);
    await page.getByRole("button", { name: "繼續這個概念", exact: true }).click();
    await expect.poll(() => creates.length).toBe(1);
    expect(creates[0].current_concept_id).toBe(secondConcept);
  });
}

test("review empty state keeps learning navigation without an empty workspace", async ({ page }) => {
  await routes(page);
  await page.goto(`/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}`);
  await page.getByRole("tab", { name: "複習重點", exact: true }).click();
  await expect(page.locator(".review-empty")).toContainText("練習後，幫你找出複習方向");
  await expect(page.locator(".review-workspace")).toHaveCount(0);
  await page.getByRole("button", { name: "查看學習導覽", exact: true }).click();
  await expect(page.getByRole("tab", { name: "概念地圖", exact: true })).toHaveAttribute("aria-selected", "true");
});

for (const count of [0, 30]) test(`review ${count} weak concepts preserves membership and list scrolling`, async ({ page }) => {
  await page.setViewportSize({ width: 1536, height: 1024 });
  const view = workspaceView(32, true);
  const current = view.concepts[0].concept_id;
  const state = { ...progress, current_concept_id: current,
    next_action: { ...progress.next_action, target_concept_id: current, target_claim_id: view.concepts[0].claims[0].claim_id },
    concept_states: view.concepts.map((concept, i) => ({ ...progress.concept_states[0], concept_id: concept.concept_id, label: concept.label, status: i < count ? "needs_review" : "learning", weak_claim_ids: i < count ? [concept.claims[0].claim_id] : [] })) };
  await learningMapRoutes(page, view, true);
  await page.route("**/v1/materials/*/knowledge-structures/*/study-sessions/*/resume?*", route => json(route, {
    schema: "study-resume/v1", session: { ...session(), current_concept_id: current }, run_id: runId, source_artifact_id: artifactId,
    knowledge_structure: view, progress: state, assessments: [], selected_assessment_revision: null,
  }));
  await page.goto(`/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}`);
  await page.getByRole("tab", { name: "複習重點", exact: true }).click();
  if (!count) {
    await expect(page.locator(".review-empty")).toContainText("目前沒有需要複習的概念");
    await expect(page.locator(".review-workspace")).toHaveCount(0);
  } else {
    const list = page.locator(".review-list ul");
    await expect(list.getByRole("button")).toHaveCount(count);
    expect(await list.evaluate(el => el.scrollHeight > el.clientHeight)).toBe(true);
    await list.getByRole("button").last().click();
    await expect(page.locator(".review-context h3")).toHaveText(view.concepts[count - 1].label);
    await expect(page.locator(".review-points > ol > li")).toHaveCount(1);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  }
});

for (const viewport of [{ width: 1536, height: 1024 }, { width: 1366, height: 768 }, { width: 1024, height: 768 }, { width: 390, height: 844 }]) {
  for (const progressState of ["available", "null", "error"]) test(`map has no global learning summary with ${progressState} progress at ${viewport.width}px`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await learningMapRoutes(page, learningMap(8), progressState !== "null");
    if (progressState === "error") await page.route("**/v1/materials/*/knowledge-structures/*/study-sessions/*/resume?*", route => json(route, { detail: "Unavailable" }, 503));
    await page.goto(`/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}`);
    if (progressState === "available") await expect(page.getByRole("button", { name: "繼續本次學習", exact: true })).toBeEnabled();
    for (const tabName of ["概念地圖", "總覽", "複習重點"]) {
      const tab = page.getByRole("tab", { name: tabName, exact: true });
      await tab.click();
      await expect(tab).toHaveAttribute("aria-selected", "true");
      const panel = page.getByRole("tabpanel");
      await expect(panel).toHaveAttribute("aria-labelledby", await tab.getAttribute("id") as string);
      await expect(page.getByText("學習摘要", { exact: true })).toHaveCount(0);
      await expect(page.locator(".map-summary-container, .map-learning-summary, .summary-progress")).toHaveCount(0);
      await expect(page.locator(".map-facts")).toContainText("8概念");
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      const adjacent = await page.locator(".map-tabs").evaluate(el => el.nextElementSibling?.classList.contains("map-content"));
      expect(adjacent).toBe(true);
      if (tabName === "複習重點") {
        if (progressState === "available") await expect(page.locator(".review-workspace")).toBeVisible();
        else await expect(page.locator(".review-empty")).toContainText(progressState === "error" ? "暫時無法顯示複習重點" : "練習後，幫你找出複習方向");
      }
      await page.evaluate(() => window.scrollTo(0, 0));
      await page.screenshot({ path: `/tmp/studydy-summary-${viewport.width}-${progressState}-${tabName}.png`, fullPage: true });
    }
    await page.getByRole("tab", { name: "複習重點", exact: true }).focus();
    await page.keyboard.press("Home");
    await expect(page.getByRole("tab", { name: "概念地圖", exact: true })).toBeFocused();
  });
}
