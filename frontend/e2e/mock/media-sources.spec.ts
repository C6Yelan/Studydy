import { expect, test, type Page, type Request } from "@playwright/test";
import type { SourceView } from "../../src/api/contracts";
import { emptyLearningActivity } from "../fixtures/learning-activity";
import {
  materialId as mapMaterialId,
  runId as mapRunId,
  artifactId,
  structureRevision,
  mockKnowledgeMapApi,
  openMapConcept,
} from "../fixtures/knowledge-map";

const uuid = (seed: number) => `11111111-1111-4111-8111-${String(seed).padStart(12, "0")}`;
const material = uuid(1);
const png = (name: string, marker: string) => ({ name, mimeType: "image/png", buffer: Buffer.from(`\x89PNG\r\n\x1a\n${marker}`) });
const mp3 = { name: "lecture.mp3", mimeType: "audio/mpeg", buffer: Buffer.alloc(1200, 7) };

async function setup(page: Page) {
  const state = {
    uploads: [] as { name: string; type: string; body: Buffer }[],
    parts: [] as { offset: number; total: number; size: number }[],
    youtube: [] as string[],
    sources: [] as SourceView[],
  };
  await page.route(/\/v[12]\//, (route) => {
    throw new Error(`Unexpected request: ${route.request().method()} ${route.request().url()}`);
  });
  await page.route("**/v1/profile", (route) => route.fulfill({ json: { schema: "learner-profile/v1", display_name: null } }));
  await page.route("**/v1/learning-activity", (route) => route.fulfill({ json: emptyLearningActivity }));
  for (const path of ["**/v1/session", "**/v1/session/refresh"])
    await page.route(path, (route) => route.fulfill({ json: { schema: "learner-identity/v1", learner_id: uuid(99) } }));
  await page.route("**/v1/source-capabilities", (route) =>
    route.fulfill({
      json: {
        schema: "source-capabilities/v2",
        // 小上限讓音訊走分段上傳。
        max_request_bytes: 512,
        max_images: 50,
        max_media_seconds: 7200,
        youtube: true,
        formats: [
          { extension: ".pdf", media_type: "application/pdf", max_bytes: 104857600, kind: "document" },
          { extension: ".png", media_type: "image/png", max_bytes: 104857600, kind: "image" },
          { extension: ".jpg", media_type: "image/jpeg", max_bytes: 104857600, kind: "image" },
          { extension: ".mp3", media_type: "audio/mpeg", max_bytes: 314572800, kind: "audio" },
          { extension: ".mp4", media_type: "video/mp4", max_bytes: 2147483648, kind: "video" },
        ],
      },
    }),
  );
  const item = () => ({
    schema: "material-library-item/v1",
    material_id: material,
    source_artifact_id: null,
    display_name: "b 等 2 張圖片",
    size_bytes: 128,
    created_at: "2026-10-25T00:00:00Z",
    latest_attempt: null,
    available_structures: [],
    study_sessions: [],
  });
  await page.route("**/v1/materials", (route) =>
    route.fulfill({ status: 201, json: { schema: "material-draft/v1", material_id: material } }),
  );
  await page.route(`**/v1/materials/${material}`, (route) => route.fulfill({ json: item() }));
  const add = (request: Request, name: string, type: string) => {
    const n = 10 + state.sources.length * 10;
    state.sources.push({
      source_id: uuid(n), normalization_id: uuid(n + 1), original_artifact_id: uuid(n + 2),
      original_name: name, media_type: type, status: "pending", normalized_artifact_id: null,
      page_count: null, error_code: null, included: false,
    });
    expect(request.headers()["idempotency-key"]).toMatch(/\S/);
  };
  const listing = () => ({ schema: "material-sources/v1", material_id: material, sources: state.sources });
  await page.route(`**/v1/materials/${material}/sources`, (route) => {
    const request = route.request();
    if (request.method() === "POST") {
      const name = decodeURIComponent(request.headers()["x-material-name"]);
      state.uploads.push({ name, type: request.headers()["content-type"], body: request.postDataBuffer()! });
      add(request, name, request.headers()["content-type"]);
    }
    return route.fulfill({ status: request.method() === "POST" ? 202 : 200, json: listing() });
  });
  await page.route(`**/v1/materials/${material}/source-parts`, (route) => {
    const request = route.request();
    const offset = Number(request.headers()["x-upload-offset"]);
    const total = Number(request.headers()["x-upload-total"]);
    const size = request.postDataBuffer()!.length;
    state.parts.push({ offset, total, size });
    const done = offset + size === total;
    if (done) add(request, decodeURIComponent(request.headers()["x-material-name"]), request.headers()["content-type"]);
    return route.fulfill({
      status: 202,
      json: { schema: "source-upload-progress/v1", received_bytes: offset + size, source_id: done ? state.sources.at(-1)!.source_id : null },
    });
  });
  await page.route(`**/v1/materials/${material}/youtube-sources`, (route) => {
    const body = route.request().postDataJSON();
    expect(body.schema).toBe("youtube-source-create/v1");
    state.youtube.push(body.url);
    add(route.request(), "公開課程.youtube", "application/vnd.studydy.youtube+json");
    return route.fulfill({ status: 202, json: listing() });
  });
  return state;
}

for (const width of [1536, 390]) {
  test(`images form one ordered source while audio and YouTube upload as their own sources at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 844 });
    const state = await setup(page);
    await page.goto("/upload");
    await expect(page.locator(".file-drop > span:last-child")).toHaveText(
      "文件 PDF（每份最大 100.0 MiB） · 圖片 PNG、JPG（一次最多 50 張、合計 100.0 MiB） · 音訊 MP3（每份最大 300.0 MiB） · 影片 MP4（每份最大 2.0 GiB） · 音訊／影片最長 2 小時 · 可選多份",
    );
    const input = page.getByLabel("選擇教材檔案", { exact: true });
    await input.setInputFiles([png("a.png", "first"), png("b.png", "second"), mp3]);
    await expect(page.locator(".chosen-file > .chosen-file-body > strong")).toHaveText(["a 等 2 張圖片", "lecture.mp3"]);
    const order = page.getByRole("list", { name: "圖片順序（第幾張即第幾頁）" });
    await expect(order.locator(".queued-image-name")).toHaveText(["第 1 頁 · a.png", "第 2 頁 · b.png"]);
    await page.getByRole("button", { name: "上移 b.png", exact: true }).click();
    await expect(order.locator(".queued-image-name")).toHaveText(["第 1 頁 · b.png", "第 2 頁 · a.png"]);

    const url = page.getByLabel("YouTube 影片網址", { exact: true });
    await url.fill("https://www.youtube.com/playlist?list=PL123");
    await page.getByRole("button", { name: "加入影片", exact: true }).click();
    await expect(page.locator("#youtube-source-problem")).toHaveText("不支援播放清單，請貼上單支影片的網址。");
    await url.fill("https://youtu.be/iG9CE55wbtY");
    await page.getByRole("button", { name: "加入影片", exact: true }).click();
    await expect(page.locator(".chosen-file > .chosen-file-body > strong")).toHaveText([
      "b 等 2 張圖片", "lecture.mp3", "https://youtu.be/iG9CE55wbtY",
    ]);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);

    await page.getByRole("button", { name: "上傳並確認來源", exact: true }).click();
    await expect(page).toHaveURL(new RegExp(`/materials/${material}/sources$`));
    // 圖片依調整後的順序打包：0001 是 b、0002 是 a。
    expect(state.uploads.map(({ name, type }) => [name, type])).toEqual([["b 等 2 張圖片.zip", "application/zip"]]);
    const zip = state.uploads[0].body.toString("latin1");
    expect(zip.indexOf("0001.png")).toBeLessThan(zip.indexOf("second"));
    expect(zip.indexOf("second")).toBeLessThan(zip.indexOf("0002.png"));
    expect(zip.indexOf("0002.png")).toBeLessThan(zip.indexOf("first"));
    // 音訊超過單次上限，依序分段且總長固定。
    expect(state.parts).toEqual([
      { offset: 0, total: 1200, size: 512 },
      { offset: 512, total: 1200, size: 512 },
      { offset: 1024, total: 1200, size: 176 },
    ]);
    expect(state.youtube).toEqual(["https://youtu.be/iG9CE55wbtY"]);
    await expect(page.locator(".source-row .source-name")).toHaveText(["b 等 2 張圖片.zip", "lecture.mp3", "公開課程.youtube"]);
  });
}

test("timed sources show segments and the dialog plays from the cited time", async ({ page }) => {
  await mockKnowledgeMapApi(page);
  const sources = [
    {
      format: "audio",
      original_name: "lecture.mp3",
      label: "02:00–04:00（第 2 段逐字稿）",
      playback: { kind: "audio", url: `/v1/artifacts/${artifactId}/media`, start_seconds: 120, end_seconds: 240 },
    },
    {
      format: "youtube",
      original_name: "公開課程.youtube",
      label: "00:00–02:00（第 1 段逐字稿）",
      playback: { kind: "youtube", video_id: "iG9CE55wbtY", start_seconds: 0, end_seconds: 120 },
    },
  ];
  let current = 0;
  await page.route(/\/evidence\/[^/]+\/source$/, (route) =>
    route.fulfill({
      json: {
        schema: "evidence-source/v1",
        ...sources[current],
        original_url: `/v1/artifacts/${artifactId}/download`,
        preview_url: `/v1/artifacts/${artifactId}#page=2`,
        normalized_page: 2,
        accuracy: "exact",
        origin_locators: [{ start_seconds: sources[current].playback.start_seconds, end_seconds: sources[current].playback.end_seconds }],
      },
    }),
  );
  await page.route(`**/v1/artifacts/${artifactId}/media`, (route) =>
    route.fulfill({ status: 200, contentType: "audio/mpeg", body: Buffer.alloc(0) }),
  );
  await page.route("https://www.youtube-nocookie.com/**", (route) => route.fulfill({ contentType: "text/html", body: "<p>player</p>" }));
  await page.goto(
    `/materials/${mapMaterialId}/runs/${mapRunId}/knowledge-structures/${encodeURIComponent(structureRevision)}`,
  );
  await openMapConcept(page, "Stack");
  await page.getByRole("button", { name: /查看第 1 頁來源/ }).click();
  const dialog = page.getByRole("dialog", { name: "教材來源" });
  await expect(dialog).toContainText("02:00–04:00（第 2 段逐字稿）");
  await expect(dialog.locator("audio")).toHaveAttribute("src", `/v1/artifacts/${artifactId}/media#t=120`);
  await expect(dialog.getByRole("link", { name: "開啟逐字稿頁" })).toHaveAttribute("href", `/v1/artifacts/${artifactId}#page=2`);
  await expect(dialog.getByRole("link", { name: "下載原檔" })).toBeVisible();
  await dialog.getByRole("button", { name: "關閉" }).click();
  current = 1;
  await page.getByRole("button", { name: /查看第 1 頁來源/ }).click();
  await expect(dialog.locator("iframe")).toHaveAttribute(
    "src",
    "https://www.youtube-nocookie.com/embed/iG9CE55wbtY?start=0&autoplay=1&rel=0",
  );
  await expect(dialog.getByRole("link", { name: "在 YouTube 開啟" })).toHaveAttribute(
    "href",
    "https://www.youtube.com/watch?v=iG9CE55wbtY&t=0s",
  );
  await expect(dialog.getByRole("link", { name: "下載原檔" })).toHaveCount(0);
});
