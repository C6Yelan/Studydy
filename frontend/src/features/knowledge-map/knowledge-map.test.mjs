import assert from "node:assert/strict";
import test from "node:test";

import { documentTreeConnectors, hierarchyLayout, relationConnectors } from "./knowledge-map.ts";

const view = {
  document_tree: {
    material_id: "material",
    sections: [{ section_id: "section", concept_ids: ["concept-a", "concept-b"] }],
  },
  concepts: [{ concept_id: "concept-a" }, { concept_id: "concept-b" }],
  relations: [{ relation_id: "relation", source_concept_id: "concept-a", target_concept_id: "concept-b", type: "prerequisite", learner_reason: "A is required before B." }],
};

test("tree owns layout while typed relations remain a separate overlay", () => {
  assert.equal(documentTreeConnectors(view).length, 3);
  assert.deepEqual(relationConnectors(view), [{ id: "relation", source: "concept-a", target: "concept-b", type: "prerequisite", reason: "A is required before B." }]);
  assert.equal(hierarchyLayout(view).length, 4);
});
