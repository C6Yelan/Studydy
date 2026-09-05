import type { AppRoute } from "./routes";
import { writeRoute } from "./routes";
import { Icon, type IconName } from "../ui/Icon";
import "./shell.css";

type SessionStatus = "starting" | "ready" | "failed";

function routeTitle(route: AppRoute): string {
  if (route.name === "home") return "上傳教材";
  if (route.name === "material-run") return "教材處理";
  if (route.name === "knowledge-map") return "知識地圖";
  return "本次學習";
}

type NavItem = {
  icon: IconName;
  label: string;
  active: boolean;
  open: () => void;
};

function routeNavigation(route: AppRoute): NavItem[] {
  const items: NavItem[] = [{
    icon: "upload",
    label: "上傳教材",
    active: route.name === "home",
    open: () => writeRoute({ name: "home" }),
  }];
  if (route.name !== "home") {
    items.push({
      icon: "process",
      label: "處理狀態",
      active: route.name === "material-run",
      open: () => writeRoute({ name: "material-run", materialId: route.materialId, runId: route.runId }),
    });
  }
  if (route.name === "knowledge-map" || route.name === "study-session") {
    items.push({
      icon: "map",
      label: "知識地圖",
      active: route.name === "knowledge-map",
      open: () => writeRoute({
        name: "knowledge-map",
        materialId: route.materialId,
        runId: route.runId,
        structureRevision: route.structureRevision,
      }),
    });
  }
  if (route.name === "study-session") {
    items.push({ icon: "learning", label: "本次學習", active: true, open: () => undefined });
  }
  return items;
}

export function AppShell({ children, route, sessionStatus }: {
  children: React.ReactNode;
  route: AppRoute;
  sessionStatus: SessionStatus;
}) {
  const isWorkspace = route.name === "knowledge-map" || route.name === "study-session";
  const sessionCopy = sessionStatus === "ready"
    ? "安全工作階段"
    : sessionStatus === "starting" ? "連線中" : "工作階段未連線";
  return (
    <div className={`app-shell${isWorkspace ? " is-workspace" : " is-focused"}`}>
      <header className="app-header">
        <button
          aria-label="返回 Studydy 上傳教材"
          className="brand"
          type="button"
          onClick={() => writeRoute({ name: "home" })}
        >
          <img src="/assets/studydy/brand-idle.png" alt="" />
          <span>Studydy</span>
        </button>
        <strong className="route-label">{routeTitle(route)}</strong>
        <span className={`session-mark is-${sessionStatus}`}>
          <span aria-hidden="true" />
          {sessionCopy}
        </span>
      </header>

      {isWorkspace && (
        <aside className="app-sidebar" aria-label="學習導覽區">
          <nav aria-label="主要導覽">
            {routeNavigation(route).map((item) => (
              <button
                aria-current={item.active ? "page" : undefined}
                className={item.active ? "is-active" : undefined}
                key={item.label}
                type="button"
                onClick={item.open}
              >
                <Icon name={item.icon} />
                <span>{item.label}</span>
              </button>
            ))}
          </nav>
          <div className="sidebar-helper">
            <img src="/assets/studydy/knowledge-guide.png" alt="" />
            <div>
              <strong>專注教材裡的證據</strong>
              <p>概念、關係與學習步驟都能回到來源。</p>
            </div>
          </div>
        </aside>
      )}

      <main className="app-main" id="main-content">{children}</main>
    </div>
  );
}
