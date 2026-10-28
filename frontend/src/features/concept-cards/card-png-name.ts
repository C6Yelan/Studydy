import type { StudydyApiClient } from "../../api/client";
import type { CardStudyView } from "../../api/contracts";

const fileName = (text: string) => text.replace(/[<>:"/\\|?*\u0000-\u001f]/g, "").replace(/^[ .]+|[ .]+$/g, "");

/** 單張 PNG 檔名：教材名稱-卡片編號-概念名稱（多頁卡面再加頁碼）。舊卡組缺少張數時，下載當下才讀取既有卡面計算。 */
export async function cardPngName(view: CardStudyView, conceptId: string, pageIndex: number, api: StudydyApiClient): Promise<string> {
  const index = view.navigation.findIndex(entry => entry.concept_id === conceptId);
  if (index < 0) throw new Error("找不到這張卡片，請重新開啟卡組。");
  let count = view.navigation[index].page_count;
  if (count === null) {
    const offset = Math.floor(index / 8) * 8;
    const batch = offset === 0 && view.batch ? view.batch : await api.getCardBatch(view.card_set_id, view.version, offset);
    const artwork = await import("./ConceptCardArtwork");
    count = artwork.conceptCardPages(batch.cards[index - offset], batch.context).length;
  }
  const material = fileName(view.material_name.replace(/\.(pdf|pptx?|docx?|png|jpe?g|mp3|m4a|wav|mp4)$/i, "")) || "概念卡";
  const stem = `${material}-${String(index + 1).padStart(2, "0")}-${fileName(view.navigation[index].label) || "概念卡"}`;
  return count === 1 ? `${stem}.png` : `${stem}-${pageIndex + 1}.png`;
}
