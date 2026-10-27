import assert from "node:assert/strict";
import test from "node:test";

import { ApiClientError, StudydyApiClient } from "./client.ts";

const browserStorage = new Map();
Object.defineProperty(globalThis, "localStorage", { configurable: true, value: {
  getItem: key => browserStorage.get(key) ?? null,
  setItem: (key, value) => browserStorage.set(key, String(value)),
  removeItem: key => browserStorage.delete(key),
} });

const materialId = "11111111-1111-4111-8111-111111111111";
const runId = "22222222-2222-4222-8222-222222222222";
const sessionId = "33333333-3333-4333-8333-333333333333";
const setId = "55555555-5555-4555-8555-555555555555";
const structureRevision = `knowledge-structure:sha256:${"a".repeat(64)}`;
const conceptId = `concept:sha256:${"b".repeat(64)}`;
const claimId = `claim:sha256:${"c".repeat(64)}`;
const evidenceId = `evidence:sha256:${"d".repeat(64)}`;
const blockId = `block:sha256:${"e".repeat(64)}`;

function cardSetView() {
  const view = structureView();
  return {
    schema: "card-set/v1", version: 1, card_set_id: setId, material_id: materialId,
    material_name: "Synthetic.pdf", name: "複習", card_count: view.concepts.length,
    knowledge_structure_revision: structureRevision, created_at: "2026-10-02T00:00:00Z",
    is_current_revision: true, source_resolver: decodeURIComponent(view.source_resolver),
    cards: view.concepts.map(({ concept_id, label, claims }) => ({ concept_id, label, claims })),
    status: view.status, excluded_pages: [], display_edits: {},
    generation: { status: "ready", stage: "complete", completed: view.concepts.length, total: view.concepts.length, error_code: null, retained_original: 0, cached: 0 },
  };
}

function cardStudyView() {
  const old = cardSetView(), structure = structureView();
  const batch = { schema: 'card-batch/v1', card_set_id: setId, version: 1, offset: 0, total: old.cards.length,
    cards: old.cards.map(card => ({ ...card, display_edits: [] })),
    context: { source_resolver: old.source_resolver, status: old.status, concepts: structure.concepts, relations: structure.relations, relation_evidence: [] } };
  return { ...old, schema: 'card-study/v1', navigation: old.cards.map(card => ({ concept_id: card.concept_id, label: card.label, page_count: null })), batch };
}

test("card study rejects foreign batches, wrong versions and invalid source context", async () => {
  const valid = await new StudydyApiClient(async () => Response.json(cardStudyView())).getCardStudy(setId);
  assert.equal(valid.batch.cards[0].label, 'Stack');
  for (const change of [
    value => { value.batch.card_set_id = materialId; },
    value => { value.batch.version = 2; },
    value => { value.batch.offset = 8; },
    value => { value.batch.context.source_resolver = '/wrong/evidence'; },
    value => { value.batch.cards[0].claims[0].evidence[0].page = 0; },
    value => { value.navigation[0].concept_id = `concept:sha256:${'f'.repeat(64)}`; },
    value => { value.batch.cards[0].display_edits = [{ source: 'text', text: 1 }]; },
  ]) {
    const value = cardStudyView(); change(value);
    await assert.rejects(new StudydyApiClient(async () => Response.json(value)).getCardStudy(setId), error => error.kind === 'schema');
  }
});

test("card batches and partial page counts stay bound to the selected version", async () => {
  const study = cardStudyView();
  const client = new StudydyApiClient(async (url, init) => {
    if (init.method === 'GET') {
      assert.equal(url, `/v1/card-sets/${setId}/cards?offset=0&version=1`);
      return Response.json(study.batch);
    }
    assert.deepEqual(JSON.parse(init.body), { expected_version: 1, concept_counts: { [conceptId]: 3 }, layout_policy: 'visual-concept-card/v4' });
    return Response.json({ ...study, card_count: null });
  });
  assert.equal((await client.getCardBatch(setId, 1, 0)).offset, 0);
  assert.equal((await client.saveConceptCounts(setId, 1, { [conceptId]: 3 })).card_count, null);
});

test("saved cards reject foreign identities, version-mismatched sources and invalid contents", async () => {
  assert.equal((await new StudydyApiClient(async () => Response.json(cardSetView())).getCardSet(setId)).cards[0].label, "Stack");
  for (const change of [
    (view) => { view.card_set_id = materialId; },
    (view) => { view.source_resolver = view.source_resolver.replace(structureRevision, `knowledge-structure:sha256:${"f".repeat(64)}`); },
    (view) => { view.card_count = -1; },
    (view) => { view.cards[0].claims[0].evidence = []; },
    (view) => { view.cards[0].claims[0].evidence[0].page = -1; },
    (view) => { delete view.status; },
  ]) {
    const view = cardSetView(); change(view);
    await assert.rejects(new StudydyApiClient(async () => Response.json(view)).getCardSet(setId), (error) => error.kind === "schema");
  }
});

test("card creation uses the same supplied intent key and deletion verifies its receipt", async () => {
  const requests = [];
  const saved = cardSetView();
  const client = new StudydyApiClient(async (url, init) => {
    requests.push({ url, init });
    return Response.json(saved);
  });
  const body = { schema: "card-set-create/v1", name: "複習", knowledge_structure_revision: structureRevision, concept_ids: [conceptId] };
  await client.createCardSet(materialId, body, "stable-intent");
  await client.createCardSet(materialId, body, "stable-intent");
  assert.deepEqual(requests[0], requests[1]);
  assert.equal(requests[0].init.headers["Idempotency-Key"], "stable-intent");
  assert.deepEqual(JSON.parse(requests[0].init.body), body);
  await assert.rejects(new StudydyApiClient(async () => Response.json({ schema: "card-set-deleted/v1", card_set_id: materialId })).deleteCardSet(setId), (error) => error.kind === "schema");
});

test("card_count is the persisted page count, independent of concept count", async () => {
  for (const count of [null, 17]) {
    const view = { ...cardSetView(), card_count: count };
    assert.equal((await new StudydyApiClient(async () => Response.json(view)).getCardSet(setId)).card_count, count);
  }
  const view = { ...cardSetView(), card_count: 17 };
  const client = new StudydyApiClient(async (url, init) => {
    assert.equal(url, `/v1/card-sets/${setId}/count`);
    assert.equal(init.method, "POST");
    assert.deepEqual(JSON.parse(init.body), { expected_version: 1, card_count: 17, layout_policy: "visual-concept-card/v4" });
    return Response.json(view);
  });
  assert.equal((await client.saveCardCount(setId, 1, 17)).card_count, 17);
});

function apiErrorResponse(reasonCode, status, retryable = false) {
  return Response.json(
    {
      schema: "api-error/v1",
      request_id: sessionId,
      reason_code: reasonCode,
      retryable,
      message: "Request could not be completed.",
    },
    { status },
  );
}

function runView() {
  return {
    schema: "material-processing-run/v1",
    cancel_requested_at: null,
    run_id: runId,
    material_id: materialId,
    source_artifact_id: "44444444-4444-4444-8444-444444444444",
    status: "succeeded",
    progress_stage: "completed",
    completed_pages: 1,
    total_pages: 1,
    error_code: null,
    created_at: "2026-09-05T00:00:00Z",
    updated_at: "2026-09-05T00:00:01Z",
    completed_at: "2026-09-05T00:00:01Z",
    output_binding: {
      schema: "material-run-output-binding/v1",
      knowledge_structure_revision: structureRevision,
      page_count: 1,
    },
  };
}

function structureView() {
  return {
    schema: "knowledge-structure-view/v1",
    source_resolver: `/v1/materials/${materialId}/knowledge-structures/${encodeURIComponent(structureRevision)}/evidence`,
    material_id: `material:sha256:${"1".repeat(64)}`,
    knowledge_structure_revision: structureRevision,
    status: { processing: "succeeded", quality: "accepted", decision: "retain", reason_codes: [] },
    document_tree: {
      material_id: `material:sha256:${"1".repeat(64)}`,
      sections: [
        {
          section_id: `section:sha256:${"2".repeat(64)}`,
          title: "Stacks",
          order: 0,
          heading_evidence_id: null,
          concept_ids: [conceptId],
        },
      ],
    },
    concepts: [
      {
        concept_id: conceptId,
        label: "Stack",
        aliases: [],
        claims: [
          {
            claim_id: claimId,
            text: "A stack is LIFO.",
            evidence: [
              {
                evidence_id: evidenceId,
                page_ref: `page:sha256:${"3".repeat(64)}`,
                page: 1,
                block_order: 0,
                kind: "paragraph",
                source: "native_text",
                source_locator: { page: 1, block_id: blockId, region: [1, 2, 3, 4] },
                quote: "A stack is LIFO.",
              },
            ],
          },
        ],
      },
    ],
    relations: [],
    initial_learning_path: [{ position: 1, concept_id: conceptId, reason: "document_order" }],
    excluded_pages: [],
  };
}

test("material reads bind revisions and preserve saved labels and Evidence", async () => {
  const saved = structureView();
  saved.concepts[0].label = "主機";
  saved.concepts[0].aliases = ["Host"];
  const requests = [];
  const client = new StudydyApiClient(async (input) => {
    requests.push(String(input));
    return Response.json(String(input).includes("knowledge-structures") ? saved : runView());
  });
  assert.equal((await client.getMaterialRun(runId)).run_id, runId);
  const view = await client.getKnowledgeStructure({ materialId, structureRevision });
  assert.equal(view.concepts[0].concept_id, conceptId);
  assert.equal(view.concepts[0].label, "主機");
  assert.deepEqual(view.concepts[0].aliases, ["Host"]);
  assert.deepEqual(view.concepts[0].claims, saved.concepts[0].claims);
  assert.deepEqual(requests, [
    `/v1/material-processing-runs/${runId}`,
    `/v1/materials/${materialId}/knowledge-structures/${encodeURIComponent(structureRevision)}`,
  ]);
});

test("unknown relation type fails closed with otherwise valid endpoints", async () => {
  const invalid = structureView();
  const second = structuredClone(invalid.concepts[0]);
  second.concept_id = `concept:sha256:${"f".repeat(64)}`;
  second.claims[0].claim_id = `claim:sha256:${"f".repeat(64)}`;
  invalid.concepts.push(second);
  invalid.document_tree.sections[0].concept_ids.push(second.concept_id);
  invalid.initial_learning_path.push({
    position: 2,
    concept_id: second.concept_id,
    reason: "document_order",
  });
  invalid.relations.push({
    relation_id: `relation:sha256:${"9".repeat(64)}`,
    source_concept_id: conceptId,
    target_concept_id: second.concept_id,
    type: "example",
    learner_reason: "Supported example",
  });
  const client = new StudydyApiClient(async () => Response.json(invalid));
  await client.getKnowledgeStructure({ materialId, structureRevision });
  invalid.relations[0].type = "related";
  await assert.rejects(
    new StudydyApiClient(async () => Response.json(invalid)).getKnowledgeStructure({ materialId, structureRevision }),
    (error) => error instanceof ApiClientError && error.kind === "schema",
  );
});

test("session refresh coalesces concurrent requests and reports expired sessions", async () => {
  let calls = 0;
  const client = new StudydyApiClient(async () => {
    calls += 1;
    return Response.json({ schema: "learner-identity/v1", learner_id: sessionId });
  });
  await Promise.all([client.ensureSession(), client.ensureSession(), client.ensureSession()]);
  assert.equal(calls, 1);

  const paths = [];
  const unauthorized = new StudydyApiClient(async (input) => {
    paths.push(String(input));
    return apiErrorResponse("SESSION_REQUIRED", 401);
  });
  await assert.rejects(
    unauthorized.ensureSession(),
    (error) => error.reasonCode === "SESSION_REQUIRED",
  );
  assert.deepEqual(paths, ["/v1/session/refresh"]);
});

test("expired writes are never replayed and retire the client", async () => {
  const paths = [];
  let expired = 0;
  const client = new StudydyApiClient(async (path) => {
    paths.push(path);
    return apiErrorResponse("SESSION_REQUIRED", 401);
  });
  client.onSessionExpired = () => expired++;
  await assert.rejects(
    client.createDraft("教材", "create"),
    (error) => error.reasonCode === "SESSION_REQUIRED",
  );
  await assert.rejects(client.getMaterialRun(runId));
  assert.deepEqual(paths, ["/v1/materials"]);
  assert.equal(expired, 1);
});

test("logout discards delayed responses and blocks chained writes", async () => {
  let finish;
  let calls = 0;
  const client = new StudydyApiClient(async (_path, init) => {
    calls++;
    assert.equal(init.cache, "no-store");
    return new Promise((resolve) => {
      finish = resolve;
    });
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
  const started = new Promise((resolve) => {
    parsing = resolve;
  });
  const client = new StudydyApiClient(async () => ({
    ok: true,
    status: 200,
    json: () => {
      parsing();
      return new Promise((resolve) => {
        finish = resolve;
      });
    },
  }));
  const pending = client.getMaterialRun(runId);
  await started;
  client.invalidate();
  finish(runView());
  await assert.rejects(pending, (error) => error.reasonCode === "SESSION_REQUIRED");
});

function libraryItem() {
  return {
    schema: "material-library-item/v1",
    material_id: materialId,
    source_artifact_id: "44444444-4444-4444-8444-444444444444",
    display_name: "堆疊.pdf",
    size_bytes: 120,
    created_at: "2026-09-11T00:00:00Z",
    latest_attempt: null,
    study_sessions: [],
    available_structures: [
      {
        run_id: runId,
        knowledge_structure_revision: structureRevision,
        created_at: "2026-09-11T00:01:00Z",
        status: "succeeded",
      },
    ],
  };
}

test("material library reads use server identity and carry exact published revisions", async () => {
  const requests = [];
  const item = libraryItem();
  const client = new StudydyApiClient(async (path, init) => {
    requests.push([path, init.method]);
    return Response.json(
      path === "/v1/materials" ? { schema: "material-library/v1", materials: [item] } : item,
    );
  });
  assert.equal(
    (await client.listMaterials()).materials[0].available_structures[0]
      .knowledge_structure_revision,
    structureRevision,
  );
  assert.deepEqual(await client.getMaterial(materialId), item);
  assert.deepEqual(requests, [
    ["/v1/materials", "GET"],
    [`/v1/materials/${materialId}`, "GET"],
  ]);
  await assert.rejects(
    client.getMaterial(sessionId),
    (error) => error.reasonCode === "RESPONSE_SCHEMA_MISMATCH",
  );
  item.available_structures[0].status = "failed";
  await assert.rejects(client.listMaterials(), (error) => error.kind === "schema");
});

test("uploaded filenames use UTF-8 encoding", async () => {
  const client = new StudydyApiClient(async (_path, init) => {
    assert.equal(init.headers["X-Material-Name"], encodeURIComponent("陣列 & 堆疊.pdf"));
    return Response.json({ schema: "material-sources/v1", material_id: materialId, sources: [] });
  });
  await client.uploadSource(
    materialId,
    new File(["pdf"], "陣列 & 堆疊.pdf", { type: "application/pdf" }),
    "application/pdf",
    "upload",
  );
});

function questionRecord() {
  const question = {
    schema: "single-choice-assessment/v1",
    assessment_revision: `assessment:sha256:${"4".repeat(64)}`,
    study_session_id: sessionId,
    knowledge_structure_revision: structureRevision,
    question_id: `question:sha256:${"5".repeat(64)}`,
    target_concept_id: conceptId,
    target_claim_id: claimId,
    source_evidence_ids: [evidenceId],
    question_type: "single_choice",
    prompt: "Saved question",
    options: Array.from({ length: 4 }, (_, index) => ({
      option_id: `option:sha256:${String(index + 1).repeat(64)}`,
      text: String(index),
    })),
  };
  return {
    assessment: question,
    created_at: "2026-09-11T00:01:00Z",
  };
}

function assessmentSetSummary(overrides = {}) {
  return {
    set_id: setId,
    target_concept_id: conceptId,
    kind: "diagnostic",
    diagnostic_set_id: null,
    status: "preparing",
    set_version: 1,
    requested_count: 1,
    published_count: 0,
    answered_count: 0,
    passed_count: 0,
    assessment_revisions: [],
    created_at: "2026-09-20T00:00:00Z",
    completed_at: null,
    ...overrides,
  };
}

function resumeView() {
  return {
    schema: "study-resume/v1",
    assessment_sets: [],
    selected_set_id: null, selected_set: null,
    run_id: runId,
    source_artifact_id: libraryItem().source_artifact_id,
    session: {
      schema: "study-session/v1",
      study_session_id: sessionId,
      material_id: materialId,
      knowledge_structure_revision: structureRevision,
      current_concept_id: conceptId,
      no_safe_claim_ids: [],
      deferred_concept_ids: [],
      status: "active",
      event_watermark: 0,
      started_at: "2026-09-11T00:00:00Z",
      completed_at: null,
    },
    knowledge_structure: structureView(),
    progress: {
      schema: "learner-progress/v1",
      starting_level: "some_knowledge", learning_path: [conceptId],
      assessment_cycles: [],
      study_session_id: sessionId,
      knowledge_structure_revision: structureRevision,
      event_watermark: 0,
      current_concept_id: conceptId,
      deferred_concept_ids: [],
      concept_states: [{ concept_id: conceptId, label: "Stack", status: "not_started", attempts: 0, assessable_claim_ids: [claimId] }],
      weaknesses: [],
      next_action: {
        action: "assess",
        target_concept_id: conceptId,
        target_claim_id: claimId,
        prerequisite_concept_ids: [],
        reason: "current_concept",
      },
      guidance_revision: `learner-guidance:sha256:${"6".repeat(64)}`,
    },
  };
}

test("resume binds material, run and saved study session", async () => {
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
  assert.equal(new URL(requests[0][0], "http://localhost").searchParams.get("run_id"), runId);
  await assert.rejects(
    client.resumeStudy({ ...request, materialId: sessionId }),
    (error) => error.kind === "schema",
  );
  await assert.rejects(
    client.resumeStudy({ ...request, runId: sessionId }),
    (error) => error.kind === "schema",
  );
});

test("resume rejects mismatched session revisions and unknown selected sets", async () => {
  for (const mutate of [
    (candidate) => {
      candidate.selected_set_id = materialId;
    },
    (candidate) => {
      candidate.progress.event_watermark = 1;
    },
    (candidate) => {
      candidate.session.knowledge_structure_revision = `knowledge-structure:sha256:${"f".repeat(64)}`;
    },
  ]) {
    const value = resumeView();
    mutate(value);
    await assert.rejects(
      new StudydyApiClient(async () => Response.json(value)).resumeStudy({
        materialId,
        studySessionId: sessionId,
        runId,
        structureRevision,
      }),
      (error) => error.kind === "schema",
    );
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
    {
      path: "/v1/session/login",
      body: { email: "learner@example.com", password: "Synthetic password 42" },
    },
    {
      path: "/v1/accounts",
      body: { email: "learner@example.com", password: "Synthetic password 42" },
    },
  ]);
  for (const [reason, status, message] of [
    ["INVALID_EMAIL", 400, "請輸入有效的 Email 格式。"],
    ["INVALID_CREDENTIALS", 401, "Email 或密碼不正確。"],
    ["ACCOUNT_UNAVAILABLE", 409, "這個 Email 已被使用，請使用其他 Email。"],
    ["STORAGE_UNAVAILABLE", 503, "資料服務暫時無法使用，請稍後再試。"],
    ["MATERIAL_NOT_DISCARDABLE", 409, "這份教材正在刪除，無法進行這項操作。"],
  ]) {
    const failed = new StudydyApiClient(async () =>
      apiErrorResponse(reason, status, status === 503),
    );
    await assert.rejects(
      failed.authenticate("login", "learner@example.com", "Synthetic password 42"),
      (error) =>
        error instanceof ApiClientError &&
        error.reasonCode === reason &&
        error.message === message &&
        error.retryable === (status === 503),
    );
  }
});

for (const operation of ["refresh", "login", "logout"]) {
  test(`${operation} has a bounded deadline and can retry without late abort`, async (context) => {
    context.mock.timers.enable({ apis: ["setTimeout"] });
    const signals = [];
    let hang = true;
    const client = new StudydyApiClient(async (_path, init) => {
      signals.push(init.signal);
      if (hang) return new Promise(() => {});
      return init.method === "DELETE"
        ? new Response(null, { status: 204 })
        : Response.json({ schema: "learner-identity/v1", learner_id: sessionId });
    });
    const invoke = () =>
      operation === "refresh"
        ? client.ensureSession()
        : operation === "logout"
          ? client.logout()
          : client.authenticate(operation, "learner@example.com", "Synthetic password 42");
    const rejected = assert.rejects(
      invoke(),
      (error) => error.reasonCode === "REQUEST_TIMEOUT" && error.retryable,
    );
    context.mock.timers.tick(10_000);
    await rejected;
    assert.equal(signals[0].aborted, true);
    hang = false;
    await invoke();
    context.mock.timers.tick(100_000);
    client.invalidate();
    assert.ok(signals.slice(1).every((signal) => !signal.aborted));
  });
}

test("deadline covers body parsing and late 401 cannot retire a recovered client", async (context) => {
  context.mock.timers.enable({ apis: ["setTimeout"] });
  let finish;
  let parsing;
  let first = true;
  const started = new Promise((resolve) => {
    parsing = resolve;
  });
  const client = new StudydyApiClient(async () => {
    if (!first) return Response.json({ schema: "learner-identity/v1", learner_id: sessionId });
    first = false;
    return {
      ok: false,
      status: 401,
      json: () => {
        parsing();
        return new Promise((resolve) => {
          finish = resolve;
        });
      },
    };
  });
  let expired = 0;
  client.onSessionExpired = () => expired++;
  const rejected = assert.rejects(
    client.ensureSession(),
    (error) => error.reasonCode === "REQUEST_TIMEOUT",
  );
  await started;
  context.mock.timers.tick(10_000);
  await rejected;
  await client.ensureSession();
  finish({
    schema: "api-error/v1",
    request_id: sessionId,
    reason_code: "SESSION_REQUIRED",
    retryable: false,
    message: "Request could not be completed.",
  });
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(expired, 0);
  await client.ensureSession();
});

test("intentional cancellation settles hanging auth without a timeout error", async (context) => {
  context.mock.timers.enable({ apis: ["setTimeout"] });
  const client = new StudydyApiClient(async () => new Promise(() => {}));
  const rejected = assert.rejects(
    client.ensureSession(),
    (error) => error.reasonCode === "SESSION_REQUIRED" && !error.retryable,
  );
  client.invalidate();
  await rejected;
  context.mock.timers.tick(100_000);
});

test("long product reads do not inherit auth deadline", async (context) => {
  context.mock.timers.enable({ apis: ["setTimeout"] });
  let finish;
  let signal;
  const client = new StudydyApiClient(async (_path, init) => {
    signal = init.signal;
    return new Promise((resolve) => {
      finish = resolve;
    });
  });
  const pending = client.getMaterialRun(runId);
  context.mock.timers.tick(100_000);
  assert.equal(signal.aborted, false);
  finish(Response.json(runView()));
  await pending;
});

test("malformed gateway errors remain distinct from successful schema errors and credentials", async () => {
  for (const status of [200, 400, 401, 502, 503]) {
    const client = new StudydyApiClient(
      async () => new Response("<html>upstream</html>", { status }),
    );
    await assert.rejects(
      client.authenticate("login", "learner@example.com", "Synthetic password 42"),
      (error) =>
        status >= 500
          ? error.reasonCode === "SERVICE_UNAVAILABLE" &&
            error.retryable &&
            error.message.includes("暫時無法使用")
          : error.reasonCode === (status === 401 ? "EDGE_ACCESS_REQUIRED" : "RESPONSE_SCHEMA_MISMATCH"),
    );
  }
});

function cancellationView(status) {
  const view = runView();
  view.status = status;
  if (status === "succeeded" || status === "partial") return view;
  view.progress_stage = status === "pending" ? "queued" : "evidence";
  view.completed_pages = status === "pending" ? 0 : 1;
  view.total_pages = status === "pending" ? null : 2;
  view.output_binding = null;
  view.completed_at = status === "failed" || status === "cancelled" ? "2026-09-05T00:00:02Z" : null;
  view.cancel_requested_at = status === "cancelled" ? "2026-09-05T00:00:01Z" : null;
  view.error_code = status === "failed" ? "NO_USABLE_EVIDENCE" : null;
  return view;
}

test("run responses validate cancellation states, lifecycle and timestamps", async () => {
  for (const status of ["pending", "running", "cancelled", "failed", "succeeded", "partial"]) {
    const value = cancellationView(status);
    const client = new StudydyApiClient(async () => Response.json(value));
    assert.equal((await client.getMaterialRun(runId)).status, status);
  }
  const accepted = { ...cancellationView("running"), cancel_requested_at: "2026-09-05T00:00:01Z" };
  assert.deepEqual(
    await new StudydyApiClient(async () => Response.json(accepted)).getMaterialRun(runId),
    accepted,
  );
  for (const progress_stage of ["review", "publishing"]) {
    const value = { ...accepted, progress_stage, completed_units: 18, total_units: 49 };
    assert.deepEqual(await new StudydyApiClient(async () => Response.json(value)).getMaterialRun(runId), value);
  }
  for (const [status, overrides] of [
    ["cancelled", { cancel_requested_at: null }],
    ["cancelled", { completed_at: null }],
    ["cancelled", { output_binding: runView().output_binding }],
    ["cancelled", { error_code: "FAILED" }],
    ["succeeded", { cancel_requested_at: "2026-09-05T00:00:01Z" }],
    ["running", { progress_stage: "review", completed_units: 51, total_units: 50 }],
    ["running", { completed_units: 1, total_units: null }],
    ["running", { completed_at: "2026-09-05T00:00:01Z" }],
    ["pending", { cancel_requested_at: "2026-09-05T00:00:01Z" }],
    ["failed", { cancel_requested_at: "2026-09-05T00:00:01Z" }],
    ["cancelled", { cancel_requested_at: "not-a-date" }],
    ["cancelled", { cancel_requested_at: "2026-02-30T00:00:00Z" }],
    ["cancelled", { cancel_requested_at: "2026-09-05T24:00:00Z" }],
    // JSON 序列化會省略 undefined，模擬缺少必填欄位的回應。
    ["running", { cancel_requested_at: undefined }],
    ["running", { updated_at: "invalid" }],
    ["running", { schema: "material-processing-run/v2" }],
  ]) {
    const value = { ...cancellationView(status), ...overrides };
    await assert.rejects(
      new StudydyApiClient(async () => Response.json(value)).getMaterialRun(runId),
      (error) => error.kind === "schema",
    );
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
      return Response.json(
        { schema: "material-discard/v1", material_id: materialId, state },
        { status: 202 },
      );
    });
    assert.equal((await client.discardMaterial(materialId)).state, state);
  }
  for (const value of [
    { schema: "material-discard/v2", material_id: materialId, state: "removed" },
    { schema: "material-discard/v1", material_id: "invalid", state: "removed" },
    { schema: "material-discard/v1", material_id: runId, state: "removed" },
    { schema: "material-discard/v1", material_id: materialId, state: "deleting" },
    { schema: "material-discard/v1", material_id: materialId, state: "removed", extra: true },
    { schema: "material-discard/v1", material_id: materialId },
  ]) {
    await assert.rejects(
      new StudydyApiClient(async () => Response.json(value)).discardMaterial(materialId),
      (error) => error.kind === "schema",
    );
  }
});

test("v1 library attempts preserve cancellation intent while older published maps remain usable", async () => {
  for (const status of ["running", "cancelled"]) {
    const item = libraryItem();
    const value = cancellationView(status);
    if (status === "running") value.cancel_requested_at = "2026-09-05T00:00:01Z";
    item.latest_attempt = Object.fromEntries(
      [
        "run_id",
        "status",
        "progress_stage",
        "completed_pages",
        "total_pages",
        "error_code",
        "created_at",
        "cancel_requested_at",
      ].map((key) => [key, value[key]]),
    );
    const client = new StudydyApiClient(async () =>
      Response.json({ schema: "material-library/v1", materials: [item] }),
    );
    assert.equal((await client.listMaterials()).materials[0].available_structures.length, 1);
    item.latest_attempt.cancel_requested_at = "invalid";
    await assert.rejects(client.listMaterials(), (error) => error.kind === "schema");
  }
});

test("focus is an owner-bound state setter without a create intent", async () => {
  const value = resumeView().session;
  let sent;
  const client = new StudydyApiClient(async (path, init) => {
    sent = { path, init };
    return Response.json(value);
  });
  assert.deepEqual(await client.focusStudySession(sessionId, conceptId), value);
  assert.equal(sent.path, `/v1/study-sessions/${sessionId}/focus`);
  assert.equal(sent.init.method, "POST");
  assert.ok(sent.init.headers.Origin);
  assert.equal(sent.init.headers["Idempotency-Key"], undefined);
  assert.deepEqual(JSON.parse(sent.init.body), {
    schema: "study-session-focus/v1",
    current_concept_id: conceptId,
  });
  await assert.rejects(
    client.focusStudySession(materialId, conceptId),
    (error) => error.kind === "schema",
  );
});

test("material rename uses the existing item guard and exact identity without a create key", async () => {
  const item = { ...libraryItem(), display_name: "作業系統 第五章" };
  let sent;
  const client = new StudydyApiClient(async (path, init) => {
    sent = { path, init };
    return Response.json(item);
  });
  assert.deepEqual(await client.renameMaterial(materialId, item.display_name), item);
  assert.equal(sent.path, `/v1/materials/${materialId}/rename`);
  assert.equal(sent.init.method, "POST");
  assert.ok(sent.init.headers.Origin);
  assert.equal(sent.init.headers["Idempotency-Key"], undefined);
  assert.deepEqual(JSON.parse(sent.init.body), {
    schema: "material-rename/v1",
    display_name: item.display_name,
  });
  await assert.rejects(
    client.renameMaterial(sessionId, item.display_name),
    (error) => error.kind === "schema",
  );
});

function assessmentSetView() {
  return {
    ...assessmentSetSummary({ set_version: 2 }),
    schema: "assessment-set/v1",
    study_session_id: sessionId,
    material_id: materialId,
    knowledge_structure_revision: structureRevision,
    selection_policy: "single-concept-grounded-points/v1",
    point_count: 1,
    excluded_count: 0,
    verified_count: 1,
    can_retry: false,
    can_publish_partial: false,
    can_complete: false,
    cycle: {
      diagnostic_set_id: setId,
      concept_id: conceptId,
      set_version: 2,
      outcome: "in_progress",
      active_set_id: setId,
      passed_count: 0,
      remediation_passed_count: 0,
      pending_count: 0,
      unanswered_count: 0,
      unavailable_count: 1,
      can_create_remediation: false,
      points: [
        {
          claim_id: claimId,
          result: "unavailable",
          latest_answer_event_id: null,
          latest_set_id: null,
        },
      ],
    },
    items: [
      {
        ordinal: 1,
        target_concept_id: conceptId,
        target_claim_id: claimId,
        state: "verified",
        attempts: 1,
        failure_reason: null,
        assessment: null,
        feedback: null,
        created_at: null,
        can_submit: false,
      },
    ],
  };
}

test("assessment sets validate preparation, published data and submission requests", async () => {
  const record = questionRecord();
  const base = assessmentSetView();
  const read = (value) =>
    new StudydyApiClient(async () => Response.json(value)).readAssessmentSet(sessionId, setId);
  assert.equal((await read(base)).verified_count, 1);
  const ready = structuredClone(base);
  Object.assign(ready, {
    status: "ready",
    published_count: 1,
    can_complete: true,
    assessment_revisions: [record.assessment.assessment_revision],
  });
  Object.assign(ready.items[0], {
    state: "published",
    assessment: record.assessment,
    created_at: record.created_at,
    can_submit: true,
  });
  assert.equal((await read(ready)).published_count, 1);
  const done = structuredClone(ready);
  const option = record.assessment.options[0].option_id;
  Object.assign(done, { status: "completed", can_complete: false, answered_count: 1, passed_count: 1 });
  Object.assign(done.items[0], { can_submit: false, feedback: {
    schema: "answer-feedback/v1", answer_event_id: sessionId, study_session_id: sessionId,
    assessment_revision: record.assessment.assessment_revision, question_id: record.assessment.question_id,
    selected_option_id: option, correct_option_id: option, is_correct: true, rationale: "Saved explanation",
    source_evidence_ids: [evidenceId], event_number: 1, created_at: record.created_at,
  } });
  assert.equal((await read(done)).items[0].feedback.correct_option_id, option);
  for (const invalid of ["bad", `option:sha256:${"f".repeat(64)}`]) {
    done.items[0].feedback.correct_option_id = invalid;
    await assert.rejects(read(done), error => error.kind === "schema");
  }
  delete done.items[0].feedback.correct_option_id;
  assert.equal((await read(done)).items[0].feedback.rationale, "Saved explanation");

  for (const overrides of [
    { study_session_id: materialId },
    { verified_count: 0 },
    { target_plan: {} },
    { passed_count: 1 },
    { excluded_count: 1 },
  ]) {
    await assert.rejects(read({ ...ready, ...overrides }), (error) => error.kind === "schema");
  }
  for (const overrides of [
    { prepared_document: { private: "hidden" } },
    { assessment: { ...record.assessment, target_claim_id: `claim:sha256:${"f".repeat(64)}` } },
    { assessment: { ...record.assessment, correct_option_id: "secret" } },
  ]) {
    const invalid = { ...ready, items: [{ ...ready.items[0], ...overrides }] };
    await assert.rejects(read(invalid), (error) => error.kind === "schema");
  }

  const answer = {
    assessment_revision: record.assessment.assessment_revision,
    question_id: record.assessment.question_id,
    selected_option_id: record.assessment.options[0].option_id,
  };
  let request;
  const client = new StudydyApiClient(async (path, init) => {
    request = { path, init };
    return Response.json(ready);
  });
  // 交卷回應仍是 ready，不能當作已完成交卷。
  await assert.rejects(
    client.submitAssessmentSet(sessionId, setId, [answer], 2, "one-submit"),
    (error) => error.kind === "schema",
  );
  assert.equal(
    request.path,
    `/v1/study-sessions/${sessionId}/assessment-sets/${setId}/submissions`,
  );
  assert.equal(request.init.headers["Idempotency-Key"], "one-submit");
  assert.deepEqual(JSON.parse(request.init.body), {
    schema: "assessment-set-submission/v1",
    expected_set_version: 2,
    answers: [answer],
  });
});

test("resume retries only snapshot conflicts and bounds read attempts", async () => {
  const request = { materialId, structureRevision, studySessionId: sessionId, runId };
  for (const recover of [true, false]) {
    let calls = 0;
    const client = new StudydyApiClient(async (_path, init) => {
      assert.equal(init.method, "GET");
      calls++;
      if (recover && calls === 2) return Response.json(resumeView());
      return apiErrorResponse("IDEMPOTENCY_CONFLICT", 409);
    });
    if (recover) {
      assert.equal((await client.resumeStudy(request)).session.study_session_id, sessionId);
      assert.equal(calls, 2);
    } else {
      await assert.rejects(
        client.resumeStudy(request),
        (error) => error.reasonCode === "IDEMPOTENCY_CONFLICT",
      );
      assert.equal(calls, 3);
    }
  }
});

test("active set lists allow different concepts and identify duplicate creation explicitly", async () => {
  const secondId = "66666666-6666-4666-8666-666666666666";
  const summary = assessmentSetSummary();
  const list = {
    schema: "assessment-set-list/v1",
    study_session_id: sessionId,
    knowledge_structure_revision: structureRevision,
    active_set_ids: [setId, secondId],
    sets: [
      summary,
      { ...summary, set_id: secondId, target_concept_id: `concept:sha256:${"e".repeat(64)}` },
    ],
  };
  const read = (value) =>
    new StudydyApiClient(async () => Response.json(value)).listAssessmentSets(sessionId);
  assert.equal((await read(list)).active_set_ids.length, 2);
  for (const corrupt of [
    (candidate) => {
      candidate.active_set_ids = [];
    },
    (candidate) => {
      candidate.active_set_ids = [setId, setId];
    },
    (candidate) => {
      candidate.sets[1].target_concept_id = conceptId;
    },
    (candidate) => {
      candidate.study_session_id = materialId;
    },
  ]) {
    const value = structuredClone(list);
    corrupt(value);
    await assert.rejects(read(value), (error) => error.kind === "schema");
  }
  const client = new StudydyApiClient(async () => apiErrorResponse("ASSESSMENT_SET_ACTIVE", 409));
  await assert.rejects(
    client.createAssessmentSet(sessionId, conceptId, "one-intent"),
    (error) => error.reasonCode === "ASSESSMENT_SET_ACTIVE",
  );
});

test("resume authorizes saved group membership independently of navigation focus", async () => {
  const value = resumeView();
  const other = `concept:sha256:${"e".repeat(64)}`;
  const second = structuredClone(value.knowledge_structure.concepts[0]);
  second.concept_id = other;
  second.label = "Other";
  second.claims[0].claim_id = `claim:sha256:${"e".repeat(64)}`;
  value.knowledge_structure.concepts.push(second);
  value.knowledge_structure.document_tree.sections[0].concept_ids.push(other);
  value.knowledge_structure.initial_learning_path.push({
    position: 2,
    concept_id: other,
    reason: "document_order",
  });
  value.session.current_concept_id = value.progress.current_concept_id = other;
  value.progress.concept_states.push({
    ...value.progress.concept_states[0],
    concept_id: other,
    label: "Other",
  });
  value.assessment_sets = [
    assessmentSetSummary({
      status: "ready",
      published_count: 1,
      assessment_revisions: [questionRecord().assessment.assessment_revision],
    }),
  ];
  value.selected_set_id = setId;
  value.selected_set = assessmentSetView();
  value.assessment_sets = [assessmentSetSummary({ set_version: value.selected_set.set_version })];
  const request = {
    materialId,
    structureRevision,
    studySessionId: sessionId,
    runId,
    assessmentSetId: setId,
  };
  const read = (value) =>
    new StudydyApiClient(async () => Response.json(value)).resumeStudy(request);
  assert.equal((await read(value)).selected_set_id, setId);
  for (const overrides of [{ study_session_id: materialId }, { material_id: sessionId }, { set_id: runId }, { set_version: 99 }]) {
    await assert.rejects(read({ ...value, selected_set: { ...value.selected_set, ...overrides } }), error => error.kind === "schema");
  }
  const missingSet = { ...value, assessment_sets: [], selected_set_id: null };
  await assert.rejects(read(missingSet), (error) => error.kind === "schema");
});


test("edge errors use fixed safe messages and never expose HTML", async () => {
  for (const [status, reason] of [[413, "MATERIAL_TOO_LARGE"], [429, "RATE_LIMITED"], [403, "EDGE_ACCESS_REQUIRED"]]) {
    const client = new StudydyApiClient(async () => new Response("<script>private-edge-body</script>", { status }));
    await assert.rejects(client.authenticate("login", "test@example.com", "Synthetic password 42"), error =>
      error.reasonCode === reason && !error.message.includes("private-edge-body") && error.retryable === (status === 429));
  }
  const response = new Response("<html>Access login</html>");
  Object.defineProperty(response, "redirected", { value: true });
  await assert.rejects(new StudydyApiClient(async () => response).authenticate("login", "test@example.com", "Synthetic password 42"), error => error.reasonCode === "EDGE_ACCESS_REQUIRED");
});

function podcastView() {
  const view = cardSetView();
  return {
    schema: "podcast/v1", podcast_id: setId, material_id: materialId,
    material_name: view.material_name, knowledge_structure_revision: structureRevision,
    name: "聽重點", delivery: "solo", video_enabled: false, concept_ids: [conceptId], status: "ready", error_code: null,
    version: 3, created_at: view.created_at, episode_count: 1, completed_episodes: 1, duration_seconds: 12,
    is_current_revision: true, source_resolver: view.source_resolver, source_status: view.status,
    excluded_pages: [], episodes: [{ delivery: "solo", claims: [{ ...view.cards[0].claims[0], concept_id: conceptId, label: "Stack" }],
      script: { provider: "synthetic-test", segments: [{ claim_id: claimId, turns: [{ speaker: "host", text: "堆疊遵循後進先出。" }] }] },
      audio: { artifact_id: runId, sha256: "f".repeat(64), duration_seconds: 12, provider: "synthetic-audio" } }],
  };
}

test("podcast playback rejects mismatched scripts, foreign sources and incomplete ready manifests", async () => {
  assert.equal((await new StudydyApiClient(async () => Response.json(podcastView())).getPodcast(setId)).episodes.length, 1);
  for (const change of [
    (v) => { v.podcast_id = materialId; },
    (v) => { v.source_resolver = v.source_resolver.replace(structureRevision, `knowledge-structure:sha256:${"f".repeat(64)}`); },
    (v) => { v.episodes[0].script.segments[0].claim_id = `claim:sha256:${"f".repeat(64)}`; },
    (v) => { v.episodes[0].claims[0].evidence = []; },
    (v) => { v.episodes[0].audio = null; v.completed_episodes = 0; v.duration_seconds = null; },
    (v) => { v.episodes[0].script = null; },
    // 總時長只在音訊全部完成時提供，且不可為負數。
    (v) => { v.status = "running"; },
    (v) => { v.duration_seconds = -1; },
    (v) => { delete v.duration_seconds; },
  ]) {
    const value = podcastView(); change(value);
    await assert.rejects(new StudydyApiClient(async () => Response.json(value)).getPodcast(setId), e => e.kind === "schema");
  }
});

test("dialogue podcast reads two speakers while rejecting incomplete or mismatched turns", async () => {
  const value = podcastView();
  value.delivery = value.episodes[0].delivery = "dialogue";
  value.episodes[0].script.segments = [{ claim_id: claimId, turns: [{ speaker: "host", text: "可以舉個例子嗎？" }, { speaker: "guest", text: "想像疊盤子，最後放的先取。" }] }];
  assert.equal((await new StudydyApiClient(async () => Response.json(value)).getPodcast(setId)).episodes[0].script.segments[0].turns.length, 2);
  value.episodes[0].script.segments[0].turns[1].speaker = "host";
  await assert.rejects(new StudydyApiClient(async () => Response.json(value)).getPodcast(setId), e => e.kind === "schema");
});

test("podcast host names match the delivery roles", async () => {
  const value = podcastView();
  value.delivery = value.episodes[0].delivery = "dialogue";
  value.episodes[0].script.segments = [{ claim_id: claimId, turns: [{ speaker: "host", text: "今天先從疊盤子說起。" }, { speaker: "guest", text: "最後放的先取。" }] }];
  value.episodes[0].hosts = { host: "小晴", guest: "阿哲" };
  assert.deepEqual((await new StudydyApiClient(async () => Response.json(value)).getPodcast(setId)).episodes[0].hosts, { host: "小晴", guest: "阿哲" });
  for (const hosts of [{ host: "小晴" }, { host: "小晴", guest: " " }, { host: "小晴", guest: "阿哲", extra: "x" }, "小晴"]) {
    value.episodes[0].hosts = hosts;
    await assert.rejects(new StudydyApiClient(async () => Response.json(value)).getPodcast(setId), e => e.kind === "schema");
  }
  const solo = podcastView();
  solo.episodes[0].hosts = { host: "小晴" };
  assert.equal((await new StudydyApiClient(async () => Response.json(solo)).getPodcast(setId)).episodes[0].hosts.host, "小晴");
  solo.episodes[0].hosts = { host: "小晴", guest: "阿哲" };
  await assert.rejects(new StudydyApiClient(async () => Response.json(solo)).getPodcast(setId), e => e.kind === "schema");
});


test("extended dialogue keeps all follow-up turns", async () => {
  const value = podcastView();
  value.delivery = value.episodes[0].delivery = "dialogue";
  const turns = Array.from({ length: 8 }, (_, i) => ({ speaker: i % 2 ? "guest" : "host", text: i % 2 ? "先確認條件，再決定操作。" : "那這個條件不成立時呢？" }));
  value.episodes[0].script.segments = [{ claim_id: claimId, turns }];
  assert.equal((await new StudydyApiClient(async () => Response.json(value)).getPodcast(setId)).episodes[0].script.segments[0].turns.length, 8);
  turns.push({ speaker: "host", text: "這是超過契約上限的輪次。" });
  await assert.rejects(new StudydyApiClient(async () => Response.json(value)).getPodcast(setId), e => e.kind === "schema");
});

test("dialogue permits a speaker to continue across source segments", async () => {
  const value = podcastView();
  value.delivery = value.episodes[0].delivery = "dialogue";
  const second = `claim:sha256:${"b".repeat(64)}`;
  value.episodes[0].claims.push({ ...value.episodes[0].claims[0], claim_id: second });
  value.episodes[0].script.segments = [
    { claim_id: claimId, turns: [{ speaker: "guest", text: "說明者可以先完整說清楚這個重點。" }] },
    { claim_id: second, turns: [{ speaker: "host", text: "原來這就是我剛才弄混的地方。" }] },
  ];
  assert.equal((await new StudydyApiClient(async () => Response.json(value)).getPodcast(setId)).episodes[0].script.segments.length, 2);
  value.episodes[0].script.segments[1].turns[0].speaker = "guest";
  await assert.rejects(new StudydyApiClient(async () => Response.json(value)).getPodcast(setId), e => e.kind === "schema");
});

test('teaching beats keep evidence binding without requiring review or exhaustive coverage',async()=>{
  const value=podcastView(),episode=value.episodes[0];
  episode.claims.push(structuredClone(episode.claims[0]));
  const parts=episode.claims.map((claim,index)=>({text:`來源位置${index}的說明。`,source_refs:[{source_index:index,evidence_ids:claim.evidence.map(e=>e.evidence_id)}]}));
  episode.script={schema:'podcast-script/v2',provider:'synthetic',segments:[{beat_id:'beat-0',title:'整合來源',turns:[{speaker:'host',text:parts.map(p=>p.text).join(''),parts}]}],
    review:{correctness:{passed:true,reason:'fixture'},teaching_quality:{passed:true,reason:'fixture'}}};
  assert.equal((await new StudydyApiClient(async()=>Response.json(value)).getPodcast(setId)).episodes[0].script.segments.length,1);
  delete episode.script.review;
  episode.script.segments[0].turns[0].parts[1].source_refs=[];
  assert.equal((await new StudydyApiClient(async()=>Response.json(value)).getPodcast(setId)).episodes[0].script.segments.length,1);
  episode.script.segments[0].turns[0].parts[1].source_refs=[{source_index:1,evidence_ids:episode.claims[1].evidence.map(e=>e.evidence_id)}];
  for(const change of [
    v=>{v.episodes[0].script.segments[0].turns[0].parts[1].source_refs[0].source_index=99},
    v=>{v.episodes[0].script.segments[0].turns[0].parts[0].source_refs[0].evidence_ids=['foreign']},
    v=>{v.episodes[0].script.segments[0].turns[0].text+='未綁定的文字'},
  ]) {
    const changed=structuredClone(value);change(changed);
    await assert.rejects(new StudydyApiClient(async()=>Response.json(changed)).getPodcast(setId),e=>e.kind==='schema');
  }
});

test('recording upload returns a validated turn identity for scoped voice playback', async () => {
  const requests = [];
  const audio = new Blob(['synthetic recording'], {type:'audio/webm'});
  const client = new StudydyApiClient(async (url, init) => {
    requests.push({url,init});return Response.json({turn_id:runId},{status:202});
  });
  assert.equal(await client.voiceRecording(sessionId,audio,'recording-intent'),runId);
  assert.equal(requests[0].url,`/v1/voice-conversations/${sessionId}/recordings`);
  assert.equal(requests[0].init.body,audio);
  assert.equal(requests[0].init.headers['Idempotency-Key'],'recording-intent');
  for(const value of [{}, {turn_id:'not-a-turn'}, {turn_id:42}]){
    const invalid = new StudydyApiClient(async()=>Response.json(value,{status:202}));
    await assert.rejects(invalid.voiceRecording(sessionId,audio,'recording-intent'),error=>error.kind==='schema');
  }
});


test("podcast cancellation accepts only a deletion receipt for the same podcast", async () => {
  const body={schema:"podcast-action/v1",action:"cancel",expected_version:1};
  const receipt={schema:"podcast-deleted/v1",podcast_id:setId};
  assert.deepEqual(await new StudydyApiClient(async()=>Response.json(receipt)).podcastAction(setId,body),receipt);
  for(const value of [podcastView(),{...receipt,podcast_id:materialId}])
    await assert.rejects(new StudydyApiClient(async()=>Response.json(value)).podcastAction(setId,body),e=>e.kind==="schema");
});

test("podcast background generation counts stay within the episode count", async () => {
  const value=podcastView();value.generation={active:true,completed_videos:0,failed_videos:0};
  assert.equal((await new StudydyApiClient(async()=>Response.json(value)).getPodcast(setId)).generation.active,true);
  for(const generation of [{active:"yes",completed_videos:0,failed_videos:0},{active:false,completed_videos:2,failed_videos:0},{active:false,completed_videos:1,failed_videos:1}])
    await assert.rejects(new StudydyApiClient(async()=>Response.json({...value,generation})).getPodcast(setId),e=>e.kind==="schema");
});


test("immutable structures are shared across features but never across invalidated sessions", async () => {
  let calls = 0;
  const client = new StudydyApiClient(async () => { calls++; return Response.json(structureView()); });
  const request = { materialId, structureRevision };
  const [a, b] = await Promise.all([client.getKnowledgeStructure(request), client.getKnowledgeStructure(request)]);
  assert.equal(a, b);
  assert.equal(await client.getKnowledgeStructure(request), a);
  assert.equal(calls, 1);
  client.invalidate();
  await assert.rejects(client.getKnowledgeStructure(request), error => error.reasonCode === "SESSION_REQUIRED");
});

test("failed structure reads can be retried", async () => {
  let calls = 0;
  const client = new StudydyApiClient(async () => ++calls === 1 ? Response.json({}) : Response.json(structureView()));
  const request = { materialId, structureRevision };
  await assert.rejects(client.getKnowledgeStructure(request));
  await client.getKnowledgeStructure(request);
  assert.equal(calls, 2);
});

test("current map and saved card revision stay cached together", async () => {
  const previous = `knowledge-structure:sha256:${"f".repeat(64)}`;
  const calls = [];
  const client = new StudydyApiClient(async path => {
    calls.push(path);
    const revision = decodeURIComponent(String(path).split("/").at(-1));
    const view = structureView();
    view.knowledge_structure_revision = revision;
    view.source_resolver = view.source_resolver.replace(encodeURIComponent(structureRevision), encodeURIComponent(revision));
    return Response.json(view);
  });
  for (const revision of [structureRevision, previous, structureRevision, previous]) {
    await client.getKnowledgeStructure({ materialId, structureRevision: revision });
  }
  assert.equal(calls.length, 2);
});

test('on-demand outlines, concepts and workspace enforce material/version/selection boundaries', async () => {
  const { outlineView, conceptsView, workspaceView, mapProgressView } = await import('../../e2e/fixtures/knowledge-read-views.mjs');
  const full = structureView();
  const request = { materialId, structureRevision };
  const overview = outlineView(full, materialId, runId);
  const content = conceptsView(full, materialId, [conceptId]);
  const calls = [];
  let body = overview;
  const client = new StudydyApiClient(async path => { calls.push(path); return Response.json(body); });
  assert.deepEqual(await client.getKnowledgeOutline(request), overview);
  assert.deepEqual(await client.getKnowledgeOutline(request), overview);
  assert.equal(calls.length, 1);
  assert.ok(!Object.hasOwn(overview.concepts[0], 'claims'));
  body = content;
  assert.deepEqual(await client.getConceptContext(request, [conceptId]), content);
  assert.ok(calls[1].includes('/concepts?'));
  for (const corrupt of [
    value => { value.material_id = sessionId; },
    value => { value.knowledge_structure_revision = `knowledge-structure:sha256:${'f'.repeat(64)}`; },
    value => { value.requested_concept_ids = []; },
    value => { value.concepts[0].claims[0].evidence[0].source_locator.page = -1; },
  ]) {
    const value = structuredClone(content); corrupt(value);
    await assert.rejects(new StudydyApiClient(async () => Response.json(value)).getConceptContext(request, [conceptId]), error => error.kind === 'schema');
  }
  const workspace = workspaceView(resumeView());
  const resumeRequest = { ...request, runId, studySessionId: sessionId };
  assert.equal((await new StudydyApiClient(async () => Response.json(workspace)).resumeWorkspace(resumeRequest)).schema, 'study-workspace/v1');
  for (const corrupt of [
    value => { value.context = null; },
    value => { value.outline.run_id = sessionId; },
    value => { value.progress.concept_count++; },
    value => { value.progress.concept_states = []; },
    value => { value.context.knowledge_structure_revision = `knowledge-structure:sha256:${'f'.repeat(64)}`; },
  ]) {
    const value = structuredClone(workspace); corrupt(value);
    await assert.rejects(new StudydyApiClient(async () => Response.json(value)).resumeWorkspace(resumeRequest), error => error.kind === 'schema');
  }
  const summary = mapProgressView(resumeView().progress);
  assert.equal((await new StudydyApiClient(async () => Response.json(summary)).readMapProgress(sessionId, structureRevision)).schema, 'map-progress/v1');
  client.invalidate();
  await assert.rejects(client.getKnowledgeOutline(request), error => error.reasonCode === 'SESSION_REQUIRED');
  await assert.rejects(client.getConceptContext(request, [conceptId]), error => error.reasonCode === 'SESSION_REQUIRED');
});

test("profile is read once per login, updated with Origin and pushed to listeners", async () => {
  const requests = [];
  let stored = null;
  const client = new StudydyApiClient(async (path, init) => {
    requests.push([path, init.method, init.headers?.Origin ?? null, init.body ?? null]);
    if (init.method === "POST") stored = JSON.parse(init.body).display_name;
    return Response.json({ schema: "learner-profile/v1", display_name: stored });
  });
  assert.equal((await client.getProfile()).display_name, null);
  await client.getProfile();
  assert.equal(requests.length, 1);
  const seen = [];
  const stop = client.onProfileChange((profile) => seen.push(profile.display_name));
  assert.equal((await client.updateProfile("以安")).display_name, "以安");
  assert.deepEqual(JSON.parse(requests[1][3]), { schema: "learner-profile-update/v1", display_name: "以安" });
  assert.equal(requests[1][2], globalThis.location?.origin ?? requests[1][2]);
  assert.equal((await client.getProfile()).display_name, "以安");
  assert.equal(requests.length, 2);
  stop();
  await client.updateProfile(null);
  assert.deepEqual(seen, ["以安"]);
  const tooLong = new StudydyApiClient(async () => Response.json({ schema: "learner-profile/v1", display_name: "一".repeat(21) }));
  await assert.rejects(tooLong.getProfile(), ApiClientError);
});

test("account sessions, single revoke and password change send Origin and validate the device list", async () => {
  const requests = [];
  const current = { session_id: materialId, device_label: "Chrome · Windows", created_at: "2026-10-20T01:00:00Z", last_used_at: "2026-10-23T01:00:00Z", current: true };
  const phone = { ...current, session_id: "00000000-0000-4000-8000-0000000000aa", device_label: null, current: false };
  const client = new StudydyApiClient(async (path, init) => {
    requests.push([path, init.method, init.headers?.Origin ?? null, init.body ?? null]);
    return Response.json({ schema: "account-sessions/v2", sessions: path === "/v1/account/sessions" ? [current, phone] : [current] });
  });
  assert.deepEqual((await client.getAccountSessions()).sessions, [current, phone]);
  assert.equal((await client.revokeSession(phone.session_id)).sessions.length, 1);
  assert.equal((await client.revokeOtherSessions()).sessions.length, 1);
  assert.equal((await client.changePassword("old synthetic secret", "new synthetic secret 1")).sessions.length, 1);
  assert.deepEqual(requests.map(([path, method]) => [path, method]), [
    ["/v1/account/sessions", "GET"], [`/v1/account/sessions/${phone.session_id}`, "DELETE"],
    ["/v1/account/sessions/others", "DELETE"], ["/v1/account/password", "POST"],
  ]);
  assert.ok(requests[1][2] && requests[2][2] && requests[3][2]);
  assert.deepEqual(JSON.parse(requests[3][3]), {
    schema: "password-change/v1", current_password: "old synthetic secret", new_password: "new synthetic secret 1",
  });
  // 清單必須有且只有一個目前裝置並排第一；欄位格式不符或舊版契約都拒絕。
  for (const body of [
    { schema: "account-sessions/v2", sessions: [] },
    { schema: "account-sessions/v2", sessions: [phone, current] },
    { schema: "account-sessions/v2", sessions: [current, { ...phone, current: true }] },
    { schema: "account-sessions/v2", sessions: [{ ...current, device_label: "" }] },
    { schema: "account-sessions/v2", sessions: [{ ...current, last_used_at: "yesterday" }] },
    { schema: "account-sessions/v2", sessions: [{ ...current, session_id: "not-a-uuid" }] },
    { schema: "account-sessions/v1", active_sessions: 2 },
    { schema: "learner-profile/v1" },
  ]) {
    const broken = new StudydyApiClient(async () => Response.json(body));
    await assert.rejects(broken.getAccountSessions(), ApiClientError);
  }
  const wrong = new StudydyApiClient(async () => Response.json(
    { schema: "api-error/v1", request_id: materialId, reason_code: "CURRENT_PASSWORD_INVALID", retryable: false, message: "Request could not be completed." },
    { status: 400 }));
  await assert.rejects(wrong.changePassword("a", "new synthetic secret 1"), (error) => error.reasonCode === "CURRENT_PASSWORD_INVALID" && error.message === "目前密碼不正確。");
  const limited = new StudydyApiClient(async () => Response.json(
    { schema: "api-error/v1", request_id: materialId, reason_code: "TOO_MANY_ATTEMPTS", retryable: true, message: "Request could not be completed." },
    { status: 429 }));
  await assert.rejects(limited.changePassword("a", "new synthetic secret 1"), (error) => error.reasonCode === "TOO_MANY_ATTEMPTS" && error.retryable);
});

test("library search rejects mismatched queries and malformed hits", async () => {
  const body = (change = () => undefined) => {
    const value = {
      schema: "library-search/v1", query: "堆疊", concepts_truncated: false,
      materials: [{ material_id: materialId, display_name: "資料結構", run_id: null, knowledge_structure_revision: null }],
      concepts: [{ material_id: materialId, material_name: "資料結構", run_id: runId, knowledge_structure_revision: structureRevision, concept_id: conceptId, label: "堆疊", claim_count: 2 }],
    };
    change(value);
    return value;
  };
  const search = (value, text = " 堆疊 ") => new StudydyApiClient(async () => Response.json(value)).searchLibrary(text);
  assert.equal((await search(body())).concepts[0].label, "堆疊");
  await assert.rejects(search(body(), "佇列"), ApiClientError);
  for (const change of [
    (value) => { value.concepts[0].concept_id = "concept:bad"; },
    (value) => { value.concepts[0].run_id = null; },
    (value) => { value.materials[0].run_id = runId; },
    (value) => { value.concepts[0].claim_count = -1; },
  ]) await assert.rejects(search(body(change)), ApiClientError);
});

function cardReviews(change = () => undefined) {
  const { concept_id, label, claims } = structureView().concepts[0];
  const value = {
    schema: "card-reviews/v2", forgot_count: 1,
    sets: [{ card_set_id: setId, card_count: 2, forgot_count: 1, remembered_count: 1 }],
    marks: [],
    cards: [{
      card_set_id: setId, card_set_name: "複習", material_id: materialId, material_name: "Synthetic.pdf",
      knowledge_structure_revision: structureRevision,
      source_resolver: `/v1/materials/${materialId}/knowledge-structures/${structureRevision}/evidence`,
      review_count: 2,
      card: { concept_id, label, claims },
    }],
  };
  change(value);
  return value;
}

test("card reviews keep weak cards and marks bound to their card set and source", async () => {
  const paths = [];
  const read = (value, id, scope = { cardSetId: id }) => new StudydyApiClient(async (path) => { paths.push(path); return Response.json(value); }).getCardReviews(scope);
  assert.equal((await read(cardReviews())).cards[0].card.label, "Stack");
  const marked = (value) => { value.marks = [{ card_set_id: setId, concept_id: conceptId, rating: "remembered", review_count: 1 }]; };
  assert.equal((await read(cardReviews(marked), setId)).marks.length, 1);
  // 只有指定卡組時才會有標記清單；指定卡組時不接受別的卡組。
  await assert.rejects(read(cardReviews(marked)), ApiClientError);
  await assert.rejects(read(cardReviews(), materialId), ApiClientError);
  // 指定教材時只接受這份教材的卡片。
  const material = cardReviews().cards[0].material_id;
  assert.equal((await read(cardReviews(), undefined, { materialId: material })).cards.length, 1);
  assert.equal(paths.at(-1), `/v1/card-reviews?material_id=${material}`);
  await assert.rejects(read(cardReviews(), undefined, { materialId: setId }), ApiClientError);
  for (const change of [
    (value) => { value.cards[0].source_resolver = "/v1/materials/x/evidence"; },
    (value) => { value.cards[0].review_count = 0; },
    (value) => { value.cards.push(structuredClone(value.cards[0])); },
    (value) => { value.cards[0].card.claims = []; },
    (value) => { value.sets[0].forgot_count = -1; },
    (value) => { value.sets[0].remembered_count = 2; },
    (value) => { value.schema = "card-reviews/v1"; },
  ]) await assert.rejects(read(cardReviews(change), setId), ApiClientError);
});

test("card review sends the intent key and requires the next review count", async () => {
  const sent = [];
  const reply = (body) => async (path, init) => {
    sent.push([path, init.headers["Idempotency-Key"], JSON.parse(init.body)]);
    return Response.json(body);
  };
  const review = { schema: "card-review/v2", card_set_id: setId, concept_id: conceptId, rating: "remembered", review_count: 3,
    reviewed_at: "2026-10-21T12:00:00Z" };
  const saved = await new StudydyApiClient(reply(review)).submitCardReview(setId, conceptId, "remembered", 2, "intent-1");
  assert.equal(saved.review_count, 3);
  assert.deepEqual(sent[0], [`/v1/card-sets/${setId}/reviews`, "intent-1",
    { schema: "card-review-submit/v1", concept_id: conceptId, rating: "remembered", expected_review_count: 2 }]);
  // 回應的次數或評分與請求不符時不採用。
  await assert.rejects(new StudydyApiClient(reply({ ...review, review_count: 2 })).submitCardReview(setId, conceptId, "remembered", 2, "k"), ApiClientError);
  await assert.rejects(new StudydyApiClient(reply({ ...review, rating: "forgot" })).submitCardReview(setId, conceptId, "remembered", 2, "k"), ApiClientError);
  await assert.rejects(new StudydyApiClient(reply({ ...review, schema: "card-review/v1" })).submitCardReview(setId, conceptId, "remembered", 2, "k"), ApiClientError);
  // 只剩不熟／熟悉兩種標記。
  await assert.rejects(new StudydyApiClient(reply({ ...review, rating: "fuzzy" })).submitCardReview(setId, conceptId, "remembered", 2, "k"), ApiClientError);
});

function activityView(change = () => undefined) {
  const value = {
    schema: "learning-activity/v3", timezone: "Asia/Taipei", rule_version: "xp-rules/v4", today: "2026-10-21",
    streak_days: 1, active_today: true, total_answers: 4, total_correct: 3,
    xp: { total: 120, today: 40, level: 2, level_xp: 20, level_target: 150, sources: { answers: 46, concepts: 30, cards: 4, podcast: 5, goals: 15, difficulties: 20, diagnostics: 0 } },
    quests: [{ kind: "continue_study", progress: 1, target: 1, done: true, material_id: materialId, study_session_id: sessionId, podcast_id: null, episode_index: null }],
    badges: { rule_version: "badge-rules/v7", items: [
      { badge_id: "first_answer", earned: true, earned_on: "2026-10-16", progress: 1, target: 1 },
      { badge_id: "answers_100", earned: false, earned_on: null, progress: 4, target: 100 },
    ] },
  };
  change(value);
  return value;
}

test("learning activity requires XP, level, quests and badges from the v3 rules", async () => {
  const read = (value) => new StudydyApiClient(async () => Response.json(value)).getLearningActivity();
  assert.equal((await read(activityView())).xp.level, 2);
  for (const change of [
    (value) => { value.schema = "learning-activity/v2"; },
    (value) => { value.rule_version = "xp-rules/v3"; },
    (value) => { value.xp.level_xp = 150; },
    (value) => { value.xp.today = 121; },
    (value) => { value.xp.sources.cards = -2; },
    (value) => { delete value.xp.sources.goals; },
    (value) => { value.quests[0].kind = "badge"; },
    (value) => { value.quests[0].progress = 2; },
    (value) => { value.quests[0].study_session_id = "x"; },
    (value) => { delete value.badges; },
    (value) => { value.badges.rule_version = "badge-rules/v6"; },
    (value) => { value.badges.items[1].progress = 101; },
    // 取得與進度不一致、未取得卻有日期、日期格式錯誤都不採用。
    (value) => { value.badges.items[1].earned = true; },
    (value) => { value.badges.items[1].earned_on = "2026-10-20"; },
    (value) => { value.badges.items[0].earned_on = "10/16"; },
  ]) await assert.rejects(read(activityView(change)), ApiClientError);
});

function rhythmView(change = () => undefined) {
  const days = [19, 20, 21, 22, 23, 24, 25].map((day) => ({
    date: `2026-10-${day}`, answers: 0, correct: 0, card_reviews: 0, listened_seconds: 0, active: false,
  }));
  days[2] = { ...days[2], answers: 4, correct: 3, card_reviews: 2, listened_seconds: 360, active: true };
  const value = {
    schema: "learning-rhythm/v1", timezone: "Asia/Taipei", today: "2026-10-21", period: "week", offset: 0,
    start: "2026-10-19", end: "2026-10-25", first_day: "2026-10-02", streak_days: 1, days,
  };
  change(value);
  return value;
}

test("learning rhythm asks for one period page and needs consecutive days", async () => {
  const paths = [];
  const read = (value, period = "week", offset = 0) => new StudydyApiClient(async (path) => { paths.push(path); return Response.json(value); })
    .getLearningRhythm(period, offset);
  assert.equal((await read(rhythmView())).days.length, 7);
  assert.equal(paths[0], "/v1/learning-activity/rhythm?period=week&offset=0");
  for (const [change, period, offset] of [
    [(value) => { value.schema = "learning-rhythm/v0"; }],
    [() => undefined, "month"],
    [() => undefined, "week", 1],
    [(value) => { value.first_day = "10/02"; }],
    [(value) => { delete value.days[0].listened_seconds; }],
    [(value) => { value.days[1].correct = 9; }],
    [(value) => { value.days.reverse(); }],
    [(value) => { value.end = "2026-10-26"; }],
  ]) await assert.rejects(read(rhythmView(change), period, offset), ApiClientError);
});

test("learning activity is shared briefly and re-read after a card mark", async () => {
  let reads = 0;
  const review = { schema: "card-review/v2", card_set_id: setId, concept_id: conceptId, rating: "remembered", review_count: 1,
    reviewed_at: "2026-10-21T12:00:00Z" };
  const client = new StudydyApiClient(async (path) => {
    if (path === "/v1/learning-activity") { reads++; return Response.json(activityView()); }
    return Response.json(review);
  });
  const seen = [];
  client.onLearningActivityChange((value) => seen.push(value.xp.total));
  await client.getLearningActivity();
  await client.getLearningActivity();
  await client.getLearningActivity({ fresh: true });
  assert.equal(reads, 1);
  await client.submitCardReview(setId, conceptId, "remembered", 0, "k");
  await client.getLearningActivity();
  assert.equal(reads, 2);
  assert.deepEqual(seen, [120, 120]);
});

test("learning activity after logout rejects instead of throwing synchronously", async () => {
  const client = new StudydyApiClient(async () => Response.json(activityView()));
  client.invalidate();
  // 登出時 popstate 會讓頁首再讀一次；同步拋錯會繞過呼叫端的 .then 錯誤處理。
  const pending = client.getLearningActivity();
  await assert.rejects(pending, (error) => error.reasonCode === "SESSION_REQUIRED");
});

test("podcast progress posts listening reports with keepalive and checks the podcast", async () => {
  const podcastId = "77777777-7777-4777-8777-777777777777";
  const sent = [];
  const progress = { schema: "podcast-progress/v1", podcast_id: podcastId, credited_seconds: 15,
    current: { episode_index: 1, position_seconds: 42.5, updated_at: "2026-10-21T00:00:00Z" },
    episodes: [{ episode_index: 1, position_seconds: 42.5, completed: false }] };
  const client = new StudydyApiClient(async (path, init) => { sent.push([path, init.method, init.keepalive, init.body && JSON.parse(init.body)]); return Response.json(progress); });
  const body = { schema: "podcast-progress-update/v1", episode_index: 1, position_seconds: 42.5, listened_seconds: 15 };
  assert.equal((await client.savePodcastProgress(podcastId, body, { keepalive: true })).credited_seconds, 15);
  assert.deepEqual(sent[0], [`/v1/podcasts/${podcastId}/progress`, "POST", true, body]);
  assert.equal((await client.getPodcastProgress(podcastId)).current.episode_index, 1);
  await assert.rejects(new StudydyApiClient(async () => Response.json({ ...progress, podcast_id: setId })).getPodcastProgress(podcastId), ApiClientError);
  await assert.rejects(new StudydyApiClient(async () => Response.json({ ...progress, credited_seconds: -1 })).getPodcastProgress(podcastId), ApiClientError);
});

function versionMovePreview(change = () => undefined) {
  const value = {
    schema: "version-move-preview/v1", material_id: materialId, to_revision: structureRevision,
    preview_digest: `version-move:sha256:${"f".repeat(64)}`, kept_podcasts: 1,
    card_sets: [{
      card_set_id: setId, name: "複習", from_revision: `knowledge-structure:sha256:${"9".repeat(64)}`,
      concept_count: 2, target_count: 1, carried_reviews: 1, reused_designs: 0, movable: true,
      mapped: [{ from_concept_id: conceptId, from_label: "Stack", to_concept_id: conceptId, to_label: "堆疊", basis: "evidence" }],
      unmapped: [{ concept_id: `concept:sha256:${"8".repeat(64)}`, label: "Queue", reason: "not_found" }],
    }],
    study: {
      study_session_id: sessionId, from_revision: `knowledge-structure:sha256:${"9".repeat(64)}`,
      target_study_session_id: null, completed_count: 1,
      carried: [{ concept_id: conceptId, label: "堆疊", from_labels: ["Stack"] }], not_carried: [],
      current_concept: { concept_id: conceptId, label: "堆疊" },
    },
  };
  change(value);
  return value;
}

test("version move preview keeps the mapping, reasons and digest shape", async () => {
  const read = (value) => new StudydyApiClient(async () => Response.json(value)).getVersionMove(materialId);
  assert.equal((await read(versionMovePreview())).card_sets[0].unmapped[0].reason, "not_found");
  assert.equal((await read(versionMovePreview((value) => { value.study = null; value.card_sets = []; value.preview_digest = null; }))).study, null);
  for (const change of [
    (value) => { value.material_id = runId; },
    (value) => { value.preview_digest = "sha256:x"; },
    (value) => { value.card_sets[0].unmapped[0].reason = "guess"; },
    (value) => { value.card_sets[0].mapped[0].basis = "similar"; },
    (value) => { value.card_sets[0].carried_reviews = -1; },
    (value) => { value.study.carried[0].from_labels = [1]; },
    (value) => { value.study.current_concept = { label: "x" }; },
  ]) await assert.rejects(read(versionMovePreview(change)), ApiClientError);
});

test("moving to a new version sends the confirmation key and checks the target", async () => {
  const sent = [];
  const result = { schema: "version-move/v1", move_id: runId, material_id: materialId, to_revision: structureRevision,
    card_sets: [{ from_card_set_id: setId, card_set_id: sessionId, status: "pending" }], study_session_id: sessionId,
    created_at: "2026-10-22T01:00:00Z" };
  const reply = (body) => async (path, init) => { sent.push([path, init.headers["Idempotency-Key"], JSON.parse(init.body)]); return Response.json(body); };
  const body = { schema: "version-move-create/v1", to_revision: structureRevision, card_set_ids: [setId], include_study: true,
    preview_digest: `version-move:sha256:${"f".repeat(64)}` };
  assert.equal((await new StudydyApiClient(reply(result)).moveToNewVersion(materialId, body, "move-1")).study_session_id, sessionId);
  assert.deepEqual(sent[0], [`/v1/materials/${materialId}/version-move`, "move-1", body]);
  await assert.rejects(new StudydyApiClient(reply({ ...result, to_revision: `knowledge-structure:sha256:${"1".repeat(64)}` })).moveToNewVersion(materialId, body, "k"), ApiClientError);
  await assert.rejects(new StudydyApiClient(reply({ ...result, card_sets: [{ ...result.card_sets[0], status: "failed" }] })).moveToNewVersion(materialId, body, "k"), ApiClientError);
});

test("export downloads reject HTML responses and cannot finish after account invalidation", async () => {
  const html = new StudydyApiClient(async () => new Response("<html>login</html>", { headers: { "Content-Type": "text/html" } }));
  await assert.rejects(html.downloadExport("/v1/export"), error => error.kind === "schema");
  let finish;
  let entered;
  const started = new Promise(resolve => { entered = resolve; });
  const client = new StudydyApiClient(async () => {
    const response = new Response("pdf", { headers: { "Content-Type": "application/pdf", "Content-Disposition": "attachment; filename*=UTF-8''report.pdf" } });
    response.blob = () => { entered(); return new Promise(resolve => { finish = resolve; }); };
    return response;
  });
  const pending = client.downloadExport("/v1/export");
  await started;
  client.invalidate();
  await assert.rejects(pending, error => error.reasonCode === "SESSION_REQUIRED");
  finish(new Blob(["pdf"]));
});


test("card export limits explain size, active jobs, page limits and busy downloads", async () => {
  for (const [reason, status, message] of [
    ["EXPORT_SIZE_LIMIT", 413, "512 MiB"],
    ["EXPORT_ACTIVE_LIMIT", 409, "2 份"],
    ["EXPORT_IN_PROGRESS", 409, "仍在處理或下載"],
    ["EXPORT_PAGE_LIMIT", 413, "每個概念最多 16 張"],
  ]) {
    const client = new StudydyApiClient(async () => Response.json({ schema: "api-error/v1", request_id: sessionId,
      reason_code: reason, retryable: false, message: "Request could not be completed." }, { status }));
    await assert.rejects(client.studyTools(`/v1/card-sets/${setId}/exports`, {}),
      error => error.reasonCode === reason && error.status === status && error.message.includes(message));
  }
});

test("account storage quota errors show used and limit, storage view must be consistent", async () => {
  const gib = 1024 ** 3;
  const full = new StudydyApiClient(async () => Response.json(
    { schema: "api-error/v1", request_id: materialId, reason_code: "STORAGE_QUOTA_EXCEEDED", retryable: false,
      message: "Request could not be completed.", storage: { used_bytes: 4.5 * gib, limit_bytes: 5 * gib } },
    { status: 409 }));
  await assert.rejects(full.getAccountStorage(), (error) => error.reasonCode === "STORAGE_QUOTA_EXCEEDED"
    && error.message.includes("已用 4.5 GB") && error.message.includes("上限 5 GB"));
  const busy = new StudydyApiClient(async () => Response.json(
    { schema: "api-error/v1", request_id: materialId, reason_code: "STORAGE_BUSY", retryable: true, message: "Request could not be completed." },
    { status: 503 }));
  await assert.rejects(busy.getAccountStorage(), (error) => error.reasonCode === "STORAGE_BUSY" && error.retryable
    && error.message === "同一帳號的其他新增正在處理，請稍後再試。");
  const storage = { schema: "account-storage/v2", used_bytes: gib, reserved_bytes: 0, limit_bytes: 5 * gib, remaining_bytes: 4 * gib, breakdown: { material_files_bytes: gib, podcast_audio_bytes: 0, podcast_video_bytes: 0, analysis_bytes: 0, reserved_bytes: 0 }, largest_materials: [] };
  assert.deepEqual(await new StudydyApiClient(async () => Response.json(storage)).getAccountStorage(), storage);
  for (const body of [{ ...storage, remaining_bytes: 5 * gib }, { ...storage, used_bytes: -1 }, { ...storage, schema: "account-storage/v1" },
    { ...storage, reserved_bytes: 2 * gib }, { ...storage, reserved_bytes: undefined },
    { ...storage, breakdown: { ...storage.breakdown, podcast_audio_bytes: 1 } },
    { ...storage, breakdown: { ...storage.breakdown, reserved_bytes: 1, material_files_bytes: gib - 1 } },
    { ...storage, largest_materials: [{ material_id: "invalid", display_name: "教材", used_bytes: 1 }] },
    { ...storage, largest_materials: Array.from({ length: 6 }, () => ({ material_id: materialId, display_name: "教材", used_bytes: 1 })) }]) {
    await assert.rejects(new StudydyApiClient(async () => Response.json(body)).getAccountStorage(), ApiClientError);
  }
});

test("podcast summaries must say whether video was requested", async () => {
  const { video_enabled: _, ...missing } = podcastView();
  await assert.rejects(new StudydyApiClient(async () => Response.json(missing)).getPodcast(setId), ApiClientError);
  assert.equal((await new StudydyApiClient(async () => Response.json(podcastView())).getPodcast(setId)).video_enabled, false);
  const counted = { ...podcastView(), generation: { active: false, completed_videos: 0, failed_videos: 0, expected_videos: 0 } };
  assert.equal((await new StudydyApiClient(async () => Response.json(counted)).getPodcast(setId)).generation.expected_videos, 0);
  for (const expected_videos of [-1, 2, 0.5]) {
    const broken = { ...counted, generation: { ...counted.generation, expected_videos } };
    await assert.rejects(new StudydyApiClient(async () => Response.json(broken)).getPodcast(setId), ApiClientError);
  }
});


test("v2 learning goals accept pending draft choices and preserve the update request", async () => {
  const pending = {target_date:"2026-11-01",target_percent:80};
  const value = {schema:"learning-goal/v2",material_id:materialId,knowledge_structure_revision:null,run_id:null,today:"2026-10-27",goal:null,pending_goal:pending};
  const client=new StudydyApiClient(async(url,init)=>{
    assert.equal(url,`/v1/materials/${materialId}/goal`);
    assert.deepEqual(JSON.parse(init.body),{schema:"learning-goal-update/v1",...pending});
    return Response.json(value);
  });
  assert.deepEqual((await client.setLearningGoal(materialId,pending.target_date,80)).pending_goal,pending);
  for(const invalid of [{...value,schema:"learning-goal/v1"},{...value,pending_goal:{...pending,target_percent:70}}])
    await assert.rejects(new StudydyApiClient(async()=>Response.json(invalid)).getLearningGoal(materialId),error=>error.kind==='schema');
});

test("draft goal deletion uses an empty request and validates the cleared result", async () => {
  const value = {schema:"learning-goal/v2",material_id:materialId,knowledge_structure_revision:null,run_id:null,today:"2026-10-27",goal:null,pending_goal:null};
  const client = new StudydyApiClient(async (url, init) => {
    assert.equal(url, `/v1/materials/${materialId}/goal`);
    assert.equal(init.method, "DELETE"); assert.equal(init.body, undefined); assert.ok(new Headers(init.headers).get("Origin"));
    return Response.json(value);
  });
  assert.deepEqual(await client.clearDraftLearningGoal(materialId), value);
  await assert.rejects(new StudydyApiClient(async () => Response.json({...value, pending_goal:{target_date:"2026-11-01",target_percent:80}})).clearDraftLearningGoal(materialId), error => error.kind === "schema");
});

test("v2 diagnosis requests material scope and validates the supplied completion counters", async () => {
  const value={schema:"learning-diagnosis/v2",material_id:materialId,knowledge_structure_revision:structureRevision,scope:"material",source_resolver:`/v1/materials/${materialId}/knowledge-structures/${structureRevision}/evidence`,strengths:[],weaknesses:[{concept_id:conceptId,label:"Stack",points:[{claim_id:claimId,title:"完整重點",answer_count:3,correct_count:1,latest_is_correct:false,consecutive_correct_count:0,required_consecutive_correct:2,completed:false,evidence:[]}]}],recommendations:[]};
  const request={materialId,structureRevision};
  const client=new StudydyApiClient(async(url)=>{assert.equal(new URL(url,'http://localhost').search,'');return Response.json(value);});
  assert.equal((await client.getLearningDiagnosis(request)).weaknesses[0].points[0].required_consecutive_correct,2);
  for(const invalid of [{...value,schema:"learning-diagnosis/v1"},{...value,scope:"set"}])
    await assert.rejects(new StudydyApiClient(async()=>Response.json(invalid)).getLearningDiagnosis(request),error=>error.kind==='schema');
});

test("v3 note annotations write the visible content revision and generation then reload the requested revision", async () => {
  const contentRevision=`knowledge-structure:sha256:${'f'.repeat(64)}`;
  const value={schema:"material-notes/v3",material_id:materialId,run_id:runId,knowledge_structure_revision:structureRevision,kind:"summary",status:"running",generation:4,source_status:structureView().status,paragraphs:[],outline:[],annotations:[],source_resolver:`/v1/materials/${materialId}/knowledge-structures/${contentRevision}/evidence`,error_code:null,progress:null,content_revision:contentRevision,content_generation:2,update_available:true,updating:true,started_at:"2026-10-27T00:00:00Z",updated_at:null};
  const calls=[];
  const client=new StudydyApiClient(async(url,init)=>{
    calls.push({url,method:init.method});
    if(init.method==='PUT'){
      assert.equal(url,`/v1/materials/${materialId}/knowledge-structures/${contentRevision}/notes/summary/annotations/${setId}`);
      assert.deepEqual(JSON.parse(init.body),{expected_generation:2,paragraph_id:'paragraph-1',body:'我的註記'});
      return Response.json({...value,knowledge_structure_revision:contentRevision,generation:2,update_available:false});
    }
    assert.equal(url,`/v1/materials/${materialId}/knowledge-structures/${structureRevision}/notes/summary`);
    return Response.json(value);
  });
  assert.equal((await client.saveNoteAnnotation(value,setId,'paragraph-1','我的註記')).knowledge_structure_revision,structureRevision);
  assert.deepEqual(calls.map(call=>call.method),['PUT','GET']);
  for(const invalid of [{...value,schema:'material-notes/v2'},{...value,source_resolver:`/v1/materials/${materialId}/knowledge-structures/${structureRevision}/evidence`}])
    await assert.rejects(new StudydyApiClient(async()=>Response.json(invalid)).getNotes(materialId,structureRevision,'summary'),error=>error.kind==='schema');
});


test("question entry points and assessment presence share a persistent browser id while cancel stays available", async () => {
  browserStorage.clear(); const requests=[];
  const client=new StudydyApiClient(async(url,init)=>{
    requests.push({url,method:init.method,id:new Headers(init.headers).get('X-Studydy-Browser-Id'),body:init.body});
    if(url.endsWith('/recordings'))return Response.json({turn_id:runId},{status:202});
    if(url.endsWith('/assessment-presence'))return Response.json({schema:'assessment-presence/v1',active:JSON.parse(init.body).active,expires_at:'2026-10-27T08:00:00Z'});
    return Response.json({ok:true});
  });
  await client.studyTools(`/v1/voice-conversations/${sessionId}/turns`,{question:'問題'});
  await client.voiceRecording(sessionId,new Blob(['audio'],{type:'audio/webm'}),'record');
  await client.studyTools(`/v1/voice-conversations/${sessionId}/turns/${runId}/actions`,{action:'retry'});
  await client.studyTools(`/v1/voice-conversations/${sessionId}/turns/${runId}/actions`,{action:'cancel'});
  await client.setAssessmentPresence(materialId,setId,true);
  await client.studyTools('/v1/topics',{});
  assert.match(requests[0].id,/^[0-9a-f-]{36}$/);
  for(const index of [1,2,4])assert.equal(requests[index].id,requests[0].id);
  for(const index of [3,5])assert.equal(requests[index].id,null);
  assert.deepEqual(JSON.parse(requests[4].body),{schema:'assessment-presence/v1',assessment_set_id:setId,active:true});
  const next=new StudydyApiClient(async(url,init)=>{assert.equal(new Headers(init.headers).get('X-Studydy-Browser-Id'),requests[0].id);return Response.json({ok:true});});
  await next.studyTools(`/v1/voice-conversations/${sessionId}/turns`,{question:'同一瀏覽器'});
});

test("assessment presence validates the response and guarded questions explain the 409 refusal",async()=>{
  for(const response of [{schema:'assessment-presence/v1',active:false,expires_at:null},{schema:'assessment-presence/v1',active:true,expires_at:'invalid'}])
    await assert.rejects(new StudydyApiClient(async()=>Response.json(response)).setAssessmentPresence(materialId,setId,true),error=>error.kind==='schema');
  const client=new StudydyApiClient(async()=>Response.json({schema:'api-error/v1',request_id:materialId,reason_code:'ASSESSMENT_IN_PROGRESS',retryable:false,message:'Request could not be completed.'},{status:409}));
  await assert.rejects(client.studyTools(`/v1/voice-conversations/${sessionId}/turns`,{question:'問題'}),error=>error.status===409&&error.reasonCode==='ASSESSMENT_IN_PROGRESS'&&error.message.includes('交卷後再試'));
});


test("podcast steps and omissions accept observed progress and reject malformed API values", async () => {
  const value=podcastView();
  const step={episode_index:0,step:'audio',status:'queued',started_at:null,elapsed_seconds:null,queue_ahead:null};
  value.generation={active:true,completed_videos:0,failed_videos:0,steps:[step]};
  value.episodes[0].omitted_content=[{segment_index:2,claim_ids:[claimId],reason:'source_check_failed'}];
  const client=new StudydyApiClient(async()=>Response.json(value));
  assert.deepEqual((await client.getPodcast(setId)).generation.steps,[step]);
  assert.deepEqual((await client.getPodcast(setId)).episodes[0].omitted_content,value.episodes[0].omitted_content);
  for(const patch of [{episode_index:1},{status:'complete'},{step:'unknown'},{elapsed_seconds:-1},{queue_ahead:1.5},{started_at:'yesterday'}]){
    value.generation.steps=[{...step,...patch}];
    await assert.rejects(client.getPodcast(setId),error=>error.kind==='schema');
  }
  value.generation.steps=[step];value.episodes[0].omitted_content=[{segment_index:0,claim_ids:[claimId],reason:'unverified'}];
  await assert.rejects(client.getPodcast(setId),error=>error.kind==='schema');
});


test("source display names are accepted at the API boundary and malformed names are rejected", async () => {
  const source={source_id:setId,normalization_id:runId,original_artifact_id:sessionId,normalized_artifact_id:materialId,original_name:'課程.youtube',display_name:'課程',media_type:'application/vnd.studydy.youtube+json',status:'ready',page_count:7,error_code:null};
  const client=new StudydyApiClient(async()=>Response.json({schema:'material-sources/v1',material_id:materialId,sources:[source]}));
  assert.equal((await client.getSources(materialId)).sources[0].display_name,'課程');
  source.display_name=42;
  await assert.rejects(client.getSources(materialId),error=>error.kind==='schema');
});


test("source metadata, library totals and run input summaries validate at the API boundary", async () => {
  const source={source_id:setId,normalization_id:runId,original_artifact_id:sessionId,normalized_artifact_id:materialId,original_name:'課程.youtube',display_name:'課程',media_type:'application/vnd.studydy.youtube+json',status:'ready',page_count:7,error_code:null,source_kind:'youtube',duration_seconds:833,image_count:null,transcript_display_name:'YouTube 上傳者字幕'};
  let value={schema:'material-sources/v1',material_id:materialId,sources:[source]};
  const client=new StudydyApiClient(async()=>Response.json(value));
  assert.deepEqual((await client.getSources(materialId)).sources[0],source);
  for(const patch of [{source_kind:'unknown'},{duration_seconds:-1},{image_count:1.5},{transcript_display_name:42}]){
    value.sources=[{...source,...patch}];
    await assert.rejects(client.getSources(materialId),error=>error.kind==='schema');
  }
  const item={...libraryItem(),source_totals:{page_count:12,image_count:3,media_seconds:null}};
  value=item;
  assert.deepEqual((await client.getMaterial(materialId)).source_totals,item.source_totals);
  value={...item,source_totals:{...item.source_totals,media_seconds:-1}};
  await assert.rejects(client.getMaterial(materialId),error=>error.kind==='schema');
  const run={...runView(),added_sources:[{source_id:setId,display_name:'新增.pdf'}],existing_source_count:3};
  value=run;
  assert.deepEqual((await client.getMaterialRun(runId)).added_sources,run.added_sources);
  for(const patch of [{added_sources:[{source_id:'invalid',display_name:'新增.pdf'}]},{existing_source_count:-1}]){
    value={...run,...patch};await assert.rejects(client.getMaterialRun(runId),error=>error.kind==='schema');
  }
});


test("run structure changes and concept uncertainty use validated server values", async () => {
  let value={...runView(),structure_changes:{before:{concepts:34,relations:24},after:{concepts:38,relations:29}}};
  const client=new StudydyApiClient(async()=>Response.json(value));
  assert.deepEqual((await client.getMaterialRun(runId)).structure_changes,value.structure_changes);
  for(const changes of [{before:{concepts:-1,relations:24},after:{concepts:38,relations:29}},{before:{concepts:34},after:{concepts:38,relations:29}}]){
    value={...runView(),structure_changes:changes};await assert.rejects(client.getMaterialRun(runId),e=>e.kind==='schema');
  }
  value=structureView();value.concepts[0].uncertain=true;
  assert.equal((await new StudydyApiClient(async()=>Response.json(value)).getKnowledgeStructure({materialId,structureRevision})).concepts[0].uncertain,true);
  value.concepts[0].uncertain=false;
  assert.equal((await new StudydyApiClient(async()=>Response.json(value)).getKnowledgeStructure({materialId,structureRevision})).concepts[0].uncertain,false);
  value.concepts[0].uncertain='true';
  await assert.rejects(new StudydyApiClient(async()=>Response.json(value)).getKnowledgeStructure({materialId,structureRevision}),e=>e.kind==='schema');
});
