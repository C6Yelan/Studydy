import test from "node:test";
import assert from "node:assert/strict";
import { selectConceptCardLayout } from "./concept-card-layout.ts";
import { cardRelations, relationPresentation } from "./card-relation.ts";
import { cardSourceText } from "./card-content.ts";
import { selectCardObjects } from "./icon-selection.ts";
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
  assert.equal(selectConceptCardLayout(card, view).family, "text");
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


function quotedCase(label, lines) {
  const { card, view } = fixture(); card.label = label;
  card.claims[0].text = lines.join(" ");
  card.claims[0].evidence = lines.map((quote, i) => ({ ...structuredClone(evidence), evidence_id: `e${i}`, quote }));
  return { card, view };
}
test("literal input, identifier fragments and abstract wording do not become physical icons", () => {
  for (const [label, text, forbidden] of [
    ["轉換範例", "Input: horse", ["horse"]],
    ["字串範例", '將 "horse" 當成文字處理。', ["horse"]],
    ["保存策略", "以伺服器保存內容為中心。", ["heart"]],
    ["滑動視窗", "允許範圍會向前移動。", ["window", "device-mobile"]],
    ["無連線服務", "此協定提供無連線的傳送服務。", ["connection"]],
    ["Content-Transfer-Encoding", "Content-Transfer-Encoding 說明編碼方式。", ["directions", "transfer"]],
    ["資料框架", "此框架包含網路封包。", ["frame"]],
  ]) {
    const { card } = quotedCase(label, [text]);
    const icons = selectCardObjects(card).map(o => o.kind);
    for (const icon of forbidden) assert.ok(!icons.includes(icon), `${label}: ${icon}`);
  }
  const { card } = quotedCase("窗戶", ["房間的窗戶由玻璃製成。"]);
  assert.ok(selectCardObjects(card).some(o => o.kind === "window"));
});
test("source block line breaks require an exact lexical match", () => {
  const { card } = quotedCase("來源", ["第一行", "第二行"]);
  assert.equal(cardSourceText(card.claims[0]), "第一行\n第二行");
  card.claims[0].text += " 額外說明";
  assert.equal(cardSourceText(card.claims[0]), "第一行 第二行 額外說明");
});
test("a reversed saved relation is not drawn or silently rewritten", () => {
  const { card, view } = quotedCase("通信位址", ["節點位置與端點號碼可共同描述通信位址。"]);
  view.concepts[1].label = "端點號碼";
  view.relations = [{ ...relation("part_of"), evidence_refs: ["e0"], learner_reason: "端點號碼與節點位置共同構成通信位址。" }];
  const before = structuredClone(view);
  const result = selectConceptCardLayout(card, view);
  assert.equal(result.relation, null); assert.equal(result.unresolvedRelations, true);
  assert.equal(result.family, "composition"); assert.deepEqual(result.content.fields, ["節點位置", "端點號碼"]);
  assert.deepEqual(view, before);
});
test("explicit numbered exchanges retain direction and literal payloads", () => {
  const { card, view } = quotedCase("交換範例", ["依序交換。", "1 甲端 → 乙端", "HELLO", "17", "2 乙端 → 甲端", "OK", "18", "保留此條件。"]);
  const result = selectConceptCardLayout(card, view);
  assert.equal(result.family, "sequence");
  assert.deepEqual(result.content.steps, [{ from: "甲端", to: "乙端", values: ["HELLO", "17"] }, { from: "乙端", to: "甲端", values: ["OK", "18"] }]);
  assert.equal(result.content.notes, "保留此條件。");
  card.claims[0].evidence[5].quote = "3 乙端 → 甲端";
  card.claims[0].text = card.claims[0].evidence.map(e => e.quote).join(" ");
  assert.notEqual(selectConceptCardLayout(card, view).family, "sequence");
});
test("structured paragraphs keep complete code categories and literal rows", () => {
  const codes = quotedCase("分類示例", ["代碼分類", "2xx", "完成", "5xx", "拒絕"]);
  const table = selectConceptCardLayout(codes.card, codes.view).content;
  assert.equal(table.kind, "table"); assert.deepEqual(table.rows.map(r => r.label), ["2xx", "5xx"]);
  const data = quotedCase("文字範例", ["Input: abc", "Count: 3", "Result: example"]);
  const literal = selectConceptCardLayout(data.card, data.view).content;
  assert.equal(literal.kind, "table"); assert.equal(literal.rows.at(-1).value, "example");
  const fields = quotedCase("訊息", ["訊息", "控制段  |  內容段"]);
  assert.deepEqual(selectConceptCardLayout(fields.card, fields.view).content.fields, ["控制段", "內容段"]);
});


test("related layers use evidence-backed numbers and preserve their source references", () => {
  const { card, view } = fixture(); card.label = "分層示例";
  const children = [1, 2].map(n => ({ concept_id: `layer-${n}`, label: n === 1 ? "介面層" : "資料層", claims: [{ claim_id: `layer-claim-${n}`, text: `${n} ${n === 1 ? "介面層" : "資料層"} 處理本層內容。`, evidence: [{ ...structuredClone(evidence), evidence_id: `layer-evidence-${n}`, quote: `${n} ${n === 1 ? "介面層" : "資料層"}\n處理本層內容。` }] }] }));
  view.concepts = [card, ...children];
  view.relations = children.map(c => ({ ...relation("part_of", `rel-${c.concept_id}`), source_concept_id: c.concept_id, target_concept_id: card.concept_id, evidence_refs: [c.claims[0].evidence[0].evidence_id], learner_reason: `${c.label}是此模型的層之一。` }));
  const before = structuredClone(view);
  const result = selectConceptCardLayout(card, view);
  assert.equal(result.family, "group"); assert.equal(result.content.ordered, true);
  assert.deepEqual(result.content.items.map(item => item.ordinal), [2, 1]);
  assert.ok(result.content.items[0].evidence.some(e => e.evidence_id === "layer-evidence-2"));
  assert.deepEqual(view, before);
});


test("a negated composition stays prose rather than becoming an affirmative diagram", () => {
  const { card, view } = quotedCase("通信位址", ["節點位置與端點號碼不能共同描述通信位址。"]);
  assert.notEqual(selectConceptCardLayout(card, view).family, "composition");
});


test("mentioning classification does not itself establish a category relationship", () => {
  assert.equal(relationPresentation("這個規則描述數值的範圍與用途分類。", "part_of"), "association");
  assert.equal(relationPresentation("甲是乙的一種涵義。", "part_of"), "classification");
  assert.equal(relationPresentation("元件是這種方式的一部分。", "part_of"), "part_of");
});
test("explicit numeric ranges are ordered without inventing missing members", () => {
  const { card, view } = fixture(); card.label = "數值範圍";
  const children = [20, 10].map(n => ({ concept_id: `range-${n}`, label: `區間${n}`, claims: [{ claim_id: `range-claim-${n}`, text: `${n}–${n + 9} 為區間${n}。`, evidence: [{ ...structuredClone(evidence), evidence_id: `range-evidence-${n}`, quote: `${n}–${n + 9} 為區間${n}。` }] }] }));
  view.concepts = [card, ...children];
  view.relations = children.map(c => ({ ...relation("part_of", `rel-${c.concept_id}`), source_concept_id: c.concept_id, target_concept_id: card.concept_id, evidence_refs: [c.claims[0].evidence[0].evidence_id], learner_reason: `${c.label}是數值範圍中的一個區間。` }));
  const content = selectConceptCardLayout(card, view).content;
  assert.equal(content.kind, "group");
  assert.deepEqual(content.items.map(item => item.label), ["區間10", "區間20"]);
});


test("multiple supported applications appear together without inventing component relations", () => {
  const { card, view } = fixture(); card.label = "共同用途";
  const children = ["方案甲", "方案乙"].map((label, n) => ({ concept_id: `option-${n}`, label, claims: [{ claim_id: `option-claim-${n}`, text: `${label}用於共同用途。`, evidence: [{ ...structuredClone(evidence), evidence_id: `option-evidence-${n}`, quote: `${label}用於共同用途。` }] }] }));
  view.concepts = [card, ...children];
  view.relations = children.map(c => ({ ...relation("application", `rel-${c.concept_id}`), source_concept_id: c.concept_id, target_concept_id: card.concept_id, evidence_refs: [c.claims[0].evidence[0].evidence_id], learner_reason: `${c.label}用於共同用途。` }));
  const content = selectConceptCardLayout(card, view).content;
  assert.equal(content.kind, "group"); assert.equal(content.ordered, false);
  assert.deepEqual(content.items.map(item => item.label).sort(), ["方案乙", "方案甲"].sort());
  assert.ok(view.relations.every(r => r.type === "application"));
  card.claims.push(...[1, 2, 3].map(n => ({ ...structuredClone(card.claims[0]), claim_id: `overview-${n}`, text: `總覽重點 ${n}` })));
  const overview = selectConceptCardLayout(card, view);
  assert.equal(overview.family, "text"); assert.equal(overview.claims.length, 4);
});

test("continuation images cover every claim once and preserve evidence", async () => {
  const { selectConceptCardLayouts } = await import("./concept-card-layout.ts");
  const { card, view } = fixture(9);
  view.relations = [relation("example")];
  const before = structuredClone(card);
  const pages = selectConceptCardLayouts(card, view);
  assert.ok(pages.length > 1);
  assert.deepEqual(pages.flatMap(p => p.claims), card.claims);
  assert.ok(pages.every(p => p.omitted === 0));
  assert.ok(pages.slice(1).every(p => !p.relation));
  assert.deepEqual(card, before);
});
