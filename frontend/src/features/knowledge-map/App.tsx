import { rememberMaterial, rememberedMaterial } from "../material-flow/material-memory";
import { useEffect, useRef, useState } from "react";

import { errorMessage, type StudydyApiClient } from "../../api/client";
import type {
  KnowledgeStructureView,
  LearnerProgressView,
  MaterialLibraryItem,
  StudySessionView,
} from "../../api/contracts";
import { routePath, writeRoute, type AppRoute } from "../../app/routes";
import { StateView } from "../../ui/StateView";
import { initialFocusConceptId } from "./knowledge-map";
import { KnowledgeMapWorkspace } from "./KnowledgeMapWorkspace";
import "./styles.css";

function findPublishedHead(material: MaterialLibraryItem) {
  return material.available_structures.find(
    (item) => item.knowledge_structure_revision === material.head_revision,
  );
}

export default function KnowledgeMap({
  apiClient,
  route,
}: {
  apiClient: StudydyApiClient;
  route: Extract<AppRoute, { name: "knowledge-map" }>;
}) {
  const [materialName,setMaterialName] = useState(()=>rememberedMaterial(apiClient,route.materialId)?.display_name??"" );
  const [progress, setProgress] = useState<LearnerProgressView | null>(null);
  const [savedSession, setSavedSession] = useState<Pick<
    StudySessionView,
    "study_session_id" | "status"
  > | null>(null);
  const [isLoadingProgress, setIsLoadingProgress] = useState(true);
  const [progressMessage, setProgressMessage] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const [view, setView] = useState<KnowledgeStructureView | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [startMessage, setStartMessage] = useState<string | null>(null);
  const [isStartingStudy, setIsStartingStudy] = useState(false);
  const startIntent = useRef<{ conceptId: string; key: string } | null>(null);
  const activePage = useRef(false);
  const pageVersion = useRef(0);

  useEffect(() => {
    activePage.current = true;
    pageVersion.current += 1;
    startIntent.current = null;
    setIsStartingStudy(false);
    setStartMessage(null);
    return () => {
      activePage.current = false;
    };
  }, [apiClient, route.materialId, route.runId, route.structureRevision]);

  const loadedRouteKey = useRef("");
  useEffect(() => {
    let cancelled = false;
    setMessage(null);
    setProgressMessage(null);
    setIsLoadingProgress(true);
    const routeKey = `${route.materialId}:${route.runId}:${route.structureRevision}`;
    if (loadedRouteKey.current !== routeKey) {
      setView(null);
      setProgress(null);
      setSavedSession(null);
      loadedRouteKey.current = routeKey;
    }
    const openCurrentHead = (material: MaterialLibraryItem) => {
      const head = findPublishedHead(material);
      if (!head || head.knowledge_structure_revision === route.structureRevision) return false;
      writeRoute(
        {
          name: "knowledge-map",
          materialId: route.materialId,
          runId: head.run_id,
          structureRevision: head.knowledge_structure_revision,
        },
        true,
      );
      return true;
    };
    const load = async () => {
      // 教材索引與固定版本地圖並行讀取；複習只讀進度，不重取完整學習紀錄。
      const materialTask = apiClient.getMaterial(route.materialId).then(
        (material) => { if (!cancelled) { rememberMaterial(apiClient, material); setMaterialName(material.display_name); } return { material, error: null }; },
        (error: unknown) => ({ material: null, error }),
      );
      const progressTask = materialTask
        .then(async (result) => {
          if (!result.material) throw result.error;
          const { material } = result;
          const head = findPublishedHead(material);
          if (head && head.knowledge_structure_revision !== route.structureRevision) return null;
          const saved = material.study_sessions.find(
            (item) =>
              item.run_id === route.runId &&
              item.knowledge_structure_revision === route.structureRevision,
          );
          return saved
            ? {
                saved,
                progress: await apiClient.readProgress(
                  saved.study_session_id,
                  route.structureRevision,
                ),
              }
            : null;
        })
        .then(
          (value) => ({ value, error: null }),
          (error: unknown) => ({ value: null, error }),
        );
      try {
        const [mapResult, run] = await Promise.all([
          apiClient
            .getKnowledgeStructure({
              materialId: route.materialId,
              structureRevision: route.structureRevision,
            })
            .then(
              (view) => ({ view, error: null }),
              (error: unknown) => ({ view: null, error }),
            ),
          apiClient.getMaterialRun(route.runId),
        ]);
        if (cancelled) return;
        if (
          run.material_id !== route.materialId ||
          run.output_binding?.knowledge_structure_revision !== route.structureRevision
        )
          throw new Error("RUN_STRUCTURE_MISMATCH");
        if (mapResult.view) setView(mapResult.view);
        const materialResult = await materialTask;
        if (cancelled || (materialResult.material && openCurrentHead(materialResult.material)))
          return;
        if (!mapResult.view) throw mapResult.error;
        const progressResult = await progressTask;
        if (cancelled) return;
        if (progressResult.error)
          setProgressMessage("暫時無法讀取最近的學習進度，仍可瀏覽教材地圖。");
        else {
          setProgress(progressResult.value?.progress ?? null);
          setSavedSession(progressResult.value?.saved ?? null);
        }
      } catch (error) {
        if (!cancelled) setMessage(errorMessage(error));
      } finally {
        if (!cancelled) setIsLoadingProgress(false);
      }
    };
    void load();
    return () => {
      cancelled = true;
    };
  }, [apiClient, route.materialId, route.runId, route.structureRevision, reload]);

  const openStudySession = (studySessionId: string) =>
    writeRoute({
      ...route,
      name: "study-session",
      studySessionId,
    });

  const startStudy = async (conceptId: string) => {
    if (isStartingStudy || isLoadingProgress) return;
    const version = pageVersion.current;
    // 路由可能已寫入但 React 尚未卸載舊頁；兩者都有效才接受 mutation 回應。
    const isCurrentPage = () =>
      activePage.current &&
      pageVersion.current === version &&
      window.location.pathname === routePath(route);
    if (
      savedSession &&
      (savedSession.status === "completed" || progress?.current_concept_id === conceptId)
    ) {
      openStudySession(savedSession.study_session_id);
      return;
    }
    if (startIntent.current?.conceptId !== conceptId) {
      startIntent.current = { conceptId, key: crypto.randomUUID() };
    }
    setIsStartingStudy(true);
    setStartMessage(null);
    try {
      let session = savedSession
        ? await apiClient.focusStudySession(savedSession.study_session_id, conceptId)
        : await apiClient.createStudySession(
            {
              schema: "study-session-create/v1",
              material_id: route.materialId,
              knowledge_structure_revision: route.structureRevision,
              current_concept_id: conceptId,
            },
            startIntent.current.key,
          );
      if (!isCurrentPage()) return;
      // 並行建立可能取得其他觀念的既有紀錄；未結束的紀錄仍需對齊選定觀念。
      if (session.status !== "completed" && session.current_concept_id !== conceptId) {
        session = await apiClient.focusStudySession(session.study_session_id, conceptId);
        if (!isCurrentPage()) return;
      }
      openStudySession(session.study_session_id);
    } catch (error) {
      if (!isCurrentPage()) return;
      setStartMessage(errorMessage(error));
      setIsStartingStudy(false);
    }
  };
  // 從教材其他分頁進入測驗時，等待現有 session/progress，再重用同一個入口。
  // 消耗一次性 history intent，返回／reload 不會重複建立學習紀錄。
  useEffect(() => {
    if (!view || message || isLoadingProgress || window.history.state?.assessmentEntry !== route.structureRevision) return;
    const { assessmentEntry: _, ...state } = window.history.state;
    window.history.replaceState(state, "");
    void startStudy(progress?.current_concept_id ?? initialFocusConceptId(view));
  }, [view, message, isLoadingProgress, progress, route.structureRevision]);

  if (message)
    return (
      <StateView
        action={
          <div className="state-actions">
            <button
              className="primary-button"
              type="button"
              onClick={() => setReload((value) => value + 1)}
            >
              重新讀取
            </button>
            <button
              className="secondary-button"
              type="button"
              onClick={() => writeRoute({ name: "materials" })}
            >
              返回教材庫
            </button>
          </div>
        }
        description={message}
        image="/assets/studydy/failure-confused.png"
        title="無法讀取知識地圖"
        tone="failure"
      />
    );
  if (!view)
    return (
      <StateView
        description="正在載入教材概念與學習順序。"
        live
        title="正在讀取知識地圖"
        tone="loading"
      />
    );
  return (
    <KnowledgeMapWorkspace
      key={view.knowledge_structure_revision}
      apiClient={apiClient}
      progress={progress}
      learningStateStatus={savedSession?.status ?? null}
      progressMessage={progressMessage}
      onReloadProgress={() => setReload((value) => value + 1)}
      isStartingStudy={isStartingStudy || isLoadingProgress}
      isLoadingProgress={isLoadingProgress}
      onReturnToRun={() =>
        writeRoute({ name: "material-run", materialId: route.materialId, runId: route.runId })
      }
      mapRoute={route}
      materialName={materialName}
      onStartStudy={startStudy}
      startMessage={startMessage}
      view={view}
    />
  );
}
