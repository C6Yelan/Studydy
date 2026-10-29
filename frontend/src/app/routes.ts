export type AppRoute =
  | { name: "home" }
  /** 網址對不到任何頁面；保留原網址，顯示「找不到」頁。 */
  | { name: "not-found"; path: string }
  /** 只知道教材、還不知道要開哪一頁（例如 /materials/{id}）；讀取教材後導向知識地圖或來源頁。 */
  | { name: "material"; materialId: string }
  | { name: "topics" }
  | { name: "profile" }
  | { name: "achievements" }
  | { name: "topic"; topicId: string; materialId?: string }
  | { name: "materials" }
  | { name: "material-content"; materialId: string; kind: "concept-cards" | "podcasts" | "research" | "notes" }
  | { name: "research"; materialId: string; researchId: string }
  | { name: "podcast-new" }
  | { name: "card-set-new" }
  | { name: "podcasts" }
  | { name: "podcast"; podcastId: string; materialId?: string }
  | { name: "podcast-create"; materialId: string; runId: string; structureRevision: string }
  | { name: "concept-cards" }
  | { name: "card-set"; cardSetId: string; materialId?: string }
  | { name: "card-set-edit"; cardSetId: string; materialId?: string }
  | { name: "card-set-create"; materialId: string; runId: string; structureRevision: string }
  /** 需要加強：逐張看標成不熟的概念卡；帶 cardSetId 時只看該卡組，只帶 materialId 時只看這份教材的卡組。 */
  | { name: "card-review"; cardSetId?: string; materialId?: string }
  | { name: "upload" }
  | { name: "material-sources"; materialId: string }
  | { name: "material-source-upload"; materialId: string }
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

// 常見的舊網址或簡寫：導向正確頁面，不顯示「找不到」。
const aliases: Record<string, AppRoute> = {
  "/home": { name: "home" },
  "/dashboard": { name: "home" },
  "/cards": { name: "concept-cards" },
  "/card-sets": { name: "concept-cards" },
  "/podcast": { name: "podcasts" },
  "/library": { name: "materials" },
  "/materials/new": { name: "upload" },
  "/account": { name: "profile" },
  "/settings": { name: "profile" },
};

export function readRoute(pathname: string): RouteRead {
  // 結尾多餘的斜線視為同一頁，改寫成正式網址。
  const trimmed = pathname.length > 1 ? pathname.replace(/\/+$/, "") || "/" : pathname;
  if (trimmed !== pathname) return { route: readRoute(trimmed).route, isCanonical: false };
  const alias = aliases[pathname];
  if (alias) return { route: alias, isCanonical: false };
  if (pathname === "/topics") return { route: { name: "topics" }, isCanonical: true };
  if (pathname === "/profile") return { route: { name: "profile" }, isCanonical: true };
  if (pathname === "/achievements") return { route: { name: "achievements" }, isCanonical: true };
  if (pathname === "/") return { route: { name: "home" }, isCanonical: true };
  if (pathname === "/materials") return { route: { name: "materials" }, isCanonical: true };
  if (pathname === "/podcasts/new") return { route: { name: "podcast-new" }, isCanonical: true };
  if (pathname === "/concept-cards/new") return { route: { name: "card-set-new" }, isCanonical: true };
  if (pathname === "/podcasts") return { route: { name: "podcasts" }, isCanonical: true };
  if (pathname === "/concept-cards") return { route: { name: "concept-cards" }, isCanonical: true };
  if (pathname === "/concept-cards/review") return { route: { name: "card-review" }, isCanonical: true };
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
  const fallback: RouteRead = { route: { name: "not-found", path: pathname }, isCanonical: false };
  if (materialSegment === "topics" && uuidPattern.test(materialId) && segments.length === 2) {
    const route: AppRoute = { name: "topic", topicId: materialId };
    return { route, isCanonical: routePath(route) === pathname };
  }
  if (materialSegment === "podcasts" && uuidPattern.test(materialId) && segments.length === 2) {
    const route: AppRoute = { name: "podcast", podcastId: materialId };
    return { route, isCanonical: routePath(route) === pathname };
  }
  if (materialSegment === "concept-cards" && uuidPattern.test(materialId)
    && (segments.length === 2 || (segments.length === 3 && (resourceSegment === "edit" || resourceSegment === "review")))) {
    const route: AppRoute = resourceSegment === "review" ? { name: "card-review", cardSetId: materialId }
      : { name: segments.length === 3 ? "card-set-edit" : "card-set", cardSetId: materialId };
    return { route, isCanonical: routePath(route) === pathname };
  }
  if (materialSegment !== "materials" || !uuidPattern.test(materialId)) return fallback;
  if (segments.length === 2) {
    const route: AppRoute = { name: "material", materialId };
    return { route, isCanonical: routePath(route) === pathname };
  }
  // 舊網址 /materials/{id}/map 與 /materials/{id}/knowledge-map：導向教材目前的知識地圖。
  if (segments.length === 3 && (resourceSegment === "map" || resourceSegment === "knowledge-map"))
    return { route: { name: "material", materialId }, isCanonical: false };

  let route: AppRoute;
  if (resourceSegment === "concept-cards" && runId === "review" && segments.length === 4) {
    route = { name: "card-review", materialId };
  } else if ((resourceSegment === "concept-cards" || resourceSegment === "podcasts") && uuidPattern.test(runId)
    && (segments.length === 4 || (resourceSegment === "concept-cards" && segments.length === 5 && (structureSegment === "edit" || structureSegment === "review")))) {
    route = resourceSegment === "podcasts" ? { name: "podcast", podcastId: runId, materialId }
      : structureSegment === "review" ? { name: "card-review", cardSetId: runId, materialId }
      : { name: segments.length === 5 ? "card-set-edit" : "card-set", cardSetId: runId, materialId };
  } else if (resourceSegment === "research" && runId === "scopes" && segments.length === 5 && uuidPattern.test(structureSegment)) {
    // 教材補充學習的範圍確認頁：在教材頁首內顯示。
    route = { name: "topic", topicId: structureSegment, materialId };
  } else if (resourceSegment === "research" && segments.length === 4 && uuidPattern.test(runId)) {
    route = { name: "research", materialId, researchId: runId };
  } else if (segments.length === 3 && (resourceSegment === "concept-cards" || resourceSegment === "podcasts" || resourceSegment === "research" || resourceSegment === "notes")) {
    route = { name: "material-content", materialId, kind: resourceSegment };
  } else if (segments.length === 4 && resourceSegment === "sources" && runId === "new") {
    route = { name: "material-source-upload", materialId };
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
  if (route.name === "not-found") return route.path;
  if (route.name === "material") {
    if (!uuidPattern.test(route.materialId)) throw new Error("ROUTE_INVALID");
    return `/materials/${route.materialId}`;
  }
  if (route.name === "research") {
    if (!uuidPattern.test(route.materialId) || !uuidPattern.test(route.researchId)) throw new Error("ROUTE_INVALID");
    return `/materials/${route.materialId}/research/${route.researchId}`;
  }
  if (route.name === "home") return "/";
  if (route.name === "topics") return "/topics";
  if (route.name === "profile") return "/profile";
  if (route.name === "achievements") return "/achievements";
  if (route.name === "topic") {
    if (!uuidPattern.test(route.topicId) || (route.materialId && !uuidPattern.test(route.materialId))) throw new Error("ROUTE_INVALID");
    return route.materialId ? `/materials/${route.materialId}/research/scopes/${route.topicId}` : `/topics/${route.topicId}`;
  }
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
  if (route.name === "card-review") {
    if ((route.cardSetId && !uuidPattern.test(route.cardSetId)) || (route.materialId && !uuidPattern.test(route.materialId))) throw new Error("ROUTE_INVALID");
    return `${route.materialId ? `/materials/${route.materialId}` : ""}/concept-cards${route.cardSetId ? `/${route.cardSetId}` : ""}/review`;
  }
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
  if (route.name === "material-source-upload") {
    if (!uuidPattern.test(route.materialId)) throw new Error("ROUTE_INVALID");
    return `/materials/${route.materialId}/sources/new`;
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
