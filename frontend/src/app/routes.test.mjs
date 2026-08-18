import assert from "node:assert/strict";
import test from "node:test";

import { readRoute, routePath } from "./routes.ts";

const materialId = "9f9619ff-8b86-4e3a-a2f1-2bb9424d5c72";
const runId = "bf9619ff-8b86-4e3a-a2f1-2bb9424d5c74";
const mapRevision = `knowledge-map:sha256:${"d".repeat(64)}`;

test("v2 routes round trip", () => {
  for (const route of [
    { name: "home" },
    { name: "material-run", materialId, runId },
    { name: "knowledge-map", materialId, runId, mapRevision },
  ]) {
    const path = routePath(route);
    assert.deepEqual(readRoute(path), { route, isCanonical: true });
  }
});

test("deferred downstream paths are not active routes", () => {
  for (const path of [
    `/materials/${materialId}/runs/${runId}/assessments/anything`,
    `/materials/${materialId}/learning-states/anything`,
    `/materials/${materialId}/learning-paths/anything`,
  ]) {
    assert.deepEqual(readRoute(path), { route: { name: "home" }, isCanonical: false });
  }
});
