// 海岸線只由教材 ID 決定；主圖與小地圖共用相同座標。
export function islandShape(materialId: string) {
  let seed = 2166136261;
  for (const char of materialId) seed = Math.imul(seed ^ char.charCodeAt(0), 16777619) >>> 0;
  let state = seed;
  const next = () => ((state = (Math.imul(state, 1664525) + 1013904223) >>> 0) / 4294967296);
  const phases = [next(), next(), next()].map(value => value * Math.PI * 2);
  const bay = next() * Math.PI * 2;
  const bayWidth = .25 + next() * .3;
  const rx = 300 + next() * 25, ry = 140 + next() * 30;
  let scale = 1;
  const point = (angle: number, fraction = 1): [number, number] => {
    const delta = Math.atan2(Math.sin(angle - bay), Math.cos(angle - bay));
    const radius = .96 + .12 * Math.sin(2 * angle + phases[0]) + .08 * Math.sin(3 * angle + phases[1])
      + .05 * Math.sin(5 * angle + phases[2]) - .3 * Math.exp(-((delta / bayWidth) ** 2));
    return [520 + Math.cos(angle) * rx * radius * fraction * scale, 286 + Math.sin(angle) * ry * radius * fraction * scale];
  };
  const outline = Array.from({ length: 32 }, (_, index) => point(index / 32 * Math.PI * 2));
  const area = Math.abs(outline.reduce((sum, [x, y], index) => {
    const [nextX, nextY] = outline[(index + 1) % outline.length];
    return sum + x * nextY - nextX * y;
  }, 0)) / 2;
  // 原版海岸面積約 250,000 SVG 平方單位；形狀改變時仍保留相同視覺份量。
  scale = Math.sqrt(250_000 / area);
  const points = outline.map(([x, y]) => [520 + (x - 520) * scale, 286 + (y - 286) * scale]);
  const coordinate = (value: number) => value.toFixed(2);
  let path = `M${points[0].map(coordinate).join(" ")}`;
  for (let i = 0; i < points.length; i++) {
    const a = points[(i + points.length - 1) % points.length], b = points[i];
    const c = points[(i + 1) % points.length], d = points[(i + 2) % points.length];
    path += ` C${coordinate(b[0] + (c[0] - a[0]) / 6)} ${coordinate(b[1] + (c[1] - a[1]) / 6)}`
      + ` ${coordinate(c[0] - (d[0] - b[0]) / 6)} ${coordinate(c[1] - (d[1] - b[1]) / 6)}`
      + ` ${c.map(coordinate).join(" ")}`;
  }
  const lake = point(1.8 + next() * .4, .28);
  return {
    seed, path: path + " Z",
    pond: [...lake, 48, 25] as [number, number, number, number],
    hill: point(4.8 + next() * .7, .55),
    landmarks: [point(2.75, .64), point(3.8, .38), point(.8, .55), point(6.15, .68)],
  };
}
