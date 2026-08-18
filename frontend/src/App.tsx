import { useCallback, useEffect, useState } from "react";

import { ApiClientError, StudydyApiClient } from "./api/client";
import { readRoute, writeRoute, type AppRoute } from "./app/routes";
import { MaterialFlow } from "./features/material-flow/MaterialFlow";

const apiClient = new StudydyApiClient();

type SessionState =
  | { status: "starting" }
  | { status: "ready" }
  | { status: "failed"; message: string; canRetry: boolean };

function initialRoute(): AppRoute {
  return readRoute(window.location.pathname).route;
}

function routeLabel(route: AppRoute): string {
  if (route.name === "home") return "教材上傳";
  if (route.name === "material-run") return "教材處理狀態";
  if (route.name === "knowledge-map") return "知識地圖";
  return "概念與 Evidence 複核";
}

export default function App() {
  const [route, setRoute] = useState<AppRoute>(initialRoute);
  const [session, setSession] = useState<SessionState>({ status: "starting" });

  const startSession = useCallback(() => {
    setSession({ status: "starting" });
    void apiClient.ensureSession().then(
      () => setSession({ status: "ready" }),
      (error: unknown) => {
        const knownError = error instanceof ApiClientError ? error : null;
        setSession({
          status: "failed",
          message: knownError?.message ?? "目前無法建立安全工作階段。",
          canRetry: knownError?.retryable ?? true,
        });
      },
    );
  }, []);

  useEffect(startSession, [startSession]);

  useEffect(() => {
    const readLocation = () => {
      const next = readRoute(window.location.pathname);
      if (!next.isCanonical) writeRoute({ name: "home" }, true);
      setRoute(next.route);
    };
    readLocation();
    window.addEventListener("popstate", readLocation);
    return () => window.removeEventListener("popstate", readLocation);
  }, []);

  return (
    <div className="app-shell">
      <header className="app-header">
        <button className="brand" type="button" onClick={() => writeRoute({ name: "home" })} aria-label="返回 Studydy 教材上傳">
          <span className="brand-mark" aria-hidden="true">S</span>
          <span>Studydy</span>
        </button>
        <span className="route-label">{routeLabel(route)}</span>
        <span className={`session-mark is-${session.status}`}>
          <span aria-hidden="true" />
          {session.status === "ready" && "安全工作階段"}
          {session.status === "starting" && "建立工作階段中"}
          {session.status === "failed" && "工作階段未連線"}
        </span>
      </header>
      <main className={`app-main${route.name === "knowledge-map" ? " study-main" : ""}`}>
        {session.status === "starting" && (
          <section className="state-page" aria-live="polite">
            <div className="loading-ring" />
            <h1>正在建立安全工作階段</h1>
            <p>正在連線至 Studydy，請稍候。</p>
          </section>
        )}
        {session.status === "ready" && (
          <MaterialFlow apiClient={apiClient} route={route} />
        )}
        {session.status === "failed" && (
          <section className="state-page failure-page" role="alert">
            <img className="state-illustration" src="/assets/studydy/failure-confused.png" alt="" />
            <h1>暫時無法開始</h1>
            <p>{session.message}</p>
            {session.canRetry && <button className="primary-button" type="button" onClick={startSession}>再試一次</button>}
          </section>
        )}
      </main>
    </div>
  );
}
