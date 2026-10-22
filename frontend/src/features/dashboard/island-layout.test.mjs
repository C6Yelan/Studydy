import assert from "node:assert/strict";
import test from "node:test";
import { cellX, layoutIslands } from "./island-layout.ts";

const material = (index, pages = 10) => ({
  material_id: `00000000-0000-4000-8000-${String(index).padStart(12, "0")}`,
  created_at: `2026-09-01T00:${String(index).padStart(2, "0")}:00Z`,
  latest_attempt: { total_pages: pages },
});
const positions = (materials) =>
  new Map(layoutIslands(materials).islands.map((island) => [island.material.material_id, [island.x, island.y]]));

test("adding a newer material keeps every existing island in place", () => {
  const items = Array.from({ length: 30 }, (_, index) => material(index));
  for (let count = 1; count < items.length; count++) {
    const before = positions(items.slice(0, count));
    const after = positions(items.slice(0, count + 1));
    for (const [id, point] of before) assert.deepEqual(after.get(id), point);
  }
});

test("islands keep the spacing the zoom-scaled labels rely on and never share a spot", () => {
  // 標籤寬度以「同列相鄰至少 400 地圖像素」計算（styles.css 的 --k * 400px）。
  const { islands, newSpot, bounds } = layoutIslands(Array.from({ length: 40 }, (_, index) => material(index, index * 37)));
  for (const [index, island] of islands.entries())
    for (const other of islands.slice(index + 1)) {
      const sameRow = Math.abs(island.y - other.y) < 60;
      const distance = Math.hypot(island.x - other.x, island.y - other.y);
      if (sameRow) assert.ok(Math.abs(island.x - other.x) >= cellX - 60, `${island.x},${island.y} / ${other.x},${other.y}`);
      else assert.ok(distance >= 300);
    }
  for (const island of islands) {
    assert.ok(island.w >= 260 && island.w <= 360);
    assert.ok(island.x - island.w / 2 >= bounds.minX && island.x + island.w / 2 <= bounds.maxX);
    assert.ok(Math.hypot(island.x - newSpot.x, island.y - newSpot.y) >= 300);
  }
  // 由上往下排列，下方島的圖不蓋住上方島的標籤。
  assert.deepEqual(islands.map((island) => island.y), [...islands.map((island) => island.y)].sort((a, b) => a - b));
});

test("the layout is wider than tall so it fills a landscape map", () => {
  const { bounds } = layoutIslands(Array.from({ length: 12 }, (_, index) => material(index)));
  assert.ok(bounds.maxX - bounds.minX > (bounds.maxY - bounds.minY) * 1.3);
});

test("an empty library still has a spot for the first island", () => {
  const { islands, newSpot } = layoutIslands([]);
  assert.equal(islands.length, 0);
  assert.deepEqual(newSpot, { x: 0, y: 0 });
});
