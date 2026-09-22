import { useEffect, useRef, useState } from "react";

import { errorMessage, type StudydyApiClient } from "../../api/client";
import type { KnowledgeStructureView, LearnerProgressView, MaterialLibraryItem, StudySessionView } from "../../api/contracts";
import { writeRoute, type AppRoute } from "../../app/routes";
import { StateView } from "../../ui/StateView";
import { KnowledgeMapWorkspace } from "./KnowledgeMapWorkspace";
import "./styles.css";

export default function KnowledgeMap({ apiClient, route }: {
  apiClient: StudydyApiClient;
  route: Extract<AppRoute, { name: "knowledge-map" }>;
}) {
  const [progress, setProgress] = useState<LearnerProgressView | null>(null);
  const [savedLearningState, setSavedLearningState] = useState<Pick<StudySessionView, "study_session_id" | "status"> | null>(null);
  const [isLoadingProgress, setIsLoadingProgress] = useState(true);
  const [progressMessage, setProgressMessage] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const [view, setView] = useState<KnowledgeStructureView | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [startMessage, setStartMessage] = useState<string | null>(null);
  const [isStartingStudy, setIsStartingStudy] = useState(false);
  const startIntent = useRef<{ conceptId: string; key: string } | null>(null);

  const loadedRoute = useRef("");
  useEffect(() => {
    let cancelled = false;
    setMessage(null);
    setProgressMessage(null);
    setIsLoadingProgress(true);
    const routeKey = `${route.materialId}:${route.runId}:${route.structureRevision}`;
    if (loadedRoute.current !== routeKey) {
      setView(null);
      setProgress(null); setSavedLearningState(null);
      loadedRoute.current = routeKey;
    }
    const openCurrentHead = (material: MaterialLibraryItem) => {
      const head = material.available_structures.find(item => item.knowledge_structure_revision === material.head_revision);
      if (!head || head.knowledge_structure_revision === route.structureRevision) return false;
      writeRoute({ name: "knowledge-map", materialId: route.materialId, runId: head.run_id, structureRevision: head.knowledge_structure_revision }, true);
      return true;
    };
    const load = async () => {
      // 教材索引與 immutable map 並行讀取；複習只需要 progress，不重取整份 resume。
      const materialTask = apiClient.getMaterial(route.materialId).then(material => ({material,error:null}),
        (error: unknown) => ({material:null,error}));
      const progressTask = materialTask.then(async result => {
        if (!result.material) throw result.error;
        const material = result.material;
        const head = material.available_structures.find(item => item.knowledge_structure_revision === material.head_revision);
        if (head && head.knowledge_structure_revision !== route.structureRevision) return null;
        const saved = material.study_sessions.find(item => item.run_id === route.runId && item.knowledge_structure_revision === route.structureRevision);
        return saved ? {saved,progress:await apiClient.readProgress(saved.study_session_id,route.structureRevision)} : null;
      }).then(value => ({value,error:null}), (error: unknown) => ({value:null,error}));
      try {
        const [mapResult, run] = await Promise.all([
          apiClient.getKnowledgeStructure({materialId:route.materialId,structureRevision:route.structureRevision})
            .then(view => ({view,error:null}), (error: unknown) => ({view:null,error})),
          apiClient.getMaterialRun(route.runId),
        ]);
        if (cancelled) return;
        if (run.material_id !== route.materialId || run.output_binding?.knowledge_structure_revision !== route.structureRevision) throw new Error("RUN_STRUCTURE_MISMATCH");
        if (mapResult.view) setView(mapResult.view);
        const materialResult = await materialTask;
        if (cancelled || materialResult.material && openCurrentHead(materialResult.material)) return;
        if (!mapResult.view) throw mapResult.error;
        const loaded = await progressTask;
        if (cancelled) return;
        if (loaded.error) setProgressMessage("暫時無法讀取最近的學習進度，仍可瀏覽教材地圖。");
        else {
          setProgress(loaded.value?.progress ?? null);
          setSavedLearningState(loaded.value?.saved ?? null);
        }
      } catch (error) {
        if (!cancelled) setMessage(errorMessage(error));
      } finally {
        if (!cancelled) setIsLoadingProgress(false);
      }
    };
    void load();
    return () => { cancelled = true; };
  }, [apiClient, route.materialId, route.runId, route.structureRevision, reload]);

  if (message) return (
    <StateView
      action={<div className="state-actions"><button className="primary-button" type="button" onClick={() => setReload((value) => value + 1)}>重新讀取</button><button className="secondary-button" type="button" onClick={() => writeRoute({ name: "materials" })}>返回教材庫</button></div>}
      description={message}
      image="/assets/studydy/failure-confused.png"
      title="無法讀取知識地圖"
      tone="failure"
    />
  );
  if (!view) return (
    <StateView
      description="正在載入教材概念與學習順序。"
      live
      title="正在讀取知識地圖"
      tone="loading"
    />
  );
  const startStudy = async (conceptId: string) => {
    if (isStartingStudy || isLoadingProgress) return;
    if (savedLearningState && (savedLearningState.status === "completed" || progress?.current_concept_id === conceptId)) {
      writeRoute({ ...route, name: "study-session", studySessionId: savedLearningState.study_session_id });
      return;
    }
    if (startIntent.current?.conceptId !== conceptId) {
      startIntent.current = { conceptId, key: crypto.randomUUID() };
    }
    setIsStartingStudy(true);
    setStartMessage(null);
    try {
      let session = savedLearningState ? await apiClient.focusStudySession(savedLearningState.study_session_id, conceptId) : await apiClient.createStudySession({
        schema: "study-session-create/v2",
        material_id: route.materialId,
        knowledge_structure_revision: route.structureRevision,
        current_concept_id: conceptId,
      }, startIntent.current.key);
      // A concurrent ensure may have found an existing state at another concept.
      if (session.status !== "completed" && session.current_concept_id !== conceptId) {
        session = await apiClient.focusStudySession(session.study_session_id, conceptId);
      }
      writeRoute({
        name: "study-session",
        materialId: route.materialId,
        runId: route.runId,
        structureRevision: route.structureRevision,
        studySessionId: session.study_session_id,
      });
    } catch (error) {
      setStartMessage(errorMessage(error));
      setIsStartingStudy(false);
    }
  };
  return (
    <KnowledgeMapWorkspace
      key={view.knowledge_structure_revision}
      apiClient={apiClient}
      progress={progress}
      learningStateStatus={savedLearningState?.status ?? null}
      progressMessage={progressMessage}
      onReloadProgress={() => setReload((value) => value + 1)}
      isStartingStudy={isStartingStudy || isLoadingProgress}
      isLoadingProgress={isLoadingProgress}
      onReturnToRun={() => writeRoute({ name: "material-run", materialId: route.materialId, runId: route.runId })}
      onAddSources={() => writeRoute({ name: "material-sources", materialId: route.materialId })}
      onStartStudy={startStudy}
      startMessage={startMessage}
      view={view}
    />
  );
}
