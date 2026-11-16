import { useEffect, useRef, useState, type MouseEvent, type ReactNode } from "react";
import type { StudydyApiClient } from "../../api/client";
import { writeRoute, type AppRoute } from "../../app/routes";
import { RouteLink } from "../../ui/RouteLink";
import { Icon, type IconName } from "../../ui/Icon";
import { MaterialTools } from "../material-tools/MaterialTools";
import { rememberMaterial, useRememberedMaterial } from "./material-memory";
import "./material-rail.css";

// 作答頁開著時，側欄的「知識地圖」打開地圖並聚焦剛才學的概念（由作答頁登記）。
let mapFocusConcept: string | null = null;
export function useMapFocusConcept(conceptId: string | null) {
  useEffect(() => {
    mapFocusConcept = conceptId;
    return () => { mapFocusConcept = null; };
  }, [conceptId]);
}

type Item = "knowledge-map" | "progress" | "concept-cards" | "podcasts" | "notes" | "assistant" | "research" | "sources" | "settings";
/** 手機收在「更多」裡的項目；目前在其中一頁時「更多」標成目前所在。 */
const moreItems: Item[] = ["notes", "research", "sources", "settings"];
type MapRoute = Extract<AppRoute, { name: "knowledge-map" }>;

/** 教材側欄所在的項目；教材內的卡組、Podcast、作答等子頁歸到對應的清單項目。 */
function currentItem(route: AppRoute): Item | null {
  switch (route.name) {
    case "knowledge-map": return "knowledge-map";
    case "material-progress": case "study-session": return "progress";
    case "card-set": case "card-set-edit": case "card-set-create": case "card-review": return "concept-cards";
    case "podcast": case "podcast-create": return "podcasts";
    case "material-content": return route.kind;
    case "research": case "topic": return "research";
    case "material-sources": case "material-source-upload": case "material-run": return "sources";
    case "material-settings": return "settings";
    default: return null;
  }
}

/** 網址帶著地圖版本時沿用該版本；其餘頁面用教材目前的地圖。 */
function mapRouteOf(route: AppRoute, materialId: string, structures: { run_id: string; knowledge_structure_revision: string }[] | undefined, head: string | null | undefined): MapRoute | null {
  if ("structureRevision" in route && "runId" in route) return { name: "knowledge-map", materialId, runId: route.runId, structureRevision: route.structureRevision };
  const structure = structures?.find((item) => item.knowledge_structure_revision === head) ?? structures?.[0];
  return structure ? { name: "knowledge-map", materialId, runId: structure.run_id, structureRevision: structure.knowledge_structure_revision } : null;
}

export function MaterialRail({ apiClient, route, materialId, forgot, toggle }: {
  apiClient: StudydyApiClient;
  route: AppRoute;
  materialId: string;
  /** 這份教材的不熟卡數；讀不到時為 null。 */
  forgot: number | null;
  /** 「展開／收合側欄」按鈕，放在最下面，項目位置不因頁面而變。 */
  toggle: ReactNode;
}) {
  const material = useRememberedMaterial(apiClient, materialId);
  // 頁面本身多半會讀教材索引；沒有快取時才由側欄補讀，失敗只少顯示名稱，項目照地圖版本判斷。
  useEffect(() => {
    if (material) return;
    let cancelled = false;
    void apiClient.getMaterial(materialId).then((value) => { if (!cancelled) rememberMaterial(apiClient, value); }, () => undefined);
    return () => { cancelled = true; };
  }, [apiClient, materialId, material]);
  const mapRoute = mapRouteOf(route, materialId, material?.available_structures, material?.head_revision);
  const current = currentItem(route);
  const [moreOpen, setMoreOpen] = useState(false);
  const moreButton = useRef<HTMLButtonElement>(null);
  const routeKey = JSON.stringify(route);
  useEffect(() => setMoreOpen(false), [routeKey]);
  const nav = useRef<HTMLElement>(null);
  // 點面板外面就收起；點到面板裡的項目（含不換頁的「問小幫手」）由面板的 onClick 收起。
  useEffect(() => {
    if (!moreOpen) return;
    const close = (event: PointerEvent) => { if (!nav.current?.contains(event.target as Node)) setMoreOpen(false); };
    document.addEventListener("pointerdown", close);
    return () => document.removeEventListener("pointerdown", close);
  }, [moreOpen]);

  const link = (id: Item, label: string, icon: IconName, tone: string, target: AppRoute | null, onClick?: (event: MouseEvent<HTMLAnchorElement>) => void) => {
    const count = id === "concept-cards" && forgot ? <>
      <small className="shell-count" aria-hidden="true">{forgot}</small>
      <span className="shell-sr-only">，{forgot} 張不熟</span>
    </> : null;
    const body = <>
      <i className={tone} aria-hidden="true"><Icon name={icon} size={20} /></i>
      <span className="rail-label">{label}</span>
      {count}
    </>;
    // 還沒有知識地圖（確認來源中、分析失敗）時只開放原始檔案與教材設定。
    return <li key={id}>{target
      ? <RouteLink className="rail-link" route={target} aria-current={current === id ? "page" : undefined} onClick={onClick}>{body}</RouteLink>
      : <a className="rail-link is-disabled" role="link" aria-disabled="true">{body}</a>}</li>;
  };
  const withMap = (target: (map: MapRoute) => AppRoute) => mapRoute ? target(mapRoute) : null;

  return <aside className="rail material-rail">
    <RouteLink className="rail-brand" route={{ name: "home" }} aria-label="返回 Studydy 首頁">
      <img src="/assets/studydy/brand-idle.png" alt="" />
      <span>Studydy</span>
    </RouteLink>
    <RouteLink className="rail-link material-rail-islands" route={{ name: "materials" }}>
      <i aria-hidden="true"><Icon name="arrow-left" size={20} /></i>
      <span className="rail-label">我的群島</span>
    </RouteLink>
    <p className="material-rail-name"><small>目前教材</small><b title={material?.display_name}>{material?.display_name ?? " "}</b></p>
    <nav ref={nav} className={`material-rail-nav${moreOpen ? " is-more-open" : ""}`} aria-label="教材導覽"
      onKeyDown={(event) => { if (event.key === "Escape" && moreOpen) { event.stopPropagation(); setMoreOpen(false); moreButton.current?.focus(); } }}>
      <ul className="rail-links material-rail-main">
        {link("knowledge-map", "知識地圖", "map", "is-blue", mapRoute, (event) => {
          const concept = mapFocusConcept;
          if (!concept || !mapRoute || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
          event.preventDefault();
          writeRoute(mapRoute, false, { knowledgeMap: { revision: mapRoute.structureRevision, concept, detail: concept } });
        })}
        {link("progress", "學習進度", "chart", "is-green", withMap((map) => ({ ...map, name: "material-progress" })))}
        {link("concept-cards", "翻卡片", "cards", "is-violet", withMap(() => ({ name: "material-content", materialId, kind: "concept-cards" })))}
        {link("podcasts", "聽 Podcast", "headphones", "is-amber", withMap(() => ({ name: "material-content", materialId, kind: "podcasts" })))}
      </ul>
      {/* 手機：底部只放四個常用項目，其餘收進「更多」。 */}
      <button ref={moreButton} className={`material-rail-more-toggle${current && moreItems.includes(current) ? " is-current" : ""}`} type="button" aria-expanded={moreOpen} aria-controls="material-rail-more" onClick={() => setMoreOpen((open) => !open)}>
        <span className="material-rail-dots" aria-hidden="true">⋯</span><span>更多</span>
      </button>
      <div id="material-rail-more" className="material-rail-more" onClick={(event) => { if ((event.target as HTMLElement).closest("a, button")) setMoreOpen(false); }}>
        <ul className="rail-links">
          {link("notes", "我的筆記", "book", "is-green", withMap(() => ({ name: "material-content", materialId, kind: "notes" })))}
          {/* 小幫手只依據已發布的地圖回答，沒有地圖時不提供（同舊版）。 */}
          {mapRoute && <li><MaterialTools key={`${materialId}:${mapRoute.structureRevision}`} api={apiClient} materialId={materialId} revision={mapRoute.structureRevision} fallbackFocus={moreButton} /></li>}
          {link("research", "找更多資料", "search", "is-amber", withMap(() => ({ name: "material-content", materialId, kind: "research" })))}
        </ul>
        <ul className="rail-links material-rail-minor">
          {link("sources", "原始檔案", "file", "", { name: "material-sources", materialId })}
          {link("settings", "教材設定", "edit", "", { name: "material-settings", materialId })}
        </ul>
      </div>
    </nav>
    {toggle}
  </aside>;
}

/** 教材內子頁（卡組、Podcast、找更多資料詳情、新增來源、作答）回到所屬清單；取代舊教材頁首的返回鍵。 */
export function MaterialBack({ label, onClick }: { label: string; onClick: () => void }) {
  return <button className="material-back" type="button" onClick={onClick}><Icon name="arrow-left" size={18} /><span>{label}</span></button>;
}
