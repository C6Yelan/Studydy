import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { ApiClientError, StudydyApiClient } from "./client.ts";

const requestId = "6f9619ff-8b86-4e3a-a2f1-2bb9424d5c71";
const materialId = "9f9619ff-8b86-4e3a-a2f1-2bb9424d5c72";
const artifactId = "af9619ff-8b86-4e3a-a2f1-2bb9424d5c73";
const runId = "bf9619ff-8b86-4e3a-a2f1-2bb9424d5c74";
const mapRevision = `knowledge-map:sha256:${"d".repeat(64)}`;
const phase06Fixtures = JSON.parse(readFileSync(
  new URL("../../../backend/tests/runtime/fixtures/phase06-public-fixtures-v1.json", import.meta.url),
  "utf8",
));

function apiError(status, reasonCode) {
  return Response.json({
    schema: "api-error/v1",
    request_id: requestId,
    reason_code: reasonCode,
    retryable: status === 503,
    message: "Request could not be completed.",
  }, { status });
}

function pendingRun() {
  return {
    schema: "material-processing-run/v3",
    run_id: runId,
    material_id: materialId,
    source_artifact_id: artifactId,
    status: "pending",
    progress_stage: "queued",
    completed_pages: 0,
    total_pages: null,
    output_binding: null,
    error_code: null,
    created_at: "2026-08-19T00:00:00Z",
    updated_at: "2026-08-19T00:00:00Z",
    completed_at: null,
  };
}

function successfulRun() {
  return {
    ...pendingRun(),
    status: "succeeded",
    progress_stage: "completed",
    completed_pages: 40,
    total_pages: 40,
    output_binding: {
      schema: "material-run-output-binding/v3",
      producer_bundle_id: `text-first-producer-bundle:sha256:${"1".repeat(64)}`,
      producer_run_id: `text-first-run:${runId}`,
      concept_evidence_output_id: `concept-evidence-output:sha256:${"2".repeat(64)}`,
      study_material_output_revision: `study-material-output:sha256:${"3".repeat(64)}`,
      knowledge_map_revision: mapRevision,
      runtime_binding_sha256: "4".repeat(64),
      page_count: 40,
      processing: "succeeded",
      quality: "needs_review",
      decision: "review",
      reason_codes: ["SEMANTIC_REVIEW_REQUIRED"],
      ocr_calls: 40,
      concept_calls: 40,
    },
    updated_at: "2026-08-19T00:01:00Z",
    completed_at: "2026-08-19T00:01:00Z",
  };
}

function mapView() {
  const pageRef = `page:sha256:${"5".repeat(64)}`;
  return {
    schema: "knowledge-map-view/v10",
    material_ref: `material:sha256:${"6".repeat(64)}`,
    knowledge_map_revision: mapRevision,
    source_output_id: `study-material-output:sha256:${"3".repeat(64)}`,
    status: {
      processing: "partial",
      quality: "needs_review",
      decision: "review",
      reason_codes: ["KNOWLEDGE_MAP_REVIEW_REQUIRED"],
    },
    concepts: [{
      formal_concept_id: `formal-concept:sha256:${"7".repeat(64)}`,
      label: "Public concept",
      aliases: [],
      claims: [{
        claim_id: `claim:sha256:${"9".repeat(64)}`,
        text: "Public definition",
        evidence: [{
          evidence_id: `evidence:sha256:${"8".repeat(64)}`,
          page_ref: pageRef,
          page_number: 40,
          kind: "paragraph",
          region: { coordinate_space: "unrotated_pdf_points", bbox: [72, 80, 300, 120] },
        }],
      }],
      source_concept_ids: [`concept:sha256:${"a".repeat(64)}`],
      source_page_numbers: [40],
      supplementary_resources: [],
      quality: "needs_review",
      decision: "review",
      reason_codes: ["SEMANTIC_REVIEW_REQUIRED"],
    }],
    concept_diagnostics: {
      possible_pairs: 0,
      candidate_pairs: 0,
      selected_pairs: 0,
      pair_ceiling: 16,
      qwen_same_pairs: 0,
      qwen_distinct_pairs: 0,
      qwen_uncertain_pairs: 0,
      verifier_requested_pairs: 0,
      verifier_scored_pairs: 0,
      verifier_allowed_pairs: 0,
      verifier_vetoed_pairs: 0,
      verifier_unsupported_pairs: 0,
      verifier_failed_pairs: 0,
      source_concepts_before: 1,
      canonical_concepts_after: 1,
      duplicate_delta: 0,
      coverage_before: 1,
      coverage_after: 1,
    },
    supplementary_resources: {
      processing: "succeeded",
      quality: "needs_review",
      decision: "review",
      reason_codes: [],
      binding: {
        context_revision: `map-resource-context:sha256:${"1".repeat(64)}`,
        library_revision: `resource-library:sha256:${"2".repeat(64)}`,
        matching_policy: "resource-context-exact-distinct-source/v3",
        promotion_policy: "resource-formal-concept-promotion/v1",
      },
      diagnostics: {
        matches: 0,
        promoted_matches: 0,
        promoted_resources: 0,
        dropped_matches: 0,
        split_review_matches: 0,
      },
      decisions: [],
    },
    document_tree: {
      root: {
        material_ref: `material:sha256:${"6".repeat(64)}`,
        section_ids: [`document-section:sha256:${"1".repeat(64)}`],
      },
      sections: [{
        section_id: `document-section:sha256:${"1".repeat(64)}`,
        label: "第 40 頁未命名段落",
        label_source: "unheaded_fallback",
        heading_evidence_id: null,
        source_order: {
          evidence_id: `evidence:sha256:${"8".repeat(64)}`,
          page_ref: pageRef,
          page_number: 40,
          reading_order: 0,
        },
        concept_ids: [`formal-concept:sha256:${"7".repeat(64)}`],
      }],
    },
    initial_learning_path: [{
      step_number: 1,
      formal_concept_id: `formal-concept:sha256:${"7".repeat(64)}`,
      placement_reason: "依教材第 40 頁的首次 Claim Evidence 安排。",
      order_basis: {
        section_id: `document-section:sha256:${"1".repeat(64)}`,
        page_ref: pageRef,
        page_number: 40,
        reading_order: 0,
        evidence_id: `evidence:sha256:${"8".repeat(64)}`,
      },
    }],
    excluded_pages: [],
  };
}

test("protected request 的 401 會 refresh 後重送", async () => {
  const paths = [];
  let protectedCalls = 0;
  const client = new StudydyApiClient(async (input) => {
    const path = String(input);
    paths.push(path);
    if (path.endsWith("/refresh")) return new Response(null, { status: 204 });
    protectedCalls += 1;
    return protectedCalls === 1 ? apiError(401, "SESSION_REQUIRED") : Response.json(pendingRun());
  });
  assert.equal((await client.getMaterialRun(runId)).run_id, runId);
  assert.deepEqual(paths, [
    "/v1/session/refresh",
    `/v1/material-processing-runs/${runId}`,
    "/v1/session/refresh",
    `/v1/material-processing-runs/${runId}`,
  ]);
});

test("parallel ensureSession 共用一個 refresh request", async () => {
  let refreshCalls = 0;
  const client = new StudydyApiClient(async () => {
    refreshCalls += 1;
    await Promise.resolve();
    return new Response(null, { status: 204 });
  });
  await Promise.all([client.ensureSession(), client.ensureSession(), client.ensureSession()]);
  assert.equal(refreshCalls, 1);
});

test("upload network retry 沿用同一 idempotency key", async () => {
  const keys = [];
  let calls = 0;
  const client = new StudydyApiClient(async (input, init) => {
    if (String(input).endsWith("/refresh")) return new Response(null, { status: 204 });
    calls += 1;
    keys.push(new Headers(init?.headers).get("Idempotency-Key"));
    if (calls === 1) throw new TypeError("offline");
    return Response.json({
      schema: "material/v1",
      material_id: materialId,
      source_artifact_id: artifactId,
      source_sha256: "a".repeat(64),
      size_bytes: 8,
    }, { status: 201 });
  });
  await client.createMaterial(new Blob(["%PDF-1.7"], { type: "application/pdf" }), "same-intent");
  assert.deepEqual(keys, ["same-intent", "same-intent"]);
});

test("upload invalid PDF, network and storage failures keep distinct messages", async () => {
  for (const [status, reasonCode, message] of [
    [400, "MATERIAL_PDF_INVALID", /損毀、加密或無法開啟/],
    [503, "STORAGE_UNAVAILABLE", /資料服務暫時無法使用/],
  ]) {
    let calls = 0;
    const client = new StudydyApiClient(async () => {
      calls += 1;
      return calls === 1 ? new Response(null, { status: 204 }) : apiError(status, reasonCode);
    });
    await assert.rejects(
      client.createMaterial(new Blob(["%PDF-1.7"], { type: "application/pdf" }), `failure-${reasonCode}`),
      (error) => error instanceof ApiClientError && message.test(error.message),
    );
  }

  const networkClient = new StudydyApiClient(async (input) => {
    if (String(input).endsWith("/refresh")) return new Response(null, { status: 204 });
    throw new TypeError("offline");
  });
  await assert.rejects(
    networkClient.createMaterial(new Blob(["%PDF-1.7"], { type: "application/pdf" }), "network-failure"),
    (error) => error instanceof ApiClientError
      && error.reasonCode === "NETWORK_ERROR"
      && /網路連線失敗/.test(error.message),
  );
});

test("terminal binding 不完整時拒絕假成功", async () => {
  const invalid = { ...successfulRun(), output_binding: { ...successfulRun().output_binding, quality: "accepted" } };
  let calls = 0;
  const client = new StudydyApiClient(async () => {
    calls += 1;
    return calls === 1 ? new Response(null, { status: 204 }) : Response.json(invalid);
  });
  await assert.rejects(
    client.getMaterialRun(runId),
    (error) => error instanceof ApiClientError && error.reasonCode === "RESPONSE_SCHEMA_MISMATCH",
  );
});

test("terminal binding 接受單頁多批 concept calls 並拒絕負數", async () => {
  const accepted = successfulRun();
  accepted.output_binding.page_count = 1;
  accepted.completed_pages = 1;
  accepted.total_pages = 1;
  accepted.output_binding.ocr_calls = 1;
  accepted.output_binding.concept_calls = 3;
  let calls = 0;
  const acceptedClient = new StudydyApiClient(async () => {
    calls += 1;
    return calls === 1 ? new Response(null, { status: 204 }) : Response.json(accepted);
  });
  assert.equal((await acceptedClient.getMaterialRun(runId)).output_binding.concept_calls, 3);

  const invalid = successfulRun();
  invalid.output_binding.concept_calls = -1;
  calls = 0;
  const rejectedClient = new StudydyApiClient(async () => {
    calls += 1;
    return calls === 1 ? new Response(null, { status: 204 }) : Response.json(invalid);
  });
  await assert.rejects(
    rejectedClient.getMaterialRun(runId),
    (error) => error instanceof ApiClientError && error.reasonCode === "RESPONSE_SCHEMA_MISMATCH",
  );
});

test("run v3 closed progress shape rejects v2, decreasing bounds and fake terminal", async () => {
  const invalidRuns = [
    { ...pendingRun(), schema: "material-processing-run/v2" },
    { ...pendingRun(), status: "running", progress_stage: "page_evidence", completed_pages: 3, total_pages: 2 },
    { ...pendingRun(), status: "running", progress_stage: "publishing", completed_pages: 1, total_pages: 2 },
    { ...successfulRun(), completed_pages: 39 },
  ];
  for (const invalid of invalidRuns) {
    let calls = 0;
    const client = new StudydyApiClient(async () => {
      calls += 1;
      return calls === 1 ? new Response(null, { status: 204 }) : Response.json(invalid);
    });
    await assert.rejects(
      client.getMaterialRun(runId),
      (error) => error instanceof ApiClientError && error.reasonCode === "RESPONSE_SCHEMA_MISMATCH",
    );
  }
});

test("Map v10 使用 exact run/revision 並要求 tree、path 與 claim PDF locator", async () => {
  const paths = [];
  const client = new StudydyApiClient(async (input) => {
    paths.push(String(input));
    return String(input).endsWith("/refresh")
      ? new Response(null, { status: 204 })
      : Response.json(mapView());
  });
  const view = await client.getKnowledgeMap({ materialId, runId, mapRevision });
  assert.equal(view.concepts[0].claims[0].evidence[0].page_number, 40);
  assert.equal(view.status.processing, "partial");
  assert.equal(view.excluded_pages.length, 0);
  assert.deepEqual(view.initial_learning_path, mapView().initial_learning_path);
  assert.deepEqual(paths, [
    "/v1/session/refresh",
    `/v1/materials/${materialId}/knowledge-maps/${encodeURIComponent(mapRevision)}?run_id=${runId}`,
  ]);

  const foreign = mapView();
  foreign.concepts[0].claims[0].evidence[0].page_number = 41;
  let calls = 0;
  const invalidClient = new StudydyApiClient(async () => {
    calls += 1;
    return calls === 1 ? new Response(null, { status: 204 }) : Response.json(foreign);
  });
  await assert.rejects(
    invalidClient.getKnowledgeMap({ materialId, runId, mapRevision }),
    (error) => error instanceof ApiClientError && error.reasonCode === "RESPONSE_SCHEMA_MISMATCH",
  );
});

test("Map v10 補充資源只接受 HTTP(S) public URL", async () => {
  const invalid = mapView();
  const sourceConceptId = invalid.concepts[0].source_concept_ids[0];
  invalid.concepts[0].supplementary_resources.push({
    promotion_id: `resource-promotion:sha256:${"1".repeat(64)}`,
    resource_concept_id: `resource-concept:sha256:${"2".repeat(64)}`,
    resource_id: `resource:sha256:${"3".repeat(64)}`,
    label: "Public concept",
    title: "Unsafe resource",
    authors: ["Author"],
    source_url: "javascript:alert(1)",
    citation: "Citation",
    license: "CC BY 4.0",
    license_url: "https://creativecommons.org/licenses/by/4.0/",
    use_boundary: "Attribution required",
    page_numbers: [1],
    resource_evidence_ids: [`resource-evidence:sha256:${"4".repeat(64)}`],
    match_ids: [`resource-match:sha256:${"5".repeat(64)}`],
    study_concept_ids: [sourceConceptId],
    match_reason: "EXACT_NORMALIZED_LABEL",
  });
  Object.assign(invalid.supplementary_resources.diagnostics, {
    matches: 1,
    promoted_matches: 1,
    promoted_resources: 1,
  });
  let calls = 0;
  const client = new StudydyApiClient(async () => {
    calls += 1;
    return calls === 1 ? new Response(null, { status: 204 }) : Response.json(invalid);
  });
  await assert.rejects(
    client.getKnowledgeMap({ materialId, runId, mapRevision }),
    (error) => error instanceof ApiClientError && error.reasonCode === "RESPONSE_SCHEMA_MISMATCH",
  );
});

test("Assessment public parser 接受四個選項並拒絕 answer leak", async () => {
  const read = async (value) => {
    let calls = 0;
    const client = new StudydyApiClient(async () => {
      calls += 1;
      return calls === 1 ? new Response(null, { status: 204 }) : Response.json(value, { status: 201 });
    });
    return client.createAssessment(
      phase06Fixtures.success.study_session_id,
      { schema: "assessment-create/v1", target_claim_id: phase06Fixtures.success.target_claim_id },
      "assessment-intent",
    );
  };
  assert.equal((await read(phase06Fixtures.success)).options.length, 4);
  for (const mutation of [
    (value) => value.options.pop(),
    (value) => { value.correct_option_id = value.options[0].option_id; },
    (value) => { value.private_answer = { option_id: value.options[0].option_id }; },
    (value) => { value.private_generation_provenance = { model: "private" }; },
    (value) => { value.semantic_identity = "assessment-semantic:sha256:private"; },
    (value) => { value.semantic_focus = "private answer-bearing focus"; },
  ]) {
    const invalid = structuredClone(phase06Fixtures.success);
    mutation(invalid);
    await assert.rejects(
      read(invalid),
      (error) => error instanceof ApiClientError && error.reasonCode === "RESPONSE_SCHEMA_MISMATCH",
    );
  }
});

test("Answer feedback parser 只接受 post-submit public fields", async () => {
  const feedback = phase06Fixtures.reassessment;
  let calls = 0;
  const client = new StudydyApiClient(async () => {
    calls += 1;
    return calls === 1 ? new Response(null, { status: 204 }) : Response.json(feedback, { status: 201 });
  });
  assert.equal((await client.submitAssessmentAnswer(
    feedback.study_session_id,
    feedback.assessment_revision,
    {
      schema: "answer-submission-create/v1",
      question_id: feedback.question_id,
      selected_option_id: feedback.selected_option_id,
    },
    "answer-intent",
  )).is_correct, true);

  const leaked = { ...feedback, correct_option_id: feedback.selected_option_id };
  calls = 0;
  const leakedClient = new StudydyApiClient(async () => {
    calls += 1;
    return calls === 1 ? new Response(null, { status: 204 }) : Response.json(leaked, { status: 201 });
  });
  await assert.rejects(
    leakedClient.submitAssessmentAnswer(
      feedback.study_session_id,
      feedback.assessment_revision,
      {
        schema: "answer-submission-create/v1",
        question_id: feedback.question_id,
        selected_option_id: feedback.selected_option_id,
      },
      "answer-leak-intent",
    ),
    (error) => error instanceof ApiClientError && error.reasonCode === "RESPONSE_SCHEMA_MISMATCH",
  );
});

test("StudySession-scoped learning projections 保持 revision 與 action bindings", async () => {
  const read = async (method, value) => {
    let calls = 0;
    const client = new StudydyApiClient(async () => {
      calls += 1;
      return calls === 1 ? new Response(null, { status: 204 }) : Response.json(value);
    });
    return client[method](phase06Fixtures.success.study_session_id);
  };
  assert.equal((await read("getLearningState", phase06Fixtures.low_data)).concept_states[0].status, "not_started");
  assert.equal((await read("getWeakness", phase06Fixtures.weakness)).findings[0].category, "observed_weak");
  assert.equal((await read("getAdaptivePlan", phase06Fixtures.review_needed)).plan.primary_step.action, "review");

  const falseMastery = structuredClone(phase06Fixtures.low_data);
  falseMastery.all_mastered = true;
  await assert.rejects(
    read("getLearningState", falseMastery),
    (error) => error instanceof ApiClientError && error.reasonCode === "RESPONSE_SCHEMA_MISMATCH",
  );
  const splitDecision = structuredClone(phase06Fixtures.review_needed);
  splitDecision.suggestion.action = "practice";
  await assert.rejects(
    read("getAdaptivePlan", splitDecision),
    (error) => error instanceof ApiClientError && error.reasonCode === "RESPONSE_SCHEMA_MISMATCH",
  );
});

test("Map v10 closed contract rejects retired graph fields", async () => {
  const invalid = mapView();
  invalid.relations = [];
  let calls = 0;
  const client = new StudydyApiClient(async () => {
    calls += 1;
    return calls === 1 ? new Response(null, { status: 204 }) : Response.json(invalid);
  });
  await assert.rejects(
    client.getKnowledgeMap({ materialId, runId, mapRevision }),
    (error) => error instanceof ApiClientError && error.reasonCode === "RESPONSE_SCHEMA_MISMATCH",
  );
});

test("Map v10 recursively rejects unexpected、duplicate、nonfinite、type 與 count mutations", async (context) => {
  const mutations = {
    unexpected: (view) => { view.concepts[0].unexpected_field = true; },
    duplicate: (view) => { view.concepts.push(structuredClone(view.concepts[0])); },
    nonfinite: (view) => { view.concepts[0].claims[0].evidence[0].region.bbox[0] = Number.NaN; },
    type: (view) => { view.concepts[0].claims[0].evidence[0].page_number = true; },
    count: (view) => { view.concepts[0].claims = []; },
    reference: (view) => { view.concepts[0].claims[0].evidence[0].page_number = 41; },
    topology: (view) => { view.document_tree.root.section_ids = []; },
    group_anchor: (view) => {
      view.initial_learning_path[0].order_basis.evidence_id = `evidence:sha256:${"0".repeat(64)}`;
    },
    group_label: (view) => { view.document_tree.sections[0].section_id = "invalid"; },
    path: (view) => { view.initial_learning_path[0].placement_reason = ""; },
    excluded: (view) => {
      view.status.processing = "succeeded";
      view.excluded_pages = [{
        page_ref: `page:sha256:${"b".repeat(64)}`,
        page_number: 2,
        page_evidence_id: null,
        last_stage: "page_evidence",
        processing: "failed",
        quality: "needs_review",
        decision: "reject",
        reason_codes: ["NO_USABLE_EVIDENCE"],
      }];
    },
  };
  for (const [name, mutate] of Object.entries(mutations)) {
    await context.test(name, async () => {
      const invalid = mapView();
      mutate(invalid);
      let calls = 0;
      const client = new StudydyApiClient(async () => {
        calls += 1;
        return calls === 1 ? new Response(null, { status: 204 }) : Response.json(invalid);
      });
      await assert.rejects(
        client.getKnowledgeMap({ materialId, runId, mapRevision }),
        (error) => error instanceof ApiClientError && error.reasonCode === "RESPONSE_SCHEMA_MISMATCH",
      );
    });
  }
});

test("client surface 只公開目前 frozen downstream methods", () => {
  const client = new StudydyApiClient(async () => new Response(null, { status: 204 }));
  for (const name of [
    "createStudySession", "getStudySession", "getStudyContext", "completeStudySession",
    "createAssessment", "getAssessment", "submitAssessmentAnswer", "getLearningState",
    "getWeakness", "getAdaptivePlan", "applyAdaptivePlan",
  ]) {
    assert.equal(typeof client[name], "function");
  }
  for (const name of ["submitLearningUpdate", "getLearningResourceResult"]) {
    assert.equal(client[name], undefined);
  }
  assert.equal(client.sourceArtifactUrl(artifactId), `/v1/artifacts/${artifactId}`);
  assert.equal(client.sourceArtifactUrl(artifactId, 40), `/v1/artifacts/${artifactId}#page=40`);
});
