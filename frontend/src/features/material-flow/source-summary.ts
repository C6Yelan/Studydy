import type { MaterialLibraryItem, SourceView } from "../../api/contracts";

type SourceTotals = NonNullable<MaterialLibraryItem["source_totals"]>;

export function sourceTotals(sources: SourceView[]): SourceTotals {
  const totals: SourceTotals = { page_count: 0, image_count: 0, media_seconds: 0 };
  for (const source of sources) {
    if (source.source_kind === "document") totals.page_count += source.page_count ?? 0;
    else if (source.source_kind === "image") totals.image_count += source.image_count ?? 0;
    else if (source.source_kind && ["audio", "video", "youtube"].includes(source.source_kind)) {
      totals.media_seconds = totals.media_seconds === null || source.duration_seconds == null
        ? null : totals.media_seconds + source.duration_seconds;
    }
  }
  return totals;
}

export function sourceTotalsLabel(totals: SourceTotals): string {
  const parts: string[] = [];
  if (totals.page_count > 0) parts.push(`共 ${totals.page_count} 頁`);
  if (totals.image_count > 0) parts.push(`${totals.image_count} 張圖片`);
  if (totals.media_seconds != null && totals.media_seconds > 0) {
    const seconds = Math.floor(totals.media_seconds);
    parts.push(`影音 ${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒`);
  }
  return parts.join(" · ");
}

export function sourceMetadata(source: SourceView): string {
  if (source.source_kind === "document") return source.page_count === null ? "" : `${source.page_count} 頁`;
  if (source.source_kind === "image") return source.image_count == null ? "圖片" : `${source.image_count} 張圖片`;
  const name = source.source_kind === "youtube" ? "YouTube" : source.source_kind === "video" ? "影片" : source.source_kind === "audio" ? "音訊" : "";
  if (!name || source.duration_seconds == null) return name;
  const seconds = Math.floor(source.duration_seconds);
  return `${name} · ${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}
