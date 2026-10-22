import type { MaterialLibraryItem } from "../../api/contracts";

// 群島地圖的島位：放在六角格上，依建立時間由中心往外排，新增教材不會讓既有的島搬家。
// 座標以「地圖像素」計算（縮放 1 時等於螢幕像素）。

export const cellX = 460;
export const cellY = 330;
const islandAspect = 470 / 840;

type Placed = { material: MaterialLibraryItem; x: number; y: number; w: number; h: number };
type Bounds = { minX: number; minY: number; maxX: number; maxY: number };

function hash(seed: string) {
  let value = 0;
  for (const char of seed) value = (value * 31 + char.charCodeAt(0)) >>> 0;
  return value;
}

// 六角格依「橢圓距離」由中心往外排（橫向放寬 1.7 倍），島群呈寬版，適合橫向的地圖畫面。
// 排序只依格子座標，教材增加時前面的格子不變。
function hexCells(count: number): [number, number][] {
  const radius = Math.ceil(Math.sqrt(count)) + 2;
  const cells: [number, number, number, number][] = [];
  for (let q = -radius * 2; q <= radius * 2; q++)
    for (let r = -radius; r <= radius; r++) {
      const x = cellX * (q + r / 2), y = cellY * r;
      cells.push([q, r, (x / 1.7) ** 2 + (y * 1.1) ** 2, Math.atan2(y, x)]);
    }
  cells.sort((a, b) => a[2] - b[2] || a[3] - b[3]);
  return cells.slice(0, count).map(([q, r]) => [q, r]);
}

export function layoutIslands(materials: MaterialLibraryItem[]) {
  const ordered = [...materials].sort((a, b) => a.created_at.localeCompare(b.created_at) || a.material_id.localeCompare(b.material_id));
  const cells = hexCells(ordered.length + 1);
  const islands: Placed[] = ordered.map((material, index) => {
    const [q, r] = cells[index];
    const seed = hash(material.material_id);
    // 頁數越多島越大（對數比例），只是視覺大小，不代表學習量。
    const w = Math.min(360, Math.max(260, 230 + 45 * Math.log10((material.latest_attempt?.total_pages ?? 0) + 1)));
    return { material, x: cellX * (q + r / 2) + ((seed % 61) - 30), y: cellY * r + (((seed >>> 8) % 41) - 20), w, h: w * islandAspect };
  });
  const [q, r] = cells[ordered.length];
  const newSpot = { x: cellX * (q + r / 2), y: cellY * r };
  // 範圍含島上方的標籤與外圍一圈海。
  const bounds: Bounds = { minX: newSpot.x - 160, minY: newSpot.y - 160, maxX: newSpot.x + 160, maxY: newSpot.y + 160 };
  for (const island of islands) {
    bounds.minX = Math.min(bounds.minX, island.x - island.w / 2 - 120);
    bounds.maxX = Math.max(bounds.maxX, island.x + island.w / 2 + 120);
    bounds.minY = Math.min(bounds.minY, island.y - island.h / 2 - 110);
    bounds.maxY = Math.max(bounds.maxY, island.y + island.h / 2 + 70);
  }
  // 由上往下排，下方島的圖不會蓋住上方島的標籤。
  islands.sort((a, b) => a.y - b.y);
  return { islands, newSpot, bounds };
}
