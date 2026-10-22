import assert from "node:assert/strict";
import test from "node:test";
import { learningNavigationItems, initialFocusConceptId, focusGraph, navigatorRange, groupSlices, NAVIGATOR_WINDOW } from "./knowledge-map.ts";

const view = {
  concepts: ["a", "b", "c", "d"].map((concept_id) => ({ concept_id })),
  relations: [
    {
      relation_id: "ab",
      source_concept_id: "a",
      target_concept_id: "b",
      type: "prerequisite",
      learner_reason: "A before B",
    },
    {
      relation_id: "bc",
      source_concept_id: "b",
      target_concept_id: "c",
      type: "example",
      learner_reason: "C illustrates B",
    },
    {
      relation_id: "cd",
      source_concept_id: "c",
      target_concept_id: "d",
      type: "part_of",
      learner_reason: "C is part of D",
    },
  ],
};

test("first visit starts with a connected concept without changing the learning path", () => {
  const map = {
    ...view,
    concepts: [{ concept_id: "cover" }, ...view.concepts],
    initial_learning_path: ["cover", "a", "b", "c", "d"].map((concept_id, index) => ({
      concept_id,
      position: index + 1,
      reason: "document_order",
    })),
  };
  assert.equal(initialFocusConceptId(map), "b");
  assert.equal(map.initial_learning_path[0].concept_id, "cover");
  assert.equal(initialFocusConceptId({ ...map, relations: [] }), "cover");
});

const navigation = {
  document_tree: {
    sections: [
      { section_id: "a", title: "Section A", order: 0 },
      { section_id: "b", title: "Section B", order: 1 },
    ],
  },
  concepts: [{ concept_id: "a1" }, { concept_id: "b1" }, { concept_id: "a2" }],
  initial_learning_path: [
    { position: 3, concept_id: "a2", reason: "document_order" },
    { position: 1, concept_id: "a1", reason: "document_order" },
    { position: 2, concept_id: "b1", reason: "prerequisite" },
  ],
};

test("flat navigation preserves path positions and canonical reasons regardless of sections", () => {
  const original = structuredClone(navigation);
  const items = learningNavigationItems(navigation);
  assert.deepEqual(
    items.map((item) => [item.concept.concept_id, item.step.position]),
    [
      ["a1", 1],
      ["b1", 2],
      ["a2", 3],
    ],
  );
  assert.equal(items[1].step.reason, "prerequisite");
  assert.equal(items[1].step, navigation.initial_learning_path[2]);
  assert.deepEqual(navigation, original);
});

test("a path can order C before A and B independently of document order", () => {
  const map = {
    ...navigation,
    initial_learning_path: [
      { position: 1, concept_id: "a2", reason: "document_order" },
      { position: 2, concept_id: "a1", reason: "document_order" },
      { position: 3, concept_id: "b1", reason: "prerequisite" },
    ],
  };
  assert.deepEqual(
    learningNavigationItems(map).map((item) => item.concept.concept_id),
    ["a2", "a1", "b1"],
  );
});

test("two-hop graph keeps canonical directions, parallel edges and cycles", () => {
  const map = {
    ...view,
    concepts: [...view.concepts, { concept_id: "isolated" }],
    relations: [
      ...view.relations,
      { relation_id: "ba", source_concept_id: "b", target_concept_id: "a", type: "contrast" },
      { relation_id: "ab2", source_concept_id: "a", target_concept_id: "b", type: "example" },
      { relation_id: "ac", source_concept_id: "a", target_concept_id: "c", type: "application" },
    ],
  };
  const before = structuredClone(map);
  const local = focusGraph(map, "b");
  assert.deepEqual(new Set(local.nodes.map((node) => node.id)), new Set(["a", "b", "c", "d"]));
  assert.deepEqual(
    local.relations.map((edge) => edge.relation_id),
    ["ab", "bc", "cd", "ba", "ab2", "ac"],
  );
  assert.equal(local.nodes.filter((node) => node.id === "a").length, 1);
  for (const edge of local.relations)
    assert.ok(
      local.nodes.some((node) => node.id === edge.source_concept_id) &&
        local.nodes.some((node) => node.id === edge.target_concept_id),
    );
  assert.deepEqual(
    focusGraph(map, "isolated").nodes.map((node) => node.id),
    ["isolated"],
  );
  assert.deepEqual(map, before);
});

test("large canonical maps do not become large rendered graphs", () => {
  const map = {
    concepts: Array.from({ length: 1000 }, (_, i) => ({ concept_id: String(i) })),
    relations: Array.from({ length: 999 }, (_, i) => ({
      relation_id: String(i),
      source_concept_id: String(i),
      target_concept_id: String(i + 1),
    })),
  };
  assert.deepEqual(
    new Set(focusGraph(map, "500").nodes.map((node) => node.id)),
    new Set(["498", "499", "500", "501", "502"]),
  );
  assert.equal(focusGraph(map, "500").nodes.find((node) => node.id === "498").depth, 2);
  assert.equal(focusGraph(map, "500").relations.length, 4);
  assert.deepEqual(focusGraph({ concepts: [], relations: [] }, ""), {
    nodes: [],
    relations: [],
    totalNodes: 0,
    totalRelations: 0,
  });
});

test("two-hop limits preserve a path to each rendered node and disclose omitted content", () => {
  const map = {
    concepts: Array.from({ length: 80 }, (_, i) => ({ concept_id: String(i) })),
    relations: [],
  };
  const connect = (a, b) =>
    map.relations.push({
      relation_id: String(map.relations.length),
      source_concept_id: String(a),
      target_concept_id: String(b),
    });
  for (let i = 1; i <= 6; i++) connect(0, i);
  for (let i = 7; i < 80; i++) connect(1 + ((i - 7) % 6), i);
  for (let i = 1; i < 30; i++) for (let j = i + 1; j < 30; j++) connect(i, j);
  const before = structuredClone(map);
  const projection = focusGraph(map, "0");
  assert.equal(projection.nodes.length, 30);
  assert.equal(projection.relations.length, 60);
  assert.equal(projection.totalNodes, 80);
  assert.equal(projection.totalRelations, map.relations.length);
  assert.equal(projection.nodes.filter((node) => node.depth === 1).length, 6);
  assert.equal(projection.nodes.filter((node) => node.depth === 2).length, 23);
  const reached = new Set(["0"]);
  for (let step = 0; step < 2; step++)
    for (const edge of projection.relations) {
      if (reached.has(edge.source_concept_id)) reached.add(edge.target_concept_id);
      if (reached.has(edge.target_concept_id)) reached.add(edge.source_concept_id);
    }
  assert.deepEqual(reached, new Set(projection.nodes.map((node) => node.id)));
  assert.ok(projection.relations.every((edge) => map.relations.includes(edge)));
  const next = focusGraph(map, "79");
  assert.ok(next.nodes.length <= 30 && next.relations.length <= 60);
  assert.equal(next.nodes[0].id, "79");
  assert.notDeepEqual(
    next.nodes.map((node) => node.id),
    projection.nodes.map((node) => node.id),
  );
  assert.deepEqual(map, before);
});

test("direct neighbours also respect the node limit", () => {
  const map = {
    concepts: Array.from({ length: 100 }, (_, i) => ({ concept_id: String(i) })),
    relations: Array.from({ length: 99 }, (_, i) => ({
      relation_id: String(i),
      source_concept_id: "0",
      target_concept_id: String(i + 1),
    })),
  };
  const graph = focusGraph(map, "0");
  assert.equal(graph.nodes.length, 30);
  assert.equal(graph.totalNodes, 100);
  assert.equal(graph.relations.length, 29);
  assert.equal(graph.totalRelations, 99);
});

test("map regions are used only when they cover exactly the outline concepts", async () => {
  const { mapRegions } = await import("./knowledge-map.ts");
  const outline = { concepts: ["a", "b", "c"].map((concept_id) => ({ concept_id })) };
  const regions = (groups) => ({
    schema: "knowledge-regions/v1", material_id: "m", knowledge_structure_revision: "r", basis: "source",
    regions: groups.map((concept_ids, index) => ({ region_id: `region:${index + 1}`, label: `R${index + 1}`, source_id: null, concept_ids })),
  });
  const value = mapRegions(outline, regions([["a", "c"], ["b"]]));
  assert.deepEqual([...value.byConcept], [["a", 0], ["c", 0], ["b", 1]]);
  assert.deepEqual(value.regions.map((r) => r.label), ["R1", "R2"]);
  assert.equal(mapRegions(outline, regions([["a"], ["b"]])), null);
  assert.equal(mapRegions(outline, regions([["a", "b"], ["c", "x"]])), null);
  assert.equal(mapRegions(outline, null), null);
});

test("large learning navigators render a window around the selected concept", () => {
  // 一般教材維持完整清單。
  assert.deepEqual(navigatorRange(null, 5, 200), { start: 0, end: 200 });
  const total = 4902;
  const first = navigatorRange(null, 2000, total);
  assert.deepEqual(first, { start: 2000 - NAVIGATOR_WINDOW / 2, end: 2000 + NAVIGATOR_WINDOW / 2 });
  // 範圍仍包含選取概念時沿用（包含使用者展開過的範圍）。
  const expanded = { start: first.start, end: first.end + NAVIGATOR_WINDOW };
  assert.equal(navigatorRange(expanded, first.end + 10, total), expanded);
  // 移出範圍時重新置中；靠近兩端時不超出清單。
  assert.deepEqual(navigatorRange(first, 3, total), { start: 0, end: NAVIGATOR_WINDOW });
  assert.deepEqual(navigatorRange(first, total - 1, total), { start: total - NAVIGATOR_WINDOW, end: total });
});

test("grouped navigator windows keep region order and skip regions outside the range", () => {
  const groups = [{ id: "a", items: [1, 2, 3] }, { id: "b", items: [4, 5] }, { id: "c", items: [6, 7, 8] }];
  assert.deepEqual(groupSlices(groups, { start: 2, end: 6 }).map(({ group, slice }) => [group.id, slice]),
    [["a", [3]], ["b", [4, 5]], ["c", [6]]]);
  assert.deepEqual(groupSlices(groups, { start: 3, end: 5 }).map(({ group }) => group.id), ["b"]);
});

test("chapter regions use two levels only for many chapters across several source files", async () => {
  const { mapRegions, regionColorIndex, regionBasisText, TWO_LEVEL_MIN_REGIONS } = await import("./knowledge-map.ts");
  const chapters = (count) => {
    const ids = Array.from({ length: count * 3 }, (_, i) => `c${i}`);
    return {
      outline: { concepts: ids.map((concept_id) => ({ concept_id })) },
      value: {
        schema: "knowledge-regions/v1", material_id: "m", knowledge_structure_revision: "r", basis: "chapters",
        // API 依島群排列；這裡故意交錯，前端仍要讓同群相鄰。
        regions: Array.from({ length: count }, (_, i) => ({
          region_id: `chapter:${i + 1}`, label: `章${i + 1}`, source_id: null,
          concept_ids: ids.slice(i * 3, i * 3 + 3), group_id: i % 2 ? "region:2" : "region:1",
        })),
        groups: [
          { group_id: "region:1", label: "01_生成", source_id: null },
          { group_id: "region:2", label: "02_推論", source_id: null },
          { group_id: "region:3", label: "沒有章節的檔", source_id: null },
        ],
        partial: true,
      },
    };
  };
  const big = chapters(TWO_LEVEL_MIN_REGIONS + 2);
  const two = mapRegions(big.outline, big.value);
  assert.deepEqual(two.groups.map((g) => [g.label, g.regionIndexes.length, g.conceptCount]), [["01_生成", 13, 39], ["02_推論", 13, 39]]);
  assert.deepEqual(two.regions.slice(12, 15).map((r) => [r.label, r.group]), [["章25", 0], ["章2", 1], ["章4", 1]]);
  assert.equal(two.byConcept.get("c3"), 13);
  assert.equal(regionColorIndex(two, 20), 1);
  assert.equal(regionBasisText(two), "依內容整理的章節，來源檔為島群；部分依來源檔分區");
  // 三份 demo 的章數（9／15／14）維持一層，區域順序照 API。
  const small = chapters(15);
  const one = mapRegions(small.outline, { ...small.value, partial: false });
  assert.deepEqual(one.groups, []);
  assert.deepEqual(one.regions.slice(0, 3).map((r) => [r.label, r.group]), [["章1", null], ["章2", null], ["章3", null]]);
  assert.equal(regionColorIndex(one, 2), 2);
  assert.equal(regionBasisText(one), "依教材內容整理的章節");
});
