import assert from "node:assert/strict";
import test from "node:test";

import { packImages, queueFiles, queuedName, youtubeProblem } from "./source-uploads.ts";

const MiB = 1024 * 1024;

function capabilities(documentMax = 100 * MiB) {
  return {
    schema: "source-capabilities/v2",
    max_request_bytes: 90 * MiB,
    max_images: 50,
    max_media_seconds: 7200,
    youtube: true,
    formats: [
      { extension: ".pdf", media_type: "application/pdf", max_bytes: documentMax, kind: "document" },
      { extension: ".txt", media_type: "text/plain", max_bytes: documentMax, kind: "document" },
      { extension: ".png", media_type: "image/png", max_bytes: 100 * MiB, kind: "image" },
      { extension: ".jpg", media_type: "image/jpeg", max_bytes: 100 * MiB, kind: "image" },
    ],
  };
}

function file(name, size, type = "") {
  const value = new File([new Uint8Array(Math.min(size, 16))], name, { type });
  Object.defineProperty(value, "size", { value: size });
  return value;
}

test("documents follow their advertised format and individual size limit", async () => {
  const caps = capabilities();
  const valid = file("教材.PDF", 100 * MiB, "application/pdf");
  assert.deepEqual((await queueFiles([valid], caps, [])).rejected, []);
  assert.deepEqual((await queueFiles([file("notes.txt", 12)], caps, [])).rejected, []);
  for (const [candidate, pattern] of [
    [file("教材.pdf", 10, "text/plain"), /類型不一致/],
    [file("file.exe", 10), /不支援/],
    [file("empty.pdf", 0), /空白/],
    [file("big.pdf", 100 * MiB + 1), /100 MiB/],
  ]) {
    const { queue, rejected } = await queueFiles([candidate], caps, []);
    assert.equal(queue.length, 0);
    assert.match(rejected[0], pattern);
  }
  assert.match((await queueFiles([file("big.pdf", 90 * MiB + 1)], capabilities(90 * MiB), [])).rejected[0], /90 MiB/);
});

test("images join one ordered set with count and total limits", async () => {
  const caps = { ...capabilities(), max_images: 3 };
  let { queue } = await queueFiles([file("b.png", 10), file("a.jpg", 10)], caps, []);
  assert.equal(queue.length, 1);
  assert.deepEqual(queue[0].images.map((image) => image.name), ["b.png", "a.jpg"]);
  assert.equal(queuedName(queue[0]), "b 等 2 張圖片");
  const more = await queueFiles([file("c.png", 10), file("d.png", 10)], caps, queue);
  assert.equal(more.queue[0].images.length, 3);
  assert.match(more.rejected[0], /最多 3 張/);
  const heavy = await queueFiles([file("e.png", 100 * MiB)], capabilities(), queue);
  assert.match(heavy.rejected[0], /合計最多 100 MiB/);
});

test("image sets pack deterministically in the chosen order", async () => {
  const images = [new File(["first"], "slide.PNG"), new File(["second"], "photo.jpeg")];
  const first = new Uint8Array(await (await packImages(images)).arrayBuffer());
  const second = new Uint8Array(await (await packImages(images)).arrayBuffer());
  assert.deepEqual(first, second);
  const text = new TextDecoder().decode(first);
  assert.ok(text.indexOf("0001.png") < text.indexOf("first"));
  assert.ok(text.indexOf("0002.jpeg") < text.indexOf("second"));
});

test("YouTube input accepts single videos and rejects playlists and other sites", () => {
  assert.equal(youtubeProblem("https://www.youtube.com/watch?v=iG9CE55wbtY"), null);
  assert.equal(youtubeProblem("youtu.be/iG9CE55wbtY"), null);
  assert.equal(youtubeProblem("https://www.youtube.com/shorts/iG9CE55wbtY"), null);
  assert.match(youtubeProblem("https://www.youtube.com/playlist?list=PL123"), /播放清單/);
  assert.match(youtubeProblem("https://example.com/watch?v=iG9CE55wbtY"), /YouTube/);
});
