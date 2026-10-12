import type { ConceptCard, KnowledgeStructureView } from "../../api/contracts";
import { claimText } from "../../ui/claim-text.ts";
import { cardRelation, cardRelations, hasCardEvidence } from "./card-relation.ts";
import { textUnits } from "./card-text.ts";
import { cardContent } from "./card-content.ts";
import { selectCardObjects } from "./icon-selection.ts";

export function selectConceptCardLayout(card: ConceptCard, structure: KnowledgeStructureView) {
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
export function selectConceptCardLayouts(card: ConceptCard, structure: KnowledgeStructureView) {
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
