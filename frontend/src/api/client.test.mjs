import assert from "node:assert/strict";
import test from "node:test";

import { ApiClientError, StudydyApiClient } from "./client.ts";

const materialId = "11111111-1111-4111-8111-111111111111";
const runId = "22222222-2222-4222-8222-222222222222";
const sessionId = "33333333-3333-4333-8333-333333333333";
const structureRevision = `knowledge-structure:sha256:${"a".repeat(64)}`;
const conceptId = `concept:sha256:${"b".repeat(64)}`;
const claimId = `claim:sha256:${"c".repeat(64)}`;
const evidenceId = `evidence:sha256:${"d".repeat(64)}`;
const blockId = `block:sha256:${"e".repeat(64)}`;

function runView() {
  return {
    schema: "material-processing-run/v4", run_id: runId, material_id: materialId,
    source_artifact_id: "44444444-4444-4444-8444-444444444444", status: "succeeded",
    progress_stage: "completed", completed_pages: 1, total_pages: 1, error_code: null,
    created_at: "2026-09-05T00:00:00Z", updated_at: "2026-09-05T00:00:01Z", completed_at: "2026-09-05T00:00:01Z",
    output_binding: {
      schema: "material-run-output-binding/v4", knowledge_structure_revision: structureRevision,
      runtime_lock_sha256: "f".repeat(64), page_count: 1, processing: "succeeded",
      quality: "accepted", decision: "retain", reason_codes: [], ocr_calls: 0, semantic_calls: 1,
    },
  };
}

function structureView() {
  return {
    schema: "knowledge-structure-view/v2", material_id: `material:sha256:${"1".repeat(64)}`,
    knowledge_structure_revision: structureRevision,
    status: { processing: "succeeded", quality: "accepted", decision: "retain", reason_codes: [] },
    document_tree: { material_id: `material:sha256:${"1".repeat(64)}`, sections: [{ section_id: `section:sha256:${"2".repeat(64)}`, title: "Stacks", order: 0, heading_evidence_id: null, concept_ids: [conceptId] }] },
    concepts: [{
      concept_id: conceptId, label: "Stack", aliases: [], section_ids: [`section:sha256:${"2".repeat(64)}`], source_pages: [1],
      claims: [{ claim_id: claimId, text: "A stack is LIFO.", evidence: [{ evidence_id: evidenceId, page_ref: `page:sha256:${"3".repeat(64)}`, page: 1, block_order: 0, kind: "paragraph", source: "native_text", source_locator: { page: 1, block_id: blockId, region: [1, 2, 3, 4] }, quote: "A stack is LIFO." }] }],
    }],
    relations: [], initial_learning_path: [{ position: 1, concept_id: conceptId, reason: "document_order" }], excluded_pages: [],
  };
}

test("material run and final structure use only final endpoints", async () => {
  const requests = [];
  const client = new StudydyApiClient(async (input) => {
    requests.push(String(input));
    return Response.json(String(input).includes("knowledge-structures") ? structureView() : runView());
  });
  assert.equal((await client.getMaterialRun(runId)).run_id, runId);
  const view = await client.getKnowledgeStructure({ materialId, structureRevision });
  assert.equal(view.concepts[0].concept_id, conceptId);
  assert.match(requests[1], /knowledge-structures/);
  assert.doesNotMatch(requests[1], /run_id=/);
});

test("unknown relation type and leaked private answer fail closed", async () => {
  const invalid = structureView();
  invalid.relations.push({ relation_id: `relation:sha256:${"9".repeat(64)}`, source_concept_id: conceptId, target_concept_id: conceptId, type: "related", learner_reason: "related" });
  const client = new StudydyApiClient(async () => Response.json(invalid));
  await assert.rejects(client.getKnowledgeStructure({ materialId, structureRevision }), (error) => error instanceof ApiClientError && error.kind === "schema");

  const assessment = {
    schema: "single-choice-assessment/v2", assessment_revision: `assessment:sha256:${"4".repeat(64)}`,
    study_session_id: sessionId, knowledge_structure_revision: structureRevision,
    question_id: `question:sha256:${"5".repeat(64)}`, target_concept_id: conceptId,
    target_claim_id: claimId, source_evidence_ids: [evidenceId], question_type: "single_choice",
    prompt: "Question", options: Array.from({ length: 4 }, (_, index) => ({ option_id: `option:sha256:${String(index + 1).repeat(64)}`, text: String(index) })),
    correct_option_id: `option:sha256:${"1".repeat(64)}`,
  };
  const leaked = new StudydyApiClient(async () => Response.json(assessment));
  await assert.rejects(leaked.getAssessment(sessionId, assessment.assessment_revision), (error) => error instanceof ApiClientError && error.kind === "schema");
});

test("session creation is coalesced and safe API errors stay fixed", async () => {
  let calls = 0;
  const client = new StudydyApiClient(async () => { calls += 1; return new Response(null, { status: 204 }); });
  await Promise.all([client.ensureSession(), client.ensureSession(), client.ensureSession()]);
  assert.equal(calls, 1);

  const paths = [];
  const recovered = new StudydyApiClient(async (input) => {
    paths.push(String(input));
    if (String(input).endsWith("/refresh")) {
      return Response.json({ schema: "api-error/v1", request_id: sessionId, reason_code: "SESSION_REQUIRED", retryable: false, message: "Request could not be completed." }, { status: 401 });
    }
    return new Response(null, { status: 204 });
  });
  await recovered.ensureSession();
  assert.deepEqual(paths, ["/v1/session/refresh", "/v1/session"]);

  const failed = new StudydyApiClient(async () => Response.json({ schema: "api-error/v1", request_id: sessionId, reason_code: "STORAGE_UNAVAILABLE", retryable: true, message: "Request could not be completed." }, { status: 503 }));
  await assert.rejects(failed.getMaterialRun(runId), (error) => error instanceof ApiClientError && error.reasonCode === "STORAGE_UNAVAILABLE" && error.retryable);
});
