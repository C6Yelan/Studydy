import { expect, test } from "@playwright/test";
import { readFileSync } from "node:fs";
import type { MaterialLibraryItem, KnowledgeStructureView, SourceListView } from "../src/api/contracts";

test.skip(process.env.STUDYDY_E2E_INITIAL_REAL !== "true", "Requires isolated API/database/worker fixture");

for (const width of [1536, 390]) test(`real initial mixed sources build one map and resolve each file at ${width}px`, async ({ page }, info) => {
  await page.setViewportSize({ width, height: 844 });
  await page.goto("/login");
  await page.getByLabel("Email", { exact: true }).fill("learner_test@example.com");
  await page.getByLabel("密碼", { exact: true }).fill("Synthetic test password 42");
  await page.getByRole("button", { name: "登入", exact: true }).click();
  await expect(page.getByRole("heading", { name: "歡迎回來！", level: 1, exact: true })).toBeVisible();
  await page.goto("/upload");
  await expect(page.locator(".conversion-note")).toContainText("TXT");
  await page.getByLabel("選擇教材檔案", { exact: true }).setInputFiles([
    { name: `Initial-${width}.pdf`, mimeType: "application/pdf", buffer: readFileSync(process.env.STUDYDY_E2E_INITIAL_PDF!) },
    { name: "Queue.txt", mimeType: "text/plain", buffer: Buffer.from("A queue removes the first inserted element first.\n") },
    { name: "Tree.md", mimeType: "text/markdown", buffer: Buffer.from("A binary tree consists of a root with left and right child nodes.\n") },
  ]);
  await page.getByRole("button", { name: "上傳並確認來源", exact: true }).click();
  await expect(page).toHaveURL(/\/materials\/[0-9a-f-]+\/sources$/);
  const materialId = new URL(page.url()).pathname.split("/")[2];
  const start = page.getByRole("button", { name: "開始分析教材", exact: true });
  await expect(start).toBeEnabled({ timeout: 20000 });
  await page.reload(); await expect(start).toBeEnabled();
  const before: MaterialLibraryItem = await (await page.request.get(`/v1/materials/${materialId}`)).json();
  expect(before.latest_attempt).toBeNull();
  await page.getByRole("button", { name: "上移 Tree.md", exact: true }).click();
  await page.getByRole("button", { name: "上移 Tree.md", exact: true }).click();
  await expect(page.locator(".source-card").first()).toContainText("Tree.md");
  await expect(page.locator(".source-confirmation")).toContainText("已選 3 份來源，共 3 頁");
  await page.screenshot({ path: info.outputPath("initial-confirmation.png"), fullPage: true });
  await start.click();
  await expect(page.getByRole("heading", { name: "教材整理完成", exact: true })).toBeVisible({ timeout: 20000 });
  const after: MaterialLibraryItem = await (await page.request.get(`/v1/materials/${materialId}`)).json();
  expect(after.source_count).toBe(3); expect(after.available_structures).toHaveLength(1);
  expect(after.head_revision).toBe(after.available_structures[0].knowledge_structure_revision);
  const listing: SourceListView = await (await page.request.get(`/v2/materials/${materialId}/sources`)).json();
  expect(listing.sources.every(source => source.included && source.status === "ready")).toBe(true);
  await page.getByRole("button", { name: "開啟知識地圖", exact: true }).click();
  const map: KnowledgeStructureView = await (await page.request.get(`/v1/materials/${materialId}/knowledge-structures/${encodeURIComponent(after.head_revision!)}`)).json();
  const names = new Set<string>();
  for (const concept of map.concepts) for (const claim of concept.claims) for (const evidence of claim.evidence) {
    const response = await page.request.get(`${map.source_resolver}/${evidence.evidence_id}/source`);
    expect(response.status()).toBe(200);
    const source = await response.json(); names.add(source.original_name);
    expect(source.normalized_page).toBe(1);
    expect((await page.request.get(source.preview_url.split("#")[0])).status()).toBe(200);
  }
  expect([...names].sort()).toEqual([`Initial-${width}.pdf`, "Queue.txt", "Tree.md"].sort());
  expect(map.concepts.flatMap(c => c.claims.flatMap(claim => claim.evidence)).find(e => e.page === 1)?.source_name).toBe("Tree.md");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath("initial-map.png"), fullPage: true });
  await page.getByRole("button", { name: "登出", exact: true }).click();
});
