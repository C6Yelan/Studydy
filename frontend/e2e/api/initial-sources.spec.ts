import { expect, test } from "@playwright/test";
import { readFileSync } from "node:fs";
import type {
  MaterialLibraryItem,
  KnowledgeStructureView,
  SourceListView,
} from "../../src/api/contracts";

test.skip(
  process.env.STUDYDY_E2E_INITIAL_REAL !== "true",
  "Requires isolated API/database/worker fixture",
);

test("mixed sources build one map and resolve every evidence source", async ({ page }) => {
  await page.setViewportSize({ width: 1536, height: 844 });
  await page.goto("/login");
  await page.getByLabel("Email", { exact: true }).fill("learner_test@example.com");
  await page.getByLabel("密碼", { exact: true }).fill("Synthetic test password 42");
  await page.getByRole("button", { name: "登入", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "歡迎回來！", level: 1, exact: true }),
  ).toBeVisible();
  await page.goto("/upload");
  await page.getByLabel("選擇教材檔案", { exact: true }).setInputFiles([
    {
      name: "Initial.pdf",
      mimeType: "application/pdf",
      buffer: readFileSync(process.env.STUDYDY_E2E_INITIAL_PDF!),
    },
    {
      name: "Queue.txt",
      mimeType: "text/plain",
      buffer: Buffer.from("A queue removes the first inserted element first.\n"),
    },
    {
      name: "Tree.md",
      mimeType: "text/markdown",
      buffer: Buffer.from("A binary tree consists of a root with left and right child nodes.\n"),
    },
  ]);
  await page.getByRole("button", { name: "上傳並確認來源", exact: true }).click();
  await expect(page).toHaveURL(/\/materials\/[0-9a-f-]+\/sources$/);
  const materialId = new URL(page.url()).pathname.split("/")[2];
  const start = page.getByRole("button", { name: "開始分析教材", exact: true });
  await expect(start).toBeEnabled({ timeout: 20000 });
  await page.reload();
  await expect(start).toBeEnabled();
  const before: MaterialLibraryItem = await (
    await page.request.get(`/v1/materials/${materialId}`)
  ).json();
  expect(before.latest_attempt).toBeNull();
  await page.getByRole("button", { name: "上移 Tree.md", exact: true }).click();
  await page.getByRole("button", { name: "上移 Tree.md", exact: true }).click();
  await expect(page.locator(".source-row").first()).toContainText("Tree.md");
  await start.click();
  await expect(page.getByRole("heading", { name: "教材整理完成", exact: true })).toBeVisible({
    timeout: 20000,
  });
  const after: MaterialLibraryItem = await (
    await page.request.get(`/v1/materials/${materialId}`)
  ).json();
  expect(after.source_count).toBe(3);
  expect(after.available_structures).toHaveLength(1);
  expect(after.head_revision).toBe(after.available_structures[0].knowledge_structure_revision);
  const listing: SourceListView = await (
    await page.request.get(`/v1/materials/${materialId}/sources`)
  ).json();
  expect(listing.sources).toHaveLength(3);
  expect(listing.sources.every((source) => source.included && source.status === "ready")).toBe(
    true,
  );
  await page.getByRole("button", { name: "開啟知識地圖", exact: true }).click();
  const map: KnowledgeStructureView = await (
    await page.request.get(
      `/v1/materials/${materialId}/knowledge-structures/${encodeURIComponent(after.head_revision!)}`,
    )
  ).json();
  const names = new Set<string>();
  for (const concept of map.concepts)
    for (const claim of concept.claims)
      for (const evidence of claim.evidence) {
        const expectedSource = listing.sources.find(
          (source) => source.source_id === evidence.source_id,
        )!;
        expect(expectedSource).toBeDefined();
        expect(evidence.source_name).toBe(expectedSource.original_name);
        expect(evidence.normalized_page).toBe(1);
        const response = await page.request.get(
          `${map.source_resolver}/${evidence.evidence_id}/source`,
        );
        expect(response.status()).toBe(200);
        const source = await response.json();
        names.add(source.original_name);
        expect(source.original_name).toBe(expectedSource.original_name);
        expect(source.normalized_page).toBe(evidence.normalized_page);
        expect(source.original_url).toBe(
          `/v1/artifacts/${expectedSource.original_artifact_id}/download`,
        );
        expect(source.preview_url).toBe(
          `/v1/artifacts/${expectedSource.normalized_artifact_id}#page=${evidence.normalized_page}`,
        );
        const preview = await page.request.get(source.preview_url.split("#")[0]);
        expect(preview.status()).toBe(200);
        expect((await preview.body()).subarray(0, 4).toString()).toBe("%PDF");
      }
  expect([...names].sort()).toEqual(["Initial.pdf", "Queue.txt", "Tree.md"].sort());
  expect(
    map.concepts
      .flatMap((c) => c.claims.flatMap((claim) => claim.evidence))
      .find((e) => e.page === 1)?.source_name,
  ).toBe("Tree.md");
});
