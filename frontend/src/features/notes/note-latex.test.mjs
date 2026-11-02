import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import katex from "katex";

// 後端只接受這份指令清單；網頁的 KaTeX 必須能排版其中每一個指令。
const source = readFileSync(new URL("../../../../backend/src/runtime/note_latex.py", import.meta.url), "utf8");
const commands = source.match(/COMMANDS = frozenset\("""([\s\S]*?)"""/)[1].trim().split(/\s+/);
const oneArgument = new Set(["sqrt", "text", "mathrm", "mathbf", "mathit", "mathsf", "mathcal", "boldsymbol", "hat", "bar",
  "tilde", "vec", "dot", "ddot", "overline", "underline", "widehat", "widetilde", "operatorname"]);
const twoArguments = new Set(["frac", "dfrac", "binom"]);

function sample(command) {
  if (twoArguments.has(command)) return `\\${command}{a}{b}`;
  if (oneArgument.has(command)) return `\\${command}{x}`;
  if (command === "left" || command === "right") return String.raw`\left( x \right)`;
  return `a \\${command} b`;
}

test("KaTeX renders every LaTeX command the backend accepts", () => {
  assert.ok(commands.length > 100);
  for (const command of commands) {
    assert.doesNotThrow(() => katex.renderToString(sample(command), { displayMode: true, throwOnError: true, strict: "ignore" }), command);
  }
  for (const formula of [String.raw`a\,b\;c\!d\ e`, String.raw`\{1\} \% \_ \# a\|b`,
    String.raw`\text{開放度} = \frac{\text{出口}+\text{進口}}{\text{GDP}} \times 100\%`]) {
    assert.doesNotThrow(() => katex.renderToString(formula, { displayMode: true, throwOnError: true, strict: "ignore" }), formula);
  }
});

const { mathParts } = await import("../../ui/math-text.ts");
test("shared math parses explicit delimiters and the approved notation table", () => {
  const parts = mathParts(String.raw`機率 product_i，sqrt(d_k)，alpha_bar_t，QK^T，x^2，$\theta - \eta$。`);
  assert.deepEqual(parts.filter(p => p.kind === "math").map(p => p.latex), [String.raw`\prod_{i}`, String.raw`\sqrt{d_k}`, String.raw`\bar{\alpha}_{t}`, "QK^{T}", "x^{2}", String.raw`\theta - \eta`]);
  for (const part of parts.filter(p => p.kind === "math")) assert.doesNotThrow(() => katex.renderToString(part.latex, {throwOnError:true}));
});
test("shared math preserves code and unknown notation", () => {
  const text = "`sqrt(x)` 與 ```python\nproduct_i = x^2\n``` 及 unknown_symbol";
  assert.equal(mathParts(text).some(part => part.kind === "math"), false);
  assert.equal(mathParts(text).map(part => part.text).join(""), text);
});
const { mathPlainText } = await import("../../ui/math-text.ts");
test("titles typeset only delimited math and keep ASCII notation as written", () => {
  const ascii = "softmax(QK^T / sqrt(d_k) + M)V";
  assert.deepEqual(mathParts(ascii, false), [{ kind: "text", text: ascii }]);
  const parts = mathParts(String.raw`條件機率分布 $p_\theta(x | c)$`, false);
  assert.deepEqual(parts.map(p => p.kind), ["text", "math"]);
  assert.equal(mathPlainText(String.raw`條件機率分布 $p_\theta(x | c)$`), String.raw`條件機率分布 p_\theta(x | c)`);
  assert.equal(mathPlainText(ascii), ascii);
});
