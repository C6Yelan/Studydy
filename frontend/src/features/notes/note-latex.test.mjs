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
