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
  const controls = page.getByRole("group", { name: "匯出知識地圖" });
  const download = page.waitForEvent("download");
  await controls.getByRole("button", { name: "匯出", exact: true }).click();
  expect((await download).suggestedFilename()).toBe("maps.zip");
  expect(images.map(i => i.concept_ids.length)).toEqual([24, 1, 5]);
  expect(images.flatMap(i => i.concept_ids)).toEqual(view.concepts.map(c => c.concept_id));
  expect(images.every(i => i.png.startsWith("iVBOR"))).toBe(true);
});
