import { memo } from "react";

// 島嶼配色只是視覺識別，依教材 ID 穩定挑選，不代表後端欄位。
export type IslandPalette = "meadow" | "sakura" | "autumn" | "lagoon" | "sunset" | "lavender";
const palettes: Record<IslandPalette, {
  sea1: string; sea2: string; g1: string; g2: string; g3: string; cliff: string; cliff2: string;
  sand: string; c1: string; c2: string; c3: string; roof: string; pine: boolean;
}> = {
  meadow: { sea1: "#7fdcf8", sea2: "#2f9fdc", g1: "#9be86a", g2: "#64c34a", g3: "#3e9a3c", cliff: "#c08350", cliff2: "#8f5a33", sand: "#f8e3ad", c1: "#9fe36a", c2: "#56b947", c3: "#2f8f3a", roof: "#ff7a59", pine: false },
  sakura: { sea1: "#86ece0", sea2: "#2db1c4", g1: "#b8f0bf", g2: "#7cd994", g3: "#4caf70", cliff: "#bd8a64", cliff2: "#8a5d40", sand: "#fcebc4", c1: "#ffd0e4", c2: "#f79ac2", c3: "#e06a9e", roof: "#8b5cf6", pine: false },
  autumn: { sea1: "#8fd2ff", sea2: "#3f86e6", g1: "#dff08f", g2: "#b6d863", g3: "#8bb244", cliff: "#c58a55", cliff2: "#94603a", sand: "#fbe6b4", c1: "#ffd36b", c2: "#ff9b3d", c3: "#e0602a", roof: "#3f7bff", pine: true },
  lagoon: { sea1: "#9ad8ff", sea2: "#4a8fe8", g1: "#c9f2e4", g2: "#8fdcc4", g3: "#4fb79a", cliff: "#b98b6a", cliff2: "#86603f", sand: "#fdeec9", c1: "#c6f5ff", c2: "#6fd0e8", c3: "#2fa3c4", roof: "#ffb320", pine: true },
  sunset: { sea1: "#8fdcff", sea2: "#3b8fe0", g1: "#ffe9a8", g2: "#f4cf6a", g3: "#d9a63e", cliff: "#c27f4a", cliff2: "#8e5430", sand: "#fff0c9", c1: "#ffb2a1", c2: "#ff7f6b", c3: "#e0503f", roof: "#2f6bff", pine: false },
  lavender: { sea1: "#a6d8ff", sea2: "#5a88e6", g1: "#d8f5c4", g2: "#a8e08c", g3: "#6fb85a", cliff: "#b8876a", cliff2: "#835c43", sand: "#fbecd0", c1: "#e4d4ff", c2: "#b896ff", c3: "#8b5cf6", roof: "#ff7a59", pine: true },
};
const paletteOrder: IslandPalette[] = ["meadow", "sakura", "autumn", "lagoon", "sunset", "lavender"];

export function islandPalette(seed: string): IslandPalette {
  let hash = 0;
  for (const char of seed) hash = (hash * 31 + char.charCodeAt(0)) >>> 0;
  return paletteOrder[hash % paletteOrder.length];
}

export function islandColors(palette: IslandPalette) {
  return palettes[palette];
}

const shape = "M140,300 C110,205 230,130 360,140 C470,80 640,92 730,150 C850,130 940,215 905,305 C935,410 820,480 690,462 C570,510 400,500 300,462 C180,470 110,400 140,300 Z";
const pond: [number, number, number, number] = [395, 300, 56, 28];
const landmarkSpots: [Landmark, number, number][] = [["library", 168, 350], ["observatory", 505, 258], ["village", 665, 345], ["tower", 862, 252]];
type Landmark = "library" | "observatory" | "village" | "tower";

let measurePath: SVGPathElement | null = null;
const treeCache = new Map<string, [number, number, number][]>();

// 決定性亂數：同一組參數每次畫出相同的樹。
function random(seed: number) {
  let state = seed;
  return () => (state = (state * 16807) % 2147483647) / 2147483647;
}

function scatterTrees(spacing: number, landmarks: boolean, seed: number) {
  const key = `${spacing}|${landmarks}|${seed}`;
  const cached = treeCache.get(key);
  if (cached) return cached;
  if (!measurePath) {
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("aria-hidden", "true");
    svg.setAttribute("style", "position:absolute;width:0;height:0;overflow:hidden");
    measurePath = document.createElementNS("http://www.w3.org/2000/svg", "path");
    measurePath.setAttribute("d", shape);
    svg.append(measurePath);
    document.body.append(svg);
  }
  const path = measurePath;
  const inside = (x: number, y: number) => path.isPointInFill(new DOMPoint(x, y));
  const avoid = (landmarks ? landmarkSpots.map(([, x, y]) => [x, y, 62]) : []).concat([[818, 120, 50]]);
  const next = random(seed);
  const trees: [number, number, number][] = [];
  for (let y = 120; y < 490; y += spacing * 0.8) {
    for (let x = 120; x < 930; x += spacing) {
      const px = x + (next() - 0.5) * spacing * 0.8;
      const py = y + (next() - 0.5) * spacing * 0.6;
      if (![[0, 0], [26, 0], [-26, 0], [0, 22], [0, -14]].every(([dx, dy]) => inside(px + dx, py + dy))) continue;
      const [ex, ey, erx, ery] = pond;
      if (((px - ex) / erx) ** 2 + ((py - ey) / ery) ** 2 < 1.4) continue;
      if (avoid.some(([ax, ay, r]) => Math.hypot(ax - px, ay - py) < r)) continue;
      if (next() < 0.22) continue;
      trees.push([px, py, 0.7 + next() * 0.45]);
    }
  }
  trees.sort((a, b) => a[1] - b[1]);
  treeCache.set(key, trees);
  return trees;
}

function Tree({ x, y, s, pine }: { x: number; y: number; s: number; pine: boolean }) {
  if (pine)
    return <g>
      <ellipse cx={x} cy={y + 2} rx={13 * s} ry={5 * s} fill="#000" opacity=".14" />
      <rect x={x - 2.5 * s} y={y - 10 * s} width={5 * s} height={10 * s} fill="#7b4f2e" />
      <path d={`M${x} ${y - 46 * s}L${x + 15 * s} ${y - 8 * s}L${x - 15 * s} ${y - 8 * s}Z`} className="c3" />
      <path d={`M${x} ${y - 46 * s}L${x + 6 * s} ${y - 20 * s}L${x - 11 * s} ${y - 14 * s}Z`} className="c2" />
    </g>;
  return <g>
    <ellipse cx={x + 3} cy={y + 2} rx={18 * s} ry={6 * s} fill="#000" opacity=".14" />
    <rect x={x - 3 * s} y={y - 16 * s} width={6 * s} height={16 * s} rx={2 * s} fill="#86532f" />
    <circle cx={x} cy={y - 26 * s} r={17 * s} className="c3" />
    <circle cx={x - 4 * s} cy={y - 30 * s} r={13 * s} className="c2" />
    <circle cx={x - 8 * s} cy={y - 36 * s} r={6 * s} className="c1" />
  </g>;
}

function LandmarkArt({ kind, x, y }: { kind: Landmark; x: number; y: number }) {
  const art = kind === "library" ? <>
    <ellipse cx="0" cy="4" rx="46" ry="10" fill="#000" opacity=".14" />
    <rect x="-38" y="-30" width="76" height="34" rx="3" fill="#fff6e6" />
    <rect x="-38" y="-6" width="76" height="10" fill="#efe0c4" />
    {[-28, -14, 0, 14, 28].map((cx) => <rect key={cx} x={cx - 3} y="-28" width="6" height="24" fill="#e8d6b4" />)}
    <path d="M-46 -30 L0 -56 L46 -30 Z" className="roof" />
    <circle cx="0" cy="-40" r="6" fill="#fff6e6" />
    <rect x="-8" y="-18" width="16" height="22" rx="8" fill="#6b4a2f" />
  </> : kind === "observatory" ? <>
    <ellipse cx="0" cy="4" rx="34" ry="8" fill="#000" opacity=".14" />
    <rect x="-26" y="-34" width="52" height="38" rx="4" fill="#fff6e6" />
    <path d="M-30 -34 A30 30 0 0 1 30 -34 Z" fill="#dce7f5" />
    <rect x="-7" y="-20" width="14" height="24" rx="7" className="roof" />
    <path d="M10 -64 l14 -14" stroke="#4a5677" strokeWidth="5" strokeLinecap="round" />
  </> : kind === "village" ? <>
    {([[0, 0, 1], [34, 16, 0.8], [-30, 18, 0.75]] as const).map(([dx, dy, s]) => <g key={dx} transform={`translate(${dx} ${dy}) scale(${s})`}>
      <ellipse cx="2" cy="3" rx="22" ry="6" fill="#000" opacity=".14" />
      <rect x="-16" y="-20" width="32" height="23" fill="#fff6e6" />
      <path d="M-21 -18 L0 -38 L21 -18 Z" className="roof" />
      <rect x="-5" y="-11" width="10" height="14" rx="2" fill="#7a5235" />
      <rect x="7" y="-14" width="6" height="6" fill="#8fd2ff" />
    </g>)}
  </> : <>
    <ellipse cx="0" cy="4" rx="26" ry="7" fill="#000" opacity=".14" />
    <rect x="-16" y="-60" width="32" height="64" rx="3" fill="#fff6e6" />
    <path d="M-22 -60 L0 -88 L22 -60 Z" className="roof" />
    <rect x="-5" y="-44" width="10" height="12" rx="5" fill="#8fd2ff" />
    <rect x="-6" y="-16" width="12" height="20" rx="6" fill="#6b4a2f" />
    <path d="M0 -88 v-16" stroke="#5b4636" strokeWidth="3" />
    <path d="M0 -104 l16 5 -16 5z" fill="#ff6b5e" />
  </>;
  return <g transform={`translate(${x} ${y}) scale(.82)`}>{art}</g>;
}

/** 島嶼插畫；sea 決定是否畫海面，landmarks 決定是否畫建築。 */
export const IslandArt = memo(function IslandArt({ palette, sea = false, landmarks = true, viewBox = "70 50 880 480", className }: {
  palette: IslandPalette; sea?: boolean; landmarks?: boolean; viewBox?: string; className?: string;
}) {
  const p = palettes[palette];
  const id = `island-${palette}-${sea ? "s" : "n"}`;
  const trees = scatterTrees(64, landmarks, 3);
  return <svg viewBox={viewBox} preserveAspectRatio="xMidYMid meet" className={`island-art ${id}${className ? ` ${className}` : ""}`} aria-hidden="true" focusable="false">
    <defs>
      <linearGradient id={`${id}-sea`} x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor={p.sea1} /><stop offset="1" stopColor={p.sea2} /></linearGradient>
      <style>{`.${id} .c1{fill:${p.c1}}.${id} .c2{fill:${p.c2}}.${id} .c3{fill:${p.c3}}.${id} .roof{fill:${p.roof}}`}</style>
    </defs>
    {sea && <rect x="-500" y="-500" width="2000" height="1600" fill={`url(#${id}-sea)`} />}
    <path d={shape} transform="translate(520 300) scale(1.09) translate(-520 -300)" fill="#fff" opacity={sea ? 0.28 : 0.35} />
    <path d={shape} transform="translate(0 40)" fill="#0b3b6b" opacity=".18" />
    <path d={shape} transform="translate(0 26) translate(520 300) scale(1.045) translate(-520 -300)" fill={p.sand} />
    <path d={shape} transform="translate(0 24)" fill={p.cliff2} />
    <path d={shape} transform="translate(0 14)" fill={p.cliff} />
    <path d={shape} fill={p.g2} />
    <path d={shape} transform="translate(500 286) scale(.86) translate(-520 -300)" fill={p.g1} opacity=".55" />
    <ellipse cx="420" cy="420" rx="120" ry="40" fill={p.g3} opacity=".22" />
    <ellipse cx="395" cy="300" rx="50" ry="22" fill={p.sand} />
    <ellipse cx="395" cy="298" rx="43" ry="17" fill={p.sea1} />
    <path d="M375 294 q8 -4 16 0" stroke="#fff" strokeWidth="2.5" fill="none" strokeLinecap="round" opacity=".8" />
    <path d="M700 232 C732 165 772 104 818 100 C866 104 892 172 908 238 C850 256 758 254 700 232 Z" fill={p.g3} />
    <path d="M700 232 C732 165 772 104 818 100 C800 140 790 200 795 250 C760 250 725 244 700 232 Z" fill={p.g2} />
    <path d="M818 100 l0 -30" stroke="#5b4636" strokeWidth="3" />
    <path d="M818 70 l22 7 -22 7z" fill="#ff6b5e" />
    {landmarks && landmarkSpots.map(([kind, x, y]) => <LandmarkArt key={kind} kind={kind} x={x} y={y} />)}
    {trees.map(([x, y, s], index) => <Tree key={index} x={x} y={y} s={s} pine={p.pine && index % 3 === 1} />)}
  </svg>;
});
