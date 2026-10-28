import { getFontEmbedCSS, toCanvas } from "html-to-image";
import { mathParts } from "../../ui/math-text";
import { mathHtml } from "../../ui/MathText";

// DOM 量測與 PNG 都使用同一份 KaTeX 排版，不能以原始公式字串估算寬高。
export function formulaElement(text: string, size: number, bold: boolean, color: string, font: string, width?: number) {
  const parts = mathParts(text);
  if (!parts.some(part => part.kind === "math")) return null;
  const element = document.createElement("span");
  Object.assign(element.style, { position:"absolute", left:"-100000px", top:"0", display:"inline-block", whiteSpace:"pre-wrap", maxWidth:width === undefined ? "none" : `${width}px`, fontFamily:font, fontSize:`${size}px`, fontWeight:bold ? "600" : "400", color, lineHeight:"1.4" });
  for (const part of parts) {
    const html = part.kind === "math" ? mathHtml(part.latex!, part.display) : null;
    if (html === null) element.append(document.createTextNode(part.text));
    else { const span = document.createElement("span"); span.className = part.display ? "math-text math-text-display" : "math-text"; span.innerHTML = html; element.append(span); }
  }
  document.body.append(element);
  return element;
}

export async function formulaCanvas(element: HTMLElement) {
  try {
    const fontEmbedCSS = await getFontEmbedCSS(element);
    return await toCanvas(element, { pixelRatio:1, fontEmbedCSS, style:{position:"static",left:"auto",top:"auto"} });
  } finally { element.remove(); }
}
