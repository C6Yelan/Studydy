import { writeRoute, type AppRoute } from "./routes";
import { Icon } from "../ui/Icon";
import "./shell.css";

export function AppShell({
  children,
  route,
  accountAction,
}: {
  children: React.ReactNode;
  route: AppRoute;
  accountAction?: React.ReactNode;
}) {
  const isLearningWorkspace = route.name === "knowledge-map" || route.name === "study-session";
  const isMaterialRoute = ["materials", "material-run", "material-sources", "upload"].includes(
    route.name,
  );
  return (
    <div className={`app-shell ${isLearningWorkspace ? "is-workspace" : "is-standard"}`}>
      <header className="app-header">
        <button
          aria-label="返回 Studydy 首頁"
          className="brand"
          type="button"
          onClick={() => writeRoute({ name: "home" })}
        >
          <img src="/assets/studydy/brand-idle.png" alt="" />
          <span>Studydy{!isLearningWorkspace && <small>AI 智慧學習平台</small>}</span>
        </button>
        {isLearningWorkspace && (
          <nav className="workspace-nav" aria-label="學習工作區導覽">
            {route.name === "study-session" && (
              <button
                type="button"
                onClick={() =>
                  writeRoute({
                    name: "knowledge-map",
                    materialId: route.materialId,
                    runId: route.runId,
                    structureRevision: route.structureRevision,
                  })
                }
              >
                <Icon name="map" size={18} />
                知識地圖
              </button>
            )}
            <button type="button" onClick={() => writeRoute({ name: "materials" })}>
              <Icon name="book" size={18} />
              我的教材
            </button>
          </nav>
        )}
        <div className="account-controls">{accountAction}</div>
      </header>
      {!isLearningWorkspace && (
        <aside className="app-sidebar" aria-label="學習導覽區">
          <nav aria-label="主要導覽">
            <button
              aria-current={route.name === "home" ? "page" : undefined}
              type="button"
              onClick={() => writeRoute({ name: "home" })}
            >
              <Icon name="home" />
              首頁
            </button>
            <button
              aria-current={isMaterialRoute ? "page" : undefined}
              aria-label="教材庫"
              type="button"
              onClick={() => writeRoute({ name: "materials" })}
            >
              <Icon name="book" />
              我的教材
            </button>
          </nav>
        </aside>
      )}
      <main className="app-main" id="main-content">
        {children}
      </main>
    </div>
  );
}
