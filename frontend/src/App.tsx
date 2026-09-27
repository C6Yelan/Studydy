import { useCallback, useEffect, useRef, useState } from "react";

import { ApiClientError, errorMessage, StudydyApiClient } from "./api/client";
import type { LearnerIdentity } from "./api/contracts";
import { readSessionHint, saveSessionHint } from "./api/session-hint";
import { AppShell } from "./app/AppShell";
import { readRoute, writeRoute, type AppRoute } from "./app/routes";
import { MaterialFlow } from "./features/material-flow/MaterialFlow";
import { StateView } from "./ui/StateView";
import { AccountPage } from "./features/account/AccountPage";

type SessionState =
  | { status: "starting" }
  | { status: "signed-out" }
  | { status: "ready"; identity: LearnerIdentity; api: StudydyApiClient }
  | { status: "failed"; message: string; logoutPending: boolean };

export default function App() {
  const [route, setRoute] = useState<AppRoute>(() => readRoute(window.location.pathname).route);
  const [session, setSession] = useState<SessionState>({ status: "starting" });
  const currentClient = useRef<StudydyApiClient | null>(null);
  const clientVersion = useRef(0);
  const channel = useRef<BroadcastChannel | null>(null);

  const clearPrivateView = useCallback((forgetHint = true) => {
    if (forgetHint) saveSessionHint(null);
    currentClient.current?.invalidate();
    currentClient.current = null;
    setSession({ status: "signed-out" });
    if (!["/login", "/register"].includes(window.location.pathname)) {
      window.history.replaceState(null, "", "/login");
      window.dispatchEvent(new PopStateEvent("popstate"));
    }
  }, []);

  const newClient = useCallback(() => {
    currentClient.current?.invalidate();
    const api = new StudydyApiClient();
    clientVersion.current += 1;
    currentClient.current = api;
    api.onSessionExpired = () => {
      if (currentClient.current === api) clearPrivateView();
    };
    return api;
  }, [clearPrivateView]);

  const startSession = useCallback(() => {
    setSession({ status: "starting" });
    const api = newClient();
    const remembered = readSessionHint();
    if (remembered) {
      if (["/login", "/register"].includes(window.location.pathname))
        writeRoute({ name: "home" }, true);
      setSession({ status: "ready", identity: remembered, api });
      return;
    }
    void api.ensureSession().then(
      (identity) => {
        if (currentClient.current === api) {
          if (["/login", "/register"].includes(window.location.pathname))
            writeRoute({ name: "home" }, true);
          saveSessionHint(identity);
          setSession({ status: "ready", identity, api });
        }
      },
      (error) => {
        if (currentClient.current !== api) return;
        if (error instanceof ApiClientError && error.reasonCode === "SESSION_REQUIRED")
          clearPrivateView();
        else setSession({ status: "failed", message: errorMessage(error), logoutPending: false });
      },
    );
  }, [newClient, clearPrivateView]);

  const logout = async () => {
    clearPrivateView();
    setSession({ status: "starting" });
    channel.current?.postMessage("identity-changed");
    const api = newClient();
    try {
      await api.logout();
      if (currentClient.current !== api) return;
      setSession({ status: "signed-out" });
    } catch (error) {
      if (currentClient.current !== api) return;
      setSession({
        status: "failed",
        message: `登出尚未完成。${errorMessage(error)}`,
        logoutPending: true,
      });
    }
  };

  useEffect(() => {
    startSession();
    channel.current = new BroadcastChannel("studydy-account");
    channel.current.onmessage = () => clearPrivateView(false);
    const restorePage = (event: PageTransitionEvent) => {
      if (event.persisted) startSession();
    };
    window.addEventListener("pageshow", restorePage);
    return () => {
      currentClient.current?.invalidate();
      channel.current?.close();
      window.removeEventListener("pageshow", restorePage);
    };
  }, [startSession, clearPrivateView]);

  useEffect(() => {
    const readLocation = () => {
      if (["/login", "/register"].includes(window.location.pathname)) {
        setRoute({ name: "home" });
        return;
      }
      const next = readRoute(window.location.pathname);
      if (!next.isCanonical) writeRoute({ name: "home" }, true);
      setRoute(next.isCanonical ? next.route : { name: "home" });
    };
    readLocation();
    window.addEventListener("popstate", readLocation);
    return () => window.removeEventListener("popstate", readLocation);
  }, []);

  if (session.status !== "ready") {
    const mode = window.location.pathname === "/register" ? "register" : "login";
    if (
      ["/login", "/register"].includes(window.location.pathname) ||
      session.status === "signed-out"
    )
      return (
        <AccountPage
          key={mode}
          mode={mode}
          sessionNotice={
            session.status === "failed" && session.logoutPending ? (
              <div role="alert">
                <p>{session.message}</p>
                <button type="button" onClick={() => void logout()}>
                  再試一次
                </button>
              </div>
            ) : undefined
          }
          authenticate={async (action, email, password) => {
            const api = newClient();
            let identity: LearnerIdentity;
            try {
              identity = await api.authenticate(action, email, password);
            } catch (error) {
              if (currentClient.current !== api) return;
              throw error;
            }
            if (currentClient.current !== api) return;
            writeRoute({ name: "home" }, true);
            channel.current?.postMessage("identity-changed");
            saveSessionHint(identity);
            setSession({ status: "ready", identity, api });
          }}
        />
      );
    return (
      <AppShell route={route}>
        {session.status === "starting" ? (
          <p className="app-loading" role="status">
            正在載入…
          </p>
        ) : (
          <StateView
            action={
              <button
                className="primary-button"
                type="button"
                onClick={() => (session.logoutPending ? void logout() : startSession())}
              >
                再試一次
              </button>
            }
            description={session.message}
            title="暫時無法完成"
            tone="failure"
          />
        )}
      </AppShell>
    );
  }
  return (
    <AppShell
      route={route}
      accountAction={
        <button className="secondary-button" type="button" onClick={() => void logout()}>
          登出
        </button>
      }
    >
      <MaterialFlow
        key={`${session.identity.learner_id}/${clientVersion.current}`}
        apiClient={session.api}
        route={route}
      />
    </AppShell>
  );
}
