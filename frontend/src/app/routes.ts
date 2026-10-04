export type AppRoute =
  | { name: "home" }
  | { name: "topics" }
  | { name: "topic"; topicId: string }
  | { name: "materials" }
  | { name: "material-content"; materialId: string; kind: "concept-cards" | "podcasts" | "research" }
  | { name: "podcast-new" }
  | { name: "card-set-new" }
  | { name: "podcasts" }
  | { name: "podcast"; podcastId: string; materialId?: string }
  | { name: "podcast-create"; materialId: string; runId: string; structureRevision: string }
  | { name: "concept-cards" }
  | { name: "card-set"; cardSetId: string; materialId?: string }
  | { name: "card-set-edit"; cardSetId: string; materialId?: string }
  | { name: "card-set-create"; materialId: string; runId: string; structureRevision: string }
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
  if (pathname === "/topics") return { route: { name: "topics" }, isCanonical: true };
  if (pathname === "/") return { route: { name: "home" }, isCanonical: true };
  if (pathname === "/materials") return { route: { name: "materials" }, isCanonical: true };
  if (pathname === "/podcasts/new") return { route: { name: "podcast-new" }, isCanonical: true };
  if (pathname === "/concept-cards/new") return { route: { name: "card-set-new" }, isCanonical: true };
  if (pathname === "/podcasts") return { route: { name: "podcasts" }, isCanonical: true };
  if (pathname === "/concept-cards") return { route: { name: "concept-cards" }, isCanonical: true };
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
  if (materialSegment === "topics" && uuidPattern.test(materialId) && segments.length === 2) {
    const route: AppRoute = { name: "topic", topicId: materialId };
    return { route, isCanonical: routePath(route) === pathname };
  }
  if (materialSegment === "podcasts" && uuidPattern.test(materialId) && segments.length === 2) {
    const route: AppRoute = { name: "podcast", podcastId: materialId };
    return { route, isCanonical: routePath(route) === pathname };
  }
  if (materialSegment === "concept-cards" && uuidPattern.test(materialId)
    && (segments.length === 2 || (segments.length === 3 && resourceSegment === "edit"))) {
    const route: AppRoute = { name: segments.length === 3 ? "card-set-edit" : "card-set", cardSetId: materialId };
    return { route, isCanonical: routePath(route) === pathname };
  }
  if (materialSegment !== "materials" || !uuidPattern.test(materialId)) return fallback;

  let route: AppRoute;
  if ((resourceSegment === "concept-cards" || resourceSegment === "podcasts") && uuidPattern.test(runId)
    && (segments.length === 4 || (resourceSegment === "concept-cards" && segments.length === 5 && structureSegment === "edit"))) {
    route = resourceSegment === "podcasts" ? { name: "podcast", podcastId: runId, materialId }
      : { name: segments.length === 5 ? "card-set-edit" : "card-set", cardSetId: runId, materialId };
  } else if (segments.length === 3 && (resourceSegment === "concept-cards" || resourceSegment === "podcasts" || resourceSegment === "research")) {
    route = { name: "material-content", materialId, kind: resourceSegment };
  } else if (segments.length === 3 && resourceSegment === "sources") {
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
  } else if (segments.length === 7 && sessionSegment === "create-podcast") {
    route = { name: "podcast-create", materialId, runId, structureRevision };
  } else if (segments.length === 7 && sessionSegment === "create-cards") {
    route = { name: "card-set-create", materialId, runId, structureRevision };
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
  if (route.name === "topics") return "/topics";
  if (route.name === "topic") { if (!uuidPattern.test(route.topicId)) throw new Error("ROUTE_INVALID"); return `/topics/${route.topicId}`; }
  if (route.name === "materials") return "/materials";
  if (route.name === "podcast-new") return "/podcasts/new";
  if (route.name === "card-set-new") return "/concept-cards/new";
  if (route.name === "podcasts") return "/podcasts";
  if (route.name === "podcast") {
    if (!uuidPattern.test(route.podcastId)) throw new Error("ROUTE_INVALID");
    if (route.materialId && !uuidPattern.test(route.materialId)) throw new Error("ROUTE_INVALID");
    return `${route.materialId ? `/materials/${route.materialId}` : ""}/podcasts/${route.podcastId}`;
  }
  if (route.name === "concept-cards") return "/concept-cards";
  if (route.name === "card-set" || route.name === "card-set-edit") {
    if (!uuidPattern.test(route.cardSetId)) throw new Error("ROUTE_INVALID");
    if (route.materialId && !uuidPattern.test(route.materialId)) throw new Error("ROUTE_INVALID");
    return `${route.materialId ? `/materials/${route.materialId}` : ""}/concept-cards/${route.cardSetId}${route.name === "card-set-edit" ? "/edit" : ""}`;
  }
  if (route.name === "upload") return "/upload";
  if (route.name === "material-content") {
    if (!uuidPattern.test(route.materialId)) throw new Error("ROUTE_INVALID");
    return `/materials/${route.materialId}/${route.kind}`;
  }
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
  if (route.name === "podcast-create") return `${mapPath}/create-podcast`;
  if (route.name === "card-set-create") return `${mapPath}/create-cards`;
  if (!uuidPattern.test(route.studySessionId)) throw new Error("ROUTE_INVALID");
  const studyPath = `${mapPath}/study-sessions/${route.studySessionId}`;
  if (route.assessmentSetId !== undefined) {
    if (!uuidPattern.test(route.assessmentSetId)) throw new Error("ROUTE_INVALID");
    return `${studyPath}/assessment-sets/${route.assessmentSetId}`;
  }
  return studyPath;
}

export function writeRoute(route: AppRoute, replace = false, state: object | null = null): void {
  const path = routePath(route);
  if (replace) window.history.replaceState(state, "", path);
  else window.history.pushState(state, "", path);
  window.dispatchEvent(new PopStateEvent("popstate"));
}
