import type { ConceptCard, EvidenceView, KnowledgeStructureView } from "../../api/contracts";
import { claimText } from "../../ui/claim-text.ts";
import { cardRelations, hasCardEvidence } from "./card-relation.ts";
import { textUnits } from "./card-text.ts";

type Claim = ConceptCard["claims"][number];
export type ContentRow = { label: string; value: string; claimId: string };
export type CardContent =
  | { kind: "sequence"; claims: Claim[]; intro: string; notes: string; steps: { from: string; to: string; values: string[] }[] }
  | { kind: "table"; claims: Claim[]; intro: string; rows: ContentRow[]; notes: Claim[]; mono: boolean }
  | { kind: "fields" | "composition"; claims: Claim[]; fields: string[]; heading: string }
  | { kind: "group"; claims: Claim[]; ordered: boolean; items: { label: string; description: string; conceptId: string; relationId: string; evidence: EvidenceView[]; ordinal?: number }[] }
  | { kind: "text"; claims: Claim[] };

const normalize = (text: string) => text.replace(/\s+/gu, " ").trim();
const escapePattern = (text: string) => text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

// 只有合併後字面完全相同時，才恢復來源區塊的換行；不靠猜測補欄名或內容。
export function cardSourceText(claim: Claim) {
  const text = claimText(claim);
  if (text.includes("\n")) return text;
  const evidence = claim.evidence.filter(hasCardEvidence);
  const joined = evidence.map(e => e.quote.trim()).join("\n");
  return evidence.length > 1 && normalize(joined) === normalize(text) ? joined : text;
}

function sequence(claim: Claim): CardContent | null {
  const lines = cardSourceText(claim).split("\n").map(s => s.trim()).filter(Boolean);
  const starts = lines.flatMap((line, index) => {
    const match = line.match(/^(\d+)\s+(\S+)\s*→\s*(\S+)$/u);
    return match ? [{ index, ordinal: Number(match[1]), from: match[2], to: match[3] }] : [];
  });
  if (starts.length < 2 || starts.length > 4 || !starts.every((s, i) => s.ordinal === i + 1)) return null;
  const endpoints = new Set(starts.flatMap(s => [s.from, s.to]));
  if (endpoints.size !== 2 || starts.some(s => s.from === s.to)) return null;
  const columns = starts[1].index - starts[0].index - 1;
  if (columns < 1 || columns > 5 || starts.some((s, i) => i < starts.length - 1 && starts[i + 1].index - s.index - 1 !== columns)) return null;
  const steps = starts.map(s => ({ from: s.from, to: s.to, values: lines.slice(s.index + 1, s.index + 1 + columns) }));
  if (steps.some(s => s.values.length !== columns || textUnits(s.values.join("  ")) > 65)) return null;
  const notes = lines.slice(starts.at(-1)!.index + 1 + columns);
  if (notes.length && /^[\d\s—+.-]+$/u.test(notes[0])) return null;
  return { kind: "sequence", claims: [claim], intro: lines.slice(0, starts[0].index).join("\n"), notes: notes.join("\n"), steps };
}

function claimTable(claim: Claim, claims: Claim[]): CardContent | null {
  const lines = cardSourceText(claim).split("\n").map(s => s.trim()).filter(Boolean);
  const codes = lines.flatMap((line, index) => /^\d+x{1,3}(?:\b|\s|[，,])/i.test(line) ? [index] : []);
  if (codes.length >= 2 && codes.length <= 6) {
    const rows = codes.map((start, i) => ({ label: lines[start], value: lines.slice(start + 1, codes[i + 1] ?? lines.length).join("\n"), claimId: claim.claim_id }));
    if (rows.every(row => row.value)) return { kind: "table", claims: [claim], intro: lines.slice(0, codes[0]).join("\n"), rows, notes: [], mono: false };
  }
  const pairs = lines.map(line => line.match(/^([A-Za-z][A-Za-z0-9_-]*(?: [A-Za-z]+)?):[ \t]*(.+)$/));
  if (pairs.length >= 3 && pairs.length <= 8 && pairs.every(Boolean)) {
    const notes = claims.filter(c => c !== claim);
    return { kind: "table", claims, intro: "", rows: pairs.map(m => ({ label: m![1], value: m![2], claimId: claim.claim_id })), notes, mono: true };
  }
  return null;
}

export function cardContent(card: ConceptCard, structure: KnowledgeStructureView): CardContent | null {
  const claims = card.claims.filter(c => claimText(c).trim());
  for (const claim of claims) {
    if (!claim.evidence.some(hasCardEvidence)) continue;
    const flow = sequence(claim);
    if (flow && claim === claims[0]) return flow;
    const table = claimTable(claim, claims);
    if (table) return table;
    const lines = cardSourceText(claim).split("\n").map(s => s.trim()).filter(Boolean);
    if (lines.length === 2 && /\s\|\s/u.test(lines.at(-1)!)) {
      const fields = lines.at(-1)!.split("|").map(s => s.trim());
      if (fields.length >= 2 && fields.length <= 5 && fields.every(s => s && textUnits(s) <= 32))
        return { kind: "fields", claims: [claim], fields, heading: lines.length === 2 ? lines[0] : card.label };
    }
    const composition = claimText(claim).match(/^(.+?)與(.+?)(?:可)?共同(?:構成|組成|描述)(.+)$/u);
    if (composition && !/不能|不會|無法|沒有|並非|不是/u.test(claimText(claim)) && composition[3].includes(card.label) && [composition[1], composition[2]].every(s => textUnits(s) <= 32))
      return { kind: "composition", claims: [claim], fields: [composition[1].trim(), composition[2].replace(/可$/u, "").trim()], heading: card.label };
  }
  // 多列來源本身已有欄位結構，依原順序呈現；不補層號或推導傳輸方向。
  const rows = claims.flatMap(claim => {
    if (!claim.evidence.some(hasCardEvidence)) return [];
    const lines = cardSourceText(claim).split("\n").map(s => s.trim()).filter(Boolean);
    return lines.length >= 2 && lines.length <= 4 && textUnits(lines[0]) <= 24 && !/[。！？]/u.test(lines[0])
      ? [{ label: lines[0], value: lines.slice(1).join("\n"), claimId: claim.claim_id }] : [];
  });
  if (rows.length >= 3 && rows.length <= 6) return { kind: "table", claims, intro: "", rows, notes: claims.filter(c => !rows.some(r => r.claimId === c.claim_id)), mono: false };

  const incoming = cardRelations(card, structure).filter(r => r.target.concept_id === card.concept_id);
  const distinct = (items: typeof incoming) => [...new Map(items.map(r => [r.source.concept_id, r])).values()];
  const parts = distinct(incoming.filter(r => r.relation.type === "part_of" && r.presentation !== "association"));
  const applications = distinct(incoming.filter(r => r.relation.type === "application"));
  // 總覽已有多則重點時，先保留本身的內容，避免用途範例搶走主圖。
  if (parts.length < 2 && applications.length >= 2 && claims.length > 3) return { kind: "text", claims: claims.slice(0, 4) };
  // 同一用途有多個來源概念時一併呈現，不任意只挑一個；不混成實體組件。
  const unique = parts.length >= 2 ? parts : applications;
  if (unique.length >= 2 && unique.length <= 8) {
    const items = unique.map(r => {
      const pattern = new RegExp(`(?:^|\\s)([1-9]|1[0-2])\\s+${escapePattern(r.source.label)}(?:\\s|$)`, "u");
      const numberedClaim = r.source.claims.find(c => c.evidence.some(e => hasCardEvidence(e) && pattern.test(e.quote)));
      const claim = numberedClaim ?? r.source.claims.find(c => c.evidence.some(hasCardEvidence));
      const text = claim ? cardSourceText(claim) : "";
      const ordinal = numberedClaim ? text.match(pattern)?.[1] : undefined;
      const range = text.match(/^(\d+)\s*[–—-]\s*(\d+)(?:\s|[是為])/u);
      const rangeStart = range && Number.isSafeInteger(Number(range[1])) ? Number(range[1]) : undefined;
      const lines = text.split("\n");
      const heading = lines.findIndex(line => pattern.test(line));
      return { label: r.source.label, description: ordinal && heading >= 0 && heading + 1 < lines.length ? lines[heading + 1] : text || r.relation.learner_reason,
        conceptId: r.source.concept_id, relationId: r.relation.relation_id, evidence: [...r.evidence, ...(claim?.evidence.filter(hasCardEvidence) ?? [])], ordinal: ordinal ? Number(ordinal) : undefined, rangeStart };
    });
    const ordered = items.every(item => item.ordinal !== undefined && item.label.endsWith("層")) && new Set(items.map(i => i.ordinal)).size === items.length;
    if (ordered) items.sort((a,b) => b.ordinal! - a.ordinal!);
    else if (items.every(item => item.rangeStart !== undefined)) items.sort((a,b) => a.rangeStart! - b.rangeStart!);
    return { kind: "group", claims: claims.slice(0, 3), items, ordered };
  }
  if (claims.some(c => c.evidence.some(hasCardEvidence) && (cardSourceText(c).split("\n").length >= 3 || textUnits(claimText(c)) > 240)))
    return { kind: "text", claims: claims.slice(0, 4) };
  return null;
}
