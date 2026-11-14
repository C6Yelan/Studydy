import { createContext, useEffect, useState } from "react";
import type { StudydyApiClient } from "../api/client";
import { type AppRoute } from "./routes";
import { Celebrations } from "./LearningProgress";
import { SiteSearch } from "./SiteSearch";
import { RouteLink } from "../ui/RouteLink";
import { Icon, type IconName } from "../ui/Icon";
import { SourcePanelHost } from "../ui/SourcePanel";
import "./shell.css";
import "./chrome.css";

export const MaterialRunWorkspaceContext = createContext<((runId:string|null)=>void)|null>(null);

type Section = "home" | "materials" | "cards" | "podcasts" | "achievements" | "upload" | "profile";
// 全站左側欄的主要入口；手機上同一組入口移到底部頁籤（short 是頁籤上的字）。
const sections: { id: Section; label: string; short: string; icon: IconName; tone: string; route: AppRoute }[] = [
  { id: "home", label: "首頁", short: "首頁", icon: "home", tone: "is-blue", route: { name: "home" } },
  { id: "materials", label: "我的群島", short: "群島", icon: "book", tone: "is-green", route: { name: "materials" } },
  { id: "cards", label: "翻卡片", short: "翻卡片", icon: "cards", tone: "is-violet", route: { name: "concept-cards" } },
  { id: "podcasts", label: "Podcast", short: "Podcast", icon: "headphones", tone: "is-amber", route: { name: "podcasts" } },
  { id: "achievements", label: "成就", short: "成就", icon: "trophy", tone: "is-amber", route: { name: "achievements" } },
];

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
function useForgotCards(apiClient: StudydyApiClient | undefined, routeKey: string) {
  const [count, setCount] = useState<number | null>(null);
  useEffect(() => {
    if (!apiClient) return;
    let cancelled = false;
    const load = () => void apiClient.getCardReviews().then(
      (value) => { if (!cancelled) setCount(value.forgot_count); },
      () => { if (!cancelled) setCount(null); },
    );
    load();
    window.addEventListener("focus", load);
    return () => { cancelled = true; window.removeEventListener("focus", load); };
  }, [apiClient, routeKey]);
  return count;
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
  const isLearningWorkspace = (route.name === "material-run" && route.runId === materialRunId) || (route.name === "topic" && !!route.materialId) || ["knowledge-map", "study-session", "material-sources", "material-source-upload", "material-content", "research", "podcast", "podcast-create", "card-set", "card-set-edit", "card-set-create", "card-review"].includes(route.name);
  // 進入單一教材後由教材頁首（返回、教材名稱、分頁、問答）取代全站側欄與手機底部頁籤；CSS 在教材頁首出現後才隱藏，
  // 讀取中或沒有教材頁首的畫面仍有導覽可離開。從全站「翻卡片」「Podcast」直接開啟（沒有 materialId）的頁面仍屬全站。
  const isMaterialWorkspace = isLearningWorkspace && "materialId" in route && !!route.materialId && apiClient !== undefined;
  const section = currentSection(route);
  const routeKey = JSON.stringify(route);
  const forgot = useForgotCards(apiClient, routeKey);
  const current = (id: Section) => section === id ? "page" as const : undefined;
  const count = (id: Section) => id === "cards" && forgot ? <>
    <small className="shell-count" aria-hidden="true">{forgot}</small>
    <span className="shell-sr-only">，{forgot} 張不熟</span>
  </> : null;
  return (
    <MaterialRunWorkspaceContext.Provider value={setMaterialRunId}>
    <div className={`app-shell ${isLearningWorkspace ? "is-workspace" : "is-standard"}${isMaterialWorkspace ? " is-material" : ""}${wideRoutes.includes(route.name) ? " is-wide" : ""}${route.name === "home" ? " is-home" : ""}${route.name === "achievements" ? " is-achievements" : ""}`}>
      <aside className="rail">
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
      </aside>
      <main className="app-main" id="main-content">
        {children}
      </main>
      <SourcePanelHost routeKey={routeKey} />
      {/* 手機：側欄的主要入口改成底部頁籤；搜尋、新增教材與個人檔案留在頂端那一列。 */}
      <nav className="phone-nav" aria-label="手機導覽">
        {sections.map((item) => (
          <RouteLink key={item.id} route={item.route} aria-current={current(item.id)}>
            <Icon name={item.icon} size={22} />
            <span>{item.short}</span>
            {count(item.id)}
          </RouteLink>
        ))}
      </nav>
      {apiClient && learnerId && <Celebrations apiClient={apiClient} learnerId={learnerId} />}
    </div>
    </MaterialRunWorkspaceContext.Provider>
  );
}
