import type { ConceptCard, KnowledgeStructureView } from "../../api/contracts";
import { claimText } from "../../ui/claim-text";
import { hasCardEvidence } from "./card-relation";
import { selectConceptCardLayout, type CardObject } from "./concept-card-layout";
import { wrapCardText } from "./card-text";

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
function CardText({ text, x, y, width, height, size = 30, minSize = size, fill = ink, bold = false, center = false, notice = "原文節錄 · 全文見背面" }: TextProps) {
  let fontSize = size;
  let metrics = fontMetrics(fontSize, bold ? 600 : 400);
  let lineHeight = Math.max(fontSize * 1.4, metrics.ascent + metrics.descent);
  let wrapped = wrapCardText(text, width - 4, t => measure(t, fontSize, false, bold ? 600 : 400));
  while (fontSize > minSize && (wrapped.overflow || wrapped.lines.length * lineHeight > height)) {
    fontSize = Math.max(minSize, fontSize - 2);
    metrics = fontMetrics(fontSize, bold ? 600 : 400);
    lineHeight = Math.max(fontSize * 1.4, metrics.ascent + metrics.descent);
    wrapped = wrapCardText(text, width - 4, t => measure(t, fontSize, false, bold ? 600 : 400));
  }
  const clipped = wrapped.overflow || wrapped.lines.length * lineHeight > height;
  const noticeMetrics = fontMetrics(18);
  const rows = Math.max(0, Math.floor((height - (clipped ? noticeMetrics.ascent + noticeMetrics.descent + 6 : 0)) / lineHeight));
  const anchor = center ? x + width / 2 : x + 2;
  return <g data-text-region={JSON.stringify({ x, y, width, height })} data-excerpt={clipped || undefined}>
    <text x={anchor} y={y + metrics.ascent} textAnchor={center ? "middle" : "start"} fontSize={fontSize} fontWeight={bold ? 600 : 400} fill={fill}>
      {wrapped.lines.slice(0, rows).map((line, i) => <tspan key={i} x={anchor} y={y + metrics.ascent + i * lineHeight}>{line}</tspan>)}
    </text>
    {clipped && <text x={anchor} y={y + height - noticeMetrics.descent} textAnchor={center ? "middle" : "start"} fontSize="18" fill={fill}>{notice}</text>}
  </g>;
}

function ObjectIcon({ kind, x, y, size = 180 }: { kind: CardObject["kind"] | "document"; x: number; y: number; size?: number }) {
  return <g transform={`translate(${x} ${y}) scale(${size / 160})`} fill="none" stroke={blue} strokeWidth="5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    {kind === "computer" ? <><rect x="8" y="16" width="144" height="98" rx="10" fill="#e2eff8" /><path d="M16 91H144M80 116V139M49 142H111" /><path d="M34 42H114M34 59H90" stroke="#77a4c1" /></>
      : kind === "mobile" ? <><rect x="40" y="5" width="80" height="150" rx="16" fill="#e2eff8" /><path d="M66 21H94M74 138H86" /><path d="M58 48H101M58 65H88M58 82H99" stroke="#77a4c1" /></>
      : kind === "server" ? <>{[12, 60, 108].map(top => <g key={top}><rect x="15" y={top} width="130" height="38" rx="8" fill="#e2eff8" /><circle cx="34" cy={top + 19} r="3" fill={green} /><path d={`M65 ${top + 19}H125`} /></g>)}</>
      : <><path d="M32 9H98L131 43V151H32Z" fill="#e2eff8" /><path d="M98 9V43H131M51 70H111M51 91H111M51 112H89" /></>}
  </g>;
}

type Layout = ReturnType<typeof selectConceptCardLayout>;
function Sparse({ layout }: { layout: Layout }) {
  if (layout.objects.length) {
    const objects = layout.objects;
    return <g data-diagram="supported-examples">
      <text x="48" y="236" fill={muted} fontSize="22">教材明示的例子</text>
      {objects.map((object, i) => {
        const cx = 600 + (i - (objects.length - 1) / 2) * (objects.length === 2 ? 440 : 345);
        return <g key={object.kind} data-object={object.kind} data-claim-id={object.claimId}>
          <circle cx={cx} cy="378" r="125" fill={i % 2 ? "#e0ede5" : "#e1ecf5"} />
          <ObjectIcon kind={object.kind} x={cx - 94} y={276} size={188} />
          <text x={cx} y="531" textAnchor="middle" fontSize="30" fontWeight="600" fill={ink}>{object.label}</text>
        </g>;
      })}
      {layout.claims.map((claim, i) => <g key={claim.claim_id} data-claim-id={claim.claim_id}>
        {i === 1 && <path d="M48 650H1152" stroke="#becfce" />}
        <CardText text={claimText(claim)} x={60} y={i === 0 ? 560 : 663} width={1080} height={85} size={30} minSize={28} />
      </g>)}
    </g>;
  }
  return <g data-diagram="concept-properties">
    <rect x="48" y="235" width="325" height="460" rx="100" fill="#e1ecf5" />
    <ObjectIcon kind="document" x={104} y={310} size={210} />
    <text x="210" y="592" textAnchor="middle" fontSize="25" fill={muted}>{layout.claims.length ? `${layout.claims.length} 則教材重點` : "尚無可用重點"}</text>
    <path d="M395 285H423V651H395" fill="none" stroke="#a0b8c7" strokeWidth="3" />
    {layout.claims.map((claim, i) => {
      const top = layout.claims.length === 1 ? 288 : 239 + i * 249;
      return <g key={claim.claim_id} data-claim-id={claim.claim_id}>
        <text x="472" y={top + 18} fontSize="19" letterSpacing="2" fill={blue}>教材摘錄 {String(i + 1).padStart(2, "0")}</text>
        <CardText text={claimText(claim)} x={472} y={top + 36} width={666} height={layout.claims.length === 1 ? 342 : 189} size={layout.claims.length === 1 ? 44 : 34} minSize={28} />
      </g>;
    })}
    {!layout.claims.length && <CardText text="此概念尚無可呈現的教材重點。請查看完整來源與教材版本狀態。" x={472} y={320} width={650} height={270} size={34} />}
  </g>;
}

function Points({ layout }: { layout: Layout }) {
  const count = layout.claims.length;
  const regions = count <= 2 ? [{ x: 48, y: 280, w: 535, h: 416 }, { x: 617, y: 280, w: 535, h: 416 }]
    : count === 3 ? [{ x: 48, y: 235, w: 490, h: 215 }, { x: 662, y: 235, w: 490, h: 215 }, { x: 355, y: 516, w: 490, h: 215 }]
    : [{ x: 48, y: 241, w: 535, h: 216 }, { x: 617, y: 241, w: 535, h: 216 }, { x: 48, y: 491, w: 535, h: 216 }, { x: 617, y: 491, w: 535, h: 216 }];
  return <g data-diagram={`points-${count}`}>
    <text x="48" y="218" fontSize="20" fill={muted}>同屬此概念的教材重點 · 連線不表示先後或因果</text>
    <path d={count === 3 ? "M293 450 600 482 907 450M600 482V516" : "M315 465H885M600 420V505"} fill="none" stroke="#9fb9c5" strokeWidth="3" />
    {count === 3 && <g><circle cx="600" cy="482" r="23" fill="#f8f7f0" stroke="#9fb9c5" /><text x="600" y="489" textAnchor="middle" fontSize="17" fill={muted}>本卡</text></g>}
    {layout.claims.map((claim, i) => {
      const r = regions[i];
      return <g key={claim.claim_id} data-claim-id={claim.claim_id}>
        <path d={`M${r.x + 30} ${r.y}H${r.x + r.w - 30}Q${r.x + r.w} ${r.y} ${r.x + r.w} ${r.y + 30}V${r.y + r.h}H${r.x}V${r.y + 30}Q${r.x} ${r.y} ${r.x + 30} ${r.y}`} fill={i % 2 ? "#e0ede5" : "#e3edf5"} />
        <text x={r.x + 22} y={r.y + 47} fill={i % 2 ? green : blue} fontSize="38" fontWeight="600">{String.fromCharCode(65 + i)}</text>
        <CardText text={claimText(claim)} x={r.x + 76} y={r.y + 20} width={r.w - 102} height={r.h - 38} size={30} minSize={26} />
      </g>;
    })}
  </g>;
}

function Relation({ layout, conceptId }: { layout: Layout; conceptId: string }) {
  const item = layout.relation!;
  const { relation, source, target } = item;
  const current = (id: string) => id === conceptId;
  const label = (text: string, x: number, y: number, width: number, height = 145) => <CardText text={text} x={x} y={y} width={width} height={height} size={40} minSize={32} bold center />;
  const arrow = <path d="M515 400H671m-16-13 16 13-16 13" fill="none" stroke={blue} strokeWidth="4" />;
  const node = (side: "source" | "target", x: number, y: number, caption: string) => {
    const concept = side === "source" ? source : target;
    return <g data-concept-id={concept.concept_id}>
      <rect x={x} y={y} width="435" height="275" rx="32" fill={current(concept.concept_id) ? "#dceaf5" : "#e0ede5"} stroke={current(concept.concept_id) ? blue : "#a5bdb3"} strokeWidth="2" />
      <text x={x + 28} y={y + 44} fontSize="22" fill={muted}>{caption}{current(concept.concept_id) ? " · 本卡概念" : ""}</text>
      {label(concept.label, x + 28, y + 81, 379)}
    </g>;
  };
  return <g data-diagram={relation.type} data-relation-id={relation.relation_id} data-source-concept-id={source.concept_id} data-target-concept-id={target.concept_id}>
    {relation.type === "part_of" ? <>
      <rect x="148" y="226" width="904" height="366" rx="42" fill="#e0ede5" stroke="#93b3a8" strokeWidth="2" />
      <text x="185" y="265" fontSize="22" fill={muted}>整體{current(target.concept_id) ? " · 本卡概念" : ""}</text>
      {label(target.label, 300, 243, 700, 102)}
      <rect x="279" y="376" width="642" height="177" rx="28" fill="#e0edf7" stroke={blue} strokeWidth="2" />
      <text x="304" y="414" fontSize="22" fill={muted}>組成部分{current(source.concept_id) ? " · 本卡概念" : ""}</text>
      {label(source.label, 305, 437, 590, 100)}
    </> : relation.type === "contrast" ? <>
      {node("source", 48, 265, "對照概念")}{node("target", 717, 265, "對照概念")}
      <path d="M579 326V481M621 326V481" stroke="#9db4bf" strokeWidth="4" />
      <CardText text={`對照：${item.axis}`} x={130} y={210} width={940} height={45} size={25} center />
    </> : relation.type === "application" ? <>
      <path d="M104 286 275 222 468 286V490L275 566 104 490Z" fill="#dceaf5" stroke={blue} strokeWidth="2" />
      <text x="275" y="293" textAnchor="middle" fontSize="22" fill={muted}>概念{current(source.concept_id) ? " · 本卡概念" : ""}</text>
      {label(source.label, 140, 326, 290)}{arrow}{node("target", 717, 267, "具體用途")}
    </> : relation.type === "example" ? <>
      {node("source", 48, 249, "抽象概念")}
      <path d="M500 383H600V447H690m-13-13 13 13-13 13" stroke={blue} strokeWidth="3" fill="none" />
      <rect x="737" y="292" width="410" height="275" rx="32" fill="#c6d9cf" />
      {node("target", 717, 270, "教材實例")}
    </> : <>
      {node("source", 48, 265, "先備知識")}{node("target", 717, 265, "學習目標")}
      <path d="M510 400H686" stroke={blue} strokeWidth="4" strokeDasharray="9 8" /><path d="m672 388 14 12-14 12" stroke={blue} strokeWidth="4" fill="none" />
      <text x="600" y="579" textAnchor="middle" fontSize="21" fill={muted}>先備：學習依賴，不表示時間流程</text>
    </>}
    <path d="M48 613H1152" stroke="#c2d0d0" />
    <CardText text={relation.learner_reason} x={64} y={635} width={1072} height={113} size={29} minSize={27} />
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
    <text x="82" y="270" fontSize="23" fill={code ? "#b9dce9" : blue}>{code ? "程式碼原文" : "公式與條件原文"}</text>
    <path d="M80 293H1120" stroke={code ? "#456473" : "#b6cbd7"} />
    <g data-text-region={JSON.stringify({ x: 118, y: 310, width: 995, height: 380 })}>
      <text x="126" y={320 + size} fontSize={size} fontFamily={MONO} xmlSpace="preserve" style={{ whiteSpace: "pre" }} fill={code ? "#f4fbff" : ink}>
        {shown.map((line, i) => <tspan data-technical-line="true" x="126" y={320 + size + i * size * 1.45} key={i}>{line}</tspan>)}
      </text>
    </g>
    {shown.map((_, i) => <text key={i} x="91" y={320 + size + i * size * 1.45} fontSize="18" textAnchor="end" fill={code ? "#8cbdca" : muted}>{i + 1}</text>)}
    {!shown.length && <CardText text="原文單行超出圖卡容量，請翻面查看完整語法與條件。" x={126} y={371} width={920} height={140} size={32} fill={code ? "#f4fbff" : ink} />}
    <text x="82" y="713" fontSize="20" fill={code ? "#b9dce9" : blue}>{clipped ? `原文節錄 · 僅列完整前 ${shown.length} 行；完整語法與條件見背面` : "保留原始符號、數值、換行與條件"}</text>
  </g>;
}

export function ConceptCardArtwork({ card, structure, materialName, svgRef }: {
  card: ConceptCard; structure: KnowledgeStructureView; materialName: string; svgRef?: React.Ref<SVGSVGElement>;
}) {
  const layout = selectConceptCardLayout(card, structure);
  const evidence = [...card.claims.flatMap(c => c.evidence), ...(layout.relation?.evidence ?? [])].filter(hasCardEvidence);
  const sources = [...new Set(evidence.map(e => `${e.source_name || materialName || "教材名稱未提供"} · 第 ${e.normalized_page ?? e.page} 頁`))];
  const familyName = { sparse: "圖解筆記", points: "重點結構", relation: "概念關係", technical: "技術筆記" }[layout.family];
  // 語系與字型渲染跟著 SVG 匯出，避免獨立圖片沿用不同的 CJK 字形／標點規則。
  return <svg ref={svgRef} className="visual-concept-card" xmlns="http://www.w3.org/2000/svg" width="1200" height="900" viewBox="0 0 1200 900"
    textRendering="optimizeLegibility" style={{ fontSynthesis: "none" }} lang="zh-Hant" xmlLang="zh-Hant" role="img" aria-label={`${card.label}視覺化概念卡`} fontFamily={FONT} data-layout={layout.family} data-renderer-policy={layout.policy}>
    <title>{`${card.label}｜概念卡`}</title>
    <desc>{[...card.claims.map(claimText), layout.relation?.relation.learner_reason, ...sources].filter(Boolean).join("\n")}</desc>
    <rect width="1200" height="900" rx="28" fill="#f8f7f0" />
    <rect x="0" y="0" width="12" height="900" rx="6" fill={blue} />
    <text x="48" y="48" fontSize="21" letterSpacing="2" fill={blue}>STUDYDY / 概念卡</text>
    <text x="325" y="48" fontSize="20" fill={muted}>{familyName}</text>
    {layout.review && <g aria-label="教材版本待複核"><rect x="866" y="20" width="286" height="44" rx="22" fill="#f7dfb4" /><text x="1009" y="50" textAnchor="middle" fontSize="22" fill="#765123">教材版本待複核</text></g>}
    <CardText text={card.label} x={48} y={71} width={1104} height={109} size={43} minSize={35} bold notice="標題節錄 · 完整名稱見背面" />
    <path d="M48 189H1152M48 772H1152" stroke="#c8d4d2" />
    {(layout.missingEvidence || layout.unresolvedRelations) && <text x="535" y="48" fontSize="18" fill="#8a5425">{layout.missingEvidence ? "來源不足" : "部分關聯來源不足，未繪製"}</text>}
    {layout.family === "technical" ? <Technical layout={layout} /> : layout.family === "relation" ? <Relation layout={layout} conceptId={card.concept_id} /> : layout.family === "points" ? <Points layout={layout} /> : <Sparse layout={layout} />}
    <CardText text={sources.length ? sources.join("；") : "來源未提供 · 請查看教材版本與完整來源"} x={48} y={786} width={1104} height={65} size={21} notice="來源節錄 · 全部來源見背面" />
    <text x="48" y="884" fontSize="19" fill={muted}>{layout.omitted ? `另有 ${layout.omitted} 則教材重點 · 完整內容、條件與來源見背面` : "教材原文／節錄 · 完整內容、條件與來源見背面"}</text>
  </svg>;
}
