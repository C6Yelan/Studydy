export type AppRoute =
  | { name: "home" }
  | { name: "material-run"; materialId: string; runId: string }
  | { name: "knowledge-map"; materialId: string; runId: string; mapRevision: string }
  | { name: "study-session"; materialId: string; runId: string; mapRevision: string; studySessionId: string };

export type RouteRead = { route: AppRoute; isCanonical: boolean };

const uuidPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const mapPattern = /^knowledge-map:sha256:[0-9a-f]{64}$/;

function validSegment(value: string): boolean {
  return value === encodeURIComponent(value) && !value.includes("%") && !value.includes("/");
}

export function readRoute(pathname: string): RouteRead {
  if (pathname === "/") return { route: { name: "home" }, isCanonical: true };
  const segments = pathname.split("/").filter(Boolean).map((part) => {
    try {
      return decodeURIComponent(part);
    } catch {
      return "";
    }
  });
  if (
    segments.length === 4
    && segments[0] === "materials"
    && uuidPattern.test(segments[1])
    && segments[2] === "runs"
    && uuidPattern.test(segments[3])
  ) {
    const route: AppRoute = { name: "material-run", materialId: segments[1], runId: segments[3] };
    return { route, isCanonical: routePath(route) === pathname };
  }
  if (
    segments.length === 8
    && segments[0] === "materials"
    && uuidPattern.test(segments[1])
    && segments[2] === "runs"
    && uuidPattern.test(segments[3])
    && segments[4] === "knowledge-maps"
    && mapPattern.test(segments[5])
    && segments[6] === "study-sessions"
    && uuidPattern.test(segments[7])
  ) {
    const route: AppRoute = {
      name: "study-session",
      materialId: segments[1],
      runId: segments[3],
      mapRevision: segments[5],
      studySessionId: segments[7],
    };
    return { route, isCanonical: routePath(route) === pathname };
  }
  if (
    segments.length === 6
    && segments[0] === "materials"
    && uuidPattern.test(segments[1])
    && segments[2] === "runs"
    && uuidPattern.test(segments[3])
    && segments[4] === "knowledge-maps"
    && mapPattern.test(segments[5])
  ) {
    const route: AppRoute = {
      name: "knowledge-map",
      materialId: segments[1],
      runId: segments[3],
      mapRevision: segments[5],
    };
    return { route, isCanonical: routePath(route) === pathname };
  }
  return { route: { name: "home" }, isCanonical: false };
}

export function routePath(route: AppRoute): string {
  if (route.name === "home") return "/";
  if (!validSegment(route.materialId) || !validSegment(route.runId)) throw new Error("ROUTE_INVALID");
  const base = `/materials/${route.materialId}/runs/${route.runId}`;
  if (route.name === "material-run") return base;
  if (!mapPattern.test(route.mapRevision)) throw new Error("ROUTE_INVALID");
  const mapPath = `${base}/knowledge-maps/${encodeURIComponent(route.mapRevision)}`;
  if (route.name === "knowledge-map") return mapPath;
  if (!validSegment(route.studySessionId) || !uuidPattern.test(route.studySessionId)) throw new Error("ROUTE_INVALID");
  return `${mapPath}/study-sessions/${route.studySessionId}`;
}

export function writeRoute(route: AppRoute, replace = false): void {
  const path = routePath(route);
  if (replace) window.history.replaceState(null, "", path);
  else window.history.pushState(null, "", path);
  window.dispatchEvent(new PopStateEvent("popstate"));
}
