export type MathPart = { kind: "text" | "code" | "math"; text: string; latex?: string; display?: boolean };

// 僅轉換已決定的字面記法；未知識別符與程式碼保持原文。
export function notationLatex(text: string): string {
  return text.replace(/\bproduct_([A-Za-z0-9]+)\b/g, "\\prod_{$1}")
    .replace(/\b(alpha|beta|gamma|theta|sigma|mu|eta|epsilon|lambda|[A-Za-z])_bar_([A-Za-z0-9]+)\b/g,
      (_, name: string, sub: string) => `\\bar{${name.length > 1 ? "\\" : ""}${name}}_{${sub}}`)
    .replace(/\bsqrt\(([^()]*)\)/g, "\\sqrt{$1}")
    .replace(/\^([A-Za-z0-9]+)/g, "^{$1}");
}

export function mathParts(text: string): MathPart[] {
  const parts: MathPart[] = [];
  // 程式區塊與 inline code 優先匹配，不能將裡面的運算式改成排版標記。
  const pattern = /```[\s\S]*?```|`[^`\n]*`|\$\$([\s\S]+?)\$\$|\\\[([\s\S]+?)\\\]|\\\(([\s\S]+?)\\\)|\$([^$\n]+?)\$|\bproduct_[A-Za-z0-9]+\b|\bsqrt\([^()\n]+\)|\b(?:alpha|beta|gamma|theta|sigma|mu|eta|epsilon|lambda|[A-Za-z])_bar_[A-Za-z0-9]+\b|\b[A-Za-z][A-Za-z0-9_]*\^[A-Za-z0-9]+/g;
  let end = 0;
  for (const match of text.matchAll(pattern)) {
    if (match.index! > end) parts.push({ kind: "text", text: text.slice(end, match.index) });
    const raw = match[0];
    parts.push(raw.startsWith("`") ? { kind: "code", text: raw } : {
      kind: "math", text: raw, latex: match[1] ?? match[2] ?? match[3] ?? match[4] ?? notationLatex(raw), display: !!(match[1] || match[2]),
    });
    end = match.index! + raw.length;
  }
  if (end < text.length) parts.push({ kind: "text", text: text.slice(end) });
  return parts;
}
