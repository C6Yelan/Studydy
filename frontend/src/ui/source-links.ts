import type { EvidenceView } from "../api/contracts";

type SourceLink<T = EvidenceView> = T & { citation_count: number; references: T[] };

export function sourceLinks<T extends Pick<EvidenceView, "evidence_id" | "page" | "normalized_page" | "source_id" | "source_name"> & {quote?: string}>(evidence: T[]): SourceLink<T>[] {
  // 僅合併同教材同 PDF 頁的入口，保留代表 Evidence 供 resolver 回查；不修改 Evidence。
  const links = new Map<string, SourceLink<T>>();
  for (const item of evidence) {
    const key = JSON.stringify([
      item.source_id
        ? ["id", item.source_id]
        : item.source_name
          ? ["name", item.source_name]
          : ["evidence", item.evidence_id],
      item.normalized_page ?? item.page,
    ]);
    const link = links.get(key);
    if (link) {
      link.citation_count++;
      if (!link.references.some(reference => reference.evidence_id === item.evidence_id && reference.quote === item.quote))
        link.references.push(item);
    } else links.set(key, {...item, citation_count: 1, references: [item]});
  }
  return [...links.values()];
}

export function citationNumberRanges(numbers: number[]): string {
  const ranges: string[] = [];
  for (let index = 0; index < numbers.length; index++) {
    const first = numbers[index];
    while (index + 1 < numbers.length && numbers[index + 1] === numbers[index] + 1) index++;
    ranges.push(first === numbers[index] ? String(first) : `${first}–${numbers[index]}`);
  }
  return ranges.join("、");
}
