import assert from "node:assert/strict";
import test from "node:test";

import { readRoute, routePath } from "./routes.ts";

const materialId = "11111111-1111-4111-8111-111111111111";
const runId = "22222222-2222-4222-8222-222222222222";
const studySessionId = "33333333-3333-4333-8333-333333333333";
const structureRevision = `knowledge-structure:sha256:${"d".repeat(64)}`;

test("final knowledge-structure routes round trip", () => {
  for (const route of [
    { name: "knowledge-map", materialId, runId, structureRevision },
    { name: "study-session", materialId, runId, structureRevision, studySessionId },
  ]) {
    const path = routePath(route);
    assert.deepEqual(readRoute(path), { route, isCanonical: true });
    assert.match(path, /knowledge-structures/);
    assert.doesNotMatch(path, /knowledge-maps/);
  }
});

test("retired map revisions do not parse", () => {
  assert.equal(readRoute(`/materials/${materialId}/runs/${runId}/knowledge-maps/knowledge-map:sha256:${"a".repeat(64)}`).route.name, "home");
});

test("library and upload are canonical; removed detail bookmarks are not routes", () => {
  for (const route of [{ name: "home" }, { name: "materials" }, { name: "maps" }, { name: "upload" }]) {
    assert.deepEqual(readRoute(routePath(route)), { route, isCanonical: true });
  }
  assert.equal(readRoute("/materials/not-an-id").isCanonical, false);
  assert.equal(readRoute(`/materials/${materialId}`).isCanonical, false);
});

test("selected saved assessments survive canonical study URLs", () => {
  const route = { name: "study-session", materialId, runId, structureRevision, studySessionId,
    assessmentRevision: `assessment:sha256:${"c".repeat(64)}` };
  assert.deepEqual(readRoute(routePath(route)), { route, isCanonical: true });
  assert.throws(() => routePath({ ...route, assessmentRevision: "not-an-assessment" }));
});
