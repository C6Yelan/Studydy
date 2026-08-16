export type AppRoute =
  | { name: "home" }
  | { name: "material-run"; materialId: string; runId: string }
  | {
      name: "knowledge-map";
      materialId: string;
      runId: string;
      mapRevision: string;
      pathRevision: string;
    }
  | { name: "assessment"; materialId: string; runId: string; assessmentRevision: string }
  | { name: "learning-state"; materialId: string; stateRevision: string };

export type RouteRead = { route: AppRoute; isValid: boolean };
export type AppHistoryState = { learningStateReplay?: boolean };

const uuidPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const revisionPatterns = {
  map: /^knowledge-map:sha256:[0-9a-f]{64}$/,
  path: /^initial-learning-path:sha256:[0-9a-f]{64}$/,
  assessment: /^assessment:sha256:[0-9a-f]{64}$/,
  state: /^learning-state:sha256:[0-9a-f]{64}$/,
};

function decodeSegments(pathname: string): string[] | null {
  try {
    return pathname.split("/").filter(Boolean).map(decodeURIComponent);
  } catch {
    return null;
  }
}

export function readRoute(location: Pick<Location, "pathname" | "search" | "hash">): RouteRead {
  if (location.search || location.hash) return { route: { name: "home" }, isValid: false };
  if (location.pathname === "/") return { route: { name: "home" }, isValid: true };
  const segments = decodeSegments(location.pathname);
  if (!segments) return { route: { name: "home" }, isValid: false };

  if (
    segments.length === 6
    && segments[0] === "materials"
    && segments[2] === "runs"
    && segments[4] === "assessments"
    && uuidPattern.test(segments[1])
    && uuidPattern.test(segments[3])
    && revisionPatterns.assessment.test(segments[5])
  ) {
    return {
      route: {
        name: "assessment",
        materialId: segments[1],
        runId: segments[3],
        assessmentRevision: segments[5],
      },
      isValid: true,
    };
  }

  if (
    segments.length === 4
    && segments[0] === "materials"
    && segments[2] === "runs"
    && uuidPattern.test(segments[1])
    && uuidPattern.test(segments[3])
  ) {
    return {
      route: { name: "material-run", materialId: segments[1], runId: segments[3] },
      isValid: true,
    };
  }

  if (
    segments.length === 8
    && segments[0] === "materials"
    && segments[2] === "runs"
    && segments[4] === "maps"
    && segments[6] === "paths"
    && uuidPattern.test(segments[1])
    && uuidPattern.test(segments[3])
    && revisionPatterns.map.test(segments[5])
    && revisionPatterns.path.test(segments[7])
  ) {
    return {
      route: {
        name: "knowledge-map",
        materialId: segments[1],
        runId: segments[3],
        mapRevision: segments[5],
        pathRevision: segments[7],
      },
      isValid: true,
    };
  }

  if (
    segments.length === 4
    && segments[0] === "materials"
    && segments[2] === "states"
    && uuidPattern.test(segments[1])
    && revisionPatterns.state.test(segments[3])
  ) {
    return {
      route: { name: "learning-state", materialId: segments[1], stateRevision: segments[3] },
      isValid: true,
    };
  }

  return { route: { name: "home" }, isValid: false };
}

export function routePath(route: AppRoute): string {
  if (route.name === "home") return "/";
  if (route.name === "material-run") {
    if (!uuidPattern.test(route.materialId) || !uuidPattern.test(route.runId)) return "/";
    return `/materials/${encodeURIComponent(route.materialId)}/runs/${encodeURIComponent(route.runId)}`;
  }
  if (route.name === "knowledge-map") {
    if (!uuidPattern.test(route.materialId) || !uuidPattern.test(route.runId)) return "/";
    if (!revisionPatterns.map.test(route.mapRevision)) return "/";
    if (!revisionPatterns.path.test(route.pathRevision)) return "/";
    return [
      "/materials",
      encodeURIComponent(route.materialId),
      "runs",
      encodeURIComponent(route.runId),
      "maps",
      encodeURIComponent(route.mapRevision),
      "paths",
      encodeURIComponent(route.pathRevision),
    ].join("/");
  }
  if (route.name === "assessment") {
    if (!uuidPattern.test(route.materialId) || !uuidPattern.test(route.runId)) return "/";
    if (!revisionPatterns.assessment.test(route.assessmentRevision)) return "/";
    return [
      "/materials",
      encodeURIComponent(route.materialId),
      "runs",
      encodeURIComponent(route.runId),
      "assessments",
      encodeURIComponent(route.assessmentRevision),
    ].join("/");
  }
  if (!uuidPattern.test(route.materialId) || !revisionPatterns.state.test(route.stateRevision)) return "/";
  return `/materials/${encodeURIComponent(route.materialId)}/states/${encodeURIComponent(route.stateRevision)}`;
}

export function writeRoute(route: AppRoute, replace = false, state: AppHistoryState | null = null): void {
  const method = replace ? "replaceState" : "pushState";
  window.history[method](state, "", routePath(route));
  // pushState 與 replaceState 不會自行觸發 popstate，因此主畫面需要這個通知才能立即同步網址。
  window.dispatchEvent(new PopStateEvent("popstate", { state }));
}
