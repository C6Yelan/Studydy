import { expect, test, type Locator, type Page } from "@playwright/test";
import type {
  MaterialLibraryItem,
  MaterialProcessingRunView,
  SourceView,
} from "../../src/api/contracts";

const uuid = (seed: number) => `22222222-2222-4222-8222-${String(seed).padStart(12, "0")}`;
const materialId = uuid(1);
const oldRunId = uuid(2);
const newRunId = uuid(3);
const learnerId = uuid(99);
const revision = `knowledge-structure:sha256:${"a".repeat(64)}`;
const stamp = "2026-09-21T00:00:00Z";
const sourcesPath = `/materials/${materialId}/sources`;
const sourcesApi = `/v1/materials/${materialId}/sources`;
const intentKey = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

test.beforeEach(async ({ page }) => {
  await page.clock.install({ time: new Date(stamp) });
  await page.clock.pauseAt(new Date(stamp));
});

async function mockSources(page: Page, append = false) {
  const state = {
    sources: [
      ["Networks.pdf", "application/pdf"],
      [
        "Transport.pptx",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
      ],
      ["Applications.txt", "text/plain"],
    ].map(
      ([original_name, media_type], index): SourceView => ({
        source_id: uuid(10 + index),
        normalization_id: uuid(20 + index),
        original_artifact_id: uuid(30 + index),
        normalized_artifact_id: uuid(40 + index),
        original_name,
        media_type,
        status: "ready",
        page_count: 10,
        error_code: null,
        included: append && index === 0,
      }),
    ),
    run: null as MaterialProcessingRunView | null,
    starts: [] as { schema: string; base_revision: string | null; normalization_ids: string[] }[],
    removed: [] as string[],
    retried: [] as string[],
  };
  const listing = () => ({
    schema: "material-sources/v1",
    material_id: materialId,
    sources: state.sources,
  });
  const item = (): MaterialLibraryItem => ({
    schema: "material-library-item/v1",
    material_id: materialId,
    display_name: "網路概論",
    source_artifact_id: uuid(40),
    source_count: state.sources.length,
    size_bytes: 1024,
    created_at: stamp,
    latest_attempt: state.run,
    study_sessions: [],
    head_revision: append ? revision : null,
    available_structures: append
      ? [
          {
            run_id: oldRunId,
            knowledge_structure_revision: revision,
            status: "succeeded",
            created_at: stamp,
          },
        ]
      : [],
  });
  await page.route(/\/v[12]\//, (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    switch (path) {
      case "/v1/session":
      case "/v1/session/refresh":
        expect(request.method()).toBe(path.endsWith("/refresh") ? "POST" : "GET");
        return route.fulfill({ json: { schema: "learner-identity/v1", learner_id: learnerId } });
      case "/v1/source-capabilities":
        expect(request.method()).toBe("GET");
        return route.fulfill({
          json: {
            schema: "source-capabilities/v1",
            formats: [
              { extension: ".pdf", media_type: "application/pdf", max_bytes: 104857600 },
              { extension: ".txt", media_type: "text/plain", max_bytes: 104857600 },
            ],
          },
        });
      case `/v1/materials/${materialId}`:
        expect(request.method()).toBe("GET");
        return route.fulfill({ json: item() });
      case sourcesApi:
        expect(request.method()).toBe("GET");
        return route.fulfill({ json: listing() });
      case `/v1/materials/${materialId}/revisions`:
        expect(request.method()).toBe("POST");
        expect(request.headers()["idempotency-key"]).toMatch(intentKey);
        state.starts.push(request.postDataJSON());
        state.run = {
          schema: "material-processing-run/v1",
          run_id: newRunId,
          material_id: materialId,
          source_artifact_id: uuid(40),
          status: "pending",
          progress_stage: "queued",
          completed_pages: 0,
          total_pages: null,
          output_binding: null,
          error_code: null,
          cancel_requested_at: null,
          created_at: stamp,
          updated_at: stamp,
          completed_at: null,
          ...(append ? { base_revision: revision } : {}),
        };
        return route.fulfill({ status: 202, json: state.run });
      case `/v1/material-processing-runs/${newRunId}`:
        expect(request.method()).toBe("GET");
        if (!state.run) throw new Error("RUN_NOT_STARTED");
        return route.fulfill({ json: state.run });
      case `${sourcesApi}/${uuid(12)}`:
        expect(request.method()).toBe("DELETE");
        state.removed.push(uuid(12));
        state.sources = state.sources.filter((source) => source.source_id !== uuid(12));
        return route.fulfill({ json: listing() });
      case `${sourcesApi}/${uuid(22)}/retry`:
        expect(request.method()).toBe("POST");
        state.retried.push(uuid(22));
        Object.assign(state.sources[2], {
          status: "ready",
          normalized_artifact_id: uuid(42),
          page_count: 10,
          error_code: null,
        });
        return route.fulfill({ json: listing() });
      default:
        throw new Error(`Unexpected source confirmation request: ${request.method()} ${path}`);
    }
  });
  return { state, listing };
}

async function readyRows(page: Page) {
  const rows = page.locator(".source-row");
  await expect(rows).toHaveCount(3);
  return rows;
}

for (const width of [1536, 390])
  test.describe(`source confirmation at ${width}px`, () => {
    test.use({ viewport: { width, height: width === 390 ? 844 : 1024 }, hasTouch: width === 390 });
    const activate = (locator: Locator) => (width === 390 ? locator.tap() : locator.click());

    test("initial confirmation previews, removes and submits sources in the chosen order", async ({
      page,
      context,
    }) => {
      const { state } = await mockSources(page);
      await context.route(`**/v1/artifacts/${uuid(40)}`, (route) => {
        return route.fulfill({ contentType: "text/plain", body: "Synthetic preview destination" });
      });
      await page.goto(sourcesPath);
      await expect(page.getByRole("heading", { name: "確認教材", exact: true })).toBeVisible();
      const rows = await readyRows(page);
      const start = page.getByRole("button", { name: "開始分析教材", exact: true });
      await expect(start).toBeEnabled();
      await rows.first().getByRole("link", { name: "預覽 PDF", exact: true }).focus();
      await page.keyboard.press("Tab");
      await expect(rows.first().getByRole("link", { name: "下載原檔", exact: true })).toBeFocused();
      const popup = page.waitForEvent("popup");
      await activate(rows.first().getByRole("link", { name: "預覽 PDF", exact: true }));
      const preview = await popup;
      await expect(preview).toHaveURL(new RegExp(`/v1/artifacts/${uuid(40)}$`));
      await preview.close();
      await expect(
        page.getByRole("button", { name: "上移 Networks.pdf", exact: true }),
      ).toBeDisabled();
      await expect(
        page.getByRole("button", { name: "下移 Applications.txt", exact: true }),
      ).toBeDisabled();
      await page.getByRole("button", { name: "上移 Transport.pptx", exact: true }).focus();
      await page.keyboard.press("Enter");
      await expect(rows.locator(".source-name")).toHaveText([
        "Transport.pptx",
        "Networks.pdf",
        "Applications.txt",
      ]);
      await activate(
        page
          .getByRole("listitem", { name: "Applications.txt", exact: true })
          .getByRole("button", { name: "移除", exact: true }),
      );
      await expect(rows).toHaveCount(2);
      expect(state.removed).toEqual([uuid(12)]);
      expect(state.starts).toEqual([]);
      await activate(start);
      await expect(page.getByRole("heading", { name: "等待開始處理", exact: true })).toBeVisible();
      await expect(page).toHaveURL(new RegExp(`/runs/${newRunId}$`));
      expect(state.starts).toEqual([
        {
          schema: "material-revision-create/v1",
          base_revision: null,
          normalization_ids: [uuid(21), uuid(20)],
        },
      ]);
    });

    test("append confirmation excludes existing and unselected sources, and recovers failed conversion", async ({
      page,
    }) => {
      const { state } = await mockSources(page, true);
      await page.goto(sourcesPath);
      await expect(page.getByRole("heading", { name: "新增教材", exact: true })).toBeVisible();
      const rows = await readyRows(page);
      await expect(rows.first().getByRole("button", { name: "移除", exact: true })).toHaveCount(0);
      await expect(page.getByRole("button", { name: /^(上移|下移) / })).toHaveCount(0);
      const start = page.getByRole("button", { name: "確認新增並更新地圖", exact: true });
      const application = page.getByRole("listitem", { name: "Applications.txt", exact: true });
      const job = state.sources[2];
      Object.assign(job, {
        status: "failed",
        normalized_artifact_id: null,
        page_count: null,
        error_code: "NORMALIZATION_FAILED",
      });
      await page.reload();
      await expect(application.getByRole("status")).toHaveText("轉換失敗");
      await activate(application.getByRole("checkbox"));
      await expect(start).toBeEnabled();
      await expect(page.locator(".source-summary")).toHaveText("本次新增 1 份教材 · 共 10 頁");
      await activate(application.getByRole("checkbox"));
      await expect(start).toHaveCount(0);
      await activate(application.getByRole("button", { name: "重試轉換", exact: true }));
      await expect(start).toBeEnabled();
      expect(state.retried).toEqual([uuid(22)]);
      await activate(application.getByRole("checkbox"));
      await expect(page.locator(".source-summary")).toHaveText("本次新增 1 份教材 · 共 10 頁");
      expect(state.starts).toEqual([]);
      await activate(start);
      await expect(page.getByRole("heading", { name: "正在更新教材", exact: true })).toBeVisible();
      await expect(page).toHaveURL(new RegExp(`/runs/${newRunId}$`));
      expect(state.starts).toEqual([
        {
          schema: "material-revision-create/v1",
          base_revision: revision,
          normalization_ids: [uuid(21)],
        },
      ]);
      await page.goto(sourcesPath);
      await expect(page.getByRole("button", { name: "查看處理狀態", exact: true })).toBeEnabled();
      const choices = page.getByRole("checkbox");
      await expect(choices).toHaveCount(2);
      for (const choice of await choices.all()) await expect(choice).toBeDisabled();
      await expect(page.getByLabel("選擇新增教材", { exact: true })).toHaveCount(0);
    });

    test("upload queue retries one file with the same intent and waits for source confirmation", async ({
      page,
    }) => {
      const { state, listing } = await mockSources(page);
      const uploadKeys: string[] = [];
      let releaseUpload!: () => void;
      const uploadGate = new Promise<void>((resolve) => {
        releaseUpload = resolve;
      });
      let releaseRefresh!: () => void;
      const refreshGate = new Promise<void>((resolve) => {
        releaseRefresh = resolve;
      });
      let holdRefresh = false;
      const extra: SourceView = {
        source_id: uuid(13),
        normalization_id: uuid(23),
        original_artifact_id: uuid(33),
        original_name: "Extra.txt",
        media_type: "text/plain",
        included: false,
        status: "running",
        normalized_artifact_id: null,
        page_count: null,
        error_code: null,
      };
      await page.route(`**${sourcesApi}`, async (route) => {
        const request = route.request();
        if (request.method() === "POST") {
          expect(request.headers()["x-material-name"]).toBe("Extra.txt");
          uploadKeys.push(request.headers()["idempotency-key"]);
          if (uploadKeys.length === 1) {
            await uploadGate;
            return route.abort("connectionreset");
          }
          state.sources.push(extra);
          holdRefresh = true;
        } else {
          expect(request.method()).toBe("GET");
          if (holdRefresh) await refreshGate;
        }
        return route.fulfill({ json: listing() });
      });
      await page.goto(sourcesPath);
      await expect(page.getByLabel("選擇新增教材", { exact: true })).toBeEnabled();
      const chooser = page.waitForEvent("filechooser");
      await activate(page.locator(".source-add-control"));
      await (
        await chooser
      ).setFiles([
        { name: "Extra.txt", mimeType: "text/plain", buffer: Buffer.from("extra") },
        { name: "image.jpg", mimeType: "image/jpeg", buffer: Buffer.from("image") },
      ]);
      const local = page.locator(".source-upload-row");
      const start = page.getByRole("button", { name: "開始分析教材", exact: true });
      await expect(local).toHaveCount(1);
      await expect(local.getByRole("status")).toHaveText("待上傳");
      await expect(local.getByRole("link")).toHaveCount(0);
      await expect(page.locator("#source-selection-error")).toContainText("image.jpg");
      await expect(page.locator(".source-summary")).toHaveText("3 份已準備 · 1 份待上傳");
      await expect(start).toHaveCount(0);
      const primary = page.locator(".source-list-card .primary-button");
      await activate(page.getByRole("button", { name: "上傳新增教材", exact: true }));
      await expect(local.getByRole("status")).toHaveText("上傳中…");
      await expect(primary).toHaveText("正在上傳…");
      await expect(primary).toBeDisabled();
      releaseUpload();
      await expect(local.getByRole("status")).toHaveText("上傳失敗");
      await expect(local.getByRole("alert")).toBeVisible();
      await expect(local.getByRole("button", { name: "移除", exact: true })).toBeEnabled();
      await activate(page.getByRole("button", { name: "重試上傳", exact: true }));
      await expect(local.getByRole("status")).toHaveText("已上傳");
      await expect(page.locator(".source-row")).toHaveCount(4);
      await expect(start).toHaveCount(0);
      releaseRefresh();
      await expect(local).toHaveCount(0);
      await expect(page.locator(".source-row")).toHaveCount(4);
      const row = page.getByRole("listitem", { name: "Extra.txt", exact: true });
      await expect(row.getByRole("status")).toHaveText("正在轉換…");
      await expect(page.locator(".source-summary")).toHaveText("3 份已準備 · 1 份正在轉換");
      Object.assign(extra, { status: "ready", normalized_artifact_id: uuid(43), page_count: 10 });
      await page.clock.runFor(3_000);
      await expect(start).toBeEnabled();
      await expect(row.getByRole("link", { name: "預覽 PDF", exact: true })).toHaveAttribute(
        "href",
        `/v1/artifacts/${uuid(43)}`,
      );
      await expect(row.getByRole("link", { name: "下載原檔", exact: true })).toHaveAttribute(
        "href",
        `/v1/artifacts/${uuid(33)}/download`,
      );
      await expect(page.locator(".source-summary")).toHaveText("4 份教材 · 共 40 頁");
      expect(uploadKeys).toHaveLength(2);
      expect(uploadKeys[0]).toMatch(intentKey);
      expect(uploadKeys[1]).toBe(uploadKeys[0]);
      expect(state.sources.filter((source) => source.source_id === extra.source_id)).toHaveLength(
        1,
      );
      expect(state.starts).toEqual([]);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
        true,
      );
    });
  });
