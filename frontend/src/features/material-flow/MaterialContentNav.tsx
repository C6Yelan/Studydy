import { writeRoute, type AppRoute } from "../../app/routes";
import { Icon } from "../../ui/Icon";

export function MaterialContentNav({ materialId, mapRoute, current }: {
  materialId: string; mapRoute: Extract<AppRoute, { name: "knowledge-map" }> | null;
  current: "knowledge-map" | "concept-cards" | "podcasts";
}) {
  return <nav className="material-content-nav" aria-label="教材學習內容">
    <button type="button" className={current === "knowledge-map" ? "primary-button" : "secondary-button"} aria-current={current === "knowledge-map" ? "page" : undefined} disabled={!mapRoute} onClick={() => { if (mapRoute) writeRoute(mapRoute); }}><Icon name="map" size={17} />知識地圖</button>
    <button type="button" className={current === "concept-cards" ? "primary-button" : "secondary-button"} aria-current={current === "concept-cards" ? "page" : undefined} onClick={() => writeRoute({ name: "material-content", materialId, kind: "concept-cards" })}><Icon name="cards" size={17} />概念卡</button>
    <button type="button" className={current === "podcasts" ? "primary-button" : "secondary-button"} aria-current={current === "podcasts" ? "page" : undefined} onClick={() => writeRoute({ name: "material-content", materialId, kind: "podcasts" })}><Icon name="headphones" size={17} />Podcast</button>
  </nav>;
}
