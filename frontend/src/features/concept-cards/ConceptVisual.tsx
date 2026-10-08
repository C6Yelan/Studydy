import type { ConceptCard, KnowledgeStructureView, RelationType } from "../../api/contracts";
import { claimText } from "../../ui/claim-text";

const relationLabels: Record<RelationType, [string, string, string]> = {
  prerequisite: ["先備關係", "先備知識", "學習目標"],
  part_of: ["組成關係", "部分", "整體"],
  application: ["應用關係", "概念", "具體用途"],
  example: ["實例關係", "抽象概念", "具體實例"],
  contrast: ["對照關係", "概念 A", "概念 B"],
};

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

function lines(text: string, width: number, count: number) {
  const result: string[] = [];
  let line = "", size = 0;
  for (const char of text) {
    const unit = /[\u0000-\u007f]/.test(char) ? 0.55 : 1;
    if (char === "\n" || size + unit > width) { result.push(line); line = ""; size = 0; }
    if (char !== "\n") { line += char; size += unit; }
  }
  result.push(line);
  const clipped = result.length > count;
  return { rows: result.slice(0, count), clipped };
}

function Label({ text, x, y }: { text: string; x: number; y: number }) {
  const { rows, clipped } = lines(text, 7, 3);
  return <text x={x} y={y} textAnchor="middle" fontSize="20" fontWeight="600" fill="#193858">
    {rows.map((row, i) => <tspan key={i} x={x} dy={i ? 26 : 0}>{row}{clipped && i === rows.length - 1 ? "…" : ""}</tspan>)}
  </text>;
}

export function ConceptVisual({ card, structure }: { card: ConceptCard; structure: KnowledgeStructureView }) {
  const relation = cardRelation(card, structure);
  const literal = card.claims.find(c => c.evidence.some(e => e.kind === "code" || e.kind === "formula"));
  const code = literal && literal.evidence.some(e => e.kind === "code");
  const excerpt = literal ? lines(claimText(literal), 17, 4) : null;
  const kind = literal ? (code ? "code" : "formula") : relation ? relation.relation.type : "structure";
  const title = literal ? (code ? "程式碼重點" : "公式重點") : relation ? relationLabels[relation.relation.type][0] : "概念筆記";
  const description = literal ? claimText(literal) : relation ? `${relation.source.label}／${relation.target.label}：${relation.relation.learner_reason}` : `${card.label}的教材重點組成，不表示時間或因果順序。`;
  return <svg className="concept-visual" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 248" role="img"
    aria-label={`${card.label}：${title}`} data-visual-kind={kind} data-relation-id={!literal ? relation?.relation.relation_id : undefined}
    fontFamily="system-ui, sans-serif">
    <title>{card.label}：{title}</title><desc>{description}</desc>
    <rect width="400" height="248" rx="20" fill={literal ? "#142f47" : "#eff6fc"} />
    <text x="22" y="29" fontSize="15" fontWeight="600" fill={literal ? "#b5d9ef" : "#42698b"}>{title}</text>
    {literal && excerpt ? <>
      <circle cx="330" cy="24" r="4" fill="#ecaa79" /><circle cx="345" cy="24" r="4" fill="#edd28b" /><circle cx="360" cy="24" r="4" fill="#8ecab8" />
      <path d="M22 44H378" stroke="#38566e" />
      <text x="22" y="83" fill="#eff8ff" fontFamily="monospace" fontSize="21" xmlSpace="preserve">
        {excerpt.rows.map((row, i) => <tspan x="22" dy={i ? 30 : 0} key={i}>{row}</tspan>)}
      </text>
      <text x="22" y="225" fontSize="14" fill="#b5d9ef">{excerpt.clipped ? "節錄 · 完整內容與條件見背面" : "教材原文 · 完整重點與來源見背面"}</text>
    </> : relation ? <>
      <rect x="18" y="56" width="157" height="137" rx="16" fill="#fff" stroke="#b1cbe2" strokeWidth="2" />
      <rect x="225" y="56" width="157" height="137" rx="16" fill="#dceeea" stroke="#a2cabc" strokeWidth="2" />
      <text x="96" y="81" textAnchor="middle" fontSize="14" fill="#42698b">{relationLabels[relation.relation.type][1]}</text>
      <text x="304" y="81" textAnchor="middle" fontSize="14" fill="#386258">{relationLabels[relation.relation.type][2]}</text>
      <Label text={relation.source.label} x={96} y={115} /><Label text={relation.target.label} x={304} y={115} />
      <path d="M177 128H223" stroke="#557993" strokeWidth="2" strokeDasharray={relation.relation.type === "prerequisite" ? "4 4" : undefined} />
      {relation.relation.type !== "contrast" && relation.relation.type !== "prerequisite" && <path d="m216 123 7 5-7 5" fill="none" stroke="#557993" strokeWidth="2" />}
      <text x="200" y="224" textAnchor="middle" fontSize="14" fill="#42698b">{relation.relation.type === "prerequisite" ? "學習依賴 · 非時間流程" : relation.relation.type === "contrast" ? "教材中的對照 · 不推定優劣" : "教材中的關係 · 說明與來源見背面"}</text>
    </> : <>
      <rect x="20" y="55" width="130" height="140" rx="12" fill="white" stroke="#a2bdd2" />
      <Label text={card.label} x={85} y={105} />
      <path d="M150 125H170M170 80V170M170 80H188M170 125H188M170 170H188" fill="none" stroke="#718ea5" strokeWidth="2" />
      {card.claims.slice(0,3).map((claim,index)=><g key={claim.claim_id}>
        <rect x="188" y={60+index*45} width="194" height="38" rx="8" fill="#fff" stroke="#b1cbe2" />
        <text x="199" y={84+index*45} fontSize="14" fill="#193858">{lines(claimText(claim),12,1).rows[0]}…</text>
      </g>)}
      <text x="200" y="226" textAnchor="middle" fontSize="14" fill="#42698b">概念與教材重點</text>
    </>}
    {structure.status.quality === "needs_review" && <text x="378" y="244" textAnchor="end" fontSize="12" fill={literal ? "#f6d59e" : "#805719"}>待確認</text>}
  </svg>;
}
