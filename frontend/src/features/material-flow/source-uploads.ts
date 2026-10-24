import type { StudydyApiClient } from "../../api/client";
import type { FormatCapability, SourceCapabilities } from "../../api/contracts";

/** 待上傳的來源：一般檔案、一組依序排列的圖片，或一個 YouTube 連結。 */
export type QueuedSource = {
  key: string;
  kind: "file" | "images" | "youtube";
  status: "pending" | "uploading" | "uploaded" | "failed";
  error: string | null;
  /** 分段上傳時已送出的比例（0～1）。 */
  progress: number | null;
  file?: File;
  images?: File[];
  url?: string;
  /** 圖片組第一次打包後保留同一份 bytes，重試時冪等鍵仍對應相同內容。 */
  packed?: File;
};

const imageSetMediaType = "application/zip";
const youtubeHosts = new Set([
  "youtube.com",
  "www.youtube.com",
  "m.youtube.com",
  "music.youtube.com",
  "youtu.be",
  "www.youtu.be",
]);

export function formatFor(name: string, formats: FormatCapability[]): FormatCapability | undefined {
  return formats.find((item) => name.toLowerCase().endsWith(item.extension));
}

export function queuedName(item: QueuedSource): string {
  if (item.kind === "youtube") return item.url ?? "YouTube 影片";
  if (item.kind === "images") {
    const images = item.images ?? [];
    const stem = images[0]?.name.replace(/\.[^.]+$/, "") ?? "圖片";
    return images.length > 1 ? `${stem} 等 ${images.length} 張圖片` : `${stem}（圖片）`;
  }
  return item.file?.name ?? "";
}

export function queuedSize(item: QueuedSource): number {
  if (item.kind === "images") return (item.images ?? []).reduce((sum, file) => sum + file.size, 0);
  return item.file?.size ?? 0;
}

/** 音訊／影片長度先在瀏覽器讀 metadata；讀不到時交由伺服器核對。 */
export function mediaDuration(file: File): Promise<number | null> {
  return new Promise((resolve) => {
    const element = document.createElement(file.type.startsWith("video/") ? "video" : "audio");
    const url = URL.createObjectURL(file);
    const finish = (value: number | null) => {
      URL.revokeObjectURL(url);
      element.removeAttribute("src");
      resolve(value);
    };
    element.preload = "metadata";
    element.onloadedmetadata = () =>
      finish(Number.isFinite(element.duration) ? element.duration : null);
    element.onerror = () => finish(null);
    window.setTimeout(() => finish(null), 10_000);
    element.src = url;
  });
}

function fileProblem(file: File, format: FormatCapability | undefined): string | null {
  if (!format) return "目前不支援這個檔案格式。";
  if (file.size === 0) return "教材不可為空白檔案。";
  if (
    format.kind === "document" &&
    file.type &&
    file.type !== "application/octet-stream" &&
    file.type !== format.media_type
  )
    return "副檔名與檔案類型不一致。";
  if (format.kind !== "image" && file.size > format.max_bytes)
    return `每份${format.kind === "audio" ? "音訊" : format.kind === "video" ? "影片" : "檔案"}最多 ${format.max_bytes / (1024 * 1024)} MiB。`;
  return null;
}

/**
 * 將選取的檔案排入待上傳清單：文件、音訊、影片各自一筆；圖片併入尚未上傳的圖片組，
 * 第幾張就是第幾頁。回傳新的清單與被拒絕的原因。
 */
export async function queueFiles(
  files: File[],
  capabilities: SourceCapabilities,
  queue: QueuedSource[],
): Promise<{ queue: QueuedSource[]; rejected: string[] }> {
  const rejected: string[] = [];
  const next = [...queue];
  const imageFormat = capabilities.formats.find((format) => format.kind === "image");
  for (const file of files) {
    const format = formatFor(file.name, capabilities.formats);
    const problem = fileProblem(file, format);
    if (problem) {
      rejected.push(`「${file.name}」：${problem}`);
      continue;
    }
    if (format!.kind === "image") {
      let set = next.find((item) => item.kind === "images" && item.status === "pending");
      if (!set) {
        set = { key: crypto.randomUUID(), kind: "images", status: "pending", error: null, progress: null, images: [] };
        next.push(set);
      }
      const images = set.images ?? [];
      const total = images.reduce((sum, image) => sum + image.size, 0) + file.size;
      if (images.length >= capabilities.max_images) {
        rejected.push(`「${file.name}」：一組圖片最多 ${capabilities.max_images} 張。`);
        continue;
      }
      if (imageFormat && total > imageFormat.max_bytes) {
        rejected.push(`「${file.name}」：一組圖片合計最多 ${imageFormat.max_bytes / (1024 * 1024)} MiB。`);
        continue;
      }
      const index = next.indexOf(set);
      next[index] = { ...set, images: [...images, file], packed: undefined };
      continue;
    }
    if (format!.kind === "audio" || format!.kind === "video") {
      const seconds = await mediaDuration(file);
      if (seconds !== null && seconds > capabilities.max_media_seconds + 1) {
        rejected.push(`「${file.name}」：長度超過 ${capabilities.max_media_seconds / 3600} 小時上限。`);
        continue;
      }
    }
    next.push({ key: crypto.randomUUID(), kind: "file", status: "pending", error: null, progress: null, file });
  }
  return { queue: next, rejected };
}

export function youtubeProblem(url: string): string | null {
  try {
    const parsed = new URL(url.includes("://") ? url : `https://${url}`);
    if (!["http:", "https:"].includes(parsed.protocol) || !youtubeHosts.has(parsed.hostname.toLowerCase()))
      return "請貼上 YouTube 影片網址。";
    if (parsed.pathname.startsWith("/playlist")) return "不支援播放清單，請貼上單支影片的網址。";
    return null;
  } catch {
    return "請貼上 YouTube 影片網址。";
  }
}

export function moveImage(item: QueuedSource, index: number, offset: -1 | 1): QueuedSource {
  const images = [...(item.images ?? [])];
  [images[index], images[index + offset]] = [images[index + offset], images[index]];
  return { ...item, images, packed: undefined };
}

export function removeImage(item: QueuedSource, index: number): QueuedSource {
  return { ...item, images: (item.images ?? []).filter((_, position) => position !== index), packed: undefined };
}

const crcTable = (() => {
  const table = new Uint32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    table[n] = c >>> 0;
  }
  return table;
})();

function crc32(bytes: Uint8Array): number {
  let crc = 0xffffffff;
  for (let index = 0; index < bytes.length; index++)
    crc = crcTable[(crc ^ bytes[index]) & 0xff] ^ (crc >>> 8);
  return (crc ^ 0xffffffff) >>> 0;
}

/** 依目前順序打包成不壓縮的 ZIP：0001.png、0002.jpg…；後端依檔名順序排成頁碼。 */
export async function packImages(images: File[]): Promise<Blob> {
  const parts: BlobPart[] = [];
  const central: Uint8Array<ArrayBuffer>[] = [];
  let offset = 0;
  const encoder = new TextEncoder();
  for (const [index, image] of images.entries()) {
    const extension = image.name.toLowerCase().match(/\.(png|jpe?g|webp)$/)![1];
    const name = new Uint8Array(encoder.encode(`${String(index + 1).padStart(4, "0")}.${extension}`));
    const data = new Uint8Array(await image.arrayBuffer());
    const crc = crc32(data);
    const local = new DataView(new ArrayBuffer(30));
    local.setUint32(0, 0x04034b50, true);
    local.setUint16(4, 20, true);
    local.setUint16(8, 0, true);
    local.setUint16(12, 0x21, true); // 1980-01-01，固定時間讓同一組圖片得到相同 bytes。
    local.setUint32(14, crc, true);
    local.setUint32(18, data.length, true);
    local.setUint32(22, data.length, true);
    local.setUint16(26, name.length, true);
    const header = new Uint8Array(local.buffer);
    parts.push(header, name, data);
    const entry = new DataView(new ArrayBuffer(46));
    entry.setUint32(0, 0x02014b50, true);
    entry.setUint16(4, 20, true);
    entry.setUint16(6, 20, true);
    entry.setUint16(14, 0x21, true);
    entry.setUint32(16, crc, true);
    entry.setUint32(20, data.length, true);
    entry.setUint32(24, data.length, true);
    entry.setUint16(28, name.length, true);
    entry.setUint32(42, offset, true);
    const record = new Uint8Array(46 + name.length);
    record.set(new Uint8Array(entry.buffer));
    record.set(name, 46);
    central.push(record);
    offset += header.length + name.length + data.length;
  }
  const size = central.reduce((sum, record) => sum + record.length, 0);
  const end = new DataView(new ArrayBuffer(22));
  end.setUint32(0, 0x06054b50, true);
  end.setUint16(8, images.length, true);
  end.setUint16(10, images.length, true);
  end.setUint32(12, size, true);
  end.setUint32(16, offset, true);
  return new Blob([...parts, ...central, new Uint8Array(end.buffer)], { type: imageSetMediaType });
}

/**
 * 上傳一筆待上傳來源。超過單次請求上限時依序分段送出，最後一段收齊後才建立來源；
 * 重試從頭送出，伺服器以同一個冪等鍵避免重複建立。
 */
export async function uploadQueued(
  apiClient: StudydyApiClient,
  materialId: string,
  item: QueuedSource,
  capabilities: SourceCapabilities,
  onProgress: (progress: number) => void,
  onPacked: (packed: File) => void,
): Promise<void> {
  if (item.kind === "youtube") {
    await apiClient.addYoutubeSource(materialId, item.url!, item.key);
    return;
  }
  let file = item.file!;
  let mediaType = formatFor(file?.name ?? "", capabilities.formats)?.media_type ?? "";
  if (item.kind === "images") {
    file = item.packed ?? new File([await packImages(item.images ?? [])], `${queuedName(item)}.zip`, { type: imageSetMediaType });
    if (!item.packed) onPacked(file);
    mediaType = imageSetMediaType;
  }
  if (file.size <= capabilities.max_request_bytes) {
    await apiClient.uploadSource(materialId, file, mediaType, item.key);
    return;
  }
  const part = Math.min(capabilities.max_request_bytes, 16 * 1024 * 1024);
  for (let offset = 0; offset < file.size; offset += part) {
    const progress = await apiClient.uploadSourcePart(
      materialId,
      file.slice(offset, offset + part),
      file.name,
      mediaType,
      item.key,
      offset,
      file.size,
    );
    onProgress(progress.received_bytes / file.size);
    if (progress.source_id) return;
  }
}
