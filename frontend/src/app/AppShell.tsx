import { createContext, useLayoutEffect, useRef, useState } from "react";
import { writeRoute, type AppRoute } from "./routes";
import { RouteLink } from "../ui/RouteLink";
import { Icon, type IconName } from "../ui/Icon";
import "./shell.css";
import "./chrome.css";

export const MaterialRunWorkspaceContext = createContext<((runId:string|null)=>void)|null>(null);

type Section = "home" | "materials" | "cards" | "podcasts" | "achievements" | "profile";
// 成就與個人檔案只放在桌機頁首；手機底部列維持四個入口加上傳（手機頁首的等級按鈕與頭像可進入這兩頁）。
const sections: { id: Section; label: string; short: string; icon: IconName; route: AppRoute; headerOnly?: boolean }[] = [
  { id: "home", label: "首頁", short: "首頁", icon: "home", route: { name: "home" } },
  { id: "materials", label: "我的教材", short: "教材", icon: "book", route: { name: "materials" } },
  { id: "cards", label: "概念卡", short: "概念卡", icon: "cards", route: { name: "concept-cards" } },
  { id: "podcasts", label: "Podcast", short: "Podcast", icon: "headphones", route: { name: "podcasts" } },
  { id: "achievements", label: "成就", short: "成就", icon: "sparkle", route: { name: "achievements" }, headerOnly: true },
  { id: "profile", label: "個人檔案", short: "個人", icon: "user", route: { name: "profile" }, headerOnly: true },
];
const tabSections = sections.filter((item) => !item.headerOnly);

// 首頁與清單類頁面在寬螢幕放寬版面（表單與閱讀頁維持標準寬度）。
const wideRoutes: AppRoute["name"][] = ["home", "materials", "concept-cards", "podcasts", "profile", "achievements"];

// 教材內的概念卡／Podcast 屬於「我的教材」；全域清單才屬於各自分區。
function currentSection(route: AppRoute): Section | null {
  if (route.name === "not-found") return null;
  if (route.name === "profile" || route.name === "achievements") return route.name;
  if (route.name === "home") return "home";
  if ("materialId" in route && route.materialId) return "materials";
  if (["concept-cards", "card-set", "card-set-edit", "card-set-new", "card-review"].includes(route.name)) return "cards";
  if (["podcasts", "podcast", "podcast-new"].includes(route.name)) return "podcasts";
  return "materials";
}

export function AppShell({
  children,
  route,
  accountAction,
  status,
}: {
  children: React.ReactNode;
  route: AppRoute;
  accountAction?: React.ReactNode;
  // 品牌旁的學習進度（連續天數／等級）；未登入時不顯示。
  status?: React.ReactNode;
}) {
  const [materialRunId,setMaterialRunId]=useState<string|null>(null);
  const isLearningWorkspace = (route.name === "material-run" && route.runId === materialRunId) || ["knowledge-map", "study-session", "material-sources", "material-content", "research", "podcast", "podcast-create", "card-set", "card-set-edit", "card-set-create", "card-review"].includes(route.name);
  // 進入單一教材後由教材頁首（返回、教材名稱、分頁、問答）取代全站頁首與手機底部導覽，內外分層。
  // 全站頁首只在教材頁首出現後才隱藏（CSS），讀取中或沒有教材頁首的畫面仍有導覽可離開。
  // 從全站「概念卡」「Podcast」直接開啟（沒有 materialId）的頁面仍屬全站。
  const isMaterialWorkspace = isLearningWorkspace && "materialId" in route && !!route.materialId && accountAction !== undefined;
  const section = currentSection(route);
  // 同一教材內切換分頁時，新頁面讀完資料才畫出教材頁首；這段空檔（最多 3 秒）也不顯示全站頁首，避免閃一下。
  // 從教材外進入時不套用，讀取中仍保留全站導覽。
  const materialId = isMaterialWorkspace && "materialId" in route ? route.materialId ?? null : null;
  const previousMaterial = useRef<string | null>(null);
  const [switching, setSwitching] = useState(false);
  useLayoutEffect(() => {
    const sameMaterial = materialId !== null && previousMaterial.current === materialId;
    previousMaterial.current = materialId;
    setSwitching(sameMaterial);
    if (!sameMaterial) return;
    const timer = window.setTimeout(() => setSwitching(false), 3000);
    return () => window.clearTimeout(timer);
  }, [route, materialId]);
  return (
    <MaterialRunWorkspaceContext.Provider value={setMaterialRunId}>
    <div className={`app-shell ${isLearningWorkspace ? "is-workspace" : "is-standard"}${isMaterialWorkspace ? " is-material" : ""}${switching ? " is-switching-tab" : ""}${wideRoutes.includes(route.name) ? " is-wide" : ""}${route.name === "home" ? " is-home" : ""}${route.name === "achievements" ? " is-achievements" : ""}`}>
      {/* 教材內不顯示頁首進度，但升級與徽章動畫仍要能出現。 */}
      {isMaterialWorkspace && <div className="material-shell-status">{status}</div>}
      <header className="app-header">
        <div className="header-start">
          <button
            aria-label="返回 Studydy 首頁"
            className="brand"
            type="button"
            onClick={() => writeRoute({ name: "home" })}
          >
            <img src="/assets/studydy/brand-idle.png" alt="" />
            <span>Studydy 一點就通</span>
          </button>
          {!isMaterialWorkspace && status}
        </div>
        <nav className="main-nav" aria-label="主要導覽">
          {sections.map((item) => (
            <RouteLink
              key={item.id}
              route={item.route}
              // 窄一點的桌機只顯示圖示，名稱另由 aria-label 提供。
              aria-label={item.label}
              aria-current={section === item.id ? "page" : undefined}
            >
              <Icon name={item.icon} size={18} />
              <span>{item.label}</span>
            </RouteLink>
          ))}
        </nav>
        <div className="account-controls">{accountAction}</div>
      </header>
      <main className="app-main" id="main-content">
        {children}
      </main>
      <nav className="tab-bar" aria-label="手機導覽">
        {tabSections.slice(0, 2).map((item) => (
          <RouteLink key={item.id} route={item.route} aria-current={section === item.id ? "page" : undefined}>
            <Icon name={item.icon} size={22} />
            <span>{item.short}</span>
          </RouteLink>
        ))}
        <button className="tab-bar-upload" type="button" aria-label="上傳教材" onClick={() => writeRoute({ name: "upload" })}>
          <Icon name="plus" size={26} />
        </button>
        {tabSections.slice(2).map((item) => (
          <RouteLink key={item.id} route={item.route} aria-current={section === item.id ? "page" : undefined}>
            <Icon name={item.icon} size={22} />
            <span>{item.short}</span>
          </RouteLink>
        ))}
      </nav>
    </div>
    </MaterialRunWorkspaceContext.Provider>
  );
}
