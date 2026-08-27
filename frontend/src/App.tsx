import { useCallback, useEffect, useState } from "react";

import { ApiClientError, StudydyApiClient } from "./api/client";
import { AppShell } from "./app/AppShell";
import { readRoute, writeRoute, type AppRoute } from "./app/routes";
import { MaterialFlow } from "./features/material-flow/MaterialFlow";
import { StateView } from "./ui/StateView";

const apiClient = new StudydyApiClient();

type SessionState =
  | { status: "starting" }
  | { status: "ready" }
  | { status: "failed"; message: string; canRetry: boolean };

function initialRoute(): AppRoute {
  return readRoute(window.location.pathname).route;
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
    <AppShell route={route} sessionStatus={session.status}>
        {session.status === "starting" && (
          <StateView
            description="正在連線至 Studydy，請稍候。"
            live
            title="正在建立安全工作階段"
            tone="loading"
          />
        )}
        {session.status === "ready" && (
          <MaterialFlow apiClient={apiClient} route={route} />
        )}
        {session.status === "failed" && (
          <StateView
            action={session.canRetry && <button className="primary-button" type="button" onClick={startSession}>再試一次</button>}
            description={session.message}
            image="/assets/studydy/failure-confused.png"
            title="暫時無法開始"
            tone="failure"
          />
        )}
    </AppShell>
  );
}
