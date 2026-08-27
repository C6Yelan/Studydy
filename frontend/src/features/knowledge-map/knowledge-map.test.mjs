import assert from "node:assert/strict";
import test from "node:test";

import {
  focusNeighborhood,
  learningPathReason,
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

test("focus neighborhood keeps typed published edges around the selected node", () => {
  const view = {
    concepts: [
      { formal_concept_id: "a", label: "A", source_page_numbers: [1] },
      { formal_concept_id: "b", label: "B", source_page_numbers: [2] },
      { formal_concept_id: "c", label: "C", source_page_numbers: [3] },
    ],
    relations: [
      { relation_id: "ab", type: "prerequisite", source_formal_concept_id: "a", target_formal_concept_id: "b", is_in_prerequisite_cycle: false },
      { relation_id: "bc", type: "related", source_formal_concept_id: "b", target_formal_concept_id: "c", is_in_prerequisite_cycle: false },
    ],
  };

  const neighborhood = focusNeighborhood(view, "b");

  assert.deepEqual(neighborhood.nodes.map((node) => node.conceptId), ["b", "a", "c"]);
  assert.deepEqual(neighborhood.relations.map((relation) => relation.relation_id), ["ab", "bc"]);
  assert.deepEqual(neighborhood.nodes[0], { conceptId: "b", x: 50, y: 50 });
});

test("learning path reason uses prerequisite or names the pedagogical fallback", () => {
  const view = {
    concepts: [
      { formal_concept_id: "a", label: "基礎", source_page_numbers: [1] },
      { formal_concept_id: "b", label: "進階", source_page_numbers: [7] },
      { formal_concept_id: "c", label: "補充", source_page_numbers: [9] },
    ],
    relations: [
      { type: "prerequisite", source_formal_concept_id: "a", target_formal_concept_id: "b", is_in_prerequisite_cycle: false },
    ],
  };

  assert.equal(learningPathReason(view, "b"), "先理解「基礎」，再進入這個概念。");
  assert.match(learningPathReason(view, "c"), /沒有可用的非循環先備關係.*第 9 頁/);
});
