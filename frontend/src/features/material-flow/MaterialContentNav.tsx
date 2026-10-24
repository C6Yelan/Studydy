import { useEffect, useRef, useState } from "react";
import { writeRoute, type AppRoute } from "../../app/routes";
import type { StudydyApiClient } from "../../api/client";
import { Icon, type IconName } from "../../ui/Icon";
import { IslandArt, islandPalette } from "../../ui/art/Island";
import { MaterialTools } from "../material-tools/MaterialTools";
import { AccountMenu } from "../../app/HeaderTools";
import { rememberMaterial, rememberedMaterial } from "./material-memory";
import { VersionMoveNotice } from "./VersionMoveNotice";
import "./material-header.css";

type Mode = "focus" | "review";
type Content = "knowledge-map" | "review" | "concept-cards" | "podcasts" | "research" | "sources" | "notes";
const items: { id: Content; label: string; icon: IconName }[] = [
  { id: "knowledge-map", label: "知識地圖", icon: "map" }, { id: "review", label: "學習與測驗", icon: "quiz" },
  { id: "concept-cards", label: "概念卡", icon: "cards" }, { id: "podcasts", label: "Podcast", icon: "headphones" },
  { id: "notes", label: "筆記", icon: "book" },
  { id: "research", label: "補充學習", icon: "sparkle" }, { id: "sources", label: "教材來源", icon: "file" },
];
const dateFormat = new Intl.DateTimeFormat("zh-TW", { year: "numeric", month: "2-digit", day: "2-digit" });
const tabId = (id: Content) => id === "knowledge-map" ? "map-tab-focus" : id === "review" ? "map-tab-review" : `material-tab-${id}`;
const panelId = (id: Content) => id === "knowledge-map" ? "map-panel-focus" : id === "review" ? "map-panel-review" : `material-panel-${id}`;

export function MaterialContentNav({ apiClient, materialId, mapRoute, current, onMapModeChange, onMapTabRef, materialName, assessmentDisabled = false, loadMaterial = false }: {
  apiClient: StudydyApiClient; materialId: string;
  mapRoute: Extract<AppRoute, { name: "knowledge-map" }> | null; current: Content;
  onMapModeChange?: (mode: Mode) => void;
  onMapTabRef?: (mode: Mode, element: HTMLButtonElement | null) => void; materialName?: string;
  assessmentDisabled?: boolean;
  /** 所在頁面本身不讀教材索引時，由資訊列補讀。 */
  loadMaterial?: boolean;
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
    // 分頁列可橫向捲動時，在還有內容的一側淡出，提示可以滑動。
    const edges = () => {
      nav.classList.toggle("can-scroll-left", nav.scrollLeft > 2);
      nav.classList.toggle("can-scroll-right", nav.scrollLeft + nav.clientWidth < nav.scrollWidth - 2);
    };
    edges();
    nav.addEventListener("scroll", edges, { passive: true });
    if (window.history.state?.focusMaterialTab) {
      nav.querySelector<HTMLElement>('[aria-selected="true"]')?.focus({ preventScroll: true });
      window.history.replaceState({ ...window.history.state, focusMaterialTab: false }, "");
    }
    const observer = new ResizeObserver(() => { reveal(); edges(); });
    observer.observe(nav);
    return () => { observer.disconnect(); nav.removeEventListener("scroll", edges); };
  }, [current]);
  const activate = (id: Content, keyboard = false) => {
    if (id === "knowledge-map" || id === "review") {
      if (!mapRoute) return;
      const mode: Mode = id === "review" ? "review" : "focus";
      if (onMapModeChange) onMapModeChange(mode);
      else writeRoute(mapRoute, false, {
        knowledgeMap: { revision: mapRoute.structureRevision, mode },
        focusMaterialTab: keyboard,
      });
    } else {
      const route: AppRoute = id === "sources" ? { name: "material-sources", materialId } : { name: "material-content", materialId, kind: id };
      writeRoute(route, false, { focusMaterialTab: keyboard });
    }
  };
  // 失敗只少顯示資訊列，不影響頁面內容。
  const [, setLoaded] = useState(0);
  const material = rememberedMaterial(apiClient, materialId);
  useEffect(() => {
    if (material || !loadMaterial) return;
    let cancelled = false;
    void apiClient.getMaterial(materialId).then((value) => {
      if (cancelled) return;
      rememberMaterial(apiClient, value);
      setLoaded((count) => count + 1);
    }, () => undefined);
    return () => { cancelled = true; };
  }, [apiClient, materialId, material, loadMaterial]);
  const name = material?.display_name ?? materialName;
  // 這是最近一次分析處理的頁數（可能只含新增來源），全部來源頁數在「教材來源」分頁。
  const pages = material?.latest_attempt?.total_pages;
  // 來源數、頁數與建立日期不常用，收進教材名稱的提示，完整資訊在「教材來源」分頁。
  const meta = material ? [material.source_count ? `${material.source_count} 個來源` : null, pages ? `最近一次分析 ${pages} 頁` : null, `建立於 ${dateFormat.format(new Date(material.created_at))}`].filter(Boolean).join(" · ") : undefined;
  // 教材內唯一的頁首：返回、教材、分頁、問答與頭像排成一列（取代全站頁首）。
  return <><header className="material-learning-toolbar material-header" aria-label="目前教材">
    <div className="material-header-identity">
      <button className="material-header-back" type="button" aria-label="回到我的教材" onClick={() => writeRoute({ name: "materials" })}><Icon name="arrow-left" size={20}/></button>
      <span className="material-header-thumb" aria-hidden="true"><IslandArt palette={islandPalette(materialId)} landmarks={false}/></span>
      {name && <p className="material-context" title={meta ? `${name}\n${meta}` : name}><span>{name}</span></p>}
    </div>
    <div ref={navigation} className="material-content-nav map-tabs" role="tablist" aria-label="教材學習內容">
      {items.map((item, index) => <button key={item.id} type="button" id={tabId(item.id)} role="tab" aria-label={item.label} title={item.label} aria-selected={current === item.id} aria-controls={panelId(item.id)}
        className={current === item.id ? "is-active" : undefined} tabIndex={current === item.id ? 0 : -1} disabled={(!mapRoute && item.id !== "sources") || (item.id === "review" && assessmentDisabled)}
        ref={element => { if (item.id === "knowledge-map" || item.id === "review") onMapTabRef?.(item.id === "review" ? "review" : "focus", element); }}
        onClick={() => activate(item.id)} onKeyDown={event => {
          let next = index;
          if (event.key === "ArrowRight") next = (index + 1) % items.length;
          else if (event.key === "ArrowLeft") next = (index - 1 + items.length) % items.length;
          else if (event.key === "Home") next = 0;
          else if (event.key === "End") next = items.length - 1;
          else return;
          event.preventDefault(); activate(items[next].id, true);
        }}><Icon name={item.icon} size={18}/><span>{item.label}</span></button>)}
    </div>
    <div className="material-header-tools">
      {mapRoute && <MaterialTools key={`${materialId}:${mapRoute.structureRevision}`} api={apiClient} materialId={materialId} revision={mapRoute.structureRevision} />}
      <AccountMenu apiClient={apiClient} />
    </div>
  </header>
  <VersionMoveNotice apiClient={apiClient} materialId={materialId}/></>;
}
