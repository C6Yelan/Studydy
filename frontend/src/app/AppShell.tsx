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
// 地圖、翻卡、需要加強、播放器把畫面留給內容：側欄預設收成圖示列，滑過或按「展開側欄」才展開。
const collapsedRoutes: AppRoute["name"][] = ["knowledge-map", "card-set", "card-review", "podcast"];

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
  const collapsible = collapsedRoutes.includes(route.name);
  const [railOpen, setRailOpen] = useState(false);
  // 收合時滑鼠在側欄上真的移動才疊出完整側欄；不用 :hover，因為版面變動時瀏覽器會對靜止的游標補送 hover，
  // 游標剛好停在左緣就會無故蓋住內容。
  const [railPeek, setRailPeek] = useState(false);
  useEffect(() => { setRailOpen(false); setRailPeek(false); }, [route.name]);
  const peek = collapsible ? {
    onPointerMove: (event: React.PointerEvent) => { if (event.pointerType === "mouse" && (event.movementX || event.movementY)) setRailPeek(true); },
    onPointerLeave: () => setRailPeek(false),
  } : {};
  const section = currentSection(route);
  const routeKey = JSON.stringify(route);
  const forgot = useForgotCards(apiClient, routeKey, materialId);
  const current = (id: Section) => section === id ? "page" as const : undefined;
  const count = (id: Section) => id === "cards" && forgot ? <>
    <small className="shell-count" aria-hidden="true">{forgot}</small>
    <span className="shell-sr-only">，{forgot} 張不熟</span>
  </> : null;
  const toggle = collapsible ? <button className="rail-toggle" type="button" aria-expanded={railOpen} onClick={() => setRailOpen((open) => !open)}>
    <Icon name={railOpen ? "chevron-left" : "chevron-right"} size={20} /><span className="rail-label">{railOpen ? "收合側欄" : "展開側欄"}</span>
  </button> : null;
  const classes = ["app-shell", isLearningWorkspace ? "is-workspace" : "is-standard", materialId && "is-material", wideRoutes.includes(route.name) && "is-wide",
    route.name === "home" && "is-home", collapsible && !railOpen && "is-rail-collapsed", railPeek && "is-rail-peek", focusMode && "is-focus"];
  return (
    <MaterialRunWorkspaceContext.Provider value={setMaterialRunId}>
    <FocusModeContext.Provider value={setFocusMode}>
    <div className={classes.filter(Boolean).join(" ")}>
      {focusMode ? null : apiClient && materialId ? <MaterialRail apiClient={apiClient} route={route} materialId={materialId} forgot={forgot} toggle={toggle} peek={peek} /> : <aside className="rail" {...peek}>
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
      {apiClient && learnerId && <Celebrations apiClient={apiClient} learnerId={learnerId} />}
    </div>
    </FocusModeContext.Provider>
    </MaterialRunWorkspaceContext.Provider>
  );
}
