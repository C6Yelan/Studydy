import assert from "node:assert/strict";
import test from "node:test";
import { claimText } from "./claim-text.ts";

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
