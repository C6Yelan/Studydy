import assert from "node:assert/strict";
import test from "node:test";

import { ApiClientError, StudydyApiClient } from "./client.ts";

const requestId = "6f9619ff-8b86-4e3a-a2f1-2bb9424d5c71";
const materialId = "9f9619ff-8b86-4e3a-a2f1-2bb9424d5c72";
const artifactId = "af9619ff-8b86-4e3a-a2f1-2bb9424d5c73";
const runId = "bf9619ff-8b86-4e3a-a2f1-2bb9424d5c74";
const mapRevision = `knowledge-map:sha256:${"d".repeat(64)}`;

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
    schema: "material-processing-run/v2",
    run_id: runId,
    material_id: materialId,
    source_artifact_id: artifactId,
    status: "pending",
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
    output_binding: {
      schema: "material-run-output-binding/v2",
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
    schema: "knowledge-map-view/v2",
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
      concept_id: `concept:sha256:${"7".repeat(64)}`,
      label: "Public concept",
      definition: "Public definition",
      key_points: ["Public point"],
      page_ref: pageRef,
      evidence: [{
        evidence_id: `evidence:sha256:${"8".repeat(64)}`,
        page_ref: pageRef,
        page_number: 40,
        kind: "paragraph",
        region: { coordinate_space: "unrotated_pdf_points", bbox: [72, 80, 300, 120] },
      }],
      quality: "needs_review",
      decision: "review",
      reason_codes: ["SEMANTIC_REVIEW_REQUIRED"],
    }],
    images: [{
      image_id: `image:sha256:${"a".repeat(64)}`,
      page_ref: pageRef,
      page_number: 40,
      region: { coordinate_space: "unrotated_pdf_points", bbox: [72, 140, 300, 260] },
      evidence: Array.from({ length: 9 }, (_, index) => ({
        evidence_id: `evidence:sha256:${(index + 1).toString(16).repeat(64)}`,
        page_ref: pageRef,
        page_number: 40,
        kind: "paragraph",
        region: { coordinate_space: "unrotated_pdf_points", bbox: [72, 80, 300, 120] },
      })),
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

test("Map v2 使用 exact run/revision 並要求 same-page PDF locator", async () => {
  const paths = [];
  const client = new StudydyApiClient(async (input) => {
    paths.push(String(input));
    return String(input).endsWith("/refresh")
      ? new Response(null, { status: 204 })
      : Response.json(mapView());
  });
  const view = await client.getKnowledgeMap({ materialId, runId, mapRevision });
  assert.equal(view.concepts[0].evidence[0].page_number, 40);
  assert.equal(view.status.processing, "partial");
  assert.equal(view.excluded_pages.length, 0);
  assert.deepEqual(
    view.images[0].evidence.map((evidence) => evidence.evidence_id),
    mapView().images[0].evidence.map((evidence) => evidence.evidence_id),
  );
  assert.deepEqual(paths, [
    "/v1/session/refresh",
    `/v1/materials/${materialId}/knowledge-maps/${encodeURIComponent(mapRevision)}?run_id=${runId}`,
  ]);

  const foreign = mapView();
  foreign.concepts[0].evidence[0].page_ref = `page:sha256:${"f".repeat(64)}`;
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

test("Map v2 recursively rejects unexpected、duplicate、nonfinite、type 與 count mutations", async (context) => {
  const mutations = {
    unexpected: (view) => { view.concepts[0].unexpected_field = true; },
    duplicate: (view) => { view.concepts.push(structuredClone(view.concepts[0])); },
    nonfinite: (view) => { view.concepts[0].evidence[0].region.bbox[0] = Number.NaN; },
    type: (view) => { view.concepts[0].evidence[0].page_number = true; },
    count: (view) => { view.concepts[0].key_points = []; },
    reference: (view) => { view.concepts[0].evidence[0].page_ref = `page:sha256:${"f".repeat(64)}`; },
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

test("client surface 不含 deferred downstream methods", () => {
  const client = new StudydyApiClient(async () => new Response(null, { status: 204 }));
  for (const name of ["getAssessment", "submitLearningUpdate", "getLearningResourceResult", "getLearningState"]) {
    assert.equal(client[name], undefined);
  }
  assert.equal(client.sourceArtifactUrl(artifactId), `/v1/artifacts/${artifactId}`);
  assert.equal(client.sourceArtifactUrl(artifactId, 40), `/v1/artifacts/${artifactId}#page=40`);
});
