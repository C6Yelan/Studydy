import { test, expect } from "@playwright/test";
import { mkdir, writeFile } from "node:fs/promises";
import { mockKnowledgeMapApi, workspaceView, materialId, structureRevision, runId, json } from "../fixtures/knowledge-map";

const names = ["極限", "導數", "連續性與間斷點的分類方式", "中間值定理", "微分中值定理與其在不等式證明中的應用", "洛必達法則", "泰勒展開式", "不定積分", "定積分的黎曼和定義與幾何意義", "微積分基本定理",
  "分部積分", "換元積分法", "瑕積分", "級數收斂判別法", "冪級數", "偏導數", "全微分", "重積分", "線積分", "格林定理與斯托克斯定理的關係"];

// 以固定亂數產生貼近真實教材的地圖：章節大小不一、先備關係有深有淺、含跨章節連線與孤立概念。
function realisticView(count: number, chapterSizes: number[], isolatedEvery: number, reach = 9) {
  const view = workspaceView(count);
  let seed = 7; const rand = () => (seed = (seed * 1103515245 + 12345) % 2147483648) / 2147483648;
  view.concepts.forEach((c, i) => { c.label = `${names[i % names.length]}${i >= names.length ? ` ${Math.floor(i / names.length) + 1}` : ""}`; });
  const ids = view.concepts.map(c => c.concept_id);
  const chapters: string[][] = []; let at = 0;
  for (const size of chapterSizes) { chapters.push(ids.slice(at, at + size)); at += size; }
  const template = view.relations[0];
  const types = ["prerequisite", "prerequisite", "prerequisite", "part_of", "application", "example", "contrast"] as const;
  const relations: typeof view.relations = [];
  const add = (a: number, b: number, type: string) => relations.push({ ...template, type, relation_id: `relation:sha256:${(relations.length + 500).toString(16).padStart(64, "0")}`, source_concept_id: ids[a], target_concept_id: ids[b] } as never);
  for (let i = 1; i < count; i++) {
    if (i % isolatedEvery === 0) continue;
    const links = 1 + Math.floor(rand() * 2.2);
    for (let k = 0; k < links; k++) {
      const a = Math.max(0, i - 1 - Math.floor(rand() * reach));
      if (a % isolatedEvery === 0 && a !== 0) continue;
      add(a, i, k === 0 ? "prerequisite" : types[Math.floor(rand() * types.length)]);
    }
  }
  view.relations = relations;
  return { view, chapters };
}

for (const [name, count, sizes, iso, reach = 9] of [["large", 66, [14, 9, 12, 7, 10, 6, 8], 7], ["small", 7, [7], 100], ["chain", 24, [24], 100, 9], ["deep", 20, [20], 100, 2]] as const) {
  test(`export shape ${name}`, async ({ page }) => {
    const { view, chapters } = realisticView(count, [...sizes], iso, reach);
    await mockKnowledgeMapApi(page, view);
    await page.route("**/regions", route => json(route, { schema: "knowledge-regions/v1", material_id: materialId,
      knowledge_structure_revision: structureRevision, basis: "chapters", groups: [], partial: false,
      regions: chapters.map((ids, i) => ({ region_id: `r${i}`, label: `第 ${i + 1} 章 ${["極限與連續", "微分學", "微分應用與最佳化問題", "積分", "級數", "多變數函數", "向量分析"][i % 7]}`, source_id: null, concept_ids: ids })) }));
    let images: { concept_ids: string[]; png: string }[] = [];
    await page.route("**/map/export/png", async route => {
      images = route.request().postDataJSON().images;
      const dir = process.env.MAP_SHAPE_OUT; // 設定後把 PNG 存出來目視檢查
      if (dir) { await mkdir(dir, { recursive: true }); for (const [i, image] of images.entries()) await writeFile(`${dir}/${name}-${i + 1}.png`, Buffer.from(image.png, "base64")); }
      return route.fulfill({ status: 200, contentType: "application/zip", headers: { "Content-Disposition": "attachment; filename*=UTF-8''maps.zip" }, body: "zip" });
    });
    await page.goto(`/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}`);
    await page.getByText("匯出地圖", { exact: true }).click();
    const download = page.waitForEvent("download");
    await page.getByRole("dialog", { name: "匯出知識地圖" }).getByRole("button", { name: "匯出", exact: true }).click();
    await download;
    const sizesOut = images.map(i => { const b = Buffer.from(i.png, "base64"); return [b.readUInt32BE(16), b.readUInt32BE(20)]; });
    expect(images.length).toBe(chapters.length);
    // 真實規模的章節圖不應極細長，也不應寬到無法閱讀。
    for (const [width, height] of sizesOut) { expect(width / height).toBeGreaterThan(0.6); expect(width / height).toBeLessThan(2.6); expect(width).toBeLessThan(2400); }
  });
}
