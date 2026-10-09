import type { ConceptCard, KnowledgeStructureView } from "../../api/contracts";

// 關係必須有可回查的 canonical Evidence；不從概念名稱推測因果或流程。
export function cardRelation(card: ConceptCard, structure: KnowledgeStructureView) {
  const evidence = new Map(structure.concepts.flatMap(c => c.claims.flatMap(claim => claim.evidence)).map(e => [e.evidence_id, e]));
  for (const relation of structure.relations) {
    if (relation.source_concept_id !== card.concept_id && relation.target_concept_id !== card.concept_id) continue;
    const source = structure.concepts.find(c => c.concept_id === relation.source_concept_id);
    const target = structure.concepts.find(c => c.concept_id === relation.target_concept_id);
    if (source && target && relation.evidence_refs.length && relation.evidence_refs.every(id => evidence.has(id))) {
      return { relation, source, target, evidence: relation.evidence_refs.map(id => evidence.get(id)!) };
    }
  }
  return null;
}

