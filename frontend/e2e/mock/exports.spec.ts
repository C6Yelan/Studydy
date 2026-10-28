import { recordMapExportPaths, mapPathCrossings } from "../fixtures/map-export-paths";
import { test, expect } from "@playwright/test";
import { writeFile } from "node:fs/promises";
import { mockKnowledgeMapApi, workspaceView, materialId, structureRevision, runId, json } from "../fixtures/knowledge-map";

test("large map exports every region and splits oversized regions without retaining prior edges", async ({ page }) => {
  const view = workspaceView(30, true);
  await mockKnowledgeMapApi(page, view);
  await page.route("**/regions", route => json(route, { schema: "knowledge-regions/v1", material_id: materialId,
    knowledge_structure_revision: structureRevision, basis: "learning_order", groups: [], partial: false,
    regions: [
      { region_id: "first", label: "第一區域", source_id: null, concept_ids: view.concepts.slice(0, 25).map(c => c.concept_id) },
      { region_id: "second", label: "第二區域", source_id: null, concept_ids: view.concepts.slice(25).map(c => c.concept_id) },
    ] }));
  let images: { concept_ids: string[]; png: string }[] = [];
  await page.route("**/map/export/png", async route => {
    images = route.request().postDataJSON().images;
    expect(await page.locator(".map-export-edges").count()).toBe(0);
    await writeFile(test.info().outputPath("large-map-first.png"), Buffer.from(images[0].png, "base64"));
    await writeFile(test.info().outputPath("large-map-last.png"), Buffer.from(images.at(-1)!.png, "base64"));
    return route.fulfill({ status: 200, contentType: "application/zip", headers: { "Content-Disposition": "attachment; filename*=UTF-8''maps.zip" }, body: "zip" });
  });
  await page.goto(`/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}`);
  await page.getByText("匯出地圖", { exact: true }).click();
  const controls = page.getByRole("dialog", { name: "匯出知識地圖" });
  const download = page.waitForEvent("download");
  await controls.getByRole("button", { name: "匯出", exact: true }).click();
  expect((await download).suggestedFilename()).toBe("maps.zip");
  expect(images.map(i => i.concept_ids.length)).toEqual([24, 1, 5]);
  expect(images.flatMap(i => i.concept_ids)).toEqual(view.concepts.map(c => c.concept_id));
  expect(images.every(i => i.png.startsWith("iVBOR"))).toBe(true);
});

test("export format cards retain errors, keyboard selection and focus through download", async ({ page }) => {
  await mockKnowledgeMapApi(page);
  let release!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  let fail = true, requests = 0;
  await page.route("**/map/export/md", async route => {
    requests++;
    await gate;
    return fail ? json(route, { schema: "api-error/v1", request_id: materialId, reason_code: "STORAGE_UNAVAILABLE", retryable: true, message: "Unavailable" }, 503)
      : route.fulfill({ status: 200, contentType: "text/markdown", headers: { "Content-Disposition": "attachment; filename*=UTF-8''map.md" }, body: "# Map" });
  });
  await page.goto(`/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}`);
  const trigger = page.getByRole("button", { name: "匯出地圖", exact: true });
  await trigger.click();
  const dialog = page.getByRole("dialog", { name: "匯出知識地圖" });
  await dialog.getByRole("radio").first().focus();
  await page.keyboard.press("ArrowDown");
  await expect(dialog.getByRole("radio", { name: "概念大綱 Markdown" })).toBeChecked();
  for (const [width, height] of [[1440, 900], [1300, 900], [1200, 900], [1024, 768], [390, 844]]) {
    await page.setViewportSize({ width, height });
    const bounds = (await dialog.boundingBox())!;
    expect(bounds.x).toBeGreaterThanOrEqual(0);
    expect(bounds.x + bounds.width).toBeLessThanOrEqual(width);
    expect(bounds.y + bounds.height).toBeLessThanOrEqual(height);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(width);
    await page.screenshot({ path: test.info().outputPath(`export-dialog-${width}.png`) });
  }
  await dialog.getByRole("button", { name: "匯出", exact: true }).click();
  await expect(dialog.getByRole("status")).toHaveText("正在準備下載檔案…");
  await expect(dialog.getByRole("button", { name: "正在匯出…" })).toBeDisabled();
  await page.keyboard.press("Escape");
  await expect(dialog).toBeVisible();
  release();
  await expect(dialog.getByRole("alert")).toBeVisible();
  await expect(dialog.getByRole("radio", { name: "概念大綱 Markdown" })).toBeChecked();
  await dialog.getByRole("button", { name: "關閉匯出" }).click();
  await expect(dialog).toBeHidden();
  await expect(trigger).toBeFocused();
  await trigger.click();
  await expect(dialog.getByRole("radio", { name: "概念大綱 Markdown" })).toBeChecked();
  fail = false;
  const download = page.waitForEvent("download");
  await dialog.getByRole("button", { name: "匯出", exact: true }).click();
  expect((await download).suggestedFilename()).toBe("map.md");
  await expect(dialog).toBeHidden();
  await expect(trigger).toBeFocused();
  expect(requests).toBe(2);
});


test("map PNG reorders crossing prerequisite paths without losing concepts", async ({page}) => {
  const view=workspaceView(4);
  const ids=view.concepts.map(c=>c.concept_id);
  view.relations=[[0,3],[1,2]].map(([source,target],index)=>({...view.relations[0],relation_id:`relation:sha256:${String(index+20).padStart(64,'0')}`,source_concept_id:ids[source],target_concept_id:ids[target]}));
  await recordMapExportPaths(page);
  await mockKnowledgeMapApi(page,view);
  await page.route('**/regions',route=>json(route,{schema:'knowledge-regions/v1',material_id:materialId,knowledge_structure_revision:structureRevision,basis:'learning_order',groups:[],partial:false,regions:[{region_id:'crossing',label:'交叉連線驗證',source_id:null,concept_ids:ids}]}));
  let images:{concept_ids:string[];png:string}[]=[];
  await page.route('**/map/export/png',route=>{images=route.request().postDataJSON().images;return route.fulfill({contentType:'image/png',headers:{'Content-Disposition':"attachment; filename*=UTF-8''map.png"},body:'png'});});
  await page.goto(`/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}`);
  await page.getByText('匯出地圖',{exact:true}).click();
  const download=page.waitForEvent('download');
  await page.getByRole('dialog',{name:'匯出知識地圖'}).getByRole('button',{name:'匯出',exact:true}).click();
  await download;
  const paths=await page.evaluate(()=>(window as any).mapExportPaths);
  expect(paths).toHaveLength(2);
  expect(mapPathCrossings(paths)).toBe(0);
  expect(images.flatMap(image=>image.concept_ids)).toEqual(ids);
  expect(images[0].png.startsWith('iVBOR')).toBe(true);
});


test("map crossing count excludes shared endpoints without hiding later intersections", () => {
  expect(mapPathCrossings([[[0,0],[2,2],[4,0]],[[0,0],[4,2]]])).toBe(1);
});
