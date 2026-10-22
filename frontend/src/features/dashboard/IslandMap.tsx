import { useCallback, useEffect, useMemo, useRef, type CSSProperties, type KeyboardEvent, type PointerEvent } from "react";
import type { MaterialLibraryItem } from "../../api/contracts";
import { writeRoute } from "../../app/routes";
import { IslandArt, islandColors, islandPalette } from "../../ui/art/Island";
import { ProgressRing } from "../../ui/art/ProgressRing";
import { Icon } from "../../ui/Icon";
import { Bone } from "../../ui/Skeleton";
import { cellX, cellY, layoutIslands } from "./island-layout";
import { islandState, openMaterial, statusText, type IslandProgress } from "./islands";

// 可拖曳、縮放的群島地圖（島位見 island-layout.ts）。畫面位移與縮放存在 CSS 變數 --tx/--ty/--k，
// 拖曳時直接改變數、不經過 React 重繪，島的標籤維持固定字級。

const minZoom = 0.25;
const maxZoom = 1.6;
const farZoom = 0.5;

type View = { x: number; y: number; k: number };

const clamp = (value: number, min: number, max: number) => Math.min(max, Math.max(min, value));
const reducedMotion = () => window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;

export function IslandMap({ materials, progress, focusId }: {
  materials: MaterialLibraryItem[] | null;
  progress: Map<string, IslandProgress>;
  focusId: string | undefined;
}) {
  const { islands, newSpot, bounds } = useMemo(() => layoutIslands(materials ?? []), [materials]);
  const viewport = useRef<HTMLDivElement>(null);
  const minimapView = useRef<SVGRectElement>(null);
  const view = useRef<View>({ x: 0, y: 0, k: 1 });
  const size = useRef({ w: 0, h: 0 });
  const placed = useRef(false);
  const animation = useRef(0);
  const pointers = useRef(new Map<number, { x: number; y: number }>());
  const gesture = useRef({ startX: 0, startY: 0, moved: false });
  const geometry = useRef({ islands, newSpot, bounds, focusId });
  geometry.current = { islands, newSpot, bounds, focusId };

  const fitZoom = () => {
    const { bounds: b } = geometry.current;
    return Math.min((size.current.w - 48) / (b.maxX - b.minX), (size.current.h - 48) / (b.maxY - b.minY), 1.1);
  };
  const zoomRange = () => [Math.max(minZoom, Math.min(0.5, fitZoom() * 0.8)), maxZoom] as const;

  // 畫面中心不離開地圖範圍；縮放限制在可讀範圍內。
  const constrain = (next: View): View => {
    const { bounds: b } = geometry.current;
    const [low, high] = zoomRange();
    const k = clamp(next.k, low, high);
    const { w, h } = size.current;
    return { k, x: clamp(next.x, w / 2 - b.maxX * k, w / 2 - b.minX * k), y: clamp(next.y, h / 2 - b.maxY * k, h / 2 - b.minY * k) };
  };

  const apply = useCallback((next: View) => {
    view.current = next;
    const element = viewport.current;
    if (!element) return;
    element.style.setProperty("--tx", `${next.x}px`);
    element.style.setProperty("--ty", `${next.y}px`);
    element.style.setProperty("--k", String(next.k));
    element.dataset.zoom = next.k < farZoom ? "far" : "near";
    const rect = minimapView.current;
    if (rect) {
      rect.setAttribute("x", String(-next.x / next.k));
      rect.setAttribute("y", String(-next.y / next.k));
      rect.setAttribute("width", String(size.current.w / next.k));
      rect.setAttribute("height", String(size.current.h / next.k));
    }
  }, []);

  const centerOn = (x: number, y: number, k: number): View =>
    ({ k, x: size.current.w / 2 - x * k, y: size.current.h / 2 - y * k });

  // 全覽：看得到全部島；島太多時改以目前學習的島為中心，維持可讀的大小。
  const overview = (): View => {
    const { bounds: b, islands: all, focusId: focus } = geometry.current;
    const k = fitZoom();
    if (k >= 0.5) return centerOn((b.minX + b.maxX) / 2, (b.minY + b.maxY) / 2, k);
    const target = all.find((island) => island.material.material_id === focus) ?? all[0];
    return centerOn(target?.x ?? 0, target?.y ?? 0, 0.5);
  };

  const animateTo = (target: View) => {
    cancelAnimationFrame(animation.current);
    const goal = constrain(target);
    if (reducedMotion()) { apply(goal); return; }
    const from = view.current;
    const started = performance.now();
    const step = (now: number) => {
      const t = Math.min(1, (now - started) / 320);
      const ease = 1 - (1 - t) ** 3;
      apply({ x: from.x + (goal.x - from.x) * ease, y: from.y + (goal.y - from.y) * ease, k: from.k + (goal.k - from.k) * ease });
      if (t < 1) animation.current = requestAnimationFrame(step);
    };
    animation.current = requestAnimationFrame(step);
  };

  // 以畫面上的一點為中心縮放（滾輪游標、雙指中點或畫面中央）。
  const zoomAround = (from: View, factor: number, sx: number, sy: number): View => {
    const [low, high] = zoomRange();
    const k = clamp(from.k * factor, low, high);
    return { k, x: sx - (sx - from.x) * (k / from.k), y: sy - (sy - from.y) * (k / from.k) };
  };
  const zoomBy = (factor: number) => animateTo(zoomAround(view.current, factor, size.current.w / 2, size.current.h / 2));

  // 量測地圖尺寸；第一次量到時進入全覽，之後尺寸改變維持原本的中心點。
  useEffect(() => {
    const element = viewport.current;
    if (!element) return;
    const observer = new ResizeObserver(([entry]) => {
      const { width, height } = entry.contentRect;
      if (!width || !height) return;
      const previous = size.current;
      size.current = { w: width, h: height };
      if (!placed.current) { placed.current = true; apply(constrain(overview())); return; }
      const { x, y, k } = view.current;
      apply(constrain({ k, x: x + (width - previous.w) / 2, y: y + (height - previous.h) / 2 }));
    });
    observer.observe(element);
    return () => { observer.disconnect(); cancelAnimationFrame(animation.current); };
  }, []);

  // 教材讀完（或數量改變）後重新全覽。
  useEffect(() => {
    if (placed.current && size.current.w) apply(constrain(overview()));
  }, [materials === null, islands.length]);

  // 滾輪縮放需要 passive: false 才能阻止頁面捲動；觸控板雙指縮放會帶 ctrlKey，靈敏度較高。
  useEffect(() => {
    const element = viewport.current;
    if (!element) return;
    const onWheel = (event: WheelEvent) => {
      event.preventDefault();
      cancelAnimationFrame(animation.current);
      const delta = event.deltaY * (event.deltaMode === 1 ? 33 : 1);
      const rect = element.getBoundingClientRect();
      apply(constrain(zoomAround(view.current, Math.exp(-delta * (event.ctrlKey ? 0.01 : 0.0015)), event.clientX - rect.left, event.clientY - rect.top)));
      element.classList.add("is-touched");
    };
    element.addEventListener("wheel", onWheel, { passive: false });
    return () => element.removeEventListener("wheel", onWheel);
  }, []);

  const local = (event: PointerEvent) => {
    const rect = viewport.current!.getBoundingClientRect();
    return { x: event.clientX - rect.left, y: event.clientY - rect.top };
  };
  const onPointerDown = (event: PointerEvent<HTMLDivElement>) => {
    if (event.pointerType === "mouse" && event.button !== 0) return;
    cancelAnimationFrame(animation.current);
    const point = local(event);
    pointers.current.set(event.pointerId, point);
    if (pointers.current.size === 1) gesture.current = { startX: point.x, startY: point.y, moved: false };
  };
  const onPointerMove = (event: PointerEvent<HTMLDivElement>) => {
    const previous = pointers.current.get(event.pointerId);
    if (!previous) return;
    const point = local(event);
    pointers.current.set(event.pointerId, point);
    const element = viewport.current!;
    if (pointers.current.size === 1) {
      // 超過 6px 才算拖曳，避免輕點島嶼時被當成移動。
      if (!gesture.current.moved && Math.hypot(point.x - gesture.current.startX, point.y - gesture.current.startY) < 6) return;
      // 剛判定為拖曳時，從按下的位置算起，門檻內的移動不遺漏。
      const from = gesture.current.moved ? previous : { x: gesture.current.startX, y: gesture.current.startY };
      if (!gesture.current.moved) {
        gesture.current.moved = true;
        element.setPointerCapture(event.pointerId);
        element.classList.add("is-dragging", "is-touched");
      }
      apply(constrain({ ...view.current, x: view.current.x + point.x - from.x, y: view.current.y + point.y - from.y }));
      return;
    }
    // 雙指：依兩指距離縮放，並跟著兩指中點平移。
    const other = [...pointers.current.entries()].find(([id]) => id !== event.pointerId)?.[1];
    if (!other) return;
    gesture.current.moved = true;
    element.classList.add("is-touched");
    const before = Math.hypot(previous.x - other.x, previous.y - other.y) || 1;
    const after = Math.hypot(point.x - other.x, point.y - other.y) || 1;
    const mid = { x: (point.x + other.x) / 2, y: (point.y + other.y) / 2 };
    const midBefore = { x: (previous.x + other.x) / 2, y: (previous.y + other.y) / 2 };
    const zoomed = zoomAround(view.current, after / before, midBefore.x, midBefore.y);
    apply(constrain({ ...zoomed, x: zoomed.x + mid.x - midBefore.x, y: zoomed.y + mid.y - midBefore.y }));
  };
  const onPointerEnd = (event: PointerEvent<HTMLDivElement>) => {
    pointers.current.delete(event.pointerId);
    if (!pointers.current.size) viewport.current?.classList.remove("is-dragging");
  };

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.target !== event.currentTarget) return;
    const pan: Record<string, [number, number]> = { ArrowLeft: [120, 0], ArrowRight: [-120, 0], ArrowUp: [0, 120], ArrowDown: [0, -120] };
    if (pan[event.key]) animateTo({ ...view.current, x: view.current.x + pan[event.key][0], y: view.current.y + pan[event.key][1] });
    else if (event.key === "+" || event.key === "=") zoomBy(1.4);
    else if (event.key === "-" || event.key === "_") zoomBy(1 / 1.4);
    else if (event.key === "0") animateTo(overview());
    else return;
    event.preventDefault();
  };

  // 用 Tab 移到畫面外的島時，把它移到畫面中央。
  const reveal = (x: number, y: number) => {
    const { x: tx, y: ty, k } = view.current;
    const sx = tx + x * k, sy = ty + y * k;
    if (sx < 80 || sy < 80 || sx > size.current.w - 80 || sy > size.current.h - 80) animateTo(centerOn(x, y, Math.max(k, 0.6)));
  };

  // 小地圖：點或拖曳把畫面中心移過去。
  const minimapMove = (event: PointerEvent<SVGSVGElement>, smooth: boolean) => {
    const svg = event.currentTarget;
    const matrix = svg.getScreenCTM();
    if (!matrix) return;
    const point = new DOMPoint(event.clientX, event.clientY).matrixTransform(matrix.inverse());
    const target = centerOn(point.x, point.y, view.current.k);
    if (smooth) animateTo(target); else apply(constrain(target));
  };

  const width = bounds.maxX - bounds.minX, height = bounds.maxY - bounds.minY;
  const seaPad = 1600;
  return <div className="island-map">
    <div ref={viewport} className="island-map-viewport" tabIndex={0} role="group"
      aria-label="學習群島地圖：拖曳移動、滾輪或雙指縮放；聚焦地圖時可用方向鍵移動，加號、減號縮放，0 回到全覽。"
      aria-busy={materials === null}
      onPointerDown={onPointerDown} onPointerMove={onPointerMove} onPointerUp={onPointerEnd} onPointerCancel={onPointerEnd}
      onClickCapture={(event) => {
        // 拖曳結束時放開滑鼠不算點擊島嶼。
        if (gesture.current.moved) { event.preventDefault(); event.stopPropagation(); gesture.current.moved = false; }
      }}
      onKeyDown={onKeyDown}>
      <div className="island-map-world" aria-hidden="true">
        <svg className="island-map-sea" style={{ left: bounds.minX - seaPad, top: bounds.minY - seaPad }}
          width={width + seaPad * 2} height={height + seaPad * 2} viewBox={`${bounds.minX - seaPad} ${bounds.minY - seaPad} ${width + seaPad * 2} ${height + seaPad * 2}`}>
          <defs>
            <pattern id="island-map-waves" width="300" height="220" patternUnits="userSpaceOnUse">
              <path d="M40 50 q12 -9 24 0 t24 0" /><path d="M200 150 q12 -9 24 0 t24 0" /><path d="M150 30 q8 -6 16 0" />
            </pattern>
          </defs>
          <rect x={bounds.minX - seaPad} y={bounds.minY - seaPad} width={width + seaPad * 2} height={height + seaPad * 2} fill="url(#island-map-waves)" />
          <g transform={`translate(${cellX / 2} ${cellY * 0.36})`}>
            <path d="M-60 6 q-16 4 -30 0" stroke="#fff" strokeWidth="3" fill="none" strokeLinecap="round" strokeDasharray="2 8" opacity=".8" />
            <path d="M-24 0 h48 l-8 12 h-32z" fill="#8a5a35" />
            <path d="M0 0 v-40 l22 34z" fill="#fff" /><path d="M0 -40 v34 h-16z" fill="#ffe27a" />
          </g>
        </svg>
      </div>
      {materials === null && <div className="island-map-skeleton" aria-hidden="true">
        {[-1, 0, 1].map((offset) => <Bone key={offset} className="is-island" style={{ left: `${50 + offset * 26}%`, top: `${50 + (offset % 2) * 16}%` }} />)}
      </div>}
      <ul className="island-map-islands" aria-label="教材島嶼">
        {islands.map(({ material, x, y, w, h }) => {
          const state = islandState(material);
          const value = progress.get(material.material_id);
          const status = statusText(state, value);
          const started = value?.completed ?? 0;
          const focus = material.material_id === focusId;
          return <li key={material.material_id} className={`map-island is-${state}${focus ? " is-focus" : ""}`}
            style={{ "--x": `${x}px`, "--y": `${y}px`, "--w": `${w}px`, "--h": `${h}px` } as CSSProperties}>
            <button type="button" onClick={() => openMaterial(material)} onFocus={() => reveal(x, y)} aria-label={`${material.display_name}，${status}`}>
              <span className="map-island-art"><IslandArt palette={islandPalette(material.material_id)} viewBox="100 60 840 470" landmarks={state !== "building"} /></span>
              <span className="island-flag">
                {focus && state === "learning" && <span className="island-pin">你在這裡</span>}
                <ProgressRing ratio={value && value.total ? started / value.total : 0} color={state === "completed" ? "var(--success)" : "var(--warning)"}>
                  {state === "building" ? <Icon name="process" size={16} /> : state === "failed" ? <Icon name="warning" size={16} /> : started}
                </ProgressRing>
                <span className="island-flag-text"><b>{material.display_name}</b><small>{status}</small></span>
              </span>
            </button>
          </li>;
        })}
        {materials !== null && <li className="map-island is-new" style={{ "--x": `${newSpot.x}px`, "--y": `${newSpot.y}px` } as CSSProperties}>
          <button className="archipelago-new" type="button" onFocus={() => reveal(newSpot.x, newSpot.y)} onClick={() => writeRoute({ name: "upload" })}>
            <span><Icon name="plus" size={24} /></span><b>發現新島嶼</b><small>上傳教材或輸入主題</small>
          </button>
        </li>}
      </ul>
      <svg className="island-map-compass" viewBox="-30 -30 60 60" aria-hidden="true">
        <circle r="28" fill="#fff" opacity=".3" /><circle r="28" fill="none" stroke="#fff" strokeWidth="2" />
        <path d="M0 -24 L6 0 L0 24 L-6 0Z" fill="#fff" /><path d="M0 -24 L6 0 L-6 0Z" fill="#ff6b5e" />
      </svg>
      <p className="island-map-hint" aria-hidden="true">拖曳移動 · 滾輪或雙指縮放</p>
    </div>
    <div className="island-map-controls" role="group" aria-label="地圖縮放">
      <button type="button" aria-label="放大" onClick={() => zoomBy(1.4)}><Icon name="plus" size={18} /></button>
      <button type="button" aria-label="縮小" onClick={() => zoomBy(1 / 1.4)}><Icon name="minus" size={18} /></button>
      <button type="button" aria-label="回到全覽" onClick={() => animateTo(overview())}><Icon name="fit" size={18} /></button>
    </div>
    {islands.length > 0 && <svg className="island-map-minimap" viewBox={`${bounds.minX} ${bounds.minY} ${width} ${height}`} preserveAspectRatio="xMidYMid meet"
      role="img" aria-label="群島縮圖，點選可移動地圖"
      onPointerDown={(event) => { event.currentTarget.setPointerCapture(event.pointerId); minimapMove(event, true); }}
      onPointerMove={(event) => { if (event.currentTarget.hasPointerCapture(event.pointerId)) minimapMove(event, false); }}>
      <rect x={bounds.minX} y={bounds.minY} width={width} height={height} className="minimap-sea" />
      {islands.map(({ material, x, y, w, h }) => <ellipse key={material.material_id} cx={x} cy={y} rx={w * 0.42} ry={h * 0.36}
        fill={islandColors(islandPalette(material.material_id)).g2} className={material.material_id === focusId ? "is-focus" : undefined} />)}
      <rect ref={minimapView} className="minimap-view" />
    </svg>}
  </div>;
}
