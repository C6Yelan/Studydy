import { useEffect, useRef } from "react";
import { writeRoute, type AppRoute } from "../../app/routes";
import type { StudydyApiClient } from "../../api/client";
import { MaterialTools } from "../material-tools/MaterialTools";

type Mode = "focus" | "review";
type Content = "knowledge-map" | "review" | "concept-cards" | "podcasts" | "research" | "sources";
const items: { id: Content; label: string }[] = [
  { id: "knowledge-map", label: "概念地圖" }, { id: "review", label: "複習重點" },
  { id: "concept-cards", label: "概念卡" }, { id: "podcasts", label: "Podcast" },
  { id: "research", label: "補充學習" }, { id: "sources", label: "教材來源" },
];
const tabId = (id: Content) => id === "knowledge-map" ? "map-tab-focus" : id === "review" ? "map-tab-review" : `material-tab-${id}`;
const panelId = (id: Content) => id === "knowledge-map" ? "map-panel-focus" : id === "review" ? "map-panel-review" : `material-panel-${id}`;

export function MaterialContentNav({ apiClient, materialId, mapRoute, current, onMapModeChange, onMapTabRef }: {
  apiClient: StudydyApiClient; materialId: string;
  mapRoute: Extract<AppRoute, { name: "knowledge-map" }> | null; current: Content;
  onMapModeChange?: (mode: Mode) => void;
  onMapTabRef?: (mode: Mode, element: HTMLButtonElement | null) => void;
}) {
  const navigation = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const nav = navigation.current;
    if (!nav) return;
    const reveal = () => {
      const selected = nav.querySelector<HTMLElement>('[aria-selected="true"]');
      if (!selected) return;
      const area = nav.getBoundingClientRect(), item = selected.getBoundingClientRect();
      if (item.right > area.right) nav.scrollLeft += item.right - area.right;
      else if (item.left < area.left) nav.scrollLeft -= area.left - item.left;
    };
    reveal();
    if (window.history.state?.focusMaterialTab) {
      nav.querySelector<HTMLElement>('[aria-selected="true"]')?.focus({ preventScroll: true });
      window.history.replaceState({ ...window.history.state, focusMaterialTab: false }, "");
    }
    const observer = new ResizeObserver(reveal);
    observer.observe(nav);
    return () => observer.disconnect();
  }, [current]);
  const activate = (id: Content, keyboard = false) => {
    if (id === "knowledge-map" || id === "review") {
      if (!mapRoute) return;
      const mode: Mode = id === "review" ? "review" : "focus";
      if (onMapModeChange) onMapModeChange(mode);
      else writeRoute(mapRoute, false, { knowledgeMap: { revision: mapRoute.structureRevision, mode }, focusMaterialTab: keyboard });
    } else {
      const route: AppRoute = id === "sources" ? { name: "material-sources", materialId } : { name: "material-content", materialId, kind: id };
      writeRoute(route, false, { focusMaterialTab: keyboard });
    }
  };
  return <div className="material-learning-toolbar">
    <div ref={navigation} className="material-content-nav map-tabs" role="tablist" aria-label="教材學習內容">
      {items.map((item, index) => <button key={item.id} type="button" id={tabId(item.id)} role="tab" aria-selected={current === item.id} aria-controls={panelId(item.id)}
        className={current === item.id ? "is-active" : undefined} tabIndex={current === item.id ? 0 : -1} disabled={!mapRoute && item.id !== "sources"}
        ref={element => { if (item.id === "knowledge-map" || item.id === "review") onMapTabRef?.(item.id === "review" ? "review" : "focus", element); }}
        onClick={() => activate(item.id)} onKeyDown={event => {
          let next = index;
          if (event.key === "ArrowRight") next = (index + 1) % items.length;
          else if (event.key === "ArrowLeft") next = (index - 1 + items.length) % items.length;
          else if (event.key === "Home") next = 0;
          else if (event.key === "End") next = items.length - 1;
          else return;
          event.preventDefault(); activate(items[next].id, true);
        }}>{item.label}</button>)}
    </div>
    <MaterialTools api={apiClient} materialId={materialId} />
  </div>;
}
