import { writeRoute, type AppRoute } from "./routes";
import { RouteLink } from "../ui/RouteLink";
import { Icon, type IconName } from "../ui/Icon";
import "./shell.css";

type Section = "home" | "materials" | "cards" | "podcasts";
const sections: { id: Section; label: string; short: string; icon: IconName; route: AppRoute }[] = [
  { id: "home", label: "首頁", short: "首頁", icon: "home", route: { name: "home" } },
  { id: "materials", label: "我的教材", short: "教材", icon: "book", route: { name: "materials" } },
  { id: "cards", label: "概念卡", short: "概念卡", icon: "cards", route: { name: "concept-cards" } },
  { id: "podcasts", label: "Podcast", short: "Podcast", icon: "headphones", route: { name: "podcasts" } },
];

// 首頁與清單類頁面在寬螢幕放寬版面（表單與閱讀頁維持標準寬度）。
const wideRoutes: AppRoute["name"][] = ["home", "materials", "concept-cards", "podcasts", "profile", "achievements"];

// 教材內的概念卡／Podcast 屬於「我的教材」；全域清單才屬於各自分區。
function currentSection(route: AppRoute): Section | null {
  if (route.name === "not-found" || route.name === "profile" || route.name === "achievements") return null;
  if (route.name === "home") return "home";
  if ("materialId" in route && route.materialId) return "materials";
  if (["concept-cards", "card-set", "card-set-edit", "card-set-new"].includes(route.name)) return "cards";
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
  const isLearningWorkspace = ["knowledge-map", "study-session", "material-sources", "material-content", "research", "podcast", "podcast-create", "card-set", "card-set-edit", "card-set-create"].includes(route.name);
  const section = currentSection(route);
  return (
    <div className={`app-shell ${isLearningWorkspace ? "is-workspace" : "is-standard"}${wideRoutes.includes(route.name) ? " is-wide" : ""}${route.name === "home" ? " is-home" : ""}`}>
      <header className="app-header">
        <div className="header-start">
          <button
            aria-label="返回 Studydy 首頁"
            className="brand"
            type="button"
            onClick={() => writeRoute({ name: "home" })}
          >
            <img src="/assets/studydy/brand-idle.png" alt="" />
            <span>Studydy</span>
          </button>
          {status}
        </div>
        <nav className="main-nav" aria-label="主要導覽">
          {sections.map((item) => (
            <RouteLink
              key={item.id}
              route={item.route}
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
        {sections.slice(0, 2).map((item) => (
          <RouteLink key={item.id} route={item.route} aria-current={section === item.id ? "page" : undefined}>
            <Icon name={item.icon} size={22} />
            <span>{item.short}</span>
          </RouteLink>
        ))}
        <button className="tab-bar-upload" type="button" aria-label="上傳教材" onClick={() => writeRoute({ name: "upload" })}>
          <Icon name="plus" size={26} />
        </button>
        {sections.slice(2).map((item) => (
          <RouteLink key={item.id} route={item.route} aria-current={section === item.id ? "page" : undefined}>
            <Icon name={item.icon} size={22} />
            <span>{item.short}</span>
          </RouteLink>
        ))}
      </nav>
    </div>
  );
}
