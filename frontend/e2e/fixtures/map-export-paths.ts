import type { Page } from '@playwright/test';

type Point = [number, number];
export async function recordMapExportPaths(page: Page) {
  await page.addInitScript(() => {
    (window as any).mapExportPaths = [];
    const paths = new WeakMap<HTMLCanvasElement, { points: [number, number][]; curved: boolean }>();
    const prototype = CanvasRenderingContext2D.prototype;
    for (const name of ['beginPath', 'moveTo', 'lineTo', 'bezierCurveTo', 'stroke'] as const) {
      const original = prototype[name];
      (prototype as any)[name] = function (...args: number[]) {
        if (this.canvas.classList.contains('map-export-edges')) {
          if (name === 'beginPath') paths.set(this.canvas, { points: [], curved: false });
          const path = paths.get(this.canvas);
          if (path && (name === 'moveTo' || name === 'lineTo')) path.points.push([args[0], args[1]]);
          if (path && name === 'bezierCurveTo') {
            const start = path.points.at(-1)!;
            for (let step = 1; step <= 32; step++) {
              const t = step / 32, u = 1 - t;
              path.points.push([
                u ** 3 * start[0] + 3 * u ** 2 * t * args[0] + 3 * u * t ** 2 * args[2] + t ** 3 * args[4],
                u ** 3 * start[1] + 3 * u ** 2 * t * args[1] + 3 * u * t ** 2 * args[3] + t ** 3 * args[5],
              ]);
            }
            path.curved = true;
          }
          if (path?.curved && name === 'stroke') (window as any).mapExportPaths.push(path.points);
        }
        return Reflect.apply(original, this, args);
      };
    }
  });
}

export function mapPathCrossings(paths: Point[][]) {
  const near = (a: Point, b: Point) => Math.hypot(a[0] - b[0], a[1] - b[1]) < .5;
  let count = 0;
  for (let i = 0; i < paths.length; i++) for (let j = i + 1; j < paths.length; j++) {
    const first = paths[i], second = paths[j];
    const shared = [first[0], first.at(-1)!].filter(a => [second[0], second.at(-1)!].some(b => near(a, b)));
    const hits: Point[] = [];
    for (let a = 1; a < first.length; a++) for (let b = 1; b < second.length; b++) {
      const p = first[a - 1], q = second[b - 1];
      const dx = first[a][0] - p[0], dy = first[a][1] - p[1];
      const ex = second[b][0] - q[0], ey = second[b][1] - q[1];
      const determinant = dx * ey - dy * ex;
      if (Math.abs(determinant) < 1e-9) continue;
      const t = ((q[0] - p[0]) * ey - (q[1] - p[1]) * ex) / determinant;
      const u = ((q[0] - p[0]) * dy - (q[1] - p[1]) * dx) / determinant;
      if (t < 0 || t > 1 || u < 0 || u > 1) continue;
      const hit: Point = [p[0] + t * dx, p[1] + t * dy];
      if (!shared.some(endpoint => near(hit, endpoint)) && !hits.some(other => near(hit, other))) hits.push(hit);
    }
    count += hits.length;
  }
  return count;
}
