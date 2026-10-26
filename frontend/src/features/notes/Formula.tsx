import { useMemo } from "react";
import katex from "katex";
import "katex/dist/katex.min.css";

/** 公式已在後端以同一組指令驗證；KaTeX 不信任任何指令，輸出只含排版標記。 */
export default function Formula({ latex }: { latex: string }) {
  const html = useMemo(() => katex.renderToString(latex, { displayMode: true, throwOnError: false, strict: "ignore", output: "htmlAndMathml" }), [latex]);
  return <div className="note-formula" dangerouslySetInnerHTML={{ __html: html }}/>;
}
