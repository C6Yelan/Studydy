import katex from "katex";
import { mathParts } from "./math-text";
import "katex/dist/katex.min.css";
import "./math-text.css";

export function mathHtml(latex: string, display = false): string | null {
  try { return katex.renderToString(latex, { displayMode: display, throwOnError: true, strict: "ignore", trust: false, output: "htmlAndMathml" }); }
  catch { return null; }
}

export function MathText({ text, code = false, notation = true }: { text: string; code?: boolean; notation?: boolean }) {
  if (code) return <>{text}</>;
  return <>{mathParts(text, notation).map((part, i) => {
    if (part.kind !== "math") return part.text;
    const html = mathHtml(part.latex!, part.display);
    return html === null ? part.text : <span key={i} className={part.display ? "math-text math-text-display" : "math-text"} dangerouslySetInnerHTML={{ __html: html }}/>;
  })}</>;
}
