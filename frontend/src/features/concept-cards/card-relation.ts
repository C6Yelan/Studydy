import type { ConceptCard, EvidenceView, KnowledgeStructureView, RelationType } from "../../api/contracts";
import { textUnits, wrapCardText } from "./card-text.ts";

export function hasCardEvidence(evidence: EvidenceView) {
  return !!evidence.quote.trim() && evidence.page > 0 && !!evidence.source_locator.block_id;
}

// 只承接原文明示的比較面向；缺少結構化比較軸時寧可不畫對照圖。
function comparisonAxis(reason: string) {
  return reason.match(/(?:比較|對照)(?:軸|的是|面向)[：:\s]*([^。；;\n]+)/u)?.[1]?.trim()
    ?? reason.match(/comparison (?:axis|dimension):\s*([^.;\n]+)/i)?.[1]?.trim() ?? null;
}

const typeOrder: Record<RelationType, number> = { part_of: 0, example: 1, application: 2, contrast: 3, prerequisite: 4 };

export function cardRelations(card: ConceptCard, structure: KnowledgeStructureView) {
  const evidence = new Map(structure.concepts.flatMap(c => c.claims.flatMap(claim => claim.evidence)).filter(hasCardEvidence).map(e => [e.evidence_id, e]));
  return structure.relations.flatMap(relation => {
    if (relation.source_concept_id !== card.concept_id && relation.target_concept_id !== card.concept_id) return [];
    if (relation.source_concept_id === relation.target_concept_id || !relation.learner_reason.trim()) return [];
    const source = structure.concepts.find(c => c.concept_id === relation.source_concept_id);
    const target = structure.concepts.find(c => c.concept_id === relation.target_concept_id);
    if (!source || !target || !relation.evidence_refs.length || !relation.evidence_refs.every(id => evidence.has(id))) return [];
    return [{ relation, source, target, evidence: relation.evidence_refs.map(id => evidence.get(id)!), axis: comparisonAxis(relation.learner_reason) }];
  }).sort((a, b) =>
    // 先選本卡作為起點的關係，再按圖像可表達性及文字容量排序；同分用 canonical ID。
    Number(a.source.concept_id !== card.concept_id) - Number(b.source.concept_id !== card.concept_id)
    || typeOrder[a.relation.type] - typeOrder[b.relation.type]
    || textUnits(a.relation.learner_reason) - textUnits(b.relation.learner_reason)
    || (a.relation.relation_id < b.relation.relation_id ? -1 : a.relation.relation_id > b.relation.relation_id ? 1 : 0));
}

export function cardRelation(card: ConceptCard, structure: KnowledgeStructureView) {
  return cardRelations(card, structure).find(({ relation, source, target, axis }) =>
    (relation.type !== "contrast" || axis !== null)
    && [source.label, target.label].every(label => {
      const fit = wrapCardText(label, relation.type === "application" ? 9 : 11, textUnits);
      return !fit.overflow && fit.lines.length <= 3;
    })
    && textUnits(relation.learner_reason) <= 105) ?? null;
}
