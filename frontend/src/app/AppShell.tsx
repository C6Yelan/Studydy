import { createContext, useEffect, useLayoutEffect, useRef, useState } from "react";
import type { StudydyApiClient } from "../api/client";
import { type AppRoute } from "./routes";
import { Celebrations } from "./LearningProgress";
import { SiteSearch } from "./SiteSearch";
import { RouteLink } from "../ui/RouteLink";
import { Icon, type IconName } from "../ui/Icon";
import { SourcePanelHost } from "../ui/SourcePanel";
import "./shell.css";
import "./chrome.css";
// 教材側欄的樣式疊在全站側欄上，要在外框樣式之後載入。
import { MaterialRail } from "../features/material-flow/MaterialRail";
import { VersionMoveNotice } from "../features/material-flow/VersionMoveNotice";

export const MaterialRunWorkspaceContext = createContext<((runId:string|null)=>void)|null>(null);
/** 答題的專注模式：頁面開啟時外框不顯示任何側欄與底部頁籤，離開由頁面自己的「×」負責。 */
export const FocusModeContext = createContext<((on: boolean) => void) | null>(null);

type Section = "home" | "materials" | "cards" | "podcasts" | "achievements" | "upload" | "profile";
// 全站左側欄的主要入口；手機上同一組入口移到底部頁籤，用同樣的字。
const sections: { id: Section; label: string; icon: IconName; tone: string; route: AppRoute }[] = [
  { id: "home", label: "首頁", icon: "home", tone: "is-blue", route: { name: "home" } },
  { id: "materials", label: "我的群島", icon: "book", tone: "is-green", route: { name: "materials" } },
  { id: "cards", label: "翻卡片", icon: "cards", tone: "is-violet", route: { name: "concept-cards" } },
  { id: "podcasts", label: "Podcast", icon: "headphones", tone: "is-amber", route: { name: "podcasts" } },
  { id: "achievements", label: "成就", icon: "trophy", tone: "is-amber", route: { name: "achievements" } },
];

// 教材內各頁換成教材側欄（「/materials/{id}」本身只是導向，不算）。
const materialRoutes: AppRoute["name"][] = ["knowledge-map", "material-progress", "study-session", "material-content", "research", "topic", "podcast", "podcast-create",
  "card-set", "card-set-edit", "card-set-create", "card-review", "material-sources", "material-source-upload", "material-run", "material-settings"];
// 側欄收合與否只由「展開／收合側欄」改變，選擇全站共用、記在這個瀏覽器；沒選過時跟著視窗寬度。
const railChoiceKey = "studydy.rail-collapsed";
function readRailChoice(): boolean | null {
  try {
    const value = localStorage.getItem(railChoiceKey);
    return value === null ? null : value === "1";
  } catch { return null; }
}
// 桌機斷點（styles.css 共用斷點）：沒選過時 1025～1280 預設收合，1281 起預設展開。
const narrowDesktopQuery = "(max-width: 1280px)";
function useNarrowDesktop() {
  const [narrow, setNarrow] = useState(() => window.matchMedia(narrowDesktopQuery).matches);
  useEffect(() => {
    const media = window.matchMedia(narrowDesktopQuery);
    const update = () => setNarrow(media.matches);
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);
  return narrow;
}
// 名稱藏起來的時候（桌機收合、平板圖示列）才需要名稱提示；手機的頂端列另有樣式，不顯示。斷點同 styles.css。
const tabletQuery = "(min-width: 721px) and (max-width: 1024px)";
const desktopQuery = "(min-width: 1025px)";
const railItemSelector = ".rail :is(.rail-brand, .rail-search, .rail-link, .rail-upload, .rail-toggle)";
type RailTip = { text: string; top: number; left: number };

// 首頁與清單類頁面在寬螢幕放寬版面（表單與閱讀頁維持標準寬度）。
const wideRoutes: AppRoute["name"][] = ["home", "materials", "concept-cards", "podcasts", "profile", "achievements"];

// 教材內的概念卡／Podcast 屬於「我的群島」；全域清單才屬於各自分區。
function currentSection(route: AppRoute): Section | null {
  if (route.name === "not-found") return null;
  if (route.name === "profile" || route.name === "achievements") return route.name;
  if (route.name === "home") return "home";
  if ("materialId" in route && route.materialId) return "materials";
  if (route.name === "upload" || route.name === "topics") return "upload";
  if (["concept-cards", "card-set", "card-set-edit", "card-set-new", "card-review"].includes(route.name)) return "cards";
  if (["podcasts", "podcast", "podcast-new"].includes(route.name)) return "podcasts";
  return "materials";
}

// 側欄「翻卡片」旁的不熟卡數：換頁與視窗回到前景時重讀；讀不到就不顯示數字。
// 教材側欄只算這份教材的卡。
function useForgotCards(apiClient: StudydyApiClient | undefined, routeKey: string, materialId: string | undefined) {
  const [count, setCount] = useState<number | null>(null);
  useEffect(() => {
    if (!apiClient) return;
    let cancelled = false;
    const load = () => void apiClient.getCardReviews({ materialId }).then(
      (value) => { if (!cancelled) setCount(value.forgot_count); },
      () => { if (!cancelled) setCount(null); },
    );
    load();
    window.addEventListener("focus", load);
    return () => { cancelled = true; window.removeEventListener("focus", load); };
  }, [apiClient, routeKey, materialId]);
  return count;
}

/** 教材更新提示放在教材頁上方；把它的高度算進 --shell-offset，填滿視窗的頁面（地圖、筆記）才不會多出捲動。 */
function MaterialNotice({ apiClient, route, materialId }: { apiClient: StudydyApiClient; route: AppRoute; materialId: string }) {
  const slot = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const element = slot.current!;
    const shell = element.closest<HTMLElement>(".app-shell")!;
    const observer = new ResizeObserver(() => shell.style.setProperty("--material-notice-height", `${element.offsetHeight}px`));
    observer.observe(element);
    return () => { observer.disconnect(); shell.style.removeProperty("--material-notice-height"); };
  }, []);
  const sources = ["material-sources", "material-source-upload"].includes(route.name);
  return <div ref={slot} className="material-notice">
    <VersionMoveNotice apiClient={apiClient} materialId={materialId}
      compact={sources || route.name === "material-progress" || route.name === "study-session"}
      entryHidden={route.name === "material-content" && route.kind === "concept-cards"} />
  </div>;
}

export function AppShell({
  children,
  route,
  apiClient,
  learnerId,
}: {
  children: React.ReactNode;
  route: AppRoute;
  // 登入後才有：搜尋、不熟卡數與慶祝動畫都要讀帳號資料；確認登入中只顯示導覽。
  apiClient?: StudydyApiClient;
  learnerId?: string;
}) {
  const [materialRunId,setMaterialRunId]=useState<string|null>(null);
  const [focusMode, setFocusMode] = useState(false);
  const isLearningWorkspace = (route.name === "material-run" && route.runId === materialRunId) || (route.name === "topic" && !!route.materialId) || ["knowledge-map", "material-progress", "study-session", "material-sources", "material-source-upload", "material-content", "research", "podcast", "podcast-create", "card-set", "card-set-edit", "card-set-create", "card-review"].includes(route.name);
  // 從全站「翻卡片」「Podcast」直接開啟（沒有 materialId）的頁面仍屬全站側欄。
  const materialId = apiClient && materialRoutes.includes(route.name) && "materialId" in route ? route.materialId : undefined;
  const [railChoice, setRailChoice] = useState(readRailChoice);
  const narrowDesktop = useNarrowDesktop();
  const collapsed = railChoice ?? narrowDesktop;
  // 名稱提示畫在側欄外（固定定位），不受側欄捲動範圍裁切，也不會撐出側欄的橫向捲軸。
  const [tip, setTip] = useState<RailTip | null>(null);
  const toggleRail = () => {
    const next = !collapsed;
    setRailChoice(next);
    setTip(null);
    // 存不進去（例如隱私模式）時這次瀏覽仍照按的結果，下次開啟回到預設。
    try { localStorage.setItem(railChoiceKey, next ? "1" : "0"); } catch { /* 照預設值 */ }
  };
  const showTip = (target: EventTarget) => {
    const item = (target as Element).closest?.(railItemSelector);
    const iconOnly = window.matchMedia(tabletQuery).matches || (collapsed && window.matchMedia(desktopQuery).matches);
    const text = item?.querySelector(":scope > span:not(.shell-sr-only)")?.textContent;
    if (!item || !iconOnly || !text) return;
    const box = item.getBoundingClientRect();
    setTip({ text, top: box.top + box.height / 2, left: item.closest(".rail")!.getBoundingClientRect().right + 8 });
  };
  const tipEvents = {
    onPointerOver: (event: React.PointerEvent) => { if (event.pointerType !== "touch") showTip(event.target); },
    onPointerOut: (event: React.PointerEvent) => {
      // 在同一個項目裡從圖示移到文字不算離開。
      if (!(event.target as Element).closest(railItemSelector)?.contains(event.relatedTarget as Node | null)) setTip(null);
    },
    onFocus: (event: React.FocusEvent) => { if ((event.target as Element).matches(":focus-visible")) showTip(event.target); },
    onBlur: () => setTip(null),
    onScrollCapture: () => setTip(null),
  };
  const section = currentSection(route);
  const routeKey = JSON.stringify(route);
  const forgot = useForgotCards(apiClient, routeKey, materialId);
  useEffect(() => setTip(null), [routeKey]);
  const current = (id: Section) => section === id ? "page" as const : undefined;
  const count = (id: Section) => id === "cards" && forgot ? <>
    <small className="shell-count" aria-hidden="true">{forgot}</small>
    <span className="shell-sr-only">，{forgot} 張不熟</span>
  </> : null;
  const toggle = <button className="rail-toggle" type="button" aria-expanded={!collapsed} onClick={toggleRail}>
    <Icon name={collapsed ? "chevron-right" : "chevron-left"} size={20} /><span className="rail-label">{collapsed ? "展開側欄" : "收合側欄"}</span>
  </button>;
  const classes = ["app-shell", isLearningWorkspace ? "is-workspace" : "is-standard", materialId && "is-material", wideRoutes.includes(route.name) && "is-wide",
    route.name === "home" && "is-home", collapsed && "is-rail-collapsed", focusMode && "is-focus"];
  return (
    <MaterialRunWorkspaceContext.Provider value={setMaterialRunId}>
    <FocusModeContext.Provider value={setFocusMode}>
    <div className={classes.filter(Boolean).join(" ")} {...tipEvents}>
      {focusMode ? null : apiClient && materialId ? <MaterialRail apiClient={apiClient} route={route} materialId={materialId} forgot={forgot} toggle={toggle} /> : <aside className="rail">
        <RouteLink className="rail-brand" route={{ name: "home" }} aria-label="返回 Studydy 首頁">
          <img src="/assets/studydy/brand-idle.png" alt="" />
          <span>Studydy</span>
        </RouteLink>
        {apiClient && <SiteSearch apiClient={apiClient} />}
        <nav className="rail-nav" aria-label="主要導覽">
          <ul className="rail-links">
            {sections.map((item) => (
              <li key={item.id}>
                <RouteLink className="rail-link" route={item.route} aria-current={current(item.id)}>
                  <i className={item.tone} aria-hidden="true"><Icon name={item.icon} size={20} /></i>
                  <span className="rail-label">{item.label}</span>
                  {count(item.id)}
                </RouteLink>
              </li>
            ))}
          </ul>
          <RouteLink className="rail-upload" route={{ name: "upload" }} aria-current={current("upload")}>
            <Icon name="plus" size={20} /><span className="rail-label">新增教材</span>
          </RouteLink>
          <RouteLink className="rail-link rail-minor" route={{ name: "profile" }} aria-current={current("profile")}>
            <i aria-hidden="true"><Icon name="user-circle" size={20} /></i>
            <span className="rail-label">個人檔案與設定</span>
          </RouteLink>
        </nav>
        {toggle}
      </aside>}
      <main className="app-main" id="main-content">
        {apiClient && materialId && route.name !== "material-settings" && <MaterialNotice apiClient={apiClient} route={route} materialId={materialId} />}
        {children}
      </main>
      <SourcePanelHost routeKey={routeKey} />
      {/* 手機：全站側欄的主要入口改成底部頁籤；搜尋、新增教材與個人檔案留在頂端那一列。教材側欄自己有底部頁籤。 */}
      {!focusMode && !materialId && <nav className="phone-nav" aria-label="手機導覽">
        {sections.map((item) => (
          <RouteLink key={item.id} route={item.route} aria-current={current(item.id)}>
            <Icon name={item.icon} size={22} />
            <span>{item.label}</span>
            {count(item.id)}
          </RouteLink>
        ))}
      </nav>}
      {tip && <div className="rail-tip" aria-hidden="true" style={{ top: tip.top, left: tip.left }}>{tip.text}</div>}
      {apiClient && learnerId && <Celebrations apiClient={apiClient} learnerId={learnerId} />}
    </div>
    </FocusModeContext.Provider>
    </MaterialRunWorkspaceContext.Provider>
  );
}
