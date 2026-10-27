import { useStudyStart } from "../study-session/useStudyStart";
import { rememberMaterial, rememberedMaterial } from "../material-flow/material-memory";
import { useEffect, useRef, useState } from "react";

import { errorMessage, isNotFound, type StudydyApiClient } from "../../api/client";
import { NotFoundView, materialNotFound } from "../../ui/NotFoundView";
import type {
  KnowledgeOutlineView,
  MapProgressView,
  MaterialLibraryItem,
  StudySessionView,
} from "../../api/contracts";
import { writeRoute, type AppRoute } from "../../app/routes";
import { StateView } from "../../ui/StateView";
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
  const [progress, setProgress] = useState<MapProgressView | null>(null);
  const [savedSession, setSavedSession] = useState<Pick<
    StudySessionView,
    "study_session_id" | "status"
  > | null>(null);
  const [isLoadingProgress, setIsLoadingProgress] = useState(true);
  const [progressMessage, setProgressMessage] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const [view, setView] = useState<KnowledgeOutlineView | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [notFound, setNotFound] = useState<"material" | "version" | null>(null);
  const launch = useStudyStart(apiClient, `${route.materialId}:${route.structureRevision}`);

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
                progress: await apiClient.readMapProgress(
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
            .getKnowledgeOutline({
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
        if (cancelled) return;
        // 網址中的教材、處理紀錄或地圖版本不存在：教材也不存在時顯示找不到教材，否則提供開啟目前版本。
        // 處理紀錄與版本對不上屬於資料不一致，維持讀取失敗畫面。
        if (isNotFound(error)) {
          const { material } = await materialTask;
          if (cancelled) return;
          setNotFound(material ? "version" : "material");
          return;
        }
        setMessage(errorMessage(error));
      } finally {
        if (!cancelled) setIsLoadingProgress(false);
      }
    };
    void load();
    return () => {
      cancelled = true;
    };
  }, [apiClient, route.materialId, route.runId, route.structureRevision, reload]);

  const startStudy = (conceptId: string) => {
    if (!isLoadingProgress) void launch.start({ ...route, conceptId });
  };

  if (notFound === "material") return <NotFoundView {...materialNotFound} />;
  if (notFound === "version")
    return <NotFoundView title="找不到這個版本的知識地圖" description="網址中的地圖版本已不存在，可以開啟這份教材目前的知識地圖。"
      back={{ label: "開啟目前的知識地圖", route: { name: "material", materialId: route.materialId } }} />;
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
              返回我的教材
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
        skeleton="map"
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
      isStartingStudy={launch.busy || isLoadingProgress}
      isLoadingProgress={isLoadingProgress}
      onReturnToRun={() =>
        writeRoute({ name: "material-run", materialId: route.materialId, runId: route.runId })
      }
      mapRoute={route}
      materialName={materialName}
      onStartStudy={startStudy}
      startMessage={launch.error}
      view={view}
    />
  );
}
