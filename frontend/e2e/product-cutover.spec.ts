import { expect, test, type Page, type Route } from "@playwright/test";

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
    resources: [],
    claims: [{
      claim_id: claimId,
      text: label === "Stack" ? "A stack follows LIFO order." : "An array stores contiguous values.",
      evidence: [{
        evidence_id: label === "Stack" ? evidenceId : `evidence:sha256:${"6".repeat(64)}`,
        page_ref: `page:sha256:${String(page).repeat(64)}`,
        page,
        block_order: 0,
        kind: "paragraph",
        source_locator: { page, block_id: `block:sha256:${String(page).repeat(64)}`, region: [1, 2, 30, 40] },
        quote: label === "Stack" ? "A stack follows LIFO order." : "An array stores contiguous values.",
      }],
    }],
  });
  return {
    schema: "knowledge-structure-view/v1",
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
  schema: "material-processing-run/v4", run_id: runId, material_id: materialId,
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
    deferred_concept_ids: [], status, started_at: "2026-09-05T00:01:00Z",
    completed_at: status === "completed" ? "2026-09-05T00:02:00Z" : null, event_watermark: 0,
  };
}

const progress = {
  schema: "learner-progress/v2", study_session_id: sessionId,
  knowledge_structure_revision: structureRevision, event_watermark: 0,
  current_concept_id: firstConcept, deferred_concept_ids: [],
  concept_states: [
    { concept_id: firstConcept, label: "Stack", status: "not_started", attempts: 0, correct_answers: 0, qualified_correct_items: 0, covered_claim_ids: [], weak_claim_ids: [], latest_is_correct: null },
    { concept_id: secondConcept, label: "Array", status: "not_started", attempts: 0, correct_answers: 0, qualified_correct_items: 0, covered_claim_ids: [], weak_claim_ids: [], latest_is_correct: null },
  ],
  weaknesses: [], next_action: { action: "assess", target_concept_id: firstConcept, target_claim_id: firstClaim, prerequisite_concept_ids: [], reason: "current_concept" },
  guidance_revision: `learner-guidance:sha256:${"f".repeat(64)}`,
};

async function json(route: Route, body: unknown, status = 200) {
  await route.fulfill({ status, json: body });
}

async function routes(page: Page) {
  await page.route("**/v1/session", (route) => route.fulfill({ status: 204 }));
  await page.route("**/v1/session/refresh", (route) => route.fulfill({ status: 204 }));
  await page.route(`**/v1/material-processing-runs/${runId}`, (route) => json(route, run));
  await page.route("**/v1/materials/*/knowledge-structures/**", (route) => json(route, structureView()));
  await page.route("**/v1/study-sessions", (route) => json(route, session(), 201));
  await page.route(`**/v1/study-sessions/${sessionId}`, (route) => json(route, session()));
  await page.route(`**/v1/study-sessions/${sessionId}/progress`, (route) => json(route, progress));
}

test("Document Tree layout and typed Relation overlay are both usable", async ({ page }) => {
  await routes(page);
  await page.goto(`/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}`);
  await expect(page.getByRole("heading", { name: "知識地圖", exact: true })).toBeVisible();
  await expect(page.locator(".concept-flow-edge.is-structural")).toHaveCount(3);
  await expect(page.locator(".concept-flow-edge.is-prerequisite")).toHaveCount(1);
  await page.locator(".concept-flow-edge.is-prerequisite .react-flow__edge-interaction").hover({ force: true });
  await expect(page.getByText("Stack must be learned before Array traversal.")).toBeVisible();
  await page.getByRole("button", { name: /收合 2 個概念/ }).first().click();
  await expect(page.locator(".react-flow__node")).toHaveCount(2);
  await page.getByRole("button", { name: /展開 2 個概念/ }).first().click();
  await expect(page.locator(".react-flow__node")).toHaveCount(4);
  await page.getByRole("tab", { name: "學習順序" }).click();
  await expect(page.locator(".learning-path li")).toHaveCount(2);
  await page.getByRole("button", { name: /Array/ }).click();
  await expect(page.getByRole("tab", { name: "概念地圖" })).toHaveAttribute("aria-selected", "true");
  await page.getByRole("button", { name: "教材概念：Stack" }).click();
  await expect(page.getByText("原始教材第 1 頁")).toBeVisible();
  await page.getByRole("button", { name: "從這個概念開始" }).click();
  await expect(page).toHaveURL(new RegExp(`/study-sessions/${sessionId}$`));
});

test("StudySession uses source-bound assessment and server feedback", async ({ page }) => {
  await routes(page);
  const assessmentRevision = `assessment:sha256:${"3".repeat(64)}`;
  const questionId = `question:sha256:${"4".repeat(64)}`;
  const options = ["LIFO", "FIFO", "RANDOM", "PRIORITY"].map((text, index) => ({ option_id: `option:sha256:${String(index + 1).repeat(64)}`, text }));
  await page.route(`**/v1/study-sessions/${sessionId}/assessments`, (route) => json(route, {
    schema: "single-choice-assessment/v2", assessment_revision: assessmentRevision,
    study_session_id: sessionId, knowledge_structure_revision: structureRevision,
    question_id: questionId, target_concept_id: firstConcept, target_claim_id: firstClaim,
    source_evidence_ids: [evidenceId], question_type: "single_choice",
    prompt: "根據教材，Stack 使用哪種順序？", options,
  }, 201));
  await page.route(`**/v1/study-sessions/${sessionId}/assessments/${encodeURIComponent(assessmentRevision)}/submissions`, (route) => json(route, {
    schema: "answer-feedback/v2", answer_event_id: "55555555-5555-4555-8555-555555555555",
    study_session_id: sessionId, assessment_revision: assessmentRevision,
    question_id: questionId, selected_option_id: options[0].option_id, is_correct: true,
    rationale: "A stack follows LIFO order.", source_evidence_ids: [evidenceId], event_number: 1,
    created_at: "2026-09-05T00:02:00Z",
  }, 201));
  await page.goto(`/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}/study-sessions/${sessionId}`);
  await page.getByRole("button", { name: "開始評量" }).click();
  await expect(page.getByRole("heading", { name: "根據教材，Stack 使用哪種順序？" })).toBeVisible();
  await page.getByLabel(/LIFO/).check();
  await page.getByRole("button", { name: "送出答案" }).click();
  await expect(page.getByRole("heading", { name: "答對了" })).toBeVisible();
  await expect(page.locator(".feedback-rationale")).toHaveText("A stack follows LIFO order.");
});
