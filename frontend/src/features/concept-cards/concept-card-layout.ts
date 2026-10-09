import type { ConceptCard, KnowledgeStructureView } from "../../api/contracts";
import { claimText } from "../../ui/claim-text.ts";
import { cardRelation, cardRelations, hasCardEvidence } from "./card-relation.ts";
import { textUnits } from "./card-text.ts";

export type CardObject = { kind: "computer" | "mobile" | "server"; label: string; claimId: string };

function exampleList(sentence: string) {
  if (/[不非未無沒]/u.test(sentence)) return [];
  const examples = sentence.match(/(?:例如|包括|包含)[：:\s]*([^。！？\n]+)/u)?.[1];
  return examples?.split(/[、，,與和及]/u).map(item => item.trim().replace(/等$/, "")) ?? [];
}

// 僅對明示的肯定例子清單畫物件；名稱命中、否定句或 Evidence 獨有字詞均不夠。
function supportedObjects(claims: ConceptCard["claims"]): CardObject[] {
  const objects: CardObject[] = [];
  const choices = [ ["服務主機", "server"], ["行動裝置", "mobile"], ["電腦", "computer"] ] as const;
  for (const claim of claims) {
    for (const sentence of claimText(claim).split(/[。！？\n]/u)) {
      const examples = exampleList(sentence);
      for (const [label, kind] of choices) {
        if (examples.includes(label) && !objects.some(o => o.kind === kind)
          && claim.evidence.some(e => hasCardEvidence(e) && e.quote.split(/[。！？\n]/u).some(quote => exampleList(quote).includes(label)))) {
          objects.push({ kind, label, claimId: claim.claim_id });
        }
      }
    }
  }
  return objects;
}

export function selectConceptCardLayout(card: ConceptCard, structure: KnowledgeStructureView) {
  const claims = card.claims.filter(c => claimText(c).trim());
  const technical = claims.find(c => c.evidence.some(e => hasCardEvidence(e) && (e.kind === "code" || e.kind === "formula")));
  const relation = cardRelation(card, structure);
  const objects = supportedObjects(claims);
  const missingEvidence = !claims.length || claims.some(c => !c.evidence.length || c.evidence.some(e => !hasCardEvidence(e)));
  const unresolvedRelations = structure.relations.filter(r => r.source_concept_id === card.concept_id || r.target_concept_id === card.concept_id).length > cardRelations(card, structure).length;
  const family = technical ? "technical" : relation ? "relation" : (claims.length <= 1 || claims.length === 2 && claims.every(c => textUnits(claimText(c)) <= 160)) ? "sparse" : "points";
  const visibleClaims = technical ? [technical] : relation ? [] : claims.slice(0, 4);
  return {
    policy: "visual-concept-card/v2", family, claims: visibleClaims,
    technicalKind: technical?.evidence.some(e => hasCardEvidence(e) && e.kind === "code") ? "code" : "formula",
    relation: family === "relation" ? relation : null,
    objects: family === "sparse" && !missingEvidence ? objects : [],
    omitted: claims.length - visibleClaims.length,
    missingEvidence, unresolvedRelations,
    review: structure.status.quality === "needs_review" || structure.status.processing !== "succeeded" || structure.status.decision !== "retain",
  } as const;
}
