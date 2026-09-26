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
  }
});

test("task routes are canonical", () => {
  for (const route of [
    { name: "home" },
    { name: "materials" },
    { name: "upload" },
    { name: "material-run", materialId, runId },
  ]) {
    assert.deepEqual(readRoute(routePath(route)), { route, isCanonical: true });
  }
});

test("unknown paths, malformed IDs and extra segments are not canonical routes", () => {
  const saved = routePath({
    name: "study-session",
    materialId,
    runId,
    structureRevision,
    studySessionId,
  });
  for (const path of [
    "/unknown",
    "/materials/not-an-id",
    `/materials/${materialId}`,
    `${saved}/extra`,
  ]) {
    assert.deepEqual(readRoute(path), { route: { name: "home" }, isCanonical: false });
  }
});
