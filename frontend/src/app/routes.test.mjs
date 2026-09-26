import assert from "node:assert/strict";
import test from "node:test";

import { readRoute, routePath } from "./routes.ts";

const materialId = "11111111-1111-4111-8111-111111111111";
const runId = "22222222-2222-4222-8222-222222222222";
const studySessionId = "33333333-3333-4333-8333-333333333333";
const structureRevision = `knowledge-structure:sha256:${"d".repeat(64)}`;
const materialRunPath = `/materials/${materialId}/runs/${runId}`;
const mapPath = `${materialRunPath}/knowledge-structures/${encodeURIComponent(structureRevision)}`;
const studyPath = `${mapPath}/study-sessions/${studySessionId}`;

test("routes use expected paths and round trip", () => {
  for (const [route, path] of [
    [{ name: "home" }, "/"],
    [{ name: "materials" }, "/materials"],
    [{ name: "upload" }, "/upload"],
    [{ name: "material-run", materialId, runId }, materialRunPath],
    [{ name: "knowledge-map", materialId, runId, structureRevision }, mapPath],
    [{ name: "study-session", materialId, runId, structureRevision, studySessionId }, studyPath],
  ]) {
    assert.equal(routePath(route), path);
    assert.deepEqual(readRoute(path), { route, isCanonical: true });
  }
});

test("unknown paths, malformed IDs and extra segments are not canonical routes", () => {
  for (const path of [
    "/unknown",
    `/materials/not-an-id/runs/${runId}`,
    `/materials/${materialId}`,
    `${studyPath}/extra`,
  ]) {
    assert.deepEqual(readRoute(path), { route: { name: "home" }, isCanonical: false });
  }
});
