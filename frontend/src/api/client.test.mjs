import assert from "node:assert/strict";
import test from "node:test";

import { ApiClientError, StudydyApiClient } from "./client.ts";

const requestId = "6f9619ff-8b86-4e3a-a2f1-2bb9424d5c71";
const materialId = "9f9619ff-8b86-4e3a-a2f1-2bb9424d5c72";
const artifactId = "af9619ff-8b86-4e3a-a2f1-2bb9424d5c73";
const runId = "bf9619ff-8b86-4e3a-a2f1-2bb9424d5c74";
const mapRevision = `knowledge-map:sha256:${"d".repeat(64)}`;
const pathRevision = `initial-learning-path:sha256:${"e".repeat(64)}`;
const resourceRevision = `learning-resource-result:sha256:${"c".repeat(64)}`;
const assessmentRevision = `assessment:sha256:${"f".repeat(64)}`;
const stateRevision = `learning-state:sha256:${"8".repeat(64)}`;

function apiError(status, reasonCode) {
  return Response.json(
    {
      schema: "api-error/v1",
      request_id: requestId,
      reason_code: reasonCode,
      retryable: status === 503,
      message: "Request could not be completed.",
    },
    { status },
  );
}

function pendingRun() {
  return {
    schema: "material-processing-run/v1",
    run_id: runId,
    material_id: materialId,
    source_artifact_id: artifactId,
    status: "pending",
    catalog_revision: null,
    output_binding: null,
    error_code: null,
    created_at: "2026-08-15T00:00:00Z",
    updated_at: "2026-08-15T00:00:00Z",
    completed_at: null,
  };
}

function successfulRun() {
  return {
    ...pendingRun(),
    status: "succeeded",
    catalog_revision: `resource-catalog:sha256:${"b".repeat(64)}`,
    output_binding: {
      schema: "material-run-output-binding/v1",
      study_material_output_revision: `study-material-output:sha256:${"a".repeat(64)}`,
      catalog_revision: `resource-catalog:sha256:${"b".repeat(64)}`,
      learning_resource_result_revision: `learning-resource-result:sha256:${"c".repeat(64)}`,
      knowledge_map_revision: `knowledge-map:sha256:${"d".repeat(64)}`,
      learning_path_revision: `initial-learning-path:sha256:${"e".repeat(64)}`,
      assessment_revision: `assessment:sha256:${"f".repeat(64)}`,
      processing: "succeeded",
      quality: "accepted",
      decision: "retain",
      reason_code: "DEVELOPMENT_OUTPUT_ACCEPTED",
      provider_call_counts: {
        page_structure: 1,
        visual_alignment_adjudication: 0,
        concept_candidate: 1,
        concept_content: 1,
        total: 3,
      },
      development_only: true,
    },
    updated_at: "2026-08-15T00:01:00Z",
    completed_at: "2026-08-15T00:01:00Z",
  };
}

function knowledgeMapView() {
  const evidence = { material_ref: "material-ref-1" };
  return {
    schema: "knowledge-map-view/v1",
    material_ref: "material-ref-1",
    knowledge_map_revision: mapRevision,
    learning_path_revision: pathRevision,
    status: { processing: "succeeded" },
    concepts: [{ evidence: [evidence] }],
    relations: [{ evidence: [evidence] }],
    review_items: [{ evidence: [evidence] }],
    path: { ordered_concept_ids: [] },
    limitations: [],
  };
}

function learningResourceResult() {
  return {
    schema: "learning-resource-result-view/v1",
    result_revision: resourceRevision,
    source_study_material_output_revision: `study-material-output:sha256:${"a".repeat(64)}`,
    catalog_revision: `resource-catalog:sha256:${"b".repeat(64)}`,
    subject: "data_structures",
    resources: [{ source_locator: "https://example.edu/stack.pdf" }],
    produced_at: "2026-08-15T00:01:00Z",
    run_id: runId,
    processing: "succeeded",
    quality: "accepted",
    decision: "retain",
    reason_code: "RESOURCE_RESULT_ACCEPTED",
  };
}

function assessmentView() {
  return {
    schema: "assessment-view/v1",
    assessment_view_id: `assessment-view:sha256:${"7".repeat(64)}`,
    version: "1",
    knowledge_map_revision: mapRevision,
    learning_path_revision: pathRevision,
    scoring_rule_version: "single-choice-exact/v1",
    questions: [{ prompt: "哪一項敘述由教材 Evidence 直接支持？" }],
    practice_sets: [],
    processing: "succeeded",
    quality: "accepted",
    decision: "retain",
    reason_code: "ASSESSMENT_ACCEPTED",
  };
}

function learningStateView() {
  return {
    schema: "learning-state-view/v1",
    state_revision: stateRevision,
    knowledge_map_revision: mapRevision,
    learning_path_revision: pathRevision,
    assessment_id: `assessment:sha256:${"6".repeat(64)}`,
    assessment_revision: assessmentRevision,
    scoring_rule_version: "single-choice-exact/v1",
    source_answer_event_ids: [],
    source_learning_event_ids: [],
    mastery: [],
    weaknesses: [],
    suggestion: {},
    processing: "succeeded",
    quality: "accepted",
    decision: "retain",
    reason_code: "LEARNING_STATE_ACCEPTED",
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
    return protectedCalls === 1
      ? apiError(401, "SESSION_REQUIRED")
      : Response.json(pendingRun(), { status: 200 });
  });
  const run = await client.getMaterialRun(runId);
  assert.equal(run.run_id, runId);
  assert.deepEqual(paths, [
    "/v1/session/refresh",
    `/v1/material-processing-runs/${runId}`,
    "/v1/session/refresh",
    `/v1/material-processing-runs/${runId}`,
  ]);
});

test("network retry 沿用同一 user intent 的 idempotency key", async () => {
  const keys = [];
  let uploadCalls = 0;
  const client = new StudydyApiClient(async (input, init) => {
    if (String(input).endsWith("/refresh")) return new Response(null, { status: 204 });
    uploadCalls += 1;
    keys.push(new Headers(init?.headers).get("Idempotency-Key"));
    if (uploadCalls === 1) throw new TypeError("network unavailable");
    return Response.json(
      {
        schema: "material/v1",
        material_id: materialId,
        source_artifact_id: artifactId,
        source_sha256: "a".repeat(64),
        size_bytes: 8,
      },
      { status: 201 },
    );
  });
  await client.createMaterial(new Blob(["%PDF-1.7"], { type: "application/pdf" }));
  assert.equal(keys.length, 2);
  assert.equal(keys[0], keys[1]);
  assert.ok(keys[0]);
});

test("terminal run 的 binding 與 failure shape 不一致時拒絕假成功", async () => {
  const invalidRuns = [
    { ...pendingRun(), status: "succeeded", completed_at: "2026-08-15T00:01:00Z" },
    { ...pendingRun(), status: "failed", error_code: null, completed_at: "2026-08-15T00:01:00Z" },
  ];
  for (const invalidRun of invalidRuns) {
    let call = 0;
    const client = new StudydyApiClient(async () => {
      call += 1;
      return call === 1
        ? new Response(null, { status: 204 })
        : Response.json(invalidRun, { status: 200 });
    });
    await assert.rejects(
      client.getMaterialRun(runId),
      (error) => error instanceof ApiClientError
        && error.reasonCode === "RESPONSE_SCHEMA_MISMATCH",
    );
  }
});

test("Knowledge Map 與 Resource request 使用同一組 exact material、run 與 revisions", async () => {
  const paths = [];
  const client = new StudydyApiClient(async (input) => {
    const path = String(input);
    paths.push(path);
    if (path.endsWith("/refresh")) return new Response(null, { status: 204 });
    if (path.includes("knowledge-map-views")) return Response.json(knowledgeMapView(), { status: 200 });
    return Response.json(learningResourceResult(), { status: 200 });
  });

  const map = await client.getKnowledgeMap({ materialId, runId, mapRevision, pathRevision });
  const resources = await client.getLearningResourceResult({
    materialId,
    runId,
    resultRevision: resourceRevision,
  });

  assert.equal(map.knowledge_map_revision, mapRevision);
  assert.equal(resources.result_revision, resourceRevision);
  assert.deepEqual(paths, [
    "/v1/session/refresh",
    `/v1/materials/${materialId}/knowledge-map-views/${encodeURIComponent(mapRevision)}/${encodeURIComponent(pathRevision)}?run_id=${runId}`,
    `/v1/materials/${materialId}/learning-resource-results/${encodeURIComponent(resourceRevision)}?run_id=${runId}`,
  ]);
});

test("Knowledge Map response revision 與 Evidence material binding fail closed", async () => {
  const invalidMaps = [
    ["map revision", { ...knowledgeMapView(), knowledge_map_revision: `knowledge-map:sha256:${"9".repeat(64)}` }],
    ["path revision", { ...knowledgeMapView(), learning_path_revision: `initial-learning-path:sha256:${"9".repeat(64)}` }],
  ];
  const foreignEvidenceCases = [
    ["concept", (map) => map.concepts[0].evidence[0]],
    ["relation", (map) => map.relations[0].evidence[0]],
    ["review item", (map) => map.review_items[0].evidence[0]],
  ];
  for (const [caseName, findEvidence] of foreignEvidenceCases) {
    const invalidMap = JSON.parse(JSON.stringify(knowledgeMapView()));
    findEvidence(invalidMap).material_ref = `foreign-material-for-${caseName}`;
    invalidMaps.push([caseName, invalidMap]);
  }
  for (const [caseName, invalidMap] of invalidMaps) {
    let call = 0;
    const client = new StudydyApiClient(async () => {
      call += 1;
      return call === 1
        ? new Response(null, { status: 204 })
        : Response.json(invalidMap, { status: 200 });
    });
    await assert.rejects(
      client.getKnowledgeMap({ materialId, runId, mapRevision, pathRevision }),
      (error) => error instanceof ApiClientError
        && error.reasonCode === "RESPONSE_SCHEMA_MISMATCH",
      caseName,
    );
  }
});

test("Assessment read 使用 exact output、map、path 與 assessment revisions", async () => {
  const paths = [];
  const client = new StudydyApiClient(async (input) => {
    const path = String(input);
    paths.push(path);
    return path.endsWith("/refresh")
      ? new Response(null, { status: 204 })
      : Response.json(assessmentView(), { status: 200 });
  });
  const assessment = await client.getAssessment({
    materialId,
    outputRevision: successfulRun().output_binding.study_material_output_revision,
    mapRevision,
    pathRevision,
    assessmentRevision,
  });
  assert.equal(assessment.questions[0].prompt, "哪一項敘述由教材 Evidence 直接支持？");
  assert.deepEqual(paths, [
    "/v1/session/refresh",
    `/v1/materials/${materialId}/assessments/${encodeURIComponent(assessmentRevision)}?output_revision=${encodeURIComponent(successfulRun().output_binding.study_material_output_revision)}&map_revision=${encodeURIComponent(mapRevision)}&path_revision=${encodeURIComponent(pathRevision)}`,
  ]);
});

test("Assessment 拒絕 answer key 與 response revision mismatch", async () => {
  const withAnswerKey = assessmentView();
  withAnswerKey.questions[0].answer_key_option_id = "option-2";
  const wrongMap = { ...assessmentView(), knowledge_map_revision: `knowledge-map:sha256:${"9".repeat(64)}` };
  const wrongPath = { ...assessmentView(), learning_path_revision: `initial-learning-path:sha256:${"9".repeat(64)}` };
  for (const invalidAssessment of [withAnswerKey, wrongMap, wrongPath]) {
    let call = 0;
    const client = new StudydyApiClient(async () => {
      call += 1;
      return call === 1
        ? new Response(null, { status: 204 })
        : Response.json(invalidAssessment, { status: 200 });
    });
    await assert.rejects(
      client.getAssessment({
        materialId,
        outputRevision: successfulRun().output_binding.study_material_output_revision,
        mapRevision,
        pathRevision,
        assessmentRevision,
      }),
      (error) => error instanceof ApiClientError
        && error.reasonCode === "RESPONSE_SCHEMA_MISMATCH",
    );
  }
});

test("LearningUpdate body 僅含允許欄位，network retry 沿用同一 idempotency key", async () => {
  const requests = [];
  let postCalls = 0;
  const client = new StudydyApiClient(async (input, init) => {
    const path = String(input);
    if (path.endsWith("/refresh")) return new Response(null, { status: 204 });
    postCalls += 1;
    requests.push({
      path,
      key: new Headers(init?.headers).get("Idempotency-Key"),
      body: JSON.parse(String(init?.body)),
    });
    if (postCalls === 1) throw new TypeError("network unavailable");
    return Response.json(learningStateView(), { status: 201 });
  });
  const update = {
    schema: "learning-update-create/v1",
    material_id: materialId,
    map_revision: mapRevision,
    path_revision: pathRevision,
    assessment_revision: assessmentRevision,
    responses: [{ question_id: "question-1", selected_option_id: "option-2" }],
  };
  const completed = await client.submitLearningUpdate(update, "same-learning-intent");
  assert.equal(completed.replayed, false);
  assert.equal(completed.state.state_revision, stateRevision);
  assert.equal(requests.length, 2);
  assert.deepEqual(requests[0], requests[1]);
  assert.deepEqual(requests[0], {
    path: `/v1/materials/${materialId}/learning-states`,
    key: "same-learning-intent",
    body: update,
  });

  const invalidUpdate = {
    ...update,
    answer_key: "option-2",
  };
  await assert.rejects(
    client.submitLearningUpdate(invalidUpdate, "new-learning-intent"),
    (error) => error instanceof ApiClientError
      && error.reasonCode === "REQUEST_INPUT_INVALID",
  );
  assert.equal(requests.length, 2);
});

test("Learning State 接受 200 replay，拒絕錯誤 revisions 與非公開欄位", async () => {
  for (const [responseState, shouldPass] of [
    [learningStateView(), true],
    [{ ...learningStateView(), assessment_revision: `assessment:sha256:${"9".repeat(64)}` }, false],
    [{ ...learningStateView(), learner_id: "private-learner" }, false],
    [{ ...learningStateView(), assessment_score: 1 }, false],
  ]) {
    let call = 0;
    const client = new StudydyApiClient(async () => {
      call += 1;
      return call === 1
        ? new Response(null, { status: 204 })
        : Response.json(responseState, { status: 200 });
    });
    const request = client.submitLearningUpdate({
      schema: "learning-update-create/v1",
      material_id: materialId,
      map_revision: mapRevision,
      path_revision: pathRevision,
      assessment_revision: assessmentRevision,
      responses: [{ question_id: "question-1", selected_option_id: "option-2" }],
    }, "replay-learning-intent");
    if (shouldPass) {
      assert.equal((await request).replayed, true);
    } else {
      await assert.rejects(
        request,
        (error) => error instanceof ApiClientError
          && error.reasonCode === "RESPONSE_SCHEMA_MISMATCH",
      );
    }
  }
});

test("Resource response 拒絕 physical path、私人 locator 與含帳密 URL", async () => {
  const unexpectedField = learningResourceResult();
  unexpectedField.resources[0].physical_path = "/private/resource.pdf";
  const privateLocator = learningResourceResult();
  privateLocator.resources[0].source_locator = "/private/resource.pdf";
  const unexpectedLocator = learningResourceResult();
  unexpectedLocator.private_locator = "/private/catalog.json";
  const credentialLocator = learningResourceResult();
  credentialLocator.resources[0].source_locator = "https://user@example.edu/stack.pdf";
  for (const invalidResource of [unexpectedField, privateLocator, unexpectedLocator, credentialLocator]) {
    let call = 0;
    const client = new StudydyApiClient(async () => {
      call += 1;
      return call === 1
        ? new Response(null, { status: 204 })
        : Response.json(invalidResource, { status: 200 });
    });
    await assert.rejects(
      client.getLearningResourceResult({ materialId, runId, resultRevision: resourceRevision }),
      (error) => error instanceof ApiClientError
        && error.reasonCode === "RESPONSE_SCHEMA_MISMATCH",
    );
  }
});

test("來源 PDF URL 只接受 opaque artifact UUID", () => {
  const client = new StudydyApiClient(async () => new Response(null, { status: 204 }));
  assert.equal(client.sourceArtifactUrl(artifactId), `/v1/artifacts/${artifactId}`);
  assert.throws(
    () => client.sourceArtifactUrl("../../private/material.pdf"),
    (error) => error instanceof ApiClientError
      && error.reasonCode === "REQUEST_INPUT_INVALID",
  );
});

test("來源 PDF session expired 後安全恢復，且只接受非空 application/pdf", async () => {
  const paths = [];
  let artifactReads = 0;
  const client = new StudydyApiClient(async (input) => {
    const path = String(input);
    paths.push(path);
    if (path.endsWith("/refresh")) return new Response(null, { status: 204 });
    artifactReads += 1;
    if (artifactReads === 1) return apiError(401, "SESSION_REQUIRED");
    return new Response("%PDF-1.7\n%%EOF", {
      status: 200,
      headers: { "Content-Type": "application/pdf" },
    });
  });
  const pdf = await client.getSourceArtifact(artifactId);
  assert.equal(pdf.type, "application/pdf");
  assert.ok(pdf.size > 0);
  assert.deepEqual(paths, [
    "/v1/session/refresh",
    `/v1/artifacts/${artifactId}`,
    "/v1/session/refresh",
    `/v1/artifacts/${artifactId}`,
  ]);

  for (const response of [
    new Response("not pdf", { status: 200, headers: { "Content-Type": "text/plain" } }),
    new Response("", { status: 200, headers: { "Content-Type": "application/pdf" } }),
  ]) {
    let call = 0;
    const invalidClient = new StudydyApiClient(async () => {
      call += 1;
      return call === 1 ? new Response(null, { status: 204 }) : response.clone();
    });
    await assert.rejects(
      invalidClient.getSourceArtifact(artifactId),
      (error) => error instanceof ApiClientError
        && error.reasonCode === "RESPONSE_SCHEMA_MISMATCH",
    );
  }
});
