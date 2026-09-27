import { expect, test, type Page } from "@playwright/test";
import type {
  MaterialLibraryItem,
  MaterialProcessingRunView,
  SourceView,
} from "../../src/api/contracts";
import {
  materialId,
  runId as oldRunId,
  sessionId,
  artifactId,
  structureRevision as revision,
  run as fixtureRun,
  session,
  progress,
  structureView,
  mockKnowledgeMapApi,
} from "../fixtures/knowledge-map";

const uuid = (seed: number) => `11111111-1111-4111-8111-${String(seed).padStart(12, "0")}`;
const publishedRun = fixtureRun as MaterialProcessingRunView;
const newRunId = uuid(3);
const newRevision = `knowledge-structure:sha256:${"c".repeat(64)}`;
const timestamp = "2026-09-18T00:00:00Z";
const updatePath = `/materials/${materialId}/runs/${newRunId}`;
const mapPath = (runId: string, revision: string) =>
  `/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(revision)}`;
const material: MaterialLibraryItem = {
  schema: "material-library-item/v1",
  material_id: materialId,
  head_revision: revision,
  source_artifact_id: artifactId,
  display_name: "資料結構",
  size_bytes: 1024,
  created_at: timestamp,
  latest_attempt: publishedRun,
  available_structures: [
    {
      run_id: oldRunId,
      knowledge_structure_revision: revision,
      created_at: timestamp,
      status: "succeeded",
    },
  ],
  study_sessions: [
    {
      study_session_id: sessionId,
      run_id: oldRunId,
      knowledge_structure_revision: revision,
      current_concept_id: progress.current_concept_id,
      status: "active",
      started_at: timestamp,
    },
  ],
};

test.beforeEach(async ({ page }) => {
  await page.clock.install({ time: new Date(timestamp) });
  await page.clock.pauseAt(new Date(timestamp));
});

async function mockMaterial(page: Page, read: () => MaterialLibraryItem) {
  for (const path of ["/v1/materials", `/v1/materials/${materialId}`])
    await page.route(`**${path}`, (route) => {
      expect(route.request().method()).toBe("GET");
      const item = read();
      return route.fulfill({
        json:
          path === "/v1/materials" ? { schema: "material-library/v1", materials: [item] } : item,
      });
    });
}

for (const width of [1536, 390]) {
  test(`append and run-only cancellation retain the current map and study at ${width}px`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 844 });
    await mockKnowledgeMapApi(page);
    await page.route("**/v1/source-capabilities", (route) =>
      route.fulfill({
        json: {
          schema: "source-capabilities/v1",
          formats: [
            { extension: ".pdf", media_type: "application/pdf", max_bytes: 104857600 },
            { extension: ".txt", media_type: "text/plain", max_bytes: 104857600 },
          ],
        },
      }),
    );
    const files = [
      { name: "B.txt", mimeType: "text/plain", buffer: Buffer.from("Queue uses FIFO.") },
      {
        name: `C-${"LongChapterFilename".repeat(5)}.pdf`,
        mimeType: "application/pdf",
        buffer: Buffer.from("%PDF-synthetic"),
      },
    ];
    const sources: SourceView[] = [
      {
        source_id: uuid(10),
        normalization_id: uuid(11),
        original_artifact_id: uuid(12),
        normalized_artifact_id: artifactId,
        original_name: "A.pdf",
        media_type: "application/pdf",
        status: "ready",
        page_count: 2,
        error_code: null,
        included: true,
      },
    ];
    let run: MaterialProcessingRunView = {
      ...publishedRun,
      run_id: newRunId,
      source_artifact_id: uuid(60),
      base_revision: revision,
      source_names: ["A.pdf", ...files.map((file) => file.name)],
      status: "running",
      progress_stage: "semantics",
      completed_pages: 1,
      total_pages: 4,
      output_binding: null,
      completed_at: null,
      created_at: timestamp,
      updated_at: timestamp,
    };
    let starts = 0;
    let cancels = 0;
    let uploads = 0;
    await mockMaterial(page, () => ({
      ...material,
      source_count: sources.length,
      latest_attempt: starts ? run : publishedRun,
    }));
    await page.route(`**/v1/materials/${materialId}/sources`, (route) => {
      if (route.request().method() === "POST") {
        const file = files[uploads++];
        expect(decodeURIComponent(route.request().headers()["x-material-name"])).toBe(file.name);
        expect(route.request().headers()["content-type"]).toBe(file.mimeType);
        expect(route.request().postDataBuffer()).toEqual(file.buffer);
        const ready = uploads === 1;
        sources.push({
          source_id: uuid(20 + uploads),
          normalization_id: uuid(30 + uploads),
          original_artifact_id: uuid(40 + uploads),
          normalized_artifact_id: ready ? uuid(50 + uploads) : null,
          original_name: file.name,
          media_type: file.mimeType,
          status: ready ? "ready" : "pending",
          page_count: ready ? 1 : null,
          error_code: null,
          included: false,
        });
      } else expect(route.request().method()).toBe("GET");
      return route.fulfill({
        json: { schema: "material-sources/v1", material_id: materialId, sources },
      });
    });
    await page.route(`**/v1/materials/${materialId}/revisions`, (route) => {
      expect(route.request().method()).toBe("POST");
      expect(route.request().postDataJSON()).toEqual({
        schema: "material-revision-create/v1",
        base_revision: revision,
        normalization_ids: [uuid(31), uuid(32)],
      });
      starts++;
      return route.fulfill({ status: 202, json: run });
    });
    await page.route(`**/v1/material-processing-runs/${newRunId}`, (route) => {
      expect(route.request().method()).toBe("GET");
      return route.fulfill({ json: run });
    });
    await page.route(`**/v1/material-processing-runs/${newRunId}/cancel`, (route) => {
      expect(route.request().method()).toBe("POST");
      expect(route.request().postDataJSON()).toEqual({
        schema: "material-revision-cancel/v1",
        base_revision: revision,
      });
      cancels++;
      run = {
        ...run,
        status: "cancelled",
        cancel_requested_at: timestamp,
        completed_at: timestamp,
      };
      return route.fulfill({ json: run });
    });
    await page.goto(`/materials/${materialId}/sources`);
    await expect(page.getByLabel("選擇新增教材", { exact: true })).toBeEnabled();
    await page.getByLabel("選擇新增教材", { exact: true }).setInputFiles(files);
    await page.getByRole("button", { name: "上傳新增教材", exact: true }).click();
    const start = page.getByRole("button", { name: "確認新增並更新地圖", exact: true });
    await expect(page.getByLabel("加入這次更新")).toHaveCount(2);
    await expect(start).toHaveCount(0);
    expect({ uploads, starts }).toEqual({ uploads: 2, starts: 0 });
    Object.assign(sources[2], { status: "ready", normalized_artifact_id: uuid(52), page_count: 1 });
    await page.clock.runFor(3_000);
    await expect(start).toBeEnabled();
    await start.click();
    await expect(page.getByRole("heading", { name: "正在更新教材", exact: true })).toBeVisible();
    await expect(page.locator(".processing-sources li")).toHaveText(run.source_names!);
    await expect(page.getByRole("button", { name: "繼續學習", exact: true })).toBeEnabled();
    await expect(page.getByRole("button", { name: "取消並刪除教材", exact: true })).toHaveCount(0);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
      true,
    );
    await page.getByRole("button", { name: "取消這次更新", exact: true }).click();
    await expect(page.getByRole("heading", { name: "已取消這次更新", exact: true })).toBeVisible();
    await page.reload();
    await expect(page.getByRole("heading", { name: "已取消這次更新", exact: true })).toBeVisible();
    await page.getByRole("button", { name: "開啟目前地圖", exact: true }).click();
    await expect(page).toHaveURL(new RegExp(`${mapPath(oldRunId, revision)}$`));
    await expect(page.getByRole("region", { name: "概念地圖工作區", exact: true })).toBeVisible();
    await page.goto("/materials");
    await page.getByRole("button", { name: "繼續學習", exact: true }).click();
    await expect(page).toHaveURL(
      new RegExp(`${mapPath(oldRunId, revision)}/study-sessions/${sessionId}$`),
    );
    await expect(page.locator(".study-session-page")).toBeVisible();
    expect({ starts, cancels }).toEqual({ starts: 1, cancels: 1 });
  });

  test(`partial update keeps its notice and opens the current head at ${width}px`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 844 });
    const view = structureView(newRevision);
    view.status = {
      processing: "partial",
      quality: "needs_review",
      decision: "review",
      reason_codes: ["RELATIONS_REJECTED"],
    };
    await mockKnowledgeMapApi(page, view);
    const run: MaterialProcessingRunView = {
      ...publishedRun,
      run_id: newRunId,
      source_artifact_id: uuid(60),
      base_revision: revision,
      source_names: ["A.pdf", "B.pdf"],
      status: "partial",
      completed_pages: 3,
      total_pages: 3,
      output_binding: {
        ...publishedRun.output_binding!,
        knowledge_structure_revision: newRevision,
        page_count: 3,
      },
    };
    const item: MaterialLibraryItem = {
      ...material,
      head_revision: newRevision,
      latest_attempt: run,
      source_artifact_id: run.source_artifact_id,
      study_sessions: [],
      available_structures: [
        ...material.available_structures,
        {
          run_id: newRunId,
          knowledge_structure_revision: newRevision,
          created_at: timestamp,
          status: "partial",
          base_revision: revision,
        },
      ],
    };
    await mockMaterial(page, () => item);
    await page.route(`**/v1/material-processing-runs/${newRunId}`, (route) => {
      expect(route.request().method()).toBe("GET");
      return route.fulfill({ json: run });
    });
    // 共用 fixture 的 resume 綁定舊 run；只覆寫本案例的新版綁定。
    await page.route(
      `**/v1/materials/${materialId}/knowledge-structures/${encodeURIComponent(newRevision)}/study-sessions/${sessionId}/resume?*`,
      (route) => {
        expect(route.request().method()).toBe("GET");
        expect(new URL(route.request().url()).searchParams.get("run_id")).toBe(newRunId);
        return route.fulfill({
          json: {
            schema: "study-resume/v1",
            run_id: newRunId,
            source_artifact_id: run.source_artifact_id,
            knowledge_structure: view,
            session: { ...session(), knowledge_structure_revision: newRevision },
            progress: { ...progress, knowledge_structure_revision: newRevision },
            assessment_sets: [],
            selected_set_id: null,
          },
        });
      },
    );
    await page.goto(updatePath);
    await expect(page.getByRole("heading", { name: "教材更新完成", exact: true })).toBeVisible();
    await expect(page.getByRole("status")).toContainText("部分內容建議對照來源確認");
    await page.getByRole("button", { name: "開啟目前地圖", exact: true }).click();
    await expect(page).toHaveURL(new RegExp(`${mapPath(newRunId, newRevision)}$`));
    await expect(page.getByRole("region", { name: "概念地圖工作區", exact: true })).toBeVisible();
    await page.goto("/materials");
    const card = page.getByRole("article", { name: item.display_name, exact: true });
    await expect(card).not.toContainText(/partial|needs_review|部分內容待複核/);
    await expect(card.getByRole("button", { name: "繼續學習", exact: true })).toHaveCount(0);
    await expect(card.getByRole("button", { name: "開啟知識地圖", exact: true })).toHaveClass(
      "primary-button",
    );
    item.study_sessions = [
      {
        ...material.study_sessions[0],
        run_id: newRunId,
        knowledge_structure_revision: newRevision,
      },
    ];
    await page.reload();
    await expect(card.getByRole("button", { name: "繼續學習", exact: true })).toHaveClass(
      "primary-button",
    );
    await expect(card.getByRole("button", { name: "開啟知識地圖", exact: true })).toHaveClass(
      "secondary-button",
    );
    await card.getByRole("button", { name: "繼續學習", exact: true }).click();
    await expect(page).toHaveURL(
      new RegExp(`${mapPath(newRunId, newRevision)}/study-sessions/${sessionId}$`),
    );
    await expect(page.locator(".study-session-page")).toBeVisible();
  });
}
