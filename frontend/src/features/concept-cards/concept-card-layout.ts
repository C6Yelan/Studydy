import type { ConceptCard, KnowledgeStructureView } from "../../api/contracts";
import { claimText } from "../../ui/claim-text.ts";
import { cardRelation, cardRelations, hasCardEvidence } from "./card-relation.ts";
import { textUnits } from "./card-text.ts";
import { selectCardObjects } from "./icon-selection.ts";

export function selectConceptCardLayout(card: ConceptCard, structure: KnowledgeStructureView) {
  const claims = card.claims.filter(c => claimText(c).trim());
  const technical = claims.find(c => c.evidence.some(e => hasCardEvidence(e) && (e.kind === "code" || e.kind === "formula")));
  const relation = cardRelation(card, structure);

  const missingEvidence = !claims.length || claims.some(c => !c.evidence.length || c.evidence.some(e => !hasCardEvidence(e)));
  const unresolvedRelations = structure.relations.filter(r => r.source_concept_id === card.concept_id || r.target_concept_id === card.concept_id).length > cardRelations(card, structure).length;
  const family = technical ? "technical" : relation ? "relation" : (claims.length <= 1 || claims.length === 2 && claims.every(c => textUnits(claimText(c)) <= 160)) ? "sparse" : "points";
  const visibleClaims = technical ? [technical] : claims.slice(0, relation ? 3 : 4);
  const objects = technical ? [] : selectCardObjects({ ...card, claims: family === "relation" ? claims : visibleClaims });
  return {
    policy: "visual-concept-card/v3", family, claims: visibleClaims,
    technicalKind: technical?.evidence.some(e => hasCardEvidence(e) && e.kind === "code") ? "code" : "formula",
    relation: family === "relation" ? relation : null,
    objects,
    omitted: claims.length - visibleClaims.length,
    missingEvidence, unresolvedRelations,
    review: structure.status.quality === "needs_review" || structure.status.processing !== "succeeded" || structure.status.decision !== "retain",
  } as const;
}
