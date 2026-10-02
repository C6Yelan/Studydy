export type AppRoute =
  | { name: "home" }
  | { name: "materials" }
  | { name: "upload" }
  | { name: "material-sources"; materialId: string }
  | { name: "material-run"; materialId: string; runId: string }
  | { name: "knowledge-map"; materialId: string; runId: string; structureRevision: string }
  | {
      name: "study-session";
      materialId: string;
      runId: string;
      structureRevision: string;
      studySessionId: string;
      assessmentSetId?: string;
    };

type RouteRead = { route: AppRoute; isCanonical: boolean };

const uuidPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const structurePattern = /^knowledge-structure:sha256:[0-9a-f]{64}$/;

export function readRoute(pathname: string): RouteRead {
  if (pathname === "/") return { route: { name: "home" }, isCanonical: true };
  if (pathname === "/materials") return { route: { name: "materials" }, isCanonical: true };
  if (pathname === "/upload") return { route: { name: "upload" }, isCanonical: true };
  const segments = pathname
    .split("/")
    .filter(Boolean)
    .map((part) => {
      try {
        return decodeURIComponent(part);
      } catch {
        return "";
      }
    });
  const [
    materialSegment,
    materialId,
    resourceSegment,
    runId,
    structureSegment,
    structureRevision,
    sessionSegment,
    studySessionId,
    setSegment,
    assessmentSetId,
  ] = segments;
  const fallback: RouteRead = { route: { name: "home" }, isCanonical: false };
  if (materialSegment !== "materials" || !uuidPattern.test(materialId)) return fallback;

  let route: AppRoute;
  if (segments.length === 3 && resourceSegment === "sources") {
    route = { name: "material-sources", materialId };
  } else if (resourceSegment !== "runs" || !uuidPattern.test(runId)) {
    return fallback;
  } else if (segments.length === 4) {
    route = { name: "material-run", materialId, runId };
  } else if (
    structureSegment !== "knowledge-structures" ||
    !structurePattern.test(structureRevision)
  ) {
    return fallback;
  } else if (segments.length === 6) {
    route = { name: "knowledge-map", materialId, runId, structureRevision };
  } else if (
    sessionSegment === "study-sessions" &&
    uuidPattern.test(studySessionId) &&
    (segments.length === 8 ||
      (segments.length === 10 &&
        setSegment === "assessment-sets" &&
        uuidPattern.test(assessmentSetId)))
  ) {
    route = {
      name: "study-session",
      materialId,
      runId,
      structureRevision,
      studySessionId,
      ...(segments.length === 10 ? { assessmentSetId } : {}),
    };
  } else {
    return fallback;
  }
  return { route, isCanonical: routePath(route) === pathname };
}

export function routePath(route: AppRoute): string {
  if (route.name === "home") return "/";
  if (route.name === "materials") return "/materials";
  if (route.name === "upload") return "/upload";
  if (route.name === "material-sources") {
    if (!uuidPattern.test(route.materialId)) throw new Error("ROUTE_INVALID");
    return `/materials/${route.materialId}/sources`;
  }
  if (!uuidPattern.test(route.materialId) || !uuidPattern.test(route.runId))
    throw new Error("ROUTE_INVALID");
  const materialRunPath = `/materials/${route.materialId}/runs/${route.runId}`;
  if (route.name === "material-run") return materialRunPath;
  if (!structurePattern.test(route.structureRevision)) throw new Error("ROUTE_INVALID");
  const mapPath = `${materialRunPath}/knowledge-structures/${encodeURIComponent(route.structureRevision)}`;
  if (route.name === "knowledge-map") return mapPath;
  if (!uuidPattern.test(route.studySessionId)) throw new Error("ROUTE_INVALID");
  const studyPath = `${mapPath}/study-sessions/${route.studySessionId}`;
  if (route.assessmentSetId !== undefined) {
    if (!uuidPattern.test(route.assessmentSetId)) throw new Error("ROUTE_INVALID");
    return `${studyPath}/assessment-sets/${route.assessmentSetId}`;
  }
  return studyPath;
}

export function writeRoute(route: AppRoute, replace = false): void {
  const path = routePath(route);
  if (replace) window.history.replaceState(null, "", path);
  else window.history.pushState(null, "", path);
  window.dispatchEvent(new PopStateEvent("popstate"));
}
