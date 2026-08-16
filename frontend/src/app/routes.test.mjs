import assert from "node:assert/strict";
import test from "node:test";

import { readRoute, routePath } from "./routes.ts";

const materialId = "9f9619ff-8b86-4e3a-a2f1-2bb9424d5c72";
const runId = "bf9619ff-8b86-4e3a-a2f1-2bb9424d5c74";
const mapRevision = `knowledge-map:sha256:${"a".repeat(64)}`;
const pathRevision = `initial-learning-path:sha256:${"b".repeat(64)}`;
const assessmentRevision = `assessment:sha256:${"d".repeat(64)}`;

test("refresh 後可由 URL 恢復必要 opaque route IDs", () => {
  const pathname = routePath({
    name: "knowledge-map",
    materialId,
    runId,
    mapRevision,
    pathRevision,
  });
  assert.deepEqual(readRoute({ pathname, search: "", hash: "" }), {
    route: { name: "knowledge-map", materialId, runId, mapRevision, pathRevision },
    isValid: true,
  });
});

test("無效 UUID、額外 query 與教材文字都安全降級", () => {
  const invalidLocations = [
    { pathname: "/materials/not-a-uuid/runs/not-a-uuid", search: "", hash: "" },
    { pathname: `/materials/${materialId}/runs/${runId}`, search: "?token=no", hash: "" },
    { pathname: "/materials/教材全文", search: "", hash: "" },
  ];
  for (const location of invalidLocations) {
    assert.deepEqual(readRoute(location), { route: { name: "home" }, isValid: false });
  }
});

test("state route 只接受 frozen revision identity", () => {
  const stateRevision = `learning-state:sha256:${"c".repeat(64)}`;
  const pathname = routePath({ name: "learning-state", materialId, stateRevision });
  assert.deepEqual(readRoute({ pathname, search: "", hash: "" }), {
    route: { name: "learning-state", materialId, stateRevision },
    isValid: true,
  });
  assert.equal(
    routePath({ name: "learning-state", materialId, stateRevision: "教材內容" }),
    "/",
  );
});

test("Assessment route 只保存 terminal run 與 exact assessment revision", () => {
  const pathname = routePath({
    name: "assessment",
    materialId,
    runId,
    assessmentRevision,
  });
  assert.deepEqual(readRoute({ pathname, search: "", hash: "" }), {
    route: { name: "assessment", materialId, runId, assessmentRevision },
    isValid: true,
  });
  assert.equal(
    routePath({ name: "assessment", materialId, runId, assessmentRevision: "教材內容" }),
    "/",
  );
});
