import assert from "node:assert/strict";
import test from "node:test";

import {
  hierarchyLayout,
  isPrimaryHierarchyRelation,
  relationPresentation,
  safeExternalUrl,
} from "./knowledge-map.ts";

test("production Relation presentation contains only the frozen three types", () => {
  assert.deepEqual(
    ["prerequisite", "contains", "related"].map((type) => ({ type, ...relationPresentation(type) })),
    [
      {
        type: "prerequisite",
        className: "is-prerequisite",
        directional: true,
        label: "先備關係",
        explanation: "來源概念需要先學，再進入目標概念。",
      },
      {
        type: "contains",
        className: "is-contains",
        directional: true,
        label: "組成關係",
        explanation: "來源概念包含目標概念作為內容的一部分。",
      },
      {
        type: "related",
        className: "is-related",
        directional: false,
        label: "互相關聯",
        explanation: "兩個概念在教材中互有關聯，沒有單向學習箭頭。",
      },
    ],
  );
});

test("resource links only allow HTTP(S)", () => {
  assert.equal(safeExternalUrl("https://example.com/resource"), "https://example.com/resource");
  assert.equal(safeExternalUrl("javascript:alert(1)"), null);
  assert.equal(safeExternalUrl("not a URL"), null);
});

test("hierarchy layout uses backend depths instead of deriving radial semantics", () => {
  const view = {
    concepts: [
      { formal_concept_id: "a", label: "A", source_page_numbers: [1] },
      { formal_concept_id: "b", label: "B", source_page_numbers: [2] },
      { formal_concept_id: "c", label: "C", source_page_numbers: [3] },
    ],
    topology: {
      nodes: [
        { formal_concept_id: "a", depth: 0, primary_parent_formal_concept_id: null, flat_group_id: "g1" },
        { formal_concept_id: "b", depth: 1, primary_parent_formal_concept_id: "a", flat_group_id: "g1" },
        { formal_concept_id: "c", depth: 0, primary_parent_formal_concept_id: null, flat_group_id: "g2" },
      ],
    },
    relations: [
      { relation_id: "ab", type: "prerequisite", source_formal_concept_id: "a", target_formal_concept_id: "b", is_in_prerequisite_cycle: false },
      { relation_id: "bc", type: "related", source_formal_concept_id: "b", target_formal_concept_id: "c", is_in_prerequisite_cycle: false },
    ],
  };

  const nodes = hierarchyLayout(view);

  assert.deepEqual(nodes.map((node) => [node.conceptId, node.x, node.y]), [
    ["a", 0, 0],
    ["c", 240, 0],
    ["b", 0, 150],
  ]);
});

test("primary hierarchy relation follows the canonical backend parent", () => {
  const view = {
    topology: { nodes: [
      { formal_concept_id: "a", primary_parent_formal_concept_id: null },
      { formal_concept_id: "b", primary_parent_formal_concept_id: "a" },
    ] },
    relations: [
      { type: "contains", source_formal_concept_id: "a", target_formal_concept_id: "b" },
      { type: "contains", source_formal_concept_id: "c", target_formal_concept_id: "b" },
    ],
  };

  assert.equal(isPrimaryHierarchyRelation(view, view.relations[0]), true);
  assert.equal(isPrimaryHierarchyRelation(view, view.relations[1]), false);
});
