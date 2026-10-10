import { createElement } from "react";
import type { ConceptCard, KnowledgeStructureView } from "../../api/contracts";
import { claimText } from "../../ui/claim-text";
import { hasCardEvidence } from "./card-relation";
import { selectConceptCardLayout } from "./concept-card-layout";
import { textUnits, wrapCardText } from "./card-text";

import shapes from "./icons/shapes.json";

const geometry = shapes as unknown as Record<string, [string, Record<string, string>][] >;

const FONT = '"Noto Sans CJK TC", "Microsoft JhengHei", system-ui, sans-serif';
const MONO = 'ui-monospace, "SFMono-Regular", Consolas, monospace';
const ink = "#173d52", muted = "#526f7c", blue = "#256495", green = "#23695d";
let measureContext: CanvasRenderingContext2D | null = null;
function measure(text: string, size: number, mono = false, weight = 400) {
  measureContext ??= document.createElement("canvas").getContext("2d");
  measureContext!.font = `${weight} ${size}px ${mono ? MONO : FONT}`;
  return measureContext!.measureText(text).width;
}

function fontMetrics(size: number, weight = 400) {
  measure("國Mg", size, false, weight);
  const metrics = measureContext!.measureText("國Mg");
  return { ascent: metrics.fontBoundingBoxAscent, descent: metrics.fontBoundingBoxDescent };
}

type TextProps = { text: string; x: number; y: number; width: number; height: number; size?: number; minSize?: number; fill?: string; bold?: boolean; center?: boolean; notice?: string };

// 每段文字有自己的可用區域；節錄提示佔用獨立空間，不把省略符插入教材原文。
function CardText({ text, x, y, width, height, size = 30, minSize = size, fill = ink, bold = false, center = false, notice = "原文節錄" }: TextProps) {
  let fontSize = size;
  let metrics = fontMetrics(fontSize, bold ? 600 : 400);
  let lineHeight = Math.max(fontSize * 1.4, metrics.ascent + metrics.descent);
  let wrapped = wrapCardText(text, width - 4, t => measure(t, fontSize, false, bold ? 600 : 400));
  while (fontSize > minSize && (wrapped.overflow || wrapped.lines.length * lineHeight > height - 6)) {
    fontSize = Math.max(minSize, fontSize - 2);
    metrics = fontMetrics(fontSize, bold ? 600 : 400);
    lineHeight = Math.max(fontSize * 1.4, metrics.ascent + metrics.descent);
    wrapped = wrapCardText(text, width - 4, t => measure(t, fontSize, false, bold ? 600 : 400));
  }
  const clipped = wrapped.overflow || wrapped.lines.length * lineHeight > height - 6;
  const noticeMetrics = fontMetrics(18);
  const rows = Math.max(0, Math.floor((height - 6 - (clipped ? noticeMetrics.ascent + noticeMetrics.descent + 6 : 0)) / lineHeight));
  const anchor = center ? x + width / 2 : x + 2;
  return <g data-text-region={JSON.stringify({ x, y, width, height })} data-excerpt={clipped || undefined}>
    <text x={anchor} y={y + metrics.ascent + 3} textAnchor={center ? "middle" : "start"} fontSize={fontSize} fontWeight={bold ? 600 : 400} fill={fill}>
      {wrapped.lines.slice(0, rows).map((line, i) => <tspan key={i} x={anchor} y={y + metrics.ascent + 3 + i * lineHeight}>{line}</tspan>)}
    </text>
    {clipped && <text x={anchor} y={y + height - noticeMetrics.descent - 3} textAnchor={center ? "middle" : "start"} fontSize="18" fill={fill}>{notice}</text>}
  </g>;
}

function ObjectIcon({ kind, x, y, size = 180 }: { kind: string; x: number; y: number; size?: number }) {
  return <g transform={`translate(${x} ${y}) scale(${size / 24})`} fill="none" stroke={blue} color={blue} strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    {geometry[kind].map(([tag, attrs], i) => createElement(tag, { ...attrs, key: i }))}
  </g>;
}

type Layout = ReturnType<typeof selectConceptCardLayout>;
// 量測內容自身高度供卡內置中；外框維持固定，避免切卡時操作列跳動。
function textHeight(text: string, width: number, size: number) {
  const metrics = fontMetrics(size);
  const lines = wrapCardText(text, width - 4, t => measure(t, size)).lines.length;
  return Math.ceil(lines * Math.max(size * 1.4, metrics.ascent + metrics.descent) + 6);
}

function sparseFrame(layout: Layout) {
  const primary = layout.objects.find(object => object.role !== "support");
  const objects = primary ? [primary, ...layout.objects.filter(object => object !== primary)].slice(0, 3) : [];
  const mode = !primary ? "prose" : layout.claims.length === 1 && (objects.length === 1 || textUnits(claimText(layout.claims[0])) > 160 || claimText(layout.claims[0]).includes("\n")) ? "illustration" : "gallery";
  const size = mode === "illustration" ? 42 : mode === "gallery" ? 32 : layout.claims.length === 1 ? 44 : 36;
  const width = mode === "illustration" ? 748 : mode === "gallery" ? 1080 : 1024;
  const padding = mode === "gallery" ? 0 : 48;
  const gap = 18;
  let top = mode === "gallery" ? 492 : 220;
  const maxHeight = (746 - top - gap * Math.max(0, layout.claims.length - 1)) / Math.max(1, layout.claims.length);
  const regions = layout.claims.map(claim => {
    const height = Math.min(maxHeight, Math.max(mode === "illustration" ? 270 : mode === "gallery" ? 54 : 110, textHeight(claimText(claim), width, size) + padding));
    const region = { top, height };
    top += height + gap;
    return region;
  });
  const bottom = regions.length ? top - gap : 330;
  return { mode, objects, size, width, regions, height: Math.ceil(bottom + 2) };
}

function Sparse({ layout, frame }: { layout: Layout; frame: ReturnType<typeof sparseFrame> }) {
  const { objects, regions, size, width } = frame;
  if (frame.mode === "illustration") {
    const object = objects[0], region = regions[0], claim = layout.claims[0];
    const iconTop = region.top + (region.height - 234) / 2;
    const contentHeight = Math.min(region.height - 48, textHeight(claimText(claim), width, size));
    return <g data-diagram="illustrated-concept">
      <g data-object={object.kind} data-claim-id={object.claimId} data-evidence-id={object.evidenceId} data-icon-role={object.role}>
        <rect x="48" y={region.top} width="294" height={region.height} rx="32" fill="#e1ecf5" />
        <ObjectIcon kind={object.kind} x={105} y={iconTop} size={180} />
        <CardText text={object.label} x={68} y={iconTop + 190} width={254} height={44} size={28} minSize={26} bold center />
      </g>
      <g data-claim-id={claim.claim_id}><CardText text={claimText(claim)} x={390} y={region.top + (region.height - contentHeight) / 2} width={width} height={contentHeight} size={size} minSize={30} /></g>
    </g>;
  }
  if (frame.mode === "gallery") return <g data-diagram="supported-icons">
    {objects.map((object, i) => {
      const cx = 600 + (i - (objects.length - 1) / 2) * (objects.length === 2 ? 440 : 345);
      return <g key={object.kind} data-object={object.kind} data-claim-id={object.claimId} data-evidence-id={object.evidenceId} data-icon-role={object.role}>
        <circle cx={cx} cy="310" r="90" fill={i % 2 ? "#e0ede5" : "#e1ecf5"} />
        <ObjectIcon kind={object.kind} x={cx - 72} y={238} size={144} />
        <CardText text={object.label} x={cx - 150} y={410} width={300} height={60} size={30} minSize={26} bold center />
      </g>;
    })}
    {layout.claims.map((claim, i) => <g key={claim.claim_id} data-claim-id={claim.claim_id}>
      {i > 0 && <path d={`M48 ${regions[i].top - 9}H1152`} stroke="#becfce" />}
      <CardText text={claimText(claim)} x={60} y={regions[i].top} width={width} height={regions[i].height} size={size} minSize={28} />
    </g>)}
  </g>;
  return <g data-diagram="concept-properties">
    {layout.claims.map((claim, i) => <g key={claim.claim_id} data-claim-id={claim.claim_id}>
      <rect x="48" y={regions[i].top} width="1104" height={regions[i].height} rx="28" fill={i % 2 ? "#e0ede5" : "#e3edf5"} />
      <CardText text={claimText(claim)} x={88} y={regions[i].top + 24} width={width} height={regions[i].height - 48} size={size} minSize={30} />
    </g>)}
    {!layout.claims.length && <CardText text="尚無可呈現的教材重點。" x={88} y={244} width={1024} height={70} size={38} />}
  </g>;
}

function compactRelationFrame(layout: Layout) {
  const { relation, axis } = layout.relation!;
  const diagramBottom = relation.type === "part_of" ? 390 : 358;
  const axisHeight = relation.type === "contrast" ? textHeight(`對照：${axis}`, 1104, 23) : 0;
  const reasonY = diagramBottom + 18 + (axisHeight ? axisHeight + 12 : 0);
  const reasonHeight = textHeight(relation.learner_reason, 1104, 25);
  let top = reasonY + reasonHeight + 18;
  const regions = layout.claims.map(claim => {
    const height = Math.max(90, textHeight(claimText(claim), 1008, 34) + 36);
    const region = { top, height };
    top += height + 14;
    return region;
  });
  const bottom = regions.length ? top - 14 : reasonY + reasonHeight;
  const height = Math.ceil(bottom + (layout.omitted ? 18 : 2));
  return height <= 760 ? { height, regions, reasonY, reasonHeight, axisHeight, diagramBottom } : null;
}

function Points({ layout }: { layout: Layout }) {
  const count = layout.claims.length;
  const regions = count <= 2 ? [{ x: 48, y: 280, w: 535, h: 416 }, { x: 617, y: 280, w: 535, h: 416 }]
    : count === 3 ? [{ x: 48, y: 235, w: 490, h: 215 }, { x: 662, y: 235, w: 490, h: 215 }, { x: 355, y: 516, w: 490, h: 215 }]
    : [{ x: 48, y: 241, w: 535, h: 216 }, { x: 617, y: 241, w: 535, h: 216 }, { x: 48, y: 491, w: 535, h: 216 }, { x: 617, y: 491, w: 535, h: 216 }];
  return <g data-diagram={`points-${count}`}>
    <path d={count === 3 ? "M293 450 600 482 907 450M600 482V516" : "M315 465H885M600 420V505"} fill="none" stroke="#9fb9c5" strokeWidth="3" />
    {count === 3 && <g><circle cx="600" cy="482" r="23" fill="#f8f7f0" stroke="#9fb9c5" /><text x="600" y="489" textAnchor="middle" fontSize="17" fill={muted}>·</text></g>}
    {layout.claims.map((claim, i) => {
      const r = regions[i];
      const object = layout.objects.find(o => o.claimId === claim.claim_id);
      return <g key={claim.claim_id} data-claim-id={claim.claim_id}>
        <path d={`M${r.x + 30} ${r.y}H${r.x + r.w - 30}Q${r.x + r.w} ${r.y} ${r.x + r.w} ${r.y + 30}V${r.y + r.h}H${r.x}V${r.y + 30}Q${r.x} ${r.y} ${r.x + 30} ${r.y}`} fill={i % 2 ? "#e0ede5" : "#e3edf5"} />
        {object ? <g data-object={object.kind} data-claim-id={object.claimId} data-evidence-id={object.evidenceId}><ObjectIcon kind={object.kind} x={r.x + 15} y={r.y + 26} size={48} /></g> : <text x={r.x + 22} y={r.y + 47} fill={i % 2 ? green : blue} fontSize="32" fontWeight="600">{String(i + 1).padStart(2, "0")}</text>}
        <CardText text={claimText(claim)} x={r.x + 76} y={r.y + 20} width={r.w - 102} height={r.h - 38} size={30} minSize={26} />
      </g>;
    })}
  </g>;
}

function Relation({ layout, conceptId, compact }: { layout: Layout; conceptId: string; compact: ReturnType<typeof compactRelationFrame> }) {
  const { relation, source, target, axis } = layout.relation!;
  const left = layout.claims.length ? 48 : 395;
  const width = 410;
  const current = (id: string) => id === conceptId;
  const label = (text: string, x: number, y: number, w: number, height = 78) =>
    <CardText text={text} x={x} y={y} width={w} height={height} size={34} minSize={28} bold center />;
  const node = (side: "source" | "target", y: number, caption: string, x = left, w = width) => {
    const concept = side === "source" ? source : target;
    return <g data-concept-id={concept.concept_id}>
      <rect x={x} y={y} width={w} height="138" rx="24" fill={current(concept.concept_id) ? "#dceaf5" : "#e0ede5"} stroke={current(concept.concept_id) ? blue : "#a5bdb3"} strokeWidth="2" />
      <text x={x + 22} y={y + 30} fontSize="21" fill={muted}>{caption}</text>
      {label(concept.label, x + 22, y + 44, w - 44)}
    </g>;
  };
  const captions = relation.type === "prerequisite" ? ["先備知識", "學習目標"]
    : relation.type === "example" ? ["抽象概念", "教材實例"]
    : relation.type === "contrast" ? ["對照概念", "對照概念"] : ["概念", "具體用途"];
  return <g data-diagram={relation.type} data-density={compact ? "compact" : "full"} data-relation-id={relation.relation_id} data-source-concept-id={source.concept_id} data-target-concept-id={target.concept_id}>
    {relation.type === "part_of" ? compact ? <>
      <rect x="48" y="220" width="1104" height="170" rx="28" fill="#e0ede5" stroke="#93b3a8" strokeWidth="2" />
      <text x="72" y="254" fontSize="21" fill={muted}>整體</text>
      {label(target.label, 72, 269, 500)}
      <rect x="650" y="244" width="470" height="120" rx="24" fill="#e0edf7" stroke={blue} strokeWidth="2" />
      <text x="672" y="274" fontSize="21" fill={muted}>組成部分</text>
      {label(source.label, 672, 286, 426, 70)}
    </> : <>
      <rect x={left} y="232" width={width} height="334" rx="30" fill="#e0ede5" stroke="#93b3a8" strokeWidth="2" />
      <text x={left + 22} y="266" fontSize="21" fill={muted}>整體</text>
      {label(target.label, left + 22, 279, width - 44)}
      <rect x={left + 28} y="387" width={width - 56} height="150" rx="24" fill="#e0edf7" stroke={blue} strokeWidth="2" />
      <text x={left + 48} y="420" fontSize="21" fill={muted}>組成部分</text>
      {label(source.label, left + 48, 433, width - 96)}
    </> : <>
      {compact ? node("source", 220, captions[0], 48, 435) : node("source", 230, captions[0])}
      {relation.type === "contrast"
        ? <CardText text={`對照：${axis}`} x={compact ? 48 : left} y={compact ? compact.diagramBottom + 18 : 378} width={compact ? 1104 : width} height={compact ? compact.axisHeight : 50} size={23} minSize={21} center />
        : <>
          <path d={compact ? "M515 289H671m-12-12 12 12-12 12" : `M${left + width / 2} 380V420m-12-12 12 12 12-12`} fill="none" stroke={blue} strokeWidth="3" strokeDasharray={relation.type === "prerequisite" ? "7 5" : undefined} />
          {relation.type === "prerequisite" && <text x={compact ? 550 : left + width / 2 + 24} y={compact ? 265 : 405} fontSize="20" fill={muted}>學習先備</text>}
        </>}
      {compact ? node("target", 220, captions[1], 717, 435) : node("target", 435, captions[1])}
    </>}
    <CardText text={relation.learner_reason} x={compact ? 48 : left} y={compact ? compact.reasonY : 596} width={compact ? 1104 : width} height={compact ? compact.reasonHeight : 150} size={25} />
    {/* 關係保留獨立圖區，重點緊鄰呈現；不把各 Claim 畫成流程或因果。 */}
    {layout.claims.map((claim, i) => {
      const gap = 14;
      const height = compact ? compact.regions[i].height : (526 - gap * (layout.claims.length - 1)) / layout.claims.length;
      const top = compact ? compact.regions[i].top : 220 + i * (height + gap);
      const x = compact ? 48 : 500, w = compact ? 1104 : 652;
      const object = layout.objects.find(o => o.claimId === claim.claim_id);
      return <g key={claim.claim_id} data-claim-id={claim.claim_id} data-relation-point="true">
        <rect x={x} y={top} width={w} height={height} rx="24" fill={i % 2 ? "#e0ede5" : "#e3edf5"} />
        {object ? <g data-object={object.kind} data-claim-id={claim.claim_id} data-evidence-id={object.evidenceId}><ObjectIcon kind={object.kind} x={x + 16} y={top + 20} size={42} /></g>
          : <text x={x + 20} y={top + 47} fontSize="26" fill={blue}>{String(i + 1).padStart(2, "0")}</text>}
        <CardText text={claimText(claim)} x={x + 74} y={top + 18} width={w - 96} height={height - 36} size={compact ? 34 : layout.claims.length === 1 ? 38 : 30} minSize={28} />
      </g>;
    })}
  </g>;
}

function Technical({ layout }: { layout: Layout }) {
  const claim = layout.claims[0];
  const raw = claimText(claim);
  const lines = raw.split(/\r?\n/);
  const code = layout.technicalKind === "code";
  let size = code ? 32 : 48;
  while (size > 24 && (lines.length * size * 1.45 > 375 || lines.some(line => measure(line, size, true) > 975))) size -= 2;
  const capacity = Math.floor(375 / (size * 1.45));
  // 技術內容只取完整的前綴行，絕不在行內折斷運算子、識別符或條件。
  const firstUnfit = lines.findIndex(line => measure(line, size, true) > 975);
  const shown = lines.slice(0, Math.min(capacity, firstUnfit < 0 ? lines.length : firstUnfit));
  const clipped = shown.length < lines.length;
  return <g data-diagram={layout.technicalKind} data-claim-id={claim.claim_id}>
    <rect x="48" y="224" width="1104" height="510" rx="28" fill={code ? "#18394d" : "#e3edf5"} />
    <text x="82" y="270" fontSize="23" fill={code ? "#b9dce9" : blue}>{code ? "程式碼" : "公式"}</text>
    <path d="M80 293H1120" stroke={code ? "#456473" : "#b6cbd7"} />
    <g data-text-region={JSON.stringify({ x: 118, y: 310, width: 995, height: 380 })}>
      <text x="126" y={320 + size} fontSize={size} fontFamily={MONO} xmlSpace="preserve" style={{ whiteSpace: "pre" }} fill={code ? "#f4fbff" : ink}>
        {shown.map((line, i) => <tspan data-technical-line="true" x="126" y={320 + size + i * size * 1.45} key={i}>{line}</tspan>)}
      </text>
    </g>
    {shown.map((_, i) => <text key={i} x="91" y={320 + size + i * size * 1.45} fontSize="18" textAnchor="end" fill={code ? "#8cbdca" : muted}>{i + 1}</text>)}
    {!shown.length && <CardText text="原文單行超出圖卡容量。" x={126} y={371} width={920} height={140} size={32} fill={code ? "#f4fbff" : ink} />}
    <text x="82" y="713" fontSize="20" fill={code ? "#b9dce9" : blue}>{clipped ? `原文節錄 · 僅列完整前 ${shown.length} 行` : ""}</text>
  </g>;
}

export function ConceptCardArtwork({ card, structure, materialName, svgRef }: {
  card: ConceptCard; structure: KnowledgeStructureView; materialName: string; svgRef?: React.Ref<SVGSVGElement>;
}) {
  const layout = selectConceptCardLayout(card, structure);
  const sparse = layout.family === "sparse" ? sparseFrame(layout) : null;
  const compactRelation = layout.family === "relation" ? compactRelationFrame(layout) : null;
  const height = 760;
  const contentHeight = sparse?.height ?? compactRelation?.height ?? height;
  const contentOffset = -30 + (height - contentHeight) / 2;
  const evidence = [...card.claims.flatMap(c => c.evidence), ...(layout.relation?.evidence ?? [])].filter(hasCardEvidence);
  const sources = [...new Set(evidence.map(e => `${e.source_name || materialName || "教材名稱未提供"} · 第 ${e.normalized_page ?? e.page} 頁`))];
  const primary = layout.family === "relation" ? layout.objects.find(o => o.role === "concept") : undefined;
  // 語系與字型渲染跟著 SVG 匯出，避免獨立圖片沿用不同的 CJK 字形／標點規則。
  return <svg ref={svgRef} className="visual-concept-card" xmlns="http://www.w3.org/2000/svg" width="1200" height={height} viewBox={`0 0 1200 ${height}`}
    textRendering="optimizeLegibility" style={{ fontSynthesis: "none" }} lang="zh-Hant" xmlLang="zh-Hant" role="img" aria-label={`${card.label}視覺化概念卡`} fontFamily={FONT} data-layout={layout.family} data-renderer-policy={layout.policy}>
    <title>{`${card.label}｜概念卡`}</title>
    <desc>{[...card.claims.map(claimText), layout.relation?.relation.learner_reason, ...sources].filter(Boolean).join("\n")}</desc>
    <metadata>{JSON.stringify({ policy: layout.policy, sources, needsReview: layout.review, missingEvidence: layout.missingEvidence, unresolvedRelations: layout.unresolvedRelations, icons: layout.objects.map(o => ({ icon: o.kind, claimId: o.claimId, evidenceId: o.evidenceId })), attribution: "Tabler Icons (MIT), CC-CEDICT / MDBG (CC BY-SA 4.0); https://studydy.net/licenses/concept-icons.txt" })}</metadata>
    <rect width="1200" height={height} rx="28" fill="#f8f7f0" />
    <CardText text={card.label} x={48} y={42} width={primary ? 976 : 1104} height={109} size={48} minSize={38} bold notice="標題節錄" />
    {primary && <g data-object={primary.kind} data-claim-id={primary.claimId} data-evidence-id={primary.evidenceId}><ObjectIcon kind={primary.kind} x={1058} y={52} size={84} /></g>}
    <path d="M48 175H1152" stroke="#c8d4d2" />
    <g transform={`translate(0 ${contentOffset})`}>
    {layout.family === "technical" ? <Technical layout={layout} /> : layout.family === "relation" ? <Relation layout={layout} conceptId={card.concept_id} compact={compactRelation} /> : layout.family === "points" ? <Points layout={layout} /> : <Sparse layout={layout} frame={sparse!} />}
    </g>
    {!!layout.omitted && <text x="48" y={height - 16} fontSize="20" fill={muted}>另有 {layout.omitted} 則重點</text>}
  </svg>;
}
