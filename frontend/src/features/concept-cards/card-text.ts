// 版型容量採穩定的保守估算；實際 SVG 斷行另以瀏覽器字型量測。
export function textUnits(text: string): number {
  return Array.from(text).reduce((n, c) => n + (/[\x00-\x7f]/.test(c) ? 0.65 : 1), 0);
}

const segmenter = new Intl.Segmenter("zh-Hant", { granularity: "word" });

// 英文詞與識別符不從中間折斷。無法容納的單詞留給節錄提示，不偽造縮寫。
export function wrapCardText(text: string, width: number, measure: (text: string) => number) {
  const lines: string[] = [];
  let overflow = false;
  for (const paragraph of text.split(/\r?\n/)) {
    let line = "";
    const words: string[] = [];
    for (const { segment } of segmenter.segment(paragraph)) {
      if (words.length && /^[\w+#./:-]+$/.test(words.at(-1)!) && /^[\w+#./:-]+$/.test(segment)) words[words.length - 1] += segment;
      else words.push(segment);
    }
    const tokens: string[] = [];
    for (const word of words) {
      const last = tokens.at(-1);
      // 先合併 ASCII 術語，再套用中文行首禁則與成對標點。
      if (last && (/^[，。！？；：、）〉》」』)\]},.!?;:%％]+$/u.test(word)
        || /^[（「『《〈(\[{]+$/u.test(last))) tokens[tokens.length - 1] += word;
      else tokens.push(word);
    }
    for (const segment of tokens) {
      if (measure(segment) > width) { overflow = true; break; }
      if (measure(line + segment) > width) { lines.push(line.trimEnd()); line = ""; }
      line += !line ? segment.trimStart() : segment;
    }
    if (line || !paragraph) lines.push(line.trimEnd());
    if (overflow) break;
  }
  return { lines, overflow };
}
