import type { ConceptCard, KnowledgeStructureView } from "../../api/contracts";
import { claimText } from "../../ui/claim-text";
import { compactLayout, selectConceptCardLayout, selectConceptCardLayouts } from "./concept-card-layout";
import { cardSourceText } from "./card-content";
import { textUnits, wrapCardText } from "./card-text";

import shapes from "./icons/shapes.json";

const geometry = shapes as unknown as Record<string, [string, Record<string, string>][] >;

const FONT = '"Noto Sans CJK TC", "Microsoft JhengHei", system-ui, sans-serif';
// 明確保留繁中文字型，避免等寬字型的系統後備採用簡體標點。
const MONO = 'ui-monospace, "SFMono-Regular", Consolas, "DejaVu Sans Mono", "Noto Sans CJK TC", "Microsoft JhengHei", monospace';
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

// 直接畫入像素畫布；保留原先的字型量測、換行與各區塊容量。
class CardCanvas {
  constructor(readonly context: CanvasRenderingContext2D) {}
  text(text: string | number, x: number, y: number, size: number, fill = ink, bold = false, align: CanvasTextAlign = "left", mono = false) {
    const c = this.context;
    c.font = `${bold ? 600 : 400} ${size}px ${mono ? MONO : FONT}`;
    c.fillStyle = fill;
    c.textAlign = align;
    c.fillText(String(text), x, y);
  }
  box(x: number, y: number, width: number, height: number, radius: number, fill: string, stroke?: string, lineWidth = 1) {
    const c = this.context;
    c.beginPath();
    c.roundRect(x, y, width, height, radius);
    c.fillStyle = fill;
    c.fill();
    if (stroke) { c.strokeStyle = stroke; c.lineWidth = lineWidth; c.stroke(); }
  }
  circle(x: number, y: number, radius: number, fill: string, stroke?: string) {
    const c = this.context;
    c.beginPath(); c.arc(x, y, radius, 0, Math.PI * 2);
    c.fillStyle = fill; c.fill();
    if (stroke) { c.strokeStyle = stroke; c.lineWidth = 1; c.stroke(); }
  }
  path(path: string, stroke?: string, lineWidth = 1, fill?: string, dash: number[] = []) {
    const c = this.context, shape = new Path2D(path);
    if (fill) { c.fillStyle = fill; c.fill(shape); }
    if (stroke) {
      c.strokeStyle = stroke; c.lineWidth = lineWidth; c.setLineDash(dash);
      c.stroke(shape); c.setLineDash([]);
    }
  }
  icon(kind: string, x: number, y: number, size = 180) {
    const c = this.context;
    c.save(); c.translate(x, y); c.scale(size / 24, size / 24);
    c.strokeStyle = blue; c.fillStyle = blue; c.lineWidth = 1.5; c.lineCap = "round"; c.lineJoin = "round";
    for (const [, attributes] of geometry[kind]) {
      c.globalAlpha = Number(attributes.opacity ?? 1);
      const shape = new Path2D(attributes.d);
      if (attributes.fill) c.fill(shape);
      c.stroke(shape);
    }
    c.restore();
  }
  cardText({ text, x, y, width, height, size = 30, minSize = size, fill = ink, bold = false, center = false, notice = "原文節錄" }: TextProps) {
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
    wrapped.lines.slice(0, rows).forEach((line, i) => this.text(line, anchor, y + metrics.ascent + 3 + i * lineHeight, fontSize, fill, bold, center ? "center" : "left"));
    if (clipped) this.text(notice, anchor, y + height - noticeMetrics.descent - 3, 18, fill, false, center ? "center" : "left");
  }
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

function relationText(layout: Layout) {
  const item = layout.relation!;
  const normalize = (text: string) => text.replace(/[\s，,。；;：:]/gu, "");
  const body = (text: string) => {
    let result = normalize(text);
    const subject = normalize(item.source.label);
    if (result.startsWith(subject)) result = result.slice(subject.length).replace(/^(?:用來|用於)/u, "");
    return result;
  };
  return layout.claims.some(claim => body(claimText(claim)) === body(item.relation.learner_reason)) ? "" : item.relation.learner_reason;
}

function compactRelationFrame(layout: Layout) {
  const { relation, axis } = layout.relation!;
  const diagramBottom = ["part_of", "classification"].includes(layout.relation!.presentation) ? 390 : 358;
  const axisHeight = relation.type === "contrast" ? textHeight(`對照：${axis}`, 1104, 23) : 0;
  const reasonY = diagramBottom + 18 + (axisHeight ? axisHeight + 12 : 0);
  const reasonHeight = relationText(layout) ? textHeight(relationText(layout), 1104, 25) : 0;
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

function sparse(draw: CardCanvas, layout: Layout, frame: ReturnType<typeof sparseFrame>) {
  const { objects, regions, size, width } = frame;
  if (frame.mode === "illustration") {
    const object = objects[0], region = regions[0], claim = layout.claims[0];
    const iconTop = region.top + (region.height - 234) / 2;
    const contentHeight = Math.min(region.height - 48, textHeight(claimText(claim), width, size));
    draw.box(48, region.top, 294, region.height, 32, "#e1ecf5");
    draw.icon(object.kind, 105, iconTop);
    draw.cardText({ text: object.label, x: 68, y: iconTop + 190, width: 254, height: 44, size: 28, minSize: 26, bold: true, center: true });
    draw.cardText({ text: claimText(claim), x: 390, y: region.top + (region.height - contentHeight) / 2, width, height: contentHeight, size, minSize: 30 });
    return;
  }
  if (frame.mode === "gallery") {
    objects.forEach((object, i) => {
      const cx = 600 + (i - (objects.length - 1) / 2) * (objects.length === 2 ? 440 : 345);
      draw.circle(cx, 310, 90, i % 2 ? "#e0ede5" : "#e1ecf5");
      draw.icon(object.kind, cx - 72, 238, 144);
      draw.cardText({ text: object.label, x: cx - 150, y: 410, width: 300, height: 60, size: 30, minSize: 26, bold: true, center: true });
    });
    layout.claims.forEach((claim, i) => {
      if (i > 0) draw.path(`M48 ${regions[i].top - 9}H1152`, "#becfce");
      draw.cardText({ text: claimText(claim), x: 60, y: regions[i].top, width, height: regions[i].height, size, minSize: 28 });
    });
    return;
  }
  layout.claims.forEach((claim, i) => {
    draw.box(48, regions[i].top, 1104, regions[i].height, 28, i % 2 ? "#e0ede5" : "#e3edf5");
    draw.cardText({ text: claimText(claim), x: 88, y: regions[i].top + 24, width, height: regions[i].height - 48, size, minSize: 30 });
  });
  if (!layout.claims.length) draw.cardText({ text: "尚無可呈現的教材重點。", x: 88, y: 244, width: 1024, height: 70, size: 38 });
}

function points(draw: CardCanvas, layout: Layout) {
  const count = layout.claims.length;
  const regions = count <= 2 ? [{ x: 48, y: 280, w: 535, h: 416 }, { x: 617, y: 280, w: 535, h: 416 }]
    : count === 3 ? [{ x: 48, y: 235, w: 490, h: 215 }, { x: 662, y: 235, w: 490, h: 215 }, { x: 355, y: 516, w: 490, h: 215 }]
    : [{ x: 48, y: 241, w: 535, h: 216 }, { x: 617, y: 241, w: 535, h: 216 }, { x: 48, y: 491, w: 535, h: 216 }, { x: 617, y: 491, w: 535, h: 216 }];
  draw.path(count === 3 ? "M293 450 600 482 907 450M600 482V516" : "M315 465H885M600 420V505", "#9fb9c5", 3);
  if (count === 3) { draw.circle(600, 482, 23, "#f8f7f0", "#9fb9c5"); draw.text("·", 600, 489, 17, muted, false, "center"); }
  layout.claims.forEach((claim, i) => {
    const r = regions[i], object = layout.objects.find(o => o.claimId === claim.claim_id);
    draw.path(`M${r.x + 30} ${r.y}H${r.x + r.w - 30}Q${r.x + r.w} ${r.y} ${r.x + r.w} ${r.y + 30}V${r.y + r.h}H${r.x}V${r.y + 30}Q${r.x} ${r.y} ${r.x + 30} ${r.y}`, undefined, 1, i % 2 ? "#e0ede5" : "#e3edf5");
    if (object) draw.icon(object.kind, r.x + 15, r.y + 26, 48);
    else draw.text(String(i + 1).padStart(2, "0"), r.x + 22, r.y + 47, 32, i % 2 ? green : blue, true);
    draw.cardText({ text: claimText(claim), x: r.x + 76, y: r.y + 20, width: r.w - 102, height: r.h - 38, size: 30, minSize: 26 });
  });
}

function relation(draw: CardCanvas, layout: Layout, conceptId: string, compact: ReturnType<typeof compactRelationFrame>) {
  const { relation, source, target, axis, presentation } = layout.relation!;
  const left = layout.claims.length ? 48 : 395, width = 410;
  const label = (text: string, x: number, y: number, width: number, height = 78) =>
    draw.cardText({ text, x, y, width, height, size: 34, minSize: 28, bold: true, center: true });
  const node = (side: "source" | "target", y: number, caption: string, x = left, w = width) => {
    const concept = side === "source" ? source : target, current = concept.concept_id === conceptId;
    draw.box(x, y, w, 138, 24, current ? "#dceaf5" : "#e0ede5", current ? blue : "#a5bdb3", 2);
    draw.text(caption, x + 22, y + 30, 21, muted);
    label(concept.label, x + 22, y + 44, w - 44);
  };
  const captions = relation.type === "prerequisite" ? ["先備知識", "學習目標"]
    : relation.type === "example" ? ["抽象概念", "教材實例"]
    : relation.type === "contrast" ? ["對照概念", "對照概念"] : ["相關概念", "相關概念"];
  if (["part_of", "classification"].includes(presentation)) {
    if (compact) {
      draw.box(48, 220, 1104, 170, 28, "#e0ede5", "#93b3a8", 2);
      draw.text(presentation === "classification" ? "分類" : "整體", 72, 254, 21, muted);
      label(target.label, 72, 269, 500);
      draw.box(650, 244, 470, 120, 24, "#e0edf7", blue, 2);
      draw.text(presentation === "classification" ? "類別" : "組成部分", 672, 274, 21, muted);
      label(source.label, 672, 286, 426, 70);
    } else {
      draw.box(left, 232, width, 334, 30, "#e0ede5", "#93b3a8", 2);
      draw.text(presentation === "classification" ? "分類" : "整體", left + 22, 266, 21, muted);
      label(target.label, left + 22, 279, width - 44);
      draw.box(left + 28, 387, width - 56, 150, 24, "#e0edf7", blue, 2);
      draw.text(presentation === "classification" ? "類別" : "組成部分", left + 48, 420, 21, muted);
      label(source.label, left + 48, 433, width - 96);
    }
  } else {
    if (compact) node("source", 220, captions[0], 48, 435);
    else node("source", 230, captions[0]);
    if (relation.type === "contrast") {
      draw.cardText({ text: `對照：${axis}`, x: compact ? 48 : left, y: compact ? compact.diagramBottom + 18 : 378, width: compact ? 1104 : width, height: compact ? compact.axisHeight : 50, size: 23, minSize: 21, center: true });
    } else {
      draw.path(compact ? `M515 289H671${presentation === "association" ? "" : "m-12-12 12 12-12 12"}` : `M${left + width / 2} 380V420${presentation === "association" ? "" : "m-12-12 12 12 12-12"}`, blue, 3, undefined, relation.type === "prerequisite" ? [7, 5] : []);
      if (relation.type === "prerequisite") draw.text("學習先備", compact ? 550 : left + width / 2 + 24, compact ? 265 : 405, 20, muted);
    }
    if (compact) node("target", 220, captions[1], 717, 435);
    else node("target", 435, captions[1]);
  }
  if (relationText(layout)) draw.cardText({ text: relationText(layout), x: compact ? 48 : left, y: compact ? compact.reasonY : 596, width: compact ? 1104 : width, height: compact ? compact.reasonHeight : 150, size: 25 });
  // 關係保留獨立圖區，重點緊鄰呈現；不把各 Claim 畫成流程或因果。
  layout.claims.forEach((claim, i) => {
    const gap = 14;
    const height = compact ? compact.regions[i].height : (526 - gap * (layout.claims.length - 1)) / layout.claims.length;
    const top = compact ? compact.regions[i].top : 220 + i * (height + gap);
    const x = compact ? 48 : 500, w = compact ? 1104 : 652;
    const object = layout.objects.find(o => o.claimId === claim.claim_id);
    draw.box(x, top, w, height, 24, i % 2 ? "#e0ede5" : "#e3edf5");
    if (object) draw.icon(object.kind, x + 16, top + 20, 42);
    else draw.text(String(i + 1).padStart(2, "0"), x + 20, top + 47, 26, blue);
    draw.cardText({ text: claimText(claim), x: x + 74, y: top + 18, width: w - 96, height: height - 36, size: compact ? 34 : layout.claims.length === 1 ? 38 : 30, minSize: 28 });
  });
}

function readable(draw: CardCanvas, layout: Layout) {
  let size = 34;
  const needed = () => layout.claims.map(c => textHeight(cardSourceText(c), 1056, size) + 28);
  while (size > 26 && needed().reduce((a,b) => a+b, 0) + (layout.claims.length - 1) * 12 > 526) size -= 2;
  const heights = needed(), total = heights.reduce((a,b) => a+b, 0);
  const available = 526 - Math.max(0, layout.claims.length - 1) * 12;
  let top = 220 + Math.max(0, (available - total) / 2);
  layout.claims.forEach((claim, i) => {
    const height = total > available ? 92 + (available - layout.claims.length * 92) * heights[i] / total : heights[i];
    const y = top; top += height + 12;
    draw.box(48, y, 1104, height, 22, i % 2 ? "#e0ede5" : "#e3edf5");
    draw.cardText({ text: cardSourceText(claim), x: 72, y: y + 14, width: 1056, height: height - 28, size, minSize: 26 });
  });
}

function contentTable(draw: CardCanvas, layout: Layout) {
  const content = layout.content!;
  if (content.kind !== "table") return;
  const { rows, notes, intro, mono } = content;
  let size = 30;
  const dimensions = () => {
    const keyWidth = Math.min(440, Math.max(140, ...rows.map(row => measure(row.label, size, false, 600) + 28)));
    const valueWidth = 1104 - keyWidth - 32;
    const heights = rows.map(row => Math.max(textHeight(row.label, keyWidth - 20, size), mono ? size * 1.4 + 6 : textHeight(row.value, valueWidth, size)) + 4);
    const noteHeights = notes.map(c => textHeight(cardSourceText(c), 1104, size));
    const introHeight = intro ? textHeight(intro, 1104, size) + 12 : 0;
    const total = introHeight + heights.reduce((a,b) => a+b, 0) + (notes.length ? 14 + noteHeights.reduce((a,b) => a+b, 0) + (notes.length - 1) * 6 : 0);
    return { keyWidth, valueWidth, heights, noteHeights, introHeight, total };
  };
  while (size > 24 && (dimensions().total > 526 || mono && rows.some(row => measure(row.value, size, true) > dimensions().valueWidth))) size -= 2;
  const d = dimensions();
  if (d.total > 526 || mono && rows.some(row => measure(row.value, size, true) > d.valueWidth)) return readable(draw, layout);
  let top = 220 + (526 - d.total) / 2;
  if (intro) draw.cardText({ text: intro, x: 48, y: top, width: 1104, height: d.introHeight - 12, size });
  top += d.introHeight;
  rows.forEach((row, i) => {
    const y = top, height = d.heights[i]; top += height;
    draw.box(48, y, 1104, height, 0, i % 2 ? "#e0ede5" : "#e3edf5");
    draw.cardText({ text: row.label, x: 58, y: y + 2, width: d.keyWidth - 20, height: height - 4, size, bold: true });
    if (mono) draw.text(row.value, 66 + d.keyWidth, y + 5 + fontMetrics(size).ascent, size, ink, false, "left", true);
    else draw.cardText({ text: row.value, x: 64 + d.keyWidth, y: y + 2, width: d.valueWidth, height: height - 4, size });
  });
  top += 14;
  notes.forEach((claim, i) => {
    draw.cardText({ text: cardSourceText(claim), x: 48, y: top, width: 1104, height: d.noteHeights[i], size });
    top += d.noteHeights[i] + 6;
  });
}

function sequence(draw: CardCanvas, layout: Layout) {
  const content = layout.content!;
  if (content.kind !== "sequence") return;
  const people = [content.steps[0].from, content.steps[0].to];
  if (content.steps.some(s => measure(s.values.join("  "), 26, true) > 650)) return readable(draw, layout);
  const notesHeight = content.notes ? textHeight(content.notes, 1104, 25) : 0;
  const introHeight = content.intro ? textHeight(content.intro, 1104, 25) : 0;
  const headingY = 220 + introHeight + 10;
  const rowStart = headingY + 100;
  const stepHeight = Math.min(76, (736 - notesHeight - 16 - rowStart) / content.steps.length);
  if (stepHeight < 58) return readable(draw, layout);
  const notesY = rowStart + stepHeight * content.steps.length + 8;
  if (content.intro) draw.cardText({ text: content.intro, x: 48, y: 220, width: 1104, height: introHeight, size: 25 });
  people.forEach((person, i) => {
    draw.box(i ? 840 : 80, headingY, 280, 58, 18, i ? "#e0ede5" : "#e3edf5");
    draw.cardText({ text: person, x: i ? 850 : 90, y: headingY + 5, width: 260, height: 48, size: 30, minSize: 26, bold: true, center: true });
    draw.path(`M${i ? 980 : 220} ${headingY + 58}V${notesY - 4}`, "#a0b8c7", 2, undefined, [6, 6]);
  });
  content.steps.forEach((step, i) => {
    const y = rowStart + i * stepHeight, forward = step.from === people[0];
    const x1 = forward ? 220 : 980, x2 = forward ? 980 : 220;
    draw.text(i + 1, 62, y + 8, 24, muted);
    draw.text(step.values.join("  "), 600, y - 14, 26, ink, false, "center", true);
    draw.path(`M${x1} ${y}H${x2}m${forward ? -12 : 12} -10 ${forward ? 12 : -12} 10 ${forward ? -12 : 12} 10`, blue, 3);
  });
  if (content.notes) draw.cardText({ text: content.notes, x: 48, y: notesY, width: 1104, height: notesHeight, size: 25 });
}

function fields(draw: CardCanvas, layout: Layout) {
  const content = layout.content!;
  if (content.kind !== "fields" && content.kind !== "composition") return;
  const compose = content.kind === "composition";
  const gap = compose ? 90 : 18, width = (1104 - gap * (content.fields.length - 1)) / content.fields.length;
  content.fields.forEach((field, i) => {
    draw.box(48 + i * (width + gap), 300, width, 142, 20, i % 2 ? "#e0ede5" : "#e3edf5");
    draw.cardText({ text: field, x: 64 + i * (width + gap), y: 327, width: width - 32, height: 88, size: 36, minSize: 28, bold: true, center: true });
    if (compose && i > 0) draw.text("+", 48 + i * (width + gap) - gap / 2, 385, 38, blue, false, "center");
  });
  if (compose) {
    draw.path("M600 464V503m-12-12 12 12 12-12", blue, 3);
    draw.cardText({ text: content.heading, x: 180, y: 524, width: 840, height: 70, size: 38, bold: true, center: true });
    draw.cardText({ text: claimText(content.claims[0]), x: 48, y: 631, width: 1104, height: 100, size: 29, minSize: 26 });
  } else draw.text("欄位順序依原文 · 非比例示意", 48, 488, 22, muted);
}

function group(draw: CardCanvas, layout: Layout) {
  const content = layout.content!;
  if (content.kind !== "group") return;
  let size = content.ordered ? 26 : 28;
  const captionHeight = content.ordered ? 0 : 38;
  const dimensions = () => ({
    rows: content.items.map(item => Math.max(52, textHeight(item.description, 720, size) + 12)),
    notes: content.claims.map(claim => textHeight(cardSourceText(claim), 1104, size)),
  });
  const total = () => { const d = dimensions(); return captionHeight + d.rows.reduce((a,b) => a+b,0) + (d.rows.length - 1)*6 + (d.notes.length ? 14 + d.notes.reduce((a,b) => a+b,0) + (d.notes.length - 1)*6 : 0); };
  while (size > 24 && total() > 526) size -= 2;
  const d = dimensions();
  if (total() > 526) return readable(draw, layout);
  let top = 220 + Math.max(0, (526 - total()) / 2);
  if (!content.ordered) draw.text("教材提及的相關項目", 48, top + 28, 22, muted);
  top += captionHeight;
  content.items.forEach((item, i) => {
    const y = top; top += d.rows[i] + 6;
    draw.box(48, y, 1104, d.rows[i], 14, i % 2 ? "#e0ede5" : "#e3edf5");
    if (content.ordered) draw.text(item.ordinal!, 66, y + 37, 28, blue);
    draw.cardText({ text: item.label, x: content.ordered ? 116 : 68, y: y + 6, width: content.ordered ? 276 : 324, height: d.rows[i] - 12, size, minSize: 24, bold: true });
    draw.cardText({ text: item.description, x: 416, y: y + 6, width: 720, height: d.rows[i] - 12, size });
  });
  content.claims.forEach((claim, i) => {
    draw.cardText({ text: cardSourceText(claim), x: 48, y: top + 8, width: 1104, height: d.notes[i], size });
    top += d.notes[i] + 6;
  });
}

function technical(draw: CardCanvas, layout: Layout) {
  const lines = claimText(layout.claims[0]).split(/\r?\n/), code = layout.technicalKind === "code";
  let size = code ? 32 : 48;
  while (size > 24 && (lines.length * size * 1.45 > 375 || lines.some(line => measure(line, size, true) > 975))) size -= 2;
  const capacity = Math.floor(375 / (size * 1.45));
  // 技術內容只取完整的前綴行，絕不在行內折斷運算子、識別符或條件。
  const firstUnfit = lines.findIndex(line => measure(line, size, true) > 975);
  const shown = lines.slice(0, Math.min(capacity, firstUnfit < 0 ? lines.length : firstUnfit));
  draw.box(48, 224, 1104, 510, 28, code ? "#18394d" : "#e3edf5");
  draw.text(code ? "程式碼" : "公式", 82, 270, 23, code ? "#b9dce9" : blue);
  draw.path("M80 293H1120", code ? "#456473" : "#b6cbd7");
  shown.forEach((line, i) => draw.text(line, 126, 320 + size + i * size * 1.45, size, code ? "#f4fbff" : ink, false, "left", true));
  shown.forEach((_, i) => draw.text(i + 1, 91, 320 + size + i * size * 1.45, 18, code ? "#8cbdca" : muted, false, "right"));
  if (!shown.length) draw.cardText({ text: "原文單行超出圖卡容量。", x: 126, y: 371, width: 920, height: 140, size: 32, fill: code ? "#f4fbff" : ink });
  draw.text(shown.length < lines.length ? `原文節錄 · 僅列完整前 ${shown.length} 行` : "", 82, 713, 20, code ? "#b9dce9" : blue);
}

const cardPages = new WeakMap<KnowledgeStructureView, WeakMap<ConceptCard, Layout[]>>();

// 依原始內容物件隔離版本，讓切卡／洗牌重用排版與圖片；資料釋放時一起回收。
// 長段落以實際字型量測切成續圖，保留 Claim 與 Evidence 身分。
export function conceptCardPages(card: ConceptCard, structure: KnowledgeStructureView): Layout[] {
  let cards = cardPages.get(structure);
  if (!cards) { cards = new WeakMap(); cardPages.set(structure, cards); }
  const cached = cards.get(card);
  if (cached) return cached;
  const result = selectConceptCardLayouts(card, structure).map(layout => compactLayout(layout, card.display_edits)).flatMap(layout => {
    if (layout.family !== "text") return [layout];
    const needed = layout.claims.reduce((n, c) => n + textHeight(cardSourceText(c), 1056, 28) + 40, 0);
    if (needed <= 526) return [layout];
    const pages: Layout[] = [];
    let claims: Layout["claims"] = [];
    let height = 0;
    const flush = () => {
      if (!claims.length) return;
      pages.push({ ...layout, content: { kind: "text", claims }, claims });
      claims = []; height = 0;
    };
    for (const claim of layout.claims) {
      const wrapped = wrapCardText(cardSourceText(claim), 1052, t => measure(t, 28));
      // 過寬的單一識別符仍保留原文與節錄提示，避免靜默丟字。
      const chunks = wrapped.overflow ? [cardSourceText(claim)] : Array.from({ length: Math.ceil(wrapped.lines.length / 10) }, (_, i) => wrapped.lines.slice(i * 10, (i + 1) * 10).join("\n"));
      for (const text of chunks) {
        const needed = textHeight(text, 1056, 28) + 40;
        if (claims.length && height + needed > 526) flush();
        claims.push({ ...claim, text }); height += needed;
      }
    }
    flush();
    return pages;
  });
  cards.set(card, result);
  return result;
}

const renderedPages = new WeakMap<Layout, Promise<Blob>>();

// 每個內容版本的同一頁只繪製一次；失敗不留快取，讓重試重新繪圖。
export function conceptCardPng(card: ConceptCard, layout: Layout, pageIndex: number, pageCount: number): Promise<Blob> {
  const cached = renderedPages.get(layout);
  if (cached) return cached;
  const rendered = document.fonts.ready.then(() => {
    const canvas = document.createElement("canvas");
    canvas.width = 1200; canvas.height = 760; canvas.lang = "zh-Hant";
    const context = canvas.getContext("2d");
    if (!context) throw new Error("PNG_CANVAS_UNAVAILABLE");
    Object.assign(context, { lang: "zh-Hant", textRendering: "optimizeLegibility" });
    const draw = new CardCanvas(context);
    const sparseLayout = layout.family === "sparse" ? sparseFrame(layout) : null;
    const compact = layout.family === "relation" ? compactRelationFrame(layout) : null;
    const contentHeight = sparseLayout?.height ?? compact?.height ?? 760;
    const primary = ["relation", "group"].includes(layout.family) ? layout.objects.find(o => o.role === "concept") : undefined;
    draw.box(0, 0, 1200, 760, 28, "#f8f7f0");
    draw.cardText({ text: card.label, x: 48, y: 42, width: primary ? 976 : 1104, height: 109, size: 48, minSize: 38, bold: true, notice: "標題節錄" });
    if (primary) draw.icon(primary.kind, 1058, 52, 84);
    draw.path("M48 175H1152", "#c8d4d2");
    context.save(); context.translate(0, -30 + (760 - contentHeight) / 2);
    switch (layout.family) {
      case "technical": technical(draw, layout); break;
      case "table": contentTable(draw, layout); break;
      case "sequence": sequence(draw, layout); break;
      case "fields": case "composition": fields(draw, layout); break;
      case "group": group(draw, layout); break;
      case "text": readable(draw, layout); break;
      case "relation": relation(draw, layout, card.concept_id, compact); break;
      case "points": points(draw, layout); break;
      default: sparse(draw, layout, sparseLayout!);
    }
    context.restore();
    if (pageCount > 1) draw.text(`第 ${pageIndex + 1} / ${pageCount} 張圖`, 1152, 744, 20, muted, false, "right");
    return new Promise<Blob>((resolve, reject) => canvas.toBlob(
      blob => blob ? resolve(blob) : reject(new Error("PNG_ENCODING_FAILED")), "image/png",
    ));
  });
  renderedPages.set(layout, rendered);
  void rendered.catch(() => renderedPages.delete(layout));
  return rendered;
}
