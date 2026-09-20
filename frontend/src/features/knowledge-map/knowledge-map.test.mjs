import assert from "node:assert/strict";
import test from "node:test";
import { learningNavigationItems, initialFocusConceptId, focusGraph } from "./knowledge-map.ts";

const view = {
  concepts: ["a", "b", "c", "d"].map(concept_id => ({ concept_id })),
  relations: [
    { relation_id: "ab", source_concept_id: "a", target_concept_id: "b", type: "prerequisite", learner_reason: "A before B" },
    { relation_id: "bc", source_concept_id: "b", target_concept_id: "c", type: "example", learner_reason: "C illustrates B" },
    { relation_id: "cd", source_concept_id: "c", target_concept_id: "d", type: "part_of", learner_reason: "C is part of D" },
  ],
};

test("first visit starts with a connected concept without changing the learning path", () => {
  const map = { ...view, initial_learning_path: [{ concept_id: "cover" }, { concept_id: "a" }, { concept_id: "b" }] };
  assert.equal(initialFocusConceptId(map), "b");
  assert.equal(map.initial_learning_path[0].concept_id, "cover");
  assert.equal(initialFocusConceptId({ ...map, relations: [] }), "cover");
});

const navigation = {
  document_tree: { sections: [
    { section_id: "a", title: "Section A", order: 0 },
    { section_id: "b", title: "Section B", order: 1 },
  ] },
  concepts: [
    { concept_id: "a1", section_ids: ["a"] },
    { concept_id: "b1", section_ids: ["b"] },
    { concept_id: "a2", section_ids: ["a"] },
  ],
  initial_learning_path: [
    { position: 3, concept_id: "a2", reason: "document_order" },
    { position: 1, concept_id: "a1", reason: "document_order" },
    { position: 2, concept_id: "b1", reason: "prerequisite" },
  ],
};

test("flat navigation preserves path positions and canonical reasons regardless of sections", () => {
  const original = structuredClone(navigation);
  const items = learningNavigationItems(navigation);
  assert.deepEqual(items.map(item => [item.concept.concept_id, item.step.position]), [["a1", 1], ["b1", 2], ["a2", 3]]);
  assert.equal(items[1].step.reason, "prerequisite");
  assert.equal(items[1].step, navigation.initial_learning_path[2]);
  assert.deepEqual(navigation, original);
});

test("a path can order C before A and B independently of document order", () => {
  const map = { ...navigation, initial_learning_path: [
    { position: 1, concept_id: "a2", reason: "document_order" },
    { position: 2, concept_id: "a1", reason: "document_order" },
    { position: 3, concept_id: "b1", reason: "prerequisite" },
  ] };
  assert.deepEqual(learningNavigationItems(map).map(item => item.concept.concept_id), ["a2", "a1", "b1"]);
});

test("defensive concepts outside the path get no invented position; broken references fail", () => {
  const map = { ...navigation, concepts: [...navigation.concepts, { concept_id: "extra", section_ids: [] }] };
  const other = learningNavigationItems(map).at(-1);
  assert.equal(other.concept.concept_id, "extra");
  assert.equal(other.step, null);
  assert.throws(() => learningNavigationItems({ ...navigation, concepts: [] }));
});

test("two-hop graph keeps canonical directions, parallel edges and cycles", () => {
  const map = { ...view, concepts: [...view.concepts, { concept_id: "isolated" }], relations: [...view.relations,
    { relation_id: "ba", source_concept_id: "b", target_concept_id: "a", type: "contrast" },
    { relation_id: "ab2", source_concept_id: "a", target_concept_id: "b", type: "example" },
    { relation_id: "ac", source_concept_id: "a", target_concept_id: "c", type: "application" },
  ] };
  const before = structuredClone(map);
  const local = focusGraph(map, "b");
  assert.deepEqual(new Set(local.nodes.map(node => node.id)), new Set(["a", "b", "c", "d"]));
  assert.deepEqual(local.relations.map(edge => edge.relation_id), ["ab", "bc", "cd", "ba", "ab2", "ac"]);
  assert.equal(local.nodes.filter(node => node.id === "a").length, 1);
  for (const edge of local.relations) assert.ok(local.nodes.some(node => node.id === edge.source_concept_id)
    && local.nodes.some(node => node.id === edge.target_concept_id));
  assert.deepEqual(focusGraph(map, "isolated").nodes.map(node => node.id), ["isolated"]);
  assert.deepEqual(map, before);
});

test("large canonical maps do not become large rendered graphs", () => {
  const map = { concepts: Array.from({ length: 1000 }, (_, i) => ({ concept_id: String(i) })),
    relations: Array.from({ length: 999 }, (_, i) => ({ relation_id: String(i), source_concept_id: String(i), target_concept_id: String(i + 1) })) };
  assert.deepEqual(new Set(focusGraph(map, "500").nodes.map(node => node.id)), new Set(["498", "499", "500", "501", "502"]));
  assert.equal(focusGraph(map, "500").nodes.find(node => node.id === "498").depth, 2);
  assert.equal(focusGraph(map, "500").relations.length, 4);
  assert.deepEqual(focusGraph({ concepts: [], relations: [] }, ""), { nodes: [], relations: [], totalNodes: 0, totalRelations: 0 });
});


test("two-hop limits preserve a path to each rendered node and disclose omitted content", () => {
  const map = { concepts: Array.from({ length: 80 }, (_, i) => ({ concept_id: String(i) })), relations: [] };
  const connect = (a, b) => map.relations.push({ relation_id: String(map.relations.length), source_concept_id: String(a), target_concept_id: String(b) });
  for (let i = 1; i <= 6; i++) connect(0, i);
  for (let i = 7; i < 80; i++) connect(1 + (i - 7) % 6, i);
  for (let i = 1; i < 30; i++) for (let j = i + 1; j < 30; j++) connect(i, j);
  const before = structuredClone(map);
  const projection = focusGraph(map, "0");
  assert.equal(projection.nodes.length, 30);
  assert.equal(projection.relations.length, 60);
  assert.equal(projection.totalNodes, 80);
  assert.equal(projection.totalRelations, map.relations.length);
  assert.equal(projection.nodes.filter(node => node.depth === 1).length, 6);
  assert.equal(projection.nodes.filter(node => node.depth === 2).length, 23);
  const reached = new Set(["0"]);
  for (let step = 0; step < 2; step++) for (const edge of projection.relations) {
    if (reached.has(edge.source_concept_id)) reached.add(edge.target_concept_id);
    if (reached.has(edge.target_concept_id)) reached.add(edge.source_concept_id);
  }
  assert.deepEqual(reached, new Set(projection.nodes.map(node => node.id)));
  assert.ok(projection.relations.every(edge => map.relations.includes(edge)));
  const next = focusGraph(map, "79");
  assert.ok(next.nodes.length <= 30 && next.relations.length <= 60);
  assert.equal(next.nodes[0].id, "79");
  assert.notDeepEqual(next.nodes.map(node => node.id), projection.nodes.map(node => node.id));
  assert.deepEqual(map, before);
});

test("direct neighbours also respect the node limit", () => {
  const map = { concepts: Array.from({ length: 100 }, (_, i) => ({ concept_id: String(i) })),
    relations: Array.from({ length: 99 }, (_, i) => ({ relation_id: String(i), source_concept_id: "0", target_concept_id: String(i + 1) })) };
  const graph = focusGraph(map, "0");
  assert.equal(graph.nodes.length, 30);
  assert.equal(graph.totalNodes, 100);
  assert.equal(graph.relations.length, 29);
  assert.equal(graph.totalRelations, 99);
});
