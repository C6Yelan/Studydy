import type { StudydyApiClient } from "../../api/client";
import type { CardStudyView } from "../../api/contracts";

// 與 ZIP 依卡組順序命名；舊卡組缺少張數時，下載當下才讀取既有卡面計算。
export async function cardPngName(view: CardStudyView, conceptId: string, pageIndex: number, api: StudydyApiClient): Promise<string> {
  const used = new Set<string>();
  const batches = new Map(view.batch ? [[0, view.batch]] : []);
  for (const [index, entry] of view.navigation.entries()) {
    let count = entry.page_count;
    if (count === null) {
      const offset = Math.floor(index / 8) * 8;
      let batch = batches.get(offset);
      if (!batch) { batch = await api.getCardBatch(view.card_set_id, view.version, offset); batches.set(offset, batch); }
      const artwork = await import("./ConceptCardArtwork");
      count = artwork.conceptCardPages(batch.cards[index - offset], batch.context).length;
    }
    const base = entry.label.replace(/[<>:"/\\|?*\u0000-\u001f]/g, "").replace(/^[ .]+|[ .]+$/g, "") || "概念卡";
    for (let page = 0; page < count; page++) {
      const stem = count === 1 ? base : `${base}-${page + 1}`;
      let name = `${stem}.png`, suffix = 2;
      const fold = (text: string) => text.toUpperCase().toLowerCase();
      while (used.has(fold(name))) name = `${stem}-${suffix++}.png`;
      used.add(fold(name));
      if (entry.concept_id === conceptId && page === pageIndex) return name;
    }
  }
  throw new Error("找不到這張卡片，請重新開啟卡組。");
}
