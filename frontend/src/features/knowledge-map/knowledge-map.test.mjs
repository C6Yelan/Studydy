import assert from "node:assert/strict";
import test from "node:test";
import { learningNavigationItems, initialFocusConceptId, focusGraph, navigatorRange, groupSlices, NAVIGATOR_WINDOW, relationVisibility, routeSegments, routeFrameIds, routeStationIds } from "./knowledge-map.ts";

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

test("without a selection every relation is lit; selecting an island dims the unrelated ones", () => {
  // ab 先備、bc 例子、cd 組成：a—b—c—d 一條鏈。
  const visible = (options) => Object.fromEntries(relationVisibility(view.relations, {
    litConceptId: null, selectedRelationId: null, ...options,
  }));
  // 沒選島時全部亮起。
  assert.deepEqual(visible({}), { ab: "lit", bc: "lit", cd: "lit" });
  // 選了 c：和 c 相連的兩條亮起，其餘關係保留但淡化。
  assert.deepEqual(visible({ litConceptId: "c" }), { ab: "dim", bc: "lit", cd: "lit" });
  // 點開的關係一定亮起，即使它不連到選取的島。
  assert.deepEqual(visible({ selectedRelationId: "cd" }), { ab: "lit", bc: "lit", cd: "lit" });
  assert.deepEqual(visible({ litConceptId: "a", selectedRelationId: "cd" }), { ab: "lit", bc: "dim", cd: "lit" });
});

test("the golden route only joins neighbouring stations that are both on screen", () => {
  const path = [["a", 1], ["b", 2], ["c", 3], ["d", 4], ["e", 5]]
    .map(([concept_id, position]) => ({ concept_id, position, reason: "document_order" }))
    .reverse();
  const route = (ids, start) => routeSegments(path, new Set(ids), start).map(({ source, target }) => `${source}→${target}`);
  // 只畫目前這站到下一站，方向是第 n 站到第 n+1 站。
  assert.deepEqual(route(["a", "b", "c", "d", "e"], "a"), ["a→b"]);
  assert.deepEqual(route(["a", "b", "c", "d", "e"], "c"), ["c→d"]);
  // 目前這站或下一站沒畫出來就不畫，不跨站連線。
  assert.deepEqual(route(["a", "c", "d", "e"], "a"), []);
  assert.deepEqual(route(["a", "b", "d", "e"], "c"), []);
  // 最後一站沒有下一站。
  assert.deepEqual(route(["a", "b", "c", "d", "e"], "e"), []);
});

test("with no island selected the camera frames the current station and the next three on screen", () => {
  const path = Array.from({ length: 8 }, (_, index) => ({ concept_id: `s${index + 1}`, position: index + 1, reason: "document_order" })).reverse();
  const all = new Set(path.map((step) => step.concept_id));
  // 開頭：第 1 站加第 2～4 站。
  assert.deepEqual(routeFrameIds(path, "s1", all), ["s1", "s2", "s3", "s4"]);
  // 中段：第 4 站起算，沒畫在畫面上的第 6 站略過，不往後補。
  assert.deepEqual(routeFrameIds(path, "s4", new Set(["s4", "s5", "s7", "s8"])), ["s4", "s5", "s7"]);
  // 結尾不足 3 站：有幾站框幾站。
  assert.deepEqual(routeFrameIds(path, "s7", all), ["s7", "s8"]);
  assert.deepEqual(routeFrameIds(path, "s8", all), ["s8"]);
  // 起點站不在畫面上時不框，由呼叫端改用其他起點。
  assert.deepEqual(routeFrameIds(path, "s2", new Set(["s1", "s3"])), []);
});

test("with no island selected the current station and the next three are drawn even outside two hops", () => {
  // a—b—c—d 的鏈：以 a 為中心只畫得到 a、b、c（兩層），第 4 站 d 在兩層外。
  const map = {
    ...view,
    initial_learning_path: ["a", "b", "c", "d"].map((concept_id, index) => ({ concept_id, position: index + 1, reason: "document_order" })),
  };
  const ids = (graph) => graph.nodes.map((node) => node.id).sort();
  assert.deepEqual(ids(focusGraph(map, "a")), ["a", "b", "c"]);
  const stations = routeStationIds(map.initial_learning_path, "a");
  assert.deepEqual(stations, ["a", "b", "c", "d"]);
  const withRoute = focusGraph(map, "a", stations);
  assert.deepEqual(ids(withRoute), ["a", "b", "c", "d"]);
  // 額外加入的站不另外畫關係，也算進「共幾個概念」。
  assert.deepEqual(withRoute.relations.map((relation) => relation.relation_id), ["ab", "bc"]);
  assert.equal(withRoute.totalNodes, 4);
  // 結尾不足 3 站時有幾站給幾站。
  assert.deepEqual(routeStationIds(map.initial_learning_path, "c"), ["c", "d"]);
});

test("after closing a far island's card the current station and the next three are all drawn", () => {
  // a～i 依序相連，目前這站是 a；選過 i 再關卡片時中心仍是 i，a～d 四站都在兩層外。
  const ids = [..."abcdefghi"];
  const chain = {
    concepts: ids.map((concept_id) => ({ concept_id })),
    relations: ids.slice(1).map((id, index) => ({ relation_id: ids[index] + id, source_concept_id: ids[index], target_concept_id: id, type: "prerequisite" })),
    initial_learning_path: ids.map((concept_id, index) => ({ concept_id, position: index + 1, reason: "document_order" })),
  };
  const stations = routeStationIds(chain.initial_learning_path, "a");
  const rendered = new Set(focusGraph(chain, "i", stations).nodes.map((node) => node.id));
  for (const id of ["a", "b", "c", "d"]) assert.ok(rendered.has(id), id);
  assert.deepEqual(routeFrameIds(chain.initial_learning_path, "a", rendered), ["a", "b", "c", "d"]);
});

test("a selected island's next station outside two hops is placed right below it", () => {
  const map = { ...view, initial_learning_path: [] };
  const node = (graph, id) => graph.nodes.find((item) => item.id === id);
  // 以 a 為中心時 d 在兩層外：放到 a 正下方，不另外畫關係，算進「共幾個概念」。
  const far = focusGraph(map, "a", [], "d");
  const [center, next] = [node(far, "a"), node(far, "d")];
  assert.equal(next.x + next.width / 2, center.x + center.width / 2);
  assert.ok(next.y > center.y);
  // 那一欄除了中心島沒有別的島，不會疊到。
  assert.ok(far.nodes.every((item) => [center, next].includes(item) || item.x + item.width <= next.x || item.x >= next.x + next.width));
  assert.deepEqual(far.relations.map((relation) => relation.relation_id), ["ab", "bc"]);
  assert.equal(far.totalNodes, 4);
  // 本來就在兩層內的下一站不搬動。
  assert.deepEqual(node(focusGraph(map, "a", [], "b"), "b"), node(focusGraph(map, "a"), "b"));
});
