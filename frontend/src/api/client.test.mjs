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
    schema: "material-processing-run/v6", cancel_requested_at: null, run_id: runId, material_id: materialId,
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
    schema: "knowledge-structure-view/v3", source_resolver: "/v2/materials/11111111-1111-4111-8111-111111111111/knowledge-structures/fixture/evidence", material_id: `material:sha256:${"1".repeat(64)}`,
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

test("KnowledgeStructureView preserves canonical labels, aliases and Evidence quotes", async () => {
  const saved = structureView();
  saved.concepts[0].label = "主機";
  saved.concepts[0].aliases = ["Host"];
  const client = new StudydyApiClient(async () => Response.json(saved));
  const view = await client.getKnowledgeStructure({ materialId, structureRevision });
  assert.equal(view.concepts[0].label, "主機");
  assert.deepEqual(view.concepts[0].aliases, ["Host"]);
  assert.deepEqual(view.concepts[0].claims, saved.concepts[0].claims);
});

test("unknown relation type and leaked private answer fail closed", async () => {
  const invalid = structureView();
  invalid.relations.push({ relation_id: `relation:sha256:${"9".repeat(64)}`, source_concept_id: conceptId, target_concept_id: conceptId, type: "related", learner_reason: "related" });
  const client = new StudydyApiClient(async () => Response.json(invalid));
  await assert.rejects(client.getKnowledgeStructure({ materialId, structureRevision }), (error) => error instanceof ApiClientError && error.kind === "schema");


});

test("session refresh is coalesced and safe API errors stay fixed", async () => {
  let calls = 0;
  const client = new StudydyApiClient(async (path) => { calls += 1; return Response.json({ schema: "learner-identity/v1", learner_id: sessionId }); });
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
  await assert.rejects(recovered.ensureSession(), (error) => error.reasonCode === "SESSION_REQUIRED");
  assert.deepEqual(paths, ["/v1/session/refresh"]);

  const failed = new StudydyApiClient(async () => Response.json({ schema: "api-error/v1", request_id: sessionId, reason_code: "STORAGE_UNAVAILABLE", retryable: true, message: "Request could not be completed." }, { status: 503 }));
  await assert.rejects(failed.getMaterialRun(runId), (error) => error instanceof ApiClientError && error.reasonCode === "STORAGE_UNAVAILABLE" && error.retryable);
});


test("expired writes are never replayed and retire the client", async () => {
  const paths = [];
  let expired = 0;
  const client = new StudydyApiClient(async (path) => {
    paths.push(path);
    return Response.json({ schema: "api-error/v1", request_id: sessionId, reason_code: "SESSION_REQUIRED", retryable: false, message: "Request could not be completed." }, { status: 401 });
  });
  client.onSessionExpired = () => expired++;
  await assert.rejects(client.createDraft("教材","create"), (error) => error.reasonCode === "SESSION_REQUIRED");
  await assert.rejects(client.getMaterialRun(runId));
  assert.deepEqual(paths, ["/v2/materials"]);
  assert.equal(expired, 1);
});

test("logout discards delayed responses and blocks chained writes", async () => {
  let finish;
  let calls = 0;
  const client = new StudydyApiClient(async (_path, init) => {
    calls++;
    assert.equal(init.cache, "no-store");
    return new Promise(resolve => { finish = resolve; });
  });
  const pending = client.getMaterialRun(runId);
  client.invalidate();
  finish(Response.json(runView()));
  await assert.rejects(pending, (error) => error.reasonCode === "SESSION_REQUIRED");
  await assert.rejects(client.createRevision(materialId, [], "old-write"));
  assert.equal(calls, 1);
});

test("responses still parsing at logout cannot publish private data", async () => {
  let finish;
  let parsing;
  const started = new Promise(resolve => { parsing = resolve; });
  const client = new StudydyApiClient(async () => ({ ok: true, status: 200, json: () => {
    parsing();
    return new Promise(resolve => { finish = resolve; });
  } }));
  const pending = client.getMaterialRun(runId);
  await started;
  client.invalidate();
  finish(runView());
  await assert.rejects(pending, (error) => error.reasonCode === "SESSION_REQUIRED");
});

function libraryItem() {
  return { schema: "material-library-item/v3", material_id: materialId,
    source_artifact_id: "44444444-4444-4444-8444-444444444444", display_name: "堆疊.pdf",
    size_bytes: 120, created_at: "2026-09-11T00:00:00Z", latest_attempt: null, study_sessions: [],
    available_structures: [{ run_id: runId, knowledge_structure_revision: structureRevision,
      created_at: "2026-09-11T00:01:00Z", status: "succeeded" }] };
}

test("material library reads use server identity and carry exact published revisions", async () => {
  const requests = [];
  const item = libraryItem();
  const client = new StudydyApiClient(async (path, init) => {
    requests.push([path, init.method]);
    return Response.json(path === "/v1/materials" ? { schema: "material-library/v2", materials: [item] } : item);
  });
  assert.equal((await client.listMaterials()).materials[0].available_structures[0].knowledge_structure_revision, structureRevision);
  assert.deepEqual(await client.getMaterial(materialId), item);
  assert.deepEqual(requests, [["/v1/materials", "GET"], [`/v1/materials/${materialId}`, "GET"]]);
  await assert.rejects(client.getMaterial(sessionId), error => error.reasonCode === "RESPONSE_SCHEMA_MISMATCH");
});

test("library rejects invalid lifecycle and uploaded filenames use UTF-8 encoding", async () => {
  const item = libraryItem();
  item.available_structures[0].status = "failed";
  const invalid = new StudydyApiClient(async () => Response.json({ schema: "material-library/v2", materials: [item] }));
  await assert.rejects(invalid.listMaterials(), error => error.kind === "schema");
  const client = new StudydyApiClient(async (_path, init) => {
    assert.equal(init.headers["X-Material-Name"], encodeURIComponent("陣列 & 堆疊.pdf"));
    return Response.json({ schema: "material-sources/v1", material_id: materialId, sources: [] });
  });
  await client.uploadSource(materialId, new File(["pdf"], "陣列 & 堆疊.pdf", {type:"application/pdf"}), "application/pdf", "upload");
});

function questionRecord() {
  const question = {
    schema: "single-choice-assessment/v2", assessment_revision: `assessment:sha256:${"4".repeat(64)}`,
    study_session_id: sessionId, knowledge_structure_revision: structureRevision,
    question_id: `question:sha256:${"5".repeat(64)}`, target_concept_id: conceptId,
    target_claim_id: claimId, source_evidence_ids: [evidenceId], question_type: "single_choice",
    prompt: "Saved question", options: Array.from({ length: 4 }, (_, index) => ({ option_id: `option:sha256:${String(index + 1).repeat(64)}`, text: String(index) })),
  };
  return {assessment:question,feedback:null,can_submit:true,created_at:"2026-09-11T00:01:00Z"};
}

function resumeView() {
  return {
    schema: "study-resume/v5", assessment_sets: [], selected_set_id: null, run_id: runId, source_artifact_id: libraryItem().source_artifact_id,
    session: { schema: "study-session/v2", study_session_id: sessionId, material_id: materialId,
      knowledge_structure_revision: structureRevision, current_concept_id: conceptId,
      no_safe_claim_ids: [], deferred_concept_ids: [], status: "active", event_watermark: 0,
      started_at: "2026-09-11T00:00:00Z", completed_at: null },
    knowledge_structure: structureView(),
    progress: { schema: "learner-progress/v4", assessment_cycles: [], study_session_id: sessionId, knowledge_structure_revision: structureRevision,
      event_watermark: 0, current_concept_id: conceptId, deferred_concept_ids: [],
      concept_states: [{ concept_id: conceptId, label: "Stack", status: "not_started" }], weaknesses: [],
      next_action: { action: "assess", target_concept_id: conceptId, target_claim_id: claimId, prerequisite_concept_ids: [], reason: "current_concept" },
      guidance_revision: `learner-guidance:sha256:${"6".repeat(64)}` },
  };
}

test("resume is a bound read and preserves the selected original assessment", async () => {
  const value = resumeView();
  const requests = [];
  const client = new StudydyApiClient(async (path, init) => {
    requests.push([String(path), init.method]);
    return Response.json(value);
  });
  const request = { materialId, studySessionId: sessionId, runId, structureRevision };
  assert.deepEqual(await client.resumeStudy(request), value);
  assert.equal(requests.length, 1);
  assert.equal(requests[0][1], "GET");
  assert.equal(new URL(requests[0][0], "http://localhost").searchParams.has("assessment_revision"), false);
  await assert.rejects(client.resumeStudy({ ...request, materialId: sessionId }), error => error.kind === "schema");
  await assert.rejects(client.resumeStudy({ ...request, runId: sessionId }), error => error.kind === "schema");
});

test("resume rejects mismatched session revisions and unknown selected sets",async()=>{
  for(const mutate of [v=>{v.selected_set_id=materialId;},v=>{v.progress.event_watermark=1;},v=>{v.session.knowledge_structure_revision=`knowledge-structure:sha256:${"f".repeat(64)}`;}]){
    const value=resumeView();mutate(value);
    await assert.rejects(new StudydyApiClient(async()=>Response.json(value)).resumeStudy({materialId,studySessionId:sessionId,runId,structureRevision}), e=>e.kind==="schema");
  }
});

test("authentication sends only Email/password and retains safe error boundaries", async () => {
  const observed = [];
  const client = new StudydyApiClient(async (path, options) => {
    observed.push({ path, body: JSON.parse(options.body) });
    return Response.json({ schema: "learner-identity/v1", learner_id: sessionId });
  });
  for (const mode of ["login", "register"]) {
    await client.authenticate(mode, "learner@example.com", "Synthetic password 42");
  }
  assert.deepEqual(observed, [
    { path: "/v1/session/login", body: { email: "learner@example.com", password: "Synthetic password 42" } },
    { path: "/v1/accounts", body: { email: "learner@example.com", password: "Synthetic password 42" } },
  ]);
  for (const [reason, status, message] of [
    ["INVALID_EMAIL", 400, "請輸入有效的 Email 格式。"],
    ["INVALID_CREDENTIALS", 401, "Email 或密碼不正確。"],
    ["ACCOUNT_UNAVAILABLE", 409, "這個 Email 已被使用，請使用其他 Email。"],
    ["STORAGE_UNAVAILABLE", 503, "資料服務暫時無法使用，請稍後再試。"],
    ["MATERIAL_NOT_DISCARDABLE", 409, "這份教材正在刪除，無法進行這項操作。"],
  ]) {
    const failed = new StudydyApiClient(async () => Response.json({ schema: "api-error/v1", request_id: sessionId, reason_code: reason, retryable: status === 503, message: "Request could not be completed." }, { status }));
    await assert.rejects(failed.authenticate("login", "learner@example.com", "Synthetic password 42"), error => error instanceof ApiClientError && error.reasonCode === reason && error.message === message);
  }
});

for (const operation of ["refresh", "identity", "login", "register", "logout"]) {
  test(`${operation} has a bounded deadline and can retry without late abort`, async t => {
    t.mock.timers.enable({ apis: ["setTimeout"] });
    const signals = [];
    let hang = true;
    const client = new StudydyApiClient(async (path, init) => {
      signals.push(init.signal);
      if (hang) return new Promise(() => {});
      return init.method === "DELETE" ? new Response(null, { status: 204 })
        : Response.json({ schema: "learner-identity/v1", learner_id: sessionId });
    });
    const invoke = () => operation === "refresh" ? client.ensureSession() : operation === "identity" ? client.currentIdentity()
      : operation === "logout" ? client.logout() : client.authenticate(operation, "learner@example.com", "Synthetic password 42");
    const rejected = assert.rejects(invoke(), e => e.reasonCode === "REQUEST_TIMEOUT" && e.retryable);
    t.mock.timers.tick(10_000);
    await rejected;
    assert.equal(signals[0].aborted, true);
    hang = false;
    await invoke();
    t.mock.timers.tick(100_000);
    client.invalidate();
    assert.ok(signals.slice(1).every(signal => !signal.aborted));
  });
}

test("deadline covers body parsing and late 401 cannot retire a recovered client", async t => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  let finish;
  let parsing;
  let first = true;
  const started = new Promise(resolve => { parsing = resolve; });
  const client = new StudydyApiClient(async () => {
    if (!first) return Response.json({ schema: "learner-identity/v1", learner_id: sessionId });
    first = false;
    return { ok: false, status: 401, json: () => { parsing(); return new Promise(resolve => { finish = resolve; }); } };
  });
  let expired = 0;
  client.onSessionExpired = () => expired++;
  const rejected = assert.rejects(client.currentIdentity(), e => e.reasonCode === "REQUEST_TIMEOUT");
  await started;
  t.mock.timers.tick(10_000);
  await rejected;
  await client.currentIdentity();
  finish({ schema: "api-error/v1", request_id: sessionId, reason_code: "SESSION_REQUIRED", retryable: false, message: "Request could not be completed." });
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(expired, 0);
  await client.currentIdentity();
});

test("intentional cancellation settles hanging auth without a timeout error", async t => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const client = new StudydyApiClient(async () => new Promise(() => {}));
  const rejected = assert.rejects(client.currentIdentity(), e => e.reasonCode === "SESSION_REQUIRED" && !e.retryable);
  client.invalidate();
  await rejected;
  t.mock.timers.tick(100_000);
});

test("long product reads do not inherit auth deadline", async t => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  let finish;
  let signal;
  const client = new StudydyApiClient(async (_path, init) => { signal = init.signal; return new Promise(resolve => { finish = resolve; }); });
  const pending = client.getMaterialRun(runId);
  t.mock.timers.tick(100_000);
  assert.equal(signal.aborted, false);
  finish(Response.json(runView()));
  await pending;
});

test("malformed gateway errors remain distinct from successful schema errors and credentials", async () => {
  for (const status of [200, 400, 401, 502, 503]) {
    const client = new StudydyApiClient(async () => new Response("<html>upstream</html>", { status }));
    await assert.rejects(client.authenticate("login", "learner@example.com", "Synthetic password 42"), e =>
      status >= 500 ? e.reasonCode === "SERVICE_UNAVAILABLE" && e.retryable && e.message.includes("暫時無法使用")
        : e.reasonCode === "RESPONSE_SCHEMA_MISMATCH");
  }
});

function cancellationView(status) {
  const view = runView();
  view.status = status;
  if (status === "succeeded" || status === "partial") {
    view.output_binding.processing = status;
    return view;
  }
  view.progress_stage = status === "pending" ? "queued" : "evidence";
  view.completed_pages = status === "pending" ? 0 : 1;
  view.total_pages = status === "pending" ? null : 2;
  view.output_binding = null;
  view.completed_at = status === "failed" || status === "cancelled" ? "2026-09-05T00:00:02Z" : null;
  view.cancel_requested_at = status === "cancelled" ? "2026-09-05T00:00:01Z" : null;
  view.error_code = status === "failed" ? "NO_USABLE_EVIDENCE" : null;
  return view;
}

test("v5 run guards accept canonical cancellation states and reject invalid lifecycle/timestamps", async () => {
  for (const status of ["pending", "running", "cancelled", "failed", "succeeded", "partial"]) {
    const value = cancellationView(status);
    const client = new StudydyApiClient(async () => Response.json(value));
    assert.equal((await client.getMaterialRun(runId)).status, status);
  }
  const accepted = { ...cancellationView("running"), cancel_requested_at: "2026-09-05T00:00:01Z" };
  assert.deepEqual(await new StudydyApiClient(async () => Response.json(accepted)).getMaterialRun(runId), accepted);
  for (const [status, corrupt] of [
    ["cancelled", v => { v.cancel_requested_at = null; }],
    ["cancelled", v => { v.completed_at = null; }],
    ["cancelled", v => { v.output_binding = runView().output_binding; }],
    ["cancelled", v => { v.error_code = "FAILED"; }],
    ["succeeded", v => { v.cancel_requested_at = "2026-09-05T00:00:01Z"; }],
    ["running", v => { v.progress_stage = "publishing"; v.cancel_requested_at = "2026-09-05T00:00:01Z"; }],
    ["running", v => { v.completed_at = "2026-09-05T00:00:01Z"; }],
    ["pending", v => { v.cancel_requested_at = "2026-09-05T00:00:01Z"; }],
    ["failed", v => { v.cancel_requested_at = "2026-09-05T00:00:01Z"; }],
    ["cancelled", v => { v.cancel_requested_at = "not-a-date"; }],
    ["cancelled", v => { v.cancel_requested_at = "2026-02-30T00:00:00Z"; }],
    ["cancelled", v => { v.cancel_requested_at = "2026-09-05T24:00:00Z"; }],
    ["running", v => { delete v.cancel_requested_at; }],
    ["running", v => { v.updated_at = "invalid"; }],
    ["running", v => { v.schema = "material-processing-run/v4"; }],
  ]) {
    const value = cancellationView(status); corrupt(value);
    await assert.rejects(new StudydyApiClient(async () => Response.json(value)).getMaterialRun(runId), e => e.kind === "schema");
  }
});

test("discardMaterial sends the canonical empty DELETE and validates its response", async () => {
  for (const state of ["removing", "removed"]) {
    const client = new StudydyApiClient(async (path, init) => {
      assert.equal(path, `/v1/materials/${materialId}`);
      assert.equal(init.method, "DELETE");
      assert.equal(init.headers.Origin, "http://127.0.0.1:4173");
      assert.equal(init.headers["Idempotency-Key"], undefined);
      assert.equal(init.body, undefined);
      return Response.json({ schema: "material-discard/v1", material_id: materialId, state }, { status: 202 });
    });
    assert.equal((await client.discardMaterial(materialId)).state, state);
    assert.equal(client.cancelMaterialRun, undefined);
  }
  for (const value of [
    { schema: "material-discard/v2", material_id: materialId, state: "removed" },
    { schema: "material-discard/v1", material_id: "invalid", state: "removed" },
    { schema: "material-discard/v1", material_id: runId, state: "removed" },
    { schema: "material-discard/v1", material_id: materialId, state: "deleting" },
    { schema: "material-discard/v1", material_id: materialId, state: "removed", extra: true },
    { schema: "material-discard/v1", material_id: materialId },
  ]) {
    await assert.rejects(new StudydyApiClient(async () => Response.json(value)).discardMaterial(materialId), e => e.kind === "schema");
  }
});

test("v2 library attempts preserve cancellation intent while older published maps remain usable", async () => {
  for (const status of ["running", "cancelled"]) {
    const item = libraryItem();
    const value = cancellationView(status);
    if (status === "running") value.cancel_requested_at = "2026-09-05T00:00:01Z";
    item.latest_attempt = Object.fromEntries(["run_id", "status", "progress_stage", "completed_pages", "total_pages", "error_code", "created_at", "cancel_requested_at"].map(key => [key, value[key]]));
    const client = new StudydyApiClient(async () => Response.json({ schema: "material-library/v2", materials: [item] }));
    assert.equal((await client.listMaterials()).materials[0].available_structures.length, 1);
    item.latest_attempt.cancel_requested_at = "invalid";
    await assert.rejects(client.listMaterials(), e => e.kind === "schema");
  }
});

test("focus is an owner-bound state setter without a create intent", async () => {
  const value = resumeView().session;
  let sent;
  const client = new StudydyApiClient(async (path, init) => { sent = { path, init }; return Response.json(value); });
  assert.deepEqual(await client.focusStudySession(sessionId, conceptId), value);
  assert.equal(sent.path, `/v1/study-sessions/${sessionId}/focus`);
  assert.equal(sent.init.method, "POST");
  assert.ok(sent.init.headers.Origin);
  assert.equal(sent.init.headers["Idempotency-Key"], undefined);
  assert.deepEqual(JSON.parse(sent.init.body), { schema: "study-session-focus/v1", current_concept_id: conceptId });
  await assert.rejects(client.focusStudySession(materialId, conceptId), error => error.kind === "schema");
});

test("material rename uses the existing item guard and exact identity without a create key", async () => {
  const item = { ...libraryItem(), display_name: "作業系統 第五章" };
  let sent;
  const client = new StudydyApiClient(async (path, init) => { sent = { path, init }; return Response.json(item); });
  assert.deepEqual(await client.renameMaterial(materialId, item.display_name), item);
  assert.equal(sent.path, `/v1/materials/${materialId}/rename`);
  assert.equal(sent.init.method, "POST");
  assert.ok(sent.init.headers.Origin);
  assert.equal(sent.init.headers["Idempotency-Key"], undefined);
  assert.deepEqual(JSON.parse(sent.init.body), { schema: "material-rename/v1", display_name: item.display_name });
  await assert.rejects(client.renameMaterial(sessionId, item.display_name), error => error.kind === "schema");
});

test("assessment sets validate scope, membership, counts and private staging", async () => {
  const record = questionRecord();
  const setId = "55555555-5555-4555-8555-555555555555";
  const base = {
    schema: "assessment-set/v3", set_id: setId, study_session_id: sessionId, material_id: materialId,
    knowledge_structure_revision: structureRevision, target_concept_id: conceptId,
    kind: "diagnostic", diagnostic_set_id: null, selection_policy: "single-concept-grounded-points/v1",
    status: "preparing", set_version: 2, requested_count: 1, published_count: 0, answered_count: 0, passed_count: 0,
    point_count: 1, excluded_count: 0, verified_count: 1, assessment_revisions: [],
    created_at: "2026-09-20T00:00:00Z", completed_at: null,
    can_retry: false, can_publish_partial: false, can_complete: false,
    cycle: { diagnostic_set_id: setId, concept_id: conceptId, set_version: 2, outcome: "in_progress", active_set_id: setId, passed_count: 0, remediation_passed_count: 0, pending_count: 0, unanswered_count: 0, unavailable_count: 1,
      can_create_remediation: false, points: [{ claim_id: claimId, result: "unavailable", latest_answer_event_id: null, latest_set_id: null }] },
    items: [{ ordinal: 1, target_claim_id: claimId, state: "verified", attempts: 1, failure_reason: null,
      assessment: null, feedback: null, created_at: null, can_submit: false }],
  };
  const read = value => new StudydyApiClient(async () => Response.json(value)).readAssessmentSet(sessionId, setId);
  assert.equal((await read(base)).verified_count, 1);
  const ready = structuredClone(base);
  Object.assign(ready, { status: "ready", published_count: 1, can_complete: true,
    assessment_revisions: [record.assessment.assessment_revision] });
  Object.assign(ready.items[0], { state: "published", assessment: record.assessment, created_at: record.created_at, can_submit: true });
  assert.equal((await read(ready)).published_count, 1);
  const answer = { assessment_revision: record.assessment.assessment_revision, question_id: record.assessment.question_id,
    selected_option_id: record.assessment.options[0].option_id };
  let request;
  const client = new StudydyApiClient(async (path, init) => { request={path,init}; return Response.json(ready); });
  await assert.rejects(client.submitAssessmentSet(sessionId,setId,[answer],2,"one-submit"),error=>error.kind==="schema");
  assert.equal(request.path, `/v1/study-sessions/${sessionId}/assessment-sets/${setId}/submissions`);
  assert.equal(request.init.headers["Idempotency-Key"], "one-submit");
  assert.deepEqual(JSON.parse(request.init.body), {schema:"assessment-set-submission/v1",expected_set_version:2,answers:[answer]});
  for (const corrupt of [
    v => { v.study_session_id = materialId; },
    v => { v.verified_count = 0; },
    v => { v.target_plan = {}; },
    v => { v.items[0].prepared_document = { private: "hidden" }; },
    v => { v.items[0].assessment.target_claim_id = `claim:sha256:${"f".repeat(64)}`; },
    v => { v.items[0].assessment.correct_option_id = "secret"; },
    v => { v.passed_count = 1; },
    v => { v.excluded_count = 1; },
  ]) {
    const value = structuredClone(ready); corrupt(value);
    await assert.rejects(read(value), error => error.kind === "schema");
  }
});

test("resume retries only snapshot conflicts and bounds read attempts", async () => {
  const request = { materialId, structureRevision, studySessionId: sessionId, runId };
  for (const recover of [true, false]) {
    let calls=0;
    const client=new StudydyApiClient(async (_path, init) => {
      assert.equal(init.method, "GET"); calls++;
      if (recover && calls===2) return Response.json(resumeView());
      return Response.json({ schema: "api-error/v1", request_id: sessionId, reason_code: "IDEMPOTENCY_CONFLICT", retryable: false, message: "Request could not be completed." }, { status: 409 });
    });
    if (recover) { assert.equal((await client.resumeStudy(request)).session.study_session_id, sessionId); assert.equal(calls, 2); }
    else { await assert.rejects(client.resumeStudy(request), e=>e.reasonCode==="IDEMPOTENCY_CONFLICT"); assert.equal(calls, 3); }
  }
});

test("active set lists allow different concepts and identify duplicate creation explicitly",async()=>{
  const firstId="55555555-5555-4555-8555-555555555555",secondId="66666666-6666-4666-8666-666666666666";
  const summary={set_id:firstId,target_concept_id:conceptId,kind:"diagnostic",diagnostic_set_id:null,status:"preparing",set_version:1,
    requested_count:1,published_count:0,answered_count:0,passed_count:0,assessment_revisions:[],created_at:"2026-09-20T00:00:00Z",completed_at:null};
  const list={schema:"assessment-set-list/v3",study_session_id:sessionId,knowledge_structure_revision:structureRevision,
    active_set_ids:[firstId,secondId],sets:[summary,{...summary,set_id:secondId,target_concept_id:`concept:sha256:${"e".repeat(64)}`} ]};
  const read=value=>new StudydyApiClient(async()=>Response.json(value)).listAssessmentSets(sessionId);
  assert.equal((await read(list)).active_set_ids.length,2);
  for(const corrupt of [v=>{v.active_set_ids=[];},v=>{v.active_set_ids=[firstId,firstId];},v=>{v.sets[1].target_concept_id=conceptId;},v=>{v.study_session_id=materialId;}]){
    const value=structuredClone(list);corrupt(value);await assert.rejects(read(value),error=>error.kind==="schema");
  }
  const client=new StudydyApiClient(async()=>Response.json({schema:"api-error/v1",request_id:sessionId,
    reason_code:"ASSESSMENT_SET_ACTIVE",retryable:false,message:"Request could not be completed."},{status:409}));
  await assert.rejects(client.createAssessmentSet(sessionId,conceptId,"one-intent"),error=>error.reasonCode==="ASSESSMENT_SET_ACTIVE");
});

test("resume authorizes saved group membership independently of navigation focus",async()=>{
  const value=resumeView(),other=`concept:sha256:${"e".repeat(64)}`,setId="55555555-5555-4555-8555-555555555555";
  const second=structuredClone(value.knowledge_structure.concepts[0]);second.concept_id=other;second.label="Other";
  second.claims[0].claim_id=`claim:sha256:${"e".repeat(64)}`;
  value.knowledge_structure.concepts.push(second);
  value.knowledge_structure.document_tree.sections[0].concept_ids.push(other);
  value.knowledge_structure.initial_learning_path.push({position:2,concept_id:other,reason:"document_order"});
  value.session.current_concept_id=value.progress.current_concept_id=other;
  value.progress.concept_states.push({...value.progress.concept_states[0],concept_id:other,label:"Other"});
  value.assessment_sets=[{set_id:setId,target_concept_id:conceptId,kind:"diagnostic",diagnostic_set_id:null,status:"ready",set_version:1,
    requested_count:1,published_count:1,answered_count:0,passed_count:0,assessment_revisions:[questionRecord().assessment.assessment_revision],
    created_at:"2026-09-20T00:00:00Z",completed_at:null}];
  value.selected_set_id=setId;
  const request={materialId,structureRevision,studySessionId:sessionId,runId,assessmentSetId:setId};
  const read=value=>new StudydyApiClient(async()=>Response.json(value)).resumeStudy(request);
  assert.equal((await read(value)).selected_set_id,setId);
  for(const corrupt of [v=>{v.assessment_sets=[];v.selected_set_id=null;}]){
    const bad=structuredClone(value);corrupt(bad);await assert.rejects(read(bad),error=>error.kind==="schema");
  }
});
