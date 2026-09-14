import type { AppRoute } from "./routes";
import { writeRoute } from "./routes";
import { Icon } from "../ui/Icon";
import "./shell.css";

export function AppShell({ children, route, accountAction }: {
  children: React.ReactNode;
  route: AppRoute;
  accountAction?: React.ReactNode;
}) {
  const learningWorkspace = route.name === "knowledge-map" || route.name === "study-session";
  const materials = ["materials", "material-run", "upload"].includes(route.name);
  return <div className={`app-shell${learningWorkspace ? " is-workspace" : " is-standard"}`}>
    <header className="app-header">
      <button aria-label="返回 Studydy 首頁" className="brand" type="button" onClick={() => writeRoute({ name: "home" })}>
        <img src="/assets/studydy/brand-idle.png" alt="" /><span>Studydy{!learningWorkspace && <small>AI 智慧學習平台</small>}</span>
      </button>
      {learningWorkspace && <nav className="workspace-nav" aria-label="學習工作區導覽">
        <button aria-current={route.name === "knowledge-map" ? "page" : undefined} type="button" onClick={() => writeRoute({ name: "knowledge-map", materialId: route.materialId, runId: route.runId, structureRevision: route.structureRevision })}><Icon name="map" size={18} />知識地圖</button>
        <button type="button" onClick={() => writeRoute({ name: "materials" })}><Icon name="book" size={18} />教材庫</button>
        <button type="button" onClick={() => writeRoute({ name: "material-run", materialId: route.materialId, runId: route.runId })}><Icon name="process" size={18} />處理狀態</button>
      </nav>}
      <div className="account-controls">{accountAction}</div>
    </header>
    {!learningWorkspace && <aside className="app-sidebar" aria-label="學習導覽區">
      <nav aria-label="主要導覽">
        <button aria-current={route.name === "home" ? "page" : undefined} type="button" onClick={() => writeRoute({ name: "home" })}><Icon name="home" />首頁</button>
        <button aria-current={materials ? "page" : undefined} aria-label="教材庫" type="button" onClick={() => writeRoute({ name: "materials" })}><Icon name="book" />我的教材</button>
      </nav>
    </aside>}
    <main className="app-main" id="main-content">{children}</main>
  </div>;
}
