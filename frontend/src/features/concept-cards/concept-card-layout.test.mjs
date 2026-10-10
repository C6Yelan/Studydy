import test from "node:test";
import assert from "node:assert/strict";
import { selectConceptCardLayout } from "./concept-card-layout.ts";
import { cardRelations } from "./card-relation.ts";
import { wrapCardText } from "./card-text.ts";

const evidence = { evidence_id: "e", quote: "教材原文", page: 1, kind: "paragraph", source_locator: { block_id: "b" } };
function fixture(n = 1) {
  const card = { concept_id: "a", label: "概念 A", claims: Array.from({ length: n }, (_, i) => ({ claim_id: `c${i}`, text: `教材重點 ${i}`, evidence: [structuredClone(evidence)] })) };
  return { card, view: { concepts: [card, { concept_id: "b", label: "概念 B", claims: [] }], relations: [], status: { quality: "accepted", processing: "succeeded", decision: "retain" } } };
}
function relation(type, id = "r") { return { relation_id: id, type, source_concept_id: "a", target_concept_id: "b", evidence_refs: ["e"], learner_reason: type === "contrast" ? "教材對照的是取出順序。" : "教材說明兩個概念的關係。" }; }

for (const n of [1, 2, 3, 4, 6]) test(`${n} claims select only occupied regions and disclose omitted claims`, () => {
  const { card, view } = fixture(n); const layout = selectConceptCardLayout(card, view);
  assert.equal(layout.family, n <= 2 ? "sparse" : "points");
  assert.equal(layout.claims.length, Math.min(n, 4)); assert.equal(layout.omitted, Math.max(0, n - 4));
  assert.deepEqual(layout.claims.map(c => c.claim_id), card.claims.slice(0, 4).map(c => c.claim_id));
});
for (const type of ["prerequisite", "part_of", "application", "example", "contrast"]) test(`${type} preserves source, target and evidence even when this card is the target`, () => {
  const { card, view } = fixture(); view.relations = [relation(type)];
  const r = selectConceptCardLayout(view.concepts[1], view).relation;
  assert.equal(r.relation, view.relations[0]); assert.equal(r.source.concept_id, card.concept_id); assert.equal(r.target.concept_id, "b"); assert.equal(r.evidence[0], card.claims[0].evidence[0]);
});
test("unsupported, incomplete and oversized relations fall back without invented comparison axes", () => {
  const { card, view } = fixture();
  for (const r of [{ ...relation("example"), evidence_refs: ["missing"] }, { ...relation("contrast"), learner_reason: "兩者不同。" }, { ...relation("part_of"), learner_reason: "過長".repeat(200) }]) {
    view.relations = [r]; assert.equal(selectConceptCardLayout(card, view).family, "sparse");
  }
  card.claims[0].evidence[0].quote = ""; view.relations = [relation("example")];
  assert.equal(selectConceptCardLayout(card, view).missingEvidence, true); assert.equal(selectConceptCardLayout(card, view).relation, null);
});
test("multiple relations are deterministic under reordering, with all valid reasons available on the back", () => {
  const { card, view } = fixture(); view.relations = [relation("prerequisite", "z"), relation("part_of", "b"), relation("part_of", "a")];
  const before = selectConceptCardLayout(card, view); view.relations.reverse();
  assert.deepEqual(selectConceptCardLayout(card, view), before); assert.equal(before.relation.relation.relation_id, "a"); assert.equal(cardRelations(card, view).length, 3);
});
for (const kind of ["code", "formula"]) test(`${kind} takes priority and preserves exact technical text`, () => {
  const { card, view } = fixture(2); view.relations = [relation("example")];
  card.claims[1].evidence[0].kind = kind;
  card.claims[1].text = "f(x) = (x² − 1)/(x − 1), x ≠ 1\nconst values = [2, 4, 6];\n  values[0] !== 0";
  const before = structuredClone(card); const layout = selectConceptCardLayout(card, view);
  assert.equal(layout.family, "technical"); assert.equal(layout.claims[0].text, before.claims[1].text); assert.deepEqual(card, before);
});
test("icons require matching affirmative source, including direct subject mentions", () => {
  const { card, view } = fixture(); const claim = card.claims[0];
  claim.text = "通訊端點，例如電腦、行動裝置與服務主機。"; claim.evidence[0].quote = claim.text;
  assert.equal(selectConceptCardLayout(card, view).objects.length, 3);
  claim.text = "不是所有端點都包含電腦。"; assert.equal(selectConceptCardLayout(card, view).objects.length, 0);
  claim.text = "通訊端點，例如電腦。"; claim.evidence[0].quote = "只談通訊端點。"; assert.equal(selectConceptCardLayout(card, view).objects.length, 0);
  claim.text = "電腦"; claim.evidence[0].quote = "例如電腦。"; assert.equal(selectConceptCardLayout(card, view).objects[0].kind, "device-desktop");
  claim.text = "惡意軟體包括電腦病毒。"; claim.evidence[0].quote = claim.text; assert.equal(selectConceptCardLayout(card, view).objects.length, 0);
});
test("long prose uses capacity-aware structure; missing source and version review stay explicit", () => {
  const { card, view } = fixture(2); card.claims[0].text = "中文 English terminology ".repeat(40);
  assert.equal(selectConceptCardLayout(card, view).family, "points");
  card.claims[0].evidence = []; view.status.quality = "needs_review";
  const result = selectConceptCardLayout(card, view); assert.equal(result.missingEvidence, true); assert.equal(result.review, true);
});
test("word wrapping keeps English terms intact and reports unfit words", () => {
  assert.deepEqual(wrapCardText("中文 TCP protocol 中文", 12, s => s.length).lines, ["中文 TCP", "protocol 中文"]);
  const result = wrapCardText("TCP extraordinarilylongidentifier", 10, s => s.length);
  assert.equal(result.overflow, true); assert.deepEqual(result.lines, ["TCP"]);
});

test("Chinese closing punctuation and paired terminology stay with their words", () => {
  assert.deepEqual(wrapCardText("教材原文。", 4, s => s.length).lines, ["教材", "原文。"]);
  assert.deepEqual(wrapCardText("使用（TCP/IP）說明", 8, s => s.length).lines, ["使用", "（TCP/IP）", "說明"]);
  assert.deepEqual(wrapCardText("第一段\n\n第二段", 10, s => s.length).lines, ["第一段", "", "第二段"]);
});

for (const [title, text, expected, forbidden] of [
  ["印表機", "印表機把文件輸出到紙張。", "printer", ""],
  ["電子郵件", "電子郵件使用網路傳送訊息，信件可以包含附件。", "mail", ""],
  ["自行車", "自行車是以人力踩踏的交通工具。", "bike", ""],
  ["選用器材", "Use a telescope instead of a microscope.", "telescope", "microscope"],
  ["禁用器材", "Do not use a camera. Use a telescope.", "telescope", "camera"],
  ["Mouse（動物）", "A mouse is a small rodent that eats seeds.", "", "mouse"],
  ["Musical keyboard", "A keyboard is a musical instrument used in concerts.", "", "keyboard"],
  ["葉節點", "A leaf is a node with no children in a graph.", "", "leaf"],
]) test(`source-backed catalog selection: ${title}`, () => {
  const { card, view } = fixture(); card.label = title;
  card.claims[0].text = text; card.claims[0].evidence[0].quote = text;
  const objects = selectConceptCardLayout(card, view).objects;
  if (expected) assert.equal(objects[0].kind, expected);
  if (forbidden) assert.ok(!objects.some(o => o.kind === forbidden));
  objects.forEach(o => { assert.equal(o.claimId, card.claims[0].claim_id); assert.equal(o.evidenceId, "e"); });
});
test("catalog cannot borrow evidence or word-sense context from another claim", () => {
  const { card, view } = fixture(2);
  card.label = "觀察器材";
  card.claims[0].text = "望遠鏡"; card.claims[0].evidence[0].quote = "上課日期";
  card.claims[1].text = "上課日期"; card.claims[1].evidence[0].quote = "望遠鏡";
  assert.equal(selectConceptCardLayout(card,view).objects.length,0);
  card.claims[0].text = "A keyboard is a musical instrument."; card.claims[0].evidence[0].quote = card.claims[0].text;
  card.claims[1].text = "Computer hardware supports typing."; card.claims[1].evidence[0].quote = card.claims[1].text;
  assert.ok(!selectConceptCardLayout(card,view).objects.some(o => o.kind === "keyboard"));
  card.label = "望遠鏡"; card.claims[0].text = "望遠鏡"; card.claims[0].evidence[0].quote = "望遠鏡";
  card.claims[0].evidence[0].source_locator.block_id = "";
  card.claims[1].evidence = [];
  assert.equal(selectConceptCardLayout(card,view).objects.length,0);
});

for (const n of [1, 3, 6]) test(`relation card includes up to three actual claims (${n} available)`, () => {
  const { card, view } = fixture(n); view.relations = [relation("part_of")];
  const before = structuredClone(card);
  const layout = selectConceptCardLayout(card, view);
  assert.equal(layout.family, "relation");
  assert.deepEqual(layout.claims, card.claims.slice(0, 3));
  assert.equal(layout.omitted, Math.max(0, n - 3));
  assert.deepEqual(card, before);
});
