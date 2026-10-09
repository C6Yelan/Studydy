import { useRef } from "react";
import type { ConceptCard, KnowledgeStructureView } from "../../api/contracts";
import { claimText } from "../../ui/claim-text";
import { cardRelation } from "./card-relation";

const relationLabels = { prerequisite: "先備", part_of: "組成", application: "應用", example: "實例", contrast: "對照" };

// 僅節錄保存的 Claim，不改寫語意；來源只對應實際顯示的重點與關係。
export function visualCardModel(card: ConceptCard, structure: KnowledgeStructureView, materialName: string) {
  const claims = card.claims.filter(c => claimText(c).trim());
  const summary = claims[0];
  const points = claims.slice(1, 5);
  const relation = cardRelation(card, structure);
  const evidence = [...claims.slice(0, 5).flatMap(c => c.evidence), ...(relation?.evidence ?? [])];
  const sources = [...new Set(evidence.map(e => `${e.source_name || materialName} · 第 ${e.normalized_page ?? e.page} 頁`))];
  const review = structure.status.quality === "needs_review" || structure.status.decision !== "retain"
    || structure.status.processing !== "succeeded" || claims.some(c => !c.evidence.length);
  return { title: card.label, summary: summary ? claimText(summary) : "尚無概念重點", points: points.map(claimText),
    sources: sources.length ? sources : [`${materialName} · 來源待確認`], review: review || !sources.length,
    relation: relation ? `${relationLabels[relation.relation.type]}：${relation.source.label} ${relation.relation.type === "contrast" ? "↔" : "→"} ${relation.target.label}` : null };
}

// 以字形寬度的保守上界分行，包含長英文、換行與中文；所有裁切都明示節錄。
function TextBlock({ text, x, y, width, rows, size = 22, fill = "#203b48", center = false }: {
  text: string; x: number; y: number; width: number; rows: number; size?: number; fill?: string; center?: boolean;
}) {
  const lines: string[] = [];
  let line = "", units = 0;
  for (const char of text.replace(/\s+/g, " ").trim()) {
    const unit = /[\x20-\x7e]/.test(char) ? 0.75 : 1.05;
    if ((units + unit) * size > width) { lines.push(line); line = ""; units = 0; }
    line += char; units += unit;
  }
  if (line) lines.push(line);
  const clipped = lines.length > rows;
  const visible = lines.slice(0, rows);
  if (clipped) visible[rows - 1] = Array.from(visible[rows - 1]).slice(0, -5).join("") + "…（節錄）";
  return <text x={x} y={y} fontSize={size} fill={fill} textAnchor={center ? "middle" : "start"}>
    {visible.map((value, i) => <tspan key={i} x={x} dy={i ? size * 1.4 : 0}>{value}</tspan>)}
  </text>;
}

export function VisualConceptCard({ card, structure, materialName, onDetails }: {
  card: ConceptCard; structure: KnowledgeStructureView; materialName: string; onDetails: () => void;
}) {
  const svg = useRef<SVGSVGElement>(null);
  const model = visualCardModel(card, structure, materialName);
  const download = () => {
    if (!svg.current) return;
    const data = new XMLSerializer().serializeToString(svg.current);
    const url = URL.createObjectURL(new Blob([data], { type: "image/svg+xml;charset=utf-8" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = `${card.label.replace(/[\\/:*?"<>|\u0000-\u001f]/g, "_").slice(0, 80) || "概念卡"}.svg`;
    link.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  const positions = [{ x: 38, y: 205 }, { x: 802, y: 205 }, { x: 38, y: 435 }, { x: 802, y: 435 }];
  return <div className="visual-card">
    <svg ref={svg} className="visual-concept-card" xmlns="http://www.w3.org/2000/svg" width="1200" height="900" viewBox="0 0 1200 900"
      role="img" aria-label={`${card.label}視覺化概念卡`} fontFamily="system-ui, sans-serif">
      <title>{`${card.label}｜概念卡`}</title>
      <desc>{[model.summary, ...model.points, model.relation, ...model.sources].filter(Boolean).join("\n")}</desc>
      <rect width="1200" height="900" rx="32" fill="#f6f3e9" />
      <path d="M38 148H1162M38 745H1162" stroke="#cbd5cc" strokeWidth="2" />
      <text x="40" y="48" fontSize="17" fill="#42665d" letterSpacing="3">STUDYDY / 概念卡</text>
      <TextBlock text={model.title} x={40} y={98} width={875} rows={1} size={34} />
      {model.review && <g aria-label="待確認"><rect x="964" y="65" width="196" height="44" rx="22" fill="#f6ddb0" /><text x="1062" y="94" textAnchor="middle" fontSize="21" fill="#724514">待確認</text></g>}
      <circle cx="600" cy="402" r="202" fill="none" stroke="#dce3d9" strokeWidth="1.5" strokeDasharray="4 10" />
      {model.points.map((point, i) => {
        const { x, y } = positions[i];
        return <g key={i}>
          <path d={`M${x < 600 ? x + 360 : x} ${y + 90} Q600 ${y + 90} 600 402`} fill="none" stroke="#96b8a7" strokeWidth="3" />
          <rect x={x} y={y} width="360" height="180" rx="24" fill={i % 2 ? "#e2eee5" : "#fffdf7"} stroke="#b4cabc" />
          <circle cx={x + 30} cy={y + 30} r="14" fill="#365f52" /><text x={x + 30} y={y + 36} fontSize="16" fill="white" textAnchor="middle">{i + 1}</text>
          <TextBlock text={point} x={x + 24} y={y + 70} width={310} rows={4} size={20} />
        </g>;
      })}
      <circle cx="600" cy="402" r="174" fill="#214e43" />
      <g fill="none" stroke="#b9dbc3" strokeWidth="2.5"><circle cx="600" cy="285" r="8" /><circle cx="579" cy="310" r="6" /><circle cx="621" cy="310" r="6" /><path d="m595 292-12 13m22-13 12 13M585 310h30" /></g>
      <TextBlock text={model.title} x={600} y={354} width={275} rows={2} size={27} fill="#fffef4" center />
      <TextBlock text={model.summary} x={600} y={443} width={270} rows={3} size={20} fill="#d8ebde" center />
      {model.relation && <g><rect x="100" y="652" width="1000" height="62" rx="31" fill="#e3e8d7" /><TextBlock text={model.relation} x={600} y={690} width={940} rows={1} size={21} center /></g>}
      <text x="40" y="779" fontSize="16" fill="#42665d">教材來源{model.review ? " · 內容／來源待確認" : ""}</text>
      <TextBlock text={model.sources.join("；")} x={40} y={811} width={1115} rows={2} size={19} />
      <text x="40" y="878" fontSize="15" fill="#5c7168">教材節錄 · 完整條件與來源請回卡組查看</text>
    </svg>
    <div className="visual-card-actions"><button type="button" className="secondary-button" onClick={download}>下載 SVG</button>
      <button type="button" className="text-button" onClick={onDetails}>完整重點與來源</button></div>
  </div>;
}
