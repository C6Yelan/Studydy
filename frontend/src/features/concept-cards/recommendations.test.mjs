import assert from "node:assert/strict";
import test from "node:test";
import { recommendCards } from "./recommendations.ts";

const view = {
  knowledge_structure_revision: "fixed-revision",
  concepts: ["a", "b", "c", "d"].map((concept_id) => ({
    concept_id, label: concept_id, claims: [{ claim_id: `${concept_id}-claim` }],
  })),
  initial_learning_path: ["c", "a", "d", "b"].map((concept_id, position) => ({ concept_id, position })),
};
const progress = {
  knowledge_structure_revision: view.knowledge_structure_revision,
  current_concept_id: "a",
  concept_states: ["a", "b", "c", "d"].map((concept_id) => ({ concept_id, status: concept_id === "b" ? "mastered" : "learning" })),
  weaknesses: [{ concept_id: "a", claim_ids: ["a-claim"] }, { concept_id: "c", claim_ids: ["c-claim"] }],
};
const ids = (result) => result.items.map((item) => item.conceptId);

test("weaknesses use the published path for stable ranking and do not pad with unrelated cards", () => {
  const before = JSON.stringify({ view, progress });
  const result = recommendCards(view, progress, "weakness", 4);
  assert.deepEqual(ids(result), ["c", "a"]);
  assert.ok(result.items.every((item) => item.reason === "有待複習重點"));
  assert.match(result.message, /不另外補入/);
  assert.deepEqual(recommendCards(view, progress, "weakness", 4), result);
  assert.equal(JSON.stringify({ view, progress }), before);
});

test("path starts at the current concept, skips mastered concepts and wraps unfinished earlier concepts", () => {
  const result = recommendCards(view, progress, "path", 4);
  assert.deepEqual(ids(result), ["a", "d", "c"]);
  assert.equal(result.items[0].reason, "目前正在學習");
  assert.deepEqual(ids(recommendCards(view, { ...progress, current_concept_id: null }, "path", 2)), ["c", "a"]);
});

test("no learning record uses the path with an explicit explanation for either mode", () => {
  for (const mode of ["weakness", "path"]) {
    const result = recommendCards(view, null, mode, 2);
    assert.deepEqual(ids(result), ["c", "a"]);
    assert.match(result.message, /尚無學習紀錄/);
    assert.ok(result.items.every((item) => !item.reason.includes("弱點")));
  }
});

test("no weaknesses and fully mastered paths have no fabricated recommendations", () => {
  assert.deepEqual(ids(recommendCards(view, { ...progress, weaknesses: [] }, "weakness", 2)), []);
  const mastered = { ...progress, concept_states: progress.concept_states.map((state) => ({ ...state, status: "mastered" })) };
  const result = recommendCards(view, mastered, "path", 3);
  assert.deepEqual(result.items, []);
  assert.match(result.message, /都已掌握/);
});

test("wrong revision or invalid counts cannot select cards; foreign claim references are not weaknesses", () => {
  assert.deepEqual(recommendCards(view, { ...progress, knowledge_structure_revision: "other" }, "path", 2).items, []);
  for (const count of [0, -1, 1.5, 999, NaN]) assert.deepEqual(recommendCards(view, progress, "weakness", count).items, []);
  const foreign = { ...progress, weaknesses: [{ concept_id: "a", claim_ids: ["unknown"] }, { concept_id: "foreign", claim_ids: ["c-claim"] }] };
  assert.deepEqual(recommendCards(view, foreign, "weakness", 2).items, []);
});
