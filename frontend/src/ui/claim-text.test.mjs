import assert from "node:assert/strict";
import test from "node:test";
import { claimText } from "./claim-text.ts";
import { answerSources } from "../features/material-tools/answerSources.ts";

test("preserves existing code lines and restores matching source whitespace only", () => {
  const text = "int values[3];\n// Three integer values\nvalues[0] = 1;";
  assert.equal(claimText({ text, evidence: [] }), text);
  assert.equal(claimText({ text: text.replaceAll("\n", " "), evidence: [{ quote: text }] }), text);
});

test("does not add evidence wording to a summary or guess program syntax", () => {
  assert.equal(
    claimText({
      text: "Arrays store values.",
      evidence: [{ quote: "int values[3];\nArrays store values." }],
    }),
    "Arrays store values.",
  );
  assert.equal(
    claimText({ text: "int a; // comment int b;", evidence: [] }),
    "int a; // comment int b;",
  );
});

test("answer citations preserve later conditions and original fragment order", () => {
  const first={evidence_id:'same-block',quote:'可執行😀。',quote_start:0,quote_end:5};
  const last={evidence_id:'same-block',quote:'但空值不可。',quote_start:5,quote_end:11};
  const answer={text:'依據教材回答。',citations:[
    {passage_id:'tail',evidence:[last]}, {passage_id:'head',evidence:[first]}],citation_indices:[0,1]};
  const before=structuredClone(answer);
  const result=answerSources(answer);
  assert.equal(result.sources.length,1);
  assert.equal(result.sources[0].quote,'可執行😀。但空值不可。');
  assert.deepEqual(answer,before);
});

test("noncontiguous quoted passages do not pretend to be continuous text", () => {
  const result=answerSources({text:'回答',citations:[{passage_id:'one',evidence:[
    {evidence_id:'block',quote:'前段。',quote_start:0,quote_end:3},
    {evidence_id:'block',quote:'後段。',quote_start:20,quote_end:23}]}],citation_indices:[0]});
  assert.equal(result.sources[0].quote,'前段。\n…\n後段。');
});

test("structured citations never rewrite numeric vectors or code literals", () => {
  const result=answerSources({text:'向量 [0,1] 與索引 values[0] 必須保持原樣。',citations:[
    {passage_id:'one',evidence:[{evidence_id:'a',quote:'原文一'}]},
    {passage_id:'two',evidence:[{evidence_id:'b',quote:'原文二'}]}]});
  assert.ok(result.text.startsWith('向量 [0,1] 與索引 values[0] 必須保持原樣。'));
});
