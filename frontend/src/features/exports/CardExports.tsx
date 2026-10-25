import type { StudydyApiClient } from "../../api/client";
import type { CardStudyView } from "../../api/contracts";
import { ExportControls } from "./ExportControls";

export function CardExports({ api, view }: { api: StudydyApiClient; view: CardStudyView }) {
  return <ExportControls label="匯出整組概念卡" options={[
    { value: "apkg", label: "Anki（.apkg）" }, { value: "pdf", label: "可列印 PDF" }, { value: "zip", label: "全部卡面 PNG（ZIP）" },
  ]} download={async format => {
    const { conceptCardPages, conceptCardCanvas } = await import("../concept-cards/ConceptCardArtwork");
    await document.fonts.ready;
    const images: { concept_id: string; page: number; png: string }[] = [];
    for (let offset = 0; offset < view.navigation.length; offset += 8) {
      const batch = await api.getCardBatch(view.card_set_id, view.version, offset);
      for (const card of batch.cards) {
        const pages = conceptCardPages(card, batch.context);
        for (let page = 0; page < pages.length; page++) {
          const canvas = await conceptCardCanvas(card, batch.context, pages[page], page, pages.length, api);
          images.push({ concept_id: card.concept_id, page, png: canvas.toDataURL("image/png").split(",")[1] });
        }
      }
    }
    await api.downloadExport(`/v1/card-sets/${view.card_set_id}/export/${format}`, { version: view.version, images });
  }} />;
}
