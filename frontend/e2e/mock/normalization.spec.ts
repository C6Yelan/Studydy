import { expect, test, type Page } from "@playwright/test";
import type {
  MaterialLibraryItem,
  MaterialProcessingRunView,
  SourceView,
} from "../../src/api/contracts";

const uuid = (seed: number) => `00000000-0000-4000-8000-${String(seed).padStart(12, "0")}`;
const materialId = uuid(1);
const sourceId = uuid(2);
const normalizationId = uuid(3);
const artifactId = uuid(4);
const previewId = uuid(5);
const runId = uuid(6);
const learnerId = uuid(7);
const sourceText = "Stacks\nA stack follows LIFO order.\n";
const intentKey = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

function source(overrides: Partial<SourceView> = {}): SourceView {
  const status = overrides.status ?? "running";
  return {
    source_id: sourceId,
    normalization_id: normalizationId,
    original_artifact_id: artifactId,
    original_name: "notes.txt",
    media_type: "text/plain",
    status,
    normalized_artifact_id: status === "ready" ? previewId : null,
    page_count: status === "ready" ? 1 : null,
    error_code: status === "failed" ? "NORMALIZATION_TIMEOUT" : null,
    ...overrides,
  };
}

function material(job: SourceView, id = materialId): MaterialLibraryItem {
  return {
    schema: "material-library-item/v1",
    material_id: id,
    source_artifact_id: job.normalized_artifact_id,
    display_name: job.original_name,
    size_bytes: 1024,
    created_at: "2026-09-17T00:00:00Z",
    latest_attempt: null,
    available_structures: [],
    study_sessions: [],
    source: job,
  };
}

async function mockSources(page: Page, getSource: () => SourceView, id = materialId) {
  await page.route(`**/v1/materials/${id}`, (route) => {
    expect(route.request().method()).toBe("GET");
    return route.fulfill({ json: material(getSource(), id) });
  });
  await page.route(`**/v1/materials/${id}/sources`, (route) => {
    expect(route.request().method()).toBe("GET");
    return route.fulfill({
      json: { schema: "material-sources/v1", material_id: id, sources: [getSource()] },
    });
  });
}

test.beforeEach(async ({ page }) => {
  await page.clock.install({ time: new Date("2026-09-18T00:02:00Z") });
  await page.clock.pauseAt(new Date("2026-09-18T00:02:00Z"));
  await page.route(/\/v[12]\//, (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    expect(request.method()).toBe(path === "/v1/session/refresh" ? "POST" : "GET");
    switch (path) {
      case "/v1/session":
      case "/v1/session/refresh":
        return route.fulfill({ json: { schema: "learner-identity/v1", learner_id: learnerId } });
      case "/v1/source-capabilities":
        return route.fulfill({
          json: {
            schema: "source-capabilities/v1",
            formats: [
              { extension: ".pdf", media_type: "application/pdf", max_bytes: 104857600 },
              { extension: ".txt", media_type: "text/plain", max_bytes: 104857600 },
            ],
          },
        });
      default:
        throw new Error(`Unexpected normalization request: ${request.method()} ${path}`);
    }
  });
});

for (const width of [1536, 390])
  test(`single non-PDF normalization resumes without reupload at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 1024 });
    let status: SourceView["status"] = "running";
    const job = () => source({ status });
    const draftKeys: string[] = [];
    const uploadKeys: string[] = [];
    await mockSources(page, job);
    // 建立與列表共用 URL；讀取來源則不能重複送出上傳。
    await page.route("**/v1/materials", (route) => {
      const request = route.request();
      if (request.method() === "GET")
        return route.fulfill({
          json: { schema: "material-library/v1", materials: [material(job())] },
        });
      expect(request.method()).toBe("POST");
      expect(request.postDataJSON()).toEqual({
        schema: "material-draft-create/v1",
        display_name: "notes.txt",
      });
      draftKeys.push(request.headers()["idempotency-key"]);
      return route.fulfill({
        status: 201,
        json: { schema: "material-draft/v1", material_id: materialId },
      });
    });
    await page.route(`**/v1/materials/${materialId}/sources`, (route) => {
      const request = route.request();
      if (request.method() !== "GET") {
        expect(request.method()).toBe("POST");
        expect(request.headers()["content-type"]).toBe("text/plain");
        expect(request.headers()["x-material-name"]).toBe("notes.txt");
        expect(request.postDataBuffer()).toEqual(Buffer.from(sourceText));
        uploadKeys.push(request.headers()["idempotency-key"]);
      }
      return route.fulfill({
        json: { schema: "material-sources/v1", material_id: materialId, sources: [job()] },
      });
    });
    await page.goto("/upload");
    await expect(page.getByLabel("選擇教材檔案", { exact: true })).toBeEnabled();
    await page.getByLabel("選擇教材檔案", { exact: true }).setInputFiles({
      name: "notes.txt",
      mimeType: "text/plain",
      buffer: Buffer.from(sourceText),
    });
    await page.getByRole("button", { name: "上傳並確認來源", exact: true }).click();
    await expect(page).toHaveURL(new RegExp(`/materials/${materialId}/sources$`));
    const row = page.locator(".source-row");
    await expect(row.getByRole("status")).toHaveText("正在轉換…");
    await expect(row.getByRole("link", { name: "預覽 PDF", exact: true })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "開始分析教材", exact: true })).toHaveCount(0);
    await page.reload();
    await expect(row.getByRole("status")).toHaveText("正在轉換…");
    status = "ready";
    await page.clock.runFor(3_000);
    await expect(page.getByRole("button", { name: "開始分析教材", exact: true })).toBeEnabled();
    await expect(row.getByRole("link", { name: "預覽 PDF", exact: true })).toHaveAttribute(
      "href",
      `/v1/artifacts/${previewId}`,
    );
    await page.goto("/materials");
    await expect(page.getByRole("article")).not.toContainText("尚未建立知識地圖");
    await expect(page.getByRole("article").getByRole("link")).toHaveCount(0);
    await page.getByRole("button", { name: "管理「notes.txt」", exact: true }).click();
    await page.getByRole("button", { name: "管理教材", exact: true }).click();
    await expect(row.getByRole("link", { name: "下載原檔", exact: true })).toHaveAttribute(
      "href",
      `/v1/artifacts/${artifactId}/download`,
    );
    expect(draftKeys).toHaveLength(1);
    expect(uploadKeys).toHaveLength(1);
    expect(draftKeys[0]).toMatch(intentKey);
    expect(uploadKeys[0]).toMatch(intentKey);
    expect(uploadKeys[0]).not.toBe(draftKeys[0]);
  });

test("failed conversion recovers after explicit retry and keeps the original", async ({ page }) => {
  let job = source({
    original_name: "discrete-mathematics.txt",
    status: "failed",
    error_code: "UTF8_REQUIRED",
  });
  let retries = 0;
  await mockSources(page, () => job);
  await page.route(`**/v1/materials/${materialId}/sources/${normalizationId}/retry`, (route) => {
    expect(route.request().method()).toBe("POST");
    expect(route.request().postData()).toBeNull();
    retries++;
    job = source({ original_name: job.original_name });
    return route.fulfill({
      json: { schema: "material-sources/v1", material_id: materialId, sources: [job] },
    });
  });
  await page.goto(`/materials/${materialId}/sources`);
  const row = page.locator(".source-row");
  await expect(row.getByRole("status")).toHaveText("轉換失敗");
  await page.reload();
  await expect(row.getByRole("status")).toHaveText("轉換失敗");
  await expect(row.getByRole("link", { name: "下載原檔", exact: true })).toHaveAttribute(
    "href",
    `/v1/artifacts/${artifactId}/download`,
  );
  expect(retries).toBe(0);
  await page.getByRole("button", { name: "重試轉換", exact: true }).click();
  await expect(row.getByRole("status")).toHaveText("正在轉換…");
  await expect(page.getByRole("button", { name: "重試轉換", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "開始分析教材", exact: true })).toHaveCount(0);
  job = source({ original_name: job.original_name, status: "ready" });
  await page.clock.runFor(3_000);
  await expect(row.getByRole("link", { name: "預覽 PDF", exact: true })).toHaveAttribute(
    "href",
    `/v1/artifacts/${previewId}`,
  );
  await expect(row.getByRole("link", { name: "下載原檔", exact: true })).toHaveAttribute(
    "href",
    `/v1/artifacts/${artifactId}/download`,
  );
  await expect(page.getByRole("button", { name: "開始分析教材", exact: true })).toBeEnabled();
  expect(retries).toBe(1);
});

test("accepted deletion of a converting source survives reload and returns to library", async ({
  page,
}) => {
  let deleting = false;
  let removed = false;
  let deletes = 0;
  const job = source({ original_name: "remove.txt" });
  const failure = {
    schema: "api-error/v1",
    request_id: uuid(8),
    reason_code: "RESOURCE_NOT_FOUND",
    retryable: false,
    message: "Request could not be completed.",
  };
  await page.route(`**/v1/materials/${materialId}/sources`, (route) => {
    expect(route.request().method()).toBe("GET");
    return removed
      ? route.fulfill({ status: 404, json: failure })
      : route.fulfill({
          json: {
            schema: "material-sources/v1",
            material_id: materialId,
            discard_requested: deleting,
            sources: [job],
          },
        });
  });
  await page.route(`**/v1/materials/${materialId}`, (route) => {
    if (route.request().method() === "DELETE") {
      expect(route.request().postData()).toBeNull();
      deletes++;
      deleting = true;
      return route.fulfill({
        status: 202,
        json: { schema: "material-discard/v1", material_id: materialId, state: "removing" },
      });
    }
    expect(route.request().method()).toBe("GET");
    return removed
      ? route.fulfill({ status: 404, json: failure })
      : route.fulfill({ json: material(job) });
  });
  await page.route("**/v1/materials", (route) => {
    expect(route.request().method()).toBe("GET");
    return route.fulfill({ json: { schema: "material-library/v1", materials: [] } });
  });
  await page.goto(`/materials/${materialId}/sources`);
  await expect(page.locator(".source-row").getByRole("status")).toHaveText("正在轉換…");
  await page.getByRole("button", { name: "刪除教材", exact: true }).click();
  await expect(page.getByText(/目前已上傳的/)).toBeVisible();
  await page.getByRole("button", { name: "確認刪除", exact: true }).click();
  // 接受刪除後才重整，避免把尚未送出的 DELETE 一併取消。
  await expect(page.getByText("正在刪除教材…", { exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByText("正在刪除教材…", { exact: true })).toBeVisible();
  await expect(page.getByLabel("選擇新增教材", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "開始分析教材", exact: true })).toHaveCount(0);
  await expect(page.locator(".source-download")).toHaveAttribute("aria-disabled", "true");
  removed = true;
  await page.clock.runFor(3_000);
  await expect(page).toHaveURL(/\/materials$/);
  await expect(page.getByRole("heading", { name: "我的教材", exact: true })).toBeVisible();
  await expect(page.getByRole("article")).toHaveCount(0);
  expect(deletes).toBe(1);
});

for (const width of [1536, 390])
  test(`library moves file management out of cards at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 1024 });
    const files: { name: string; status: SourceView["status"]; media: string }[] = [
      { name: "原生教材.pdf", status: "ready", media: "application/pdf" },
      { name: "課堂簡報.ppt", status: "ready", media: "application/vnd.ms-powerpoint" },
      { name: "課程講義.doc", status: "running", media: "application/msword" },
      { name: "離散數學筆記.txt", status: "failed", media: "text/plain" },
    ];
    const materials = files.map((file, index) =>
      material(
        source({
          source_id: uuid(20 + index),
          normalization_id: uuid(30 + index),
          original_artifact_id: uuid(40 + index),
          original_name: file.name,
          media_type: file.media,
          status: file.status,
          normalized_artifact_id: file.status === "ready" ? uuid(50 + index) : null,
        }),
        uuid(10 + index),
      ),
    );
    await page.route("**/v1/materials", (route) => {
      expect(route.request().method()).toBe("GET");
      return route.fulfill({ json: { schema: "material-library/v1", materials } });
    });
    const converted = materials[1];
    await mockSources(page, () => converted.source!, converted.material_id);
    await page.goto("/materials");
    const cards = page.getByRole("article");
    await expect(cards).toHaveCount(files.length);
    await expect(cards.getByRole("link")).toHaveCount(0);
    for (const file of files)
      await expect(page.getByRole("article", { name: file.name, exact: true })).not.toContainText(
        /教材轉換|轉換後 PDF|下載原檔/,
      );
    const card = page.getByRole("article", { name: converted.display_name, exact: true });
    await expect(card).not.toContainText("尚未建立知識地圖");
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
      true,
    );
    await card.getByRole("button", { name: "建立知識地圖", exact: true }).click();
    await expect(page).toHaveURL(new RegExp(`/materials/${converted.material_id}/sources$`));
    await expect(page.locator(".source-name")).toHaveText(converted.display_name);
    await expect(page.getByRole("link", { name: "預覽 PDF", exact: true })).toHaveAttribute(
      "href",
      `/v1/artifacts/${converted.source!.normalized_artifact_id}`,
    );
    await expect(page.getByRole("button", { name: "開始分析教材", exact: true })).toBeEnabled();
  });

for (const viewport of [
  { width: 1536, height: 1024 },
  { width: 1200, height: 768 },
  { width: 390, height: 844 },
]) {
  test(`material flow visual consistency at ${viewport.width}x${viewport.height}`, async ({
    page,
  }) => {
    await page.setViewportSize(viewport);
    const expectApplicationFrame = async () => {
      const { usable, width } = await page.locator(".app-main").evaluate((main) => {
        const css = getComputedStyle(main);
        return {
          usable:
            main.getBoundingClientRect().width -
            parseFloat(css.paddingLeft) -
            parseFloat(css.paddingRight),
          width: main.firstElementChild!.getBoundingClientRect().width,
        };
      });
      expect(width).toBeCloseTo(Math.min(usable, 1600), 0);
    };
    let filename = "資料結構與演算法講義.docx";
    const job = () =>
      source({
        original_name: filename,
        media_type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        status: "ready",
        page_count: 12,
      });
    const run: MaterialProcessingRunView = {
      schema: "material-processing-run/v1",
      run_id: runId,
      material_id: materialId,
      source_artifact_id: previewId,
      status: "running",
      progress_stage: "evidence",
      cancel_requested_at: null,
      completed_pages: 3,
      total_pages: 12,
      error_code: null,
      created_at: "2026-09-18T00:00:00Z",
      updated_at: "2026-09-18T00:01:00Z",
      completed_at: null,
      output_binding: null,
    };
    await mockSources(page, job);
    await page.route("**/v1/materials", (route) => {
      expect(route.request().method()).toBe("GET");
      return route.fulfill({
        json: { schema: "material-library/v1", materials: [material(job())] },
      });
    });
    await page.route(`**/v1/material-processing-runs/${runId}`, (route) => {
      expect(route.request().method()).toBe("GET");
      return route.fulfill({ json: run });
    });
    await page.goto("/upload");
    await expect(page.locator(".file-drop")).toContainText("PDF、TXT");
    await expectApplicationFrame();
    expect((await page.locator(".file-drop").boundingBox())!.width).toBeLessThanOrEqual(880);
    const uploadColumns = await page
      .locator(".upload-layout")
      .evaluate((element) => getComputedStyle(element).gridTemplateColumns);
    const uploadMascot = await page.locator(".upload-hero > img").boundingBox();
    await page.goto(`/materials/${materialId}/sources`);
    await expect(page.getByRole("link", { name: "預覽 PDF", exact: true })).toBeVisible();
    await expect(page.locator(".source-row").getByRole("status")).toHaveCount(0);
    await expectApplicationFrame();
    expect((await page.locator(".source-add-control").boundingBox())!.height).toBeLessThanOrEqual(
      44,
    );
    await expect(page.locator(".source-page .primary-button")).toHaveCount(1);
    expect(
      await page
        .locator(".upload-layout")
        .evaluate((element) => getComputedStyle(element).gridTemplateColumns),
    ).toBe(uploadColumns);
    const mascot = (await page.locator(".upload-hero > img").boundingBox())!;
    expect(mascot.width).toBe(uploadMascot!.width);
    expect(mascot.height).toBe(uploadMascot!.height);
    const copy = (await page.locator(".upload-hero > div").boundingBox())!;
    expect(copy.x + copy.width).toBeLessThan(mascot.x);
    const card = (await page.locator(".source-list-card").boundingBox())!;
    const rail = (await page.locator(".source-guide").boundingBox())!;
    const confirmation = (await page.locator(".source-list-footer").boundingBox())!;
    const cta = (await page.getByRole("button", { name: "開始分析教材" }).boundingBox())!;
    expect(cta.y).toBeGreaterThan(confirmation.y);
    expect(confirmation.y + confirmation.height - cta.y - cta.height).toBeLessThanOrEqual(33);
    if (viewport.width > 1200) {
      expect(rail.width).toBe(320);
      expect(rail.x - card.x - card.width).toBeCloseTo(24, 0);
    } else {
      expect(rail.y).toBeGreaterThan(confirmation.y + confirmation.height);
    }
    const back = (await page.getByRole("button", { name: "返回教材庫" }).boundingBox())!;
    await expect(page.getByRole("button", { name: "刪除教材", exact: true })).toBeVisible();
    expect(
      (await page.getByRole("button", { name: "刪除教材", exact: true }).boundingBox())!.y,
    ).toBeCloseTo(back.y, 0);
    const steps = page.locator(".source-guide li");
    await expect(steps.nth(1)).toHaveAttribute("aria-current", "step");
    for (const [index, token] of [
      [0, "--success"],
      [1, "--studydy-blue"],
      [2, "--text-secondary"],
    ] as const) {
      expect(
        await steps
          .nth(index)
          .locator(":scope > span")
          .evaluate((element, token) => {
            const expected = document.createElement("span");
            expected.style.color = `var(${token})`;
            element.append(expected);
            const matches = getComputedStyle(element).color === getComputedStyle(expected).color;
            expected.remove();
            return matches;
          }, token),
      ).toBe(true);
    }
    filename = `${"VeryLongFilenameWithoutSpaces".repeat(8)}.docx`;
    await page.reload();
    await expect(page.locator(".source-name")).toHaveText(filename);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(viewport.width);
    filename = "資料結構與演算法講義.docx";
    for (const [path, selector] of [
      [`/materials/${materialId}/runs/${runId}`, ".processing-grid"],
      ["/materials", ".library-item"],
    ]) {
      await page.goto(path);
      await expect(page.locator(selector)).toBeVisible();
      await expectApplicationFrame();
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(viewport.width);
    }
  });
}
