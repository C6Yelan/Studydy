import type { AppRoute } from "./routes";
import { writeRoute } from "./routes";
import { Icon } from "../ui/Icon";
import "./shell.css";

export function AppShell({ children, route, accountAction }: {
  children: React.ReactNode;
  route: AppRoute;
  accountAction?: React.ReactNode;
}) {
  const workspace = route.name === "knowledge-map" || route.name === "study-session";
  const mapWorkspace = route.name === "knowledge-map";
  const materials = ["materials", "material-detail", "material-run", "upload"].includes(route.name);
  return <div className={`app-shell${workspace ? " is-workspace" : " is-standard"}${mapWorkspace ? " is-map-workspace" : ""}`}>
    <header className="app-header">
      <button aria-label="返回 Studydy 首頁" className="brand" type="button" onClick={() => writeRoute({ name: "home" })}>
        <img src="/assets/studydy/brand-idle.png" alt="" /><span>Studydy{!mapWorkspace && <small>AI 智慧學習平台</small>}</span>
      </button>
      {mapWorkspace && <nav className="map-workspace-nav" aria-label="主要導覽">
        <button type="button" onClick={() => writeRoute({ name: "materials" })}><Icon name="book" size={18} />教材庫</button>
        <button type="button" onClick={() => writeRoute({ name: "material-run", materialId: route.materialId, runId: route.runId })}><Icon name="process" size={18} />處理狀態</button>
      </nav>}
      <div className="account-controls"><span className="account-avatar" aria-hidden="true"><Icon name="user" size={22} /></span>{accountAction}</div>
    </header>
    {!mapWorkspace && <aside className="app-sidebar" aria-label="學習導覽區">
      <nav aria-label="主要導覽">
        <button aria-current={route.name === "home" ? "page" : undefined} type="button" onClick={() => writeRoute({ name: "home" })}><Icon name="home" />首頁</button>
        <button aria-current={workspace || route.name === "maps" ? "page" : undefined} type="button" onClick={() => writeRoute({ name: "maps" })}><Icon name="map" />知識地圖</button>
        <button aria-current={materials ? "page" : undefined} aria-label="教材庫" type="button" onClick={() => writeRoute({ name: "materials" })}><Icon name="book" />我的教材</button>
        <button type="button" disabled title="帳號設定功能尚未提供"><Icon name="settings" />設定<span className="nav-unavailable">尚未提供</span></button>
      </nav>
      {!["home", "maps", "materials", "upload", "material-run", "study-session"].includes(route.name) && <div className="sidebar-helper">
        <img src="/assets/studydy/knowledge-guide.png" alt="Studydy 學習夥伴" />
        <div><strong>需要開始學習的協助嗎？</strong><p>從上傳第一份 PDF 開始，建立你的知識地圖。</p>
          <button className="text-button" type="button" onClick={() => writeRoute({ name: "upload" })}><Icon name="upload" size={16} />上傳第一份教材</button>
        </div>
      </div>}
    </aside>}
    <main className="app-main" id="main-content">{children}</main>
  </div>;
}
