import type { ConceptCard, ConceptCardContext, CardTextEdit } from "../../api/contracts";
import { claimText } from "../../ui/claim-text.ts";
import { cardRelation, cardRelations, hasCardEvidence } from "./card-relation.ts";
import { textUnits } from "./card-text.ts";
import { cardContent, cardSourceText } from "./card-content.ts";
import { selectCardObjects } from "./icon-selection.ts";

export function selectConceptCardLayout(card: ConceptCard, structure: ConceptCardContext) {
  const claims = card.claims.filter(c => claimText(c).trim());
  const technical = claims.find(c => c.evidence.some(e => hasCardEvidence(e) && (e.kind === "code" || e.kind === "formula")));
  const content = technical ? null : cardContent(card, structure);
  const relation = cardRelation(card, structure);

  const missingEvidence = !claims.length || claims.some(c => !c.evidence.length || c.evidence.some(e => !hasCardEvidence(e)));
  const unresolvedRelations = structure.relations.filter(r => r.source_concept_id === card.concept_id || r.target_concept_id === card.concept_id).length > cardRelations(card, structure).length;
  const family = technical ? "technical" : content ? content.kind : relation ? "relation" : (claims.length <= 1 || claims.length === 2 && claims.every(c => textUnits(claimText(c)) <= 160)) ? "sparse" : "points";
  const visibleClaims = technical ? [technical] : content ? content.claims : claims.slice(0, relation ? 3 : 4);
  const objects = ["technical", "table", "sequence", "fields", "composition"].includes(family) ? [] : selectCardObjects({ ...card, claims: family === "relation" ? claims : visibleClaims });
  return {
    policy: "visual-concept-card/v4", family, claims: visibleClaims, content,
    technicalKind: technical?.evidence.some(e => hasCardEvidence(e) && e.kind === "code") ? "code" : "formula",
    relation: family === "relation" ? relation : null,
    objects,
    omitted: claims.length - visibleClaims.length,
    missingEvidence, unresolvedRelations,
    review: structure.status.quality === "needs_review" || structure.status.processing !== "succeeded" || structure.status.decision !== "retain",
  } as const;
}

// 同一觀念可延續到多張圖；已呈現的重點不再佔用後續圖的容量。
export function selectConceptCardLayouts(card: ConceptCard, structure: ConceptCardContext) {
  const layouts: ReturnType<typeof selectConceptCardLayout>[] = [];
  let remaining = card.claims.filter(c => claimText(c).trim());
  do {
    const layout = selectConceptCardLayout({ ...card, claims: remaining }, layouts.length ? { ...structure, relations: [] } : structure);
    layouts.push({ ...layout, omitted: 0 });
    const shown = new Set(layout.claims.map(c => c.claim_id));
    remaining = remaining.filter(c => !shown.has(c.claim_id));
  } while (remaining.length);
  return layouts;
}

// 原文先決定版型與順序，再替換可顯示的文字；來源、標籤、欄位及關係身分不變。
export function compactLayout(layout: ReturnType<typeof selectConceptCardLayout>, edits: CardTextEdit[] = []): ReturnType<typeof selectConceptCardLayout> {
  if (!edits.length || layout.family === "technical") return layout;
  const normalized = (text: string) => text.trim().replace(/\s+/gu, " ");
  const replacements = new Map(edits.map(edit => [normalized(edit.source), edit.text]));
  const shorten = (text: string) => replacements.get(normalized(text)) ?? text.split(/\r?\n/u).map(line => {
    const direct = replacements.get(line.trim());
    if (direct) return direct;
    const field = line.match(/^([^:：\n]{1,32}[:：][ \t]*)(.+)$/u);
    return field && replacements.has(field[2].trim()) ? field[1] + replacements.get(field[2].trim()) : line;
  }).join("\n");
  const claim = (item: ConceptCard["claims"][number]) => ({ ...item, text: shorten(cardSourceText(item)) });
  const original = layout.content;
  let content = original ? { ...original, claims: original.claims.map(claim) } : null;
  if (content?.kind === "table") content = { ...content, intro: shorten(content.intro), rows: content.rows.map(row => ({ ...row, value: shorten(row.value) })), notes: content.notes.map(claim) };
  else if (content?.kind === "sequence") content = { ...content, intro: shorten(content.intro), notes: shorten(content.notes), steps: content.steps.map(step => ({ ...step, values: step.values.map(shorten) })) };
  else if (content?.kind === "group") content = { ...content, items: content.items.map(item => ({ ...item, description: shorten(item.description) })) };
  return { ...layout, claims: layout.claims.map(claim), content,
    relation: layout.relation ? { ...layout.relation, relation: { ...layout.relation.relation, learner_reason: shorten(layout.relation.relation.learner_reason) } } : null };
}
