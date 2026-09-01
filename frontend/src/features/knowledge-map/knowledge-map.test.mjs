import assert from "node:assert/strict";
import test from "node:test";

import {
  documentTreeConnectors,
  hierarchyLayout,
  safeExternalUrl,
} from "./knowledge-map.ts";

const view = {
  concepts: [
    { formal_concept_id: "concept-a" },
    { formal_concept_id: "concept-b" },
  ],
  document_tree: {
    root: { material_ref: "material-root", section_ids: ["section-a"] },
    sections: [{
      section_id: "section-a",
      concept_ids: ["concept-a", "concept-b"],
    }],
  },
};

test("display connectors derive only from the canonical document tree", () => {
  assert.deepEqual(documentTreeConnectors(view), [
    { id: "root:section-a", source: "material-root", target: "section-a" },
    { id: "section:section-a:concept-a", source: "section-a", target: "concept-a" },
    { id: "section:section-a:concept-b", source: "section-a", target: "concept-b" },
  ]);
});

test("Dagre layout is deterministic and includes each tree node once", () => {
  const first = hierarchyLayout(view);
  const second = hierarchyLayout(view);
  assert.deepEqual(first, second);
  assert.deepEqual(first.map((node) => node.id).sort(), [
    "concept-a", "concept-b", "material-root", "section-a",
  ]);
  assert.ok(first.every((node) => Number.isFinite(node.x) && Number.isFinite(node.y)));
});

test("resource links only allow HTTP(S)", () => {
  assert.equal(safeExternalUrl("https://example.test/resource"), "https://example.test/resource");
  assert.equal(safeExternalUrl("http://example.test/resource"), "http://example.test/resource");
  assert.equal(safeExternalUrl("javascript:alert(1)"), null);
  assert.equal(safeExternalUrl("not a url"), null);
});
