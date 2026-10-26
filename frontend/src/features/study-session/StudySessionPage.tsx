import { Progress } from "../../ui/Progress";
import { StartingLevelDialog, useStudyStart } from "./StartingLevel";
import { MaterialDiagnosticPanel } from "../assessment/MaterialDiagnosticPanel";
import { useEffect, useRef, useState } from "react";

import { ApiClientError, errorMessage, isNotFound, type StudydyApiClient } from "../../api/client";
import { NotFoundView, materialNotFound } from "../../ui/NotFoundView";
import type {
  AssessmentSetSummary,
  AssessmentSetView,
  StudyNavigationView, KnowledgeConceptsView,
  StudyProgressView,
  StudySessionView,
  StartingLevel,
} from "../../api/contracts";
import { routePath, writeRoute, type AppRoute } from "../../app/routes";
import { Icon } from "../../ui/Icon";
import { ConceptContent } from "../../ui/ConceptContent";
import { MaterialContentNav } from "../material-flow/MaterialContentNav";
import { StateView } from "../../ui/StateView";
import { assessmentPhase, type AssessmentPhase } from "../assessment/assessment-phase";
import { AssessmentSetPanel } from "../assessment/AssessmentSetPanel";
import "./styles.css";

type StudyData = {
  startingLevel: StartingLevel | null;
  progress: StudyProgressView;
  session: StudySessionView;
  view: StudyNavigationView;
  context: KnowledgeConceptsView | null;
  assessmentSets: AssessmentSetSummary[];
  selectedSetId: string | null;
  selectedSet: AssessmentSetView | null;
};

function validBinding(data: StudyData): boolean {
  const concepts = new Set(data.view.concepts.map((concept) => concept.concept_id));
  return (
    data.assessmentSets.every((group) => group.kind === "material_diagnostic" || concepts.has(group.target_concept_id)) &&
    data.progress.assessment_cycles.every((cycle) => concepts.has(cycle.concept_id)) &&
    data.progress.concept_count === data.view.concept_count &&
    data.progress.concept_states.every((state) => concepts.has(state.concept_id)) &&
    (data.progress.current_concept_id === null || concepts.has(data.progress.current_concept_id)) &&
    data.progress.next_action.prerequisite_concept_ids.every((id) => concepts.has(id))
  );
}

export function StudySessionPage({
  apiClient,
  route,
}: {
  apiClient: StudydyApiClient;
  route: Extract<AppRoute, { name: "study-session" }>;
}) {
  const launch = useStudyStart(apiClient, `${route.materialId}:${route.structureRevision}`);
  const [readingBusy, setReadingBusy] = useState(false);
  const [readingError, setReadingError] = useState<string | null>(null);
  const readingIntent = useRef<{ conceptId: string; key: string } | null>(null);
  const continueIntent = useRef<string | null>(null);
  const [data, setData] = useState<StudyData | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [refreshMessage, setRefreshMessage] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const [panelPhase, setPanelPhase] = useState<AssessmentPhase | null>(null);
  const [continuing, setContinuing] = useState(false);
  const [continueError, setContinueError] = useState<string | null>(null);
  const activePage = useRef(false);
  const pageVersion = useRef(0);
  const restoredRoute = useRef<string | null>(null);

  const load = async () => {
    const restored = await apiClient.resumeWorkspace(route);
    const next = {
      startingLevel: restored.starting_level,
      view: restored.outline,
      context: restored.context,
      session: restored.session,
      progress: restored.progress,
      assessmentSets: restored.assessment_sets,
      selectedSetId: restored.selected_set_id,
      selectedSet: restored.selected_set,
    };
    if (!validBinding(next)) throw new Error("STUDY_BINDING_MISMATCH");
    return next;
  };

  useEffect(() => {
    // 只補齊剛恢復的題組網址時保留同一份 snapshot；其他導覽仍正常重讀。
    const alreadyRestored = restoredRoute.current === routePath(route);
    restoredRoute.current = null;
    activePage.current = true;
    if (alreadyRestored) return () => { activePage.current = false; };
    let cancelled = false;
    pageVersion.current += 1;
    setPanelPhase(null);
    setData(null);
    setMessage(null);
    setRefreshMessage(null);
    setContinuing(false);
    setContinueError(null);
    setReadingBusy(false); setReadingError(null); readingIntent.current = null;
    void load().then(
      (next) => {
        if (cancelled) return;
        if (next.selectedSetId && !route.assessmentSetId && next.session.status !== "completed") {
          const destination = { ...route, assessmentSetId: next.selectedSetId };
          restoredRoute.current = routePath(destination);
          writeRoute(destination, true);
        }
        setData(next);
      },
      (error) => {
        if (!cancelled) { setNotFound(isNotFound(error)); setMessage(errorMessage(error)); }
      },
    );
    return () => {
      cancelled = true;
      activePage.current = false;
    };
  }, [apiClient, reload, route]);

  const refresh = async () => {
    const version = pageVersion.current;
    try {
      const next = await load();
      if (!activePage.current || pageVersion.current !== version) return;
      setData(next);
      setRefreshMessage(null);
    } catch (error) {
      if (pageVersion.current === version) setRefreshMessage(errorMessage(error));
    }
  };

  const reviewConcept = (conceptId?: string) => {
    writeRoute({ name: "knowledge-map", materialId: route.materialId, runId: route.runId, structureRevision: route.structureRevision }, false,
      { knowledgeMap: { revision: route.structureRevision, mode: "focus", concept: conceptId, detail: conceptId } });
  };
  const backToReview = () => writeRoute({ name: "knowledge-map", materialId: route.materialId, runId: route.runId, structureRevision: route.structureRevision }, false, { knowledgeMap: { revision: route.structureRevision, mode: "review" } });
  const back = () => reviewConcept(data?.assessmentSets.find(item => item.set_id === data.selectedSetId)?.target_concept_id
    ?? data?.progress.current_concept_id ?? undefined);

  if (notFound && message) return <NotFoundView title="找不到這段學習紀錄" description="這段學習紀錄可能已被刪除，或不屬於目前登入的帳號。" back={materialNotFound.back} />;
  if (message)
    return (
      <StateView
        action={
          <>
            <button
              className="primary-button"
              type="button"
              onClick={() => setReload((value) => value + 1)}
            >
              <Icon name="refresh" />
              重新讀取
            </button>
            <button
              className="secondary-button"
              type="button"
              onClick={backToReview}
            >
              返回學習與測驗
            </button>
          </>
        }
        description={message}
        image="/assets/studydy/failure-confused.png"
        title="無法開啟學習進度"
        tone="failure"
      />
    );
  if (!data)
    return (
      <StateView
        description="正在復原教材結構與學習進度狀態。"
        live
        skeleton="page"
        title="正在讀取學習進度"
        tone="loading"
      />
    );

  const completed = data.session.status === "completed";
  const historySets = data.assessmentSets.filter(
    (group) => group.published_count > 0 || ["completed", "cancelled"].includes(group.status),
  );
  const selectedSet = data.assessmentSets.find((group) => group.set_id === data.selectedSetId);
  if (data.startingLevel === null && data.session.status !== "completed" && data.selectedSet?.status !== "completed") return <section className="study-session-page">
    <StateView title="選擇起始程度" description="先選擇這份教材的學習方式，已保存的進度與題組會保留。" tone="empty"
      action={<button className="secondary-button" onClick={backToReview}>返回學習與測驗</button>} />
    {launch.error && <p role="alert" className="form-error">{launch.error}</p>}
    {launch.busy ? <p role="status">正在接續學習…</p> : <StartingLevelDialog api={apiClient} materialId={route.materialId} current={null} onClose={backToReview}
      onSaved={() => void launch.start(route)} />}
  </section>;
  const history = historySets.length > 0 && <aside className="study-rail" aria-label="學習紀錄">
    <details className="surface assessment-set-history" open><summary>{historySets.some(group => group.kind === "material_diagnostic") ? "題組紀錄" : "觀念題組紀錄"}（{historySets.length}）</summary><ol>
      {historySets.map((group, index) => <li key={group.set_id}><button className={`study-history-item is-${group.kind}`}
        aria-current={group.set_id === data.selectedSetId ? "true" : undefined}
        onClick={() => { setContinueError(null); writeRoute({ ...route, assessmentSetId: group.set_id }); }}>
        <b>第 {historySets.length - index} 組 · {group.kind === "material_diagnostic" ? "起始診斷" : group.kind === "remediation" ? "補強" : "初篩"}</b>
        <span>{group.kind === "material_diagnostic" ? "跨概念" : data.view.concepts.find(concept => concept.concept_id === group.target_concept_id)?.label ?? "觀念"}</span>
        <small>{group.published_count === 0 ? "此題組已結束" : group.answered_count === 0 ? "尚未作答" : `已答 ${group.answered_count}/${group.published_count}`}</small>
      </button></li>)}
    </ol></details>
  </aside>;
  if (data.selectedSet?.kind === "material_diagnostic" && data.context) {
    const continueDiagnostic = async () => {
      if (data.progress.next_action.action !== "complete") { void launch.start(route); return; }
      if (continuing || completed) return;
      const version = pageVersion.current;
      setContinuing(true); setContinueError(null);
      try {
        await apiClient.applyGuidance(route.studySessionId, { schema: "guidance-apply/v1", guidance_revision: data.progress.guidance_revision });
        if (activePage.current && pageVersion.current === version) await refresh();
      } catch (failure) {
        if (activePage.current && pageVersion.current === version) {
          setContinueError(errorMessage(failure));
          if (failure instanceof ApiClientError && failure.reasonCode === "LEARNER_GUIDANCE_STALE") await refresh();
        }
      } finally { if (activePage.current && pageVersion.current === version) setContinuing(false); }
    };
    return <section className="study-session-page">
    {launch.dialog}
    <MaterialContentNav apiClient={apiClient} materialId={route.materialId} current="review" loadMaterial
      back={{ label: "返回學習與測驗", onClick: backToReview }}
      mapRoute={{ name: "knowledge-map", materialId: route.materialId, runId: route.runId, structureRevision: route.structureRevision }} />
    {(launch.error || continueError) && <p role="alert" className="form-error">{launch.error || continueError}</p>}
    <div className={`study-workspace${historySets.length ? "" : " without-rail"}`}><div className="study-main">
      <MaterialDiagnosticPanel key={data.selectedSet.set_id} api={apiClient} initialSet={data.selectedSet} context={data.context}
        onProgressChanged={refresh} onReview={reviewConcept} onBack={back} continuing={launch.busy || continuing}
        sessionCompleted={completed} completesSession={data.progress.next_action.action === "complete"}
        onContinue={() => void continueDiagnostic()} />
    </div>{history}</div>
  </section>;
  }
  // 歷史題組顯示該題組的觀念；繼續學習仍依目前進度判斷。
  const visibleConceptId = route.assessmentSetId
    ? selectedSet?.target_concept_id
    : data.progress.current_concept_id;
  const visibleConcept = data.context?.concepts.find(
    (concept) => concept.concept_id === visibleConceptId,
  );
  if (!visibleConcept)
    return (
      <StateView
        action={
          <button className="secondary-button" type="button" onClick={backToReview}>
            返回學習與測驗
          </button>
        }
        description="目前沒有可安全顯示的教材概念。"
        image="/assets/studydy/empty-disappointed.png"
        title="目前沒有學習內容"
        tone="empty"
      />
    );

  const conceptState = data.progress.concept_states.find(state => state.concept_id === visibleConceptId);
  const noAssessment = conceptState?.assessable_claim_ids?.length === 0;
  const initialSetPhase = assessmentPhase(selectedSet);
  const layoutMode = panelPhase ?? initialSetPhase;
  const preparing = layoutMode === "preparing" || layoutMode === "intervention";
  const currentCycle = data.progress.assessment_cycles.find(
    (item) => item.concept_id === data.progress.current_concept_id,
  );
  const inheritedCheck = !selectedSet && currentCycle?.inherited_from ? currentCycle : null;
  // 開始／再次練習與只讀承接結果使用同一網址；只保留這次導覽的閱讀意圖。
  const requestedReading = window.history.state?.studyReading?.sessionId === route.studySessionId
    && window.history.state.studyReading.conceptId === visibleConcept.concept_id;
  // 已結束時只接受從地圖明確選擇再次練習的閱讀意圖。
  const readingFirst = (!completed || requestedReading) && data.startingLevel === "beginner" && !data.selectedSet && !noAssessment && (!inheritedCheck || requestedReading);
  const beginAnswers = async () => {
    if (readingBusy) return;
    setReadingBusy(true); setReadingError(null);
    const version = pageVersion.current;
    if (readingIntent.current?.conceptId !== visibleConcept.concept_id)
      readingIntent.current = { conceptId: visibleConcept.concept_id, key: crypto.randomUUID() };
    try {
      const group = await apiClient.createAssessmentSet(route.studySessionId, visibleConcept.concept_id, readingIntent.current.key);
      if (!activePage.current || pageVersion.current !== version) return;
      readingIntent.current = null;
      writeRoute({ ...route, assessmentSetId: group.set_id });
    } catch (failure) { if (activePage.current && pageVersion.current === version) setReadingError(errorMessage(failure)); }
    finally { if (activePage.current && pageVersion.current === version) setReadingBusy(false); }
  };
  const showMaterial = !preparing && !readingFirst;
  const showRail = !preparing && historySets.length > 0;
  const nextAction = data.progress.next_action;
  const completesSession = nextAction.action === "complete";
  const nextConcept =
    nextAction.action === "advance"
      ? data.view.concepts.find((item) => item.concept_id === nextAction.target_concept_id)
      : undefined;
  const currentResult =
    !completed &&
    !refreshMessage &&
    (noAssessment || (layoutMode === "result" && selectedSet?.status === "completed") || inheritedCheck) &&
    !currentCycle?.active_set_id &&
    (noAssessment || conceptState?.status === "completed" || (currentCycle && ["passed", "incomplete"].includes(currentCycle.outcome)));
  const canContinue =
    currentResult &&
    (nextConcept || completesSession) &&
    !data.assessmentSets.some(
      (item) =>
        ["preparing", "partial_ready", "ready", "in_progress"].includes(item.status) &&
        (completesSession || item.target_concept_id === data.progress.current_concept_id),
    );
  const continueLearning = async () => {
    if (!canContinue || continuing) return;
    const version = pageVersion.current;
    setContinuing(true);
    setContinueError(null);
    try {
      await apiClient.applyGuidance(route.studySessionId, {
        schema: "guidance-apply/v1",
        guidance_revision: data.progress.guidance_revision,
      });
      if (!activePage.current || pageVersion.current !== version) return;
      let nextSetId: string | null = null;
      if (!completesSession) {
        if (!continueIntent.current) continueIntent.current = crypto.randomUUID();
        const started = await apiClient.startStudy(route, continueIntent.current);
        if (!activePage.current || pageVersion.current !== version) return;
        nextSetId = started.selected_set_id;
        continueIntent.current = null;
      }
      setData(null);
      setPanelPhase(null);
      writeRoute(
        {
          name: "study-session",
          materialId: route.materialId,
          runId: route.runId,
          structureRevision: route.structureRevision,
          studySessionId: route.studySessionId,
          ...(nextSetId ? { assessmentSetId: nextSetId } : {}),
        },
        true,
      );
      setReload((value) => value + 1);
    } catch (error) {
      if (!activePage.current || pageVersion.current !== version) return;
      if (error instanceof ApiClientError && error.reasonCode === "LEARNER_GUIDANCE_STALE") {
        setContinueError("學習進度已更新，請確認最新下一步後再繼續。");
        await refresh();
      } else setContinueError(errorMessage(error));
    } finally {
      if (activePage.current && pageVersion.current === version) setContinuing(false);
    }
  };
  const continuation = canContinue
    ? {
        busy: continuing,
        onContinue: continueLearning,
        label: completesSession
          ? continuing
            ? "正在完成…"
            : "完成本次學習"
          : continuing
            ? "正在前往下一個觀念…"
            : `下一個觀念：${nextConcept!.label}`,
      }
    : undefined;
  const position = data.view.initial_learning_path.find(
    (step) => step.concept_id === visibleConcept.concept_id,
  )?.position;
  const materialCard = (
    <article className="surface current-concept-card" aria-labelledby="study-content-title">
      <p className="eyebrow">教材重點</p>
      <h2 id="study-content-title">{visibleConcept.label}</h2>
      <ConceptContent claims={visibleConcept.claims} apiClient={apiClient} sourceResolver={data.view.source_resolver} />
    </article>
  );
  return (
    <section className="study-session-page">
      {launch.dialog}
      <MaterialContentNav apiClient={apiClient} materialId={route.materialId} current="review" loadMaterial
      back={{ label: "返回學習與測驗", onClick: backToReview }}
        mapRoute={{ name: "knowledge-map", materialId: route.materialId, runId: route.runId, structureRevision: route.structureRevision }}
        // 從學習頁回到地圖時，聚焦剛才學習的概念。
        onMapModeChange={(mode) => mode === "focus" ? back() : writeRoute(
          { name: "knowledge-map", materialId: route.materialId, runId: route.runId, structureRevision: route.structureRevision },
          false, { knowledgeMap: { revision: route.structureRevision, mode: "review" } })} />
      <header className="study-header">
        <div>
          <h1>{completed && !readingFirst ? "本次學習已結束" : visibleConcept.label}</h1>
          <p className="study-header-meta">
            {position !== undefined && <span className="study-position">
              <b>第 {position} / {data.view.concept_count} 個概念</b>
              <Progress className="study-position-progress" size="small" value={position} max={data.view.concept_count} aria-label="學習概念進度" />
            </span>}
            {showMaterial && <span>學習進度會自動保存。</span>}
          </p>
        </div>
      </header>
      {continueError && (
        <div className="assessment-error" role="alert">
          {continueError}
        </div>
      )}
      {refreshMessage && (
        <div className="assessment-error" role="alert">
          {refreshMessage}
          <button className="text-button" onClick={() => void refresh()}>
            重新讀取
          </button>
        </div>
      )}
      <div className={`study-workspace is-${layoutMode}-mode${!showRail ? " without-rail" : ""}`}>
        <div className="study-main">
          {readingFirst ? <section className="surface starting-reading-intro" aria-label="先閱讀概念說明">
            {materialCard}
            {readingError && <p role="alert" className="form-error">{readingError}</p>}
            <button className="primary-button" disabled={readingBusy} onClick={() => void beginAnswers()}>{readingBusy ? "正在開始作答…" : "開始作答"}</button>
          </section> : <div className={`study-learning-grid is-${layoutMode}-mode is-set-mode`}>
            {showMaterial && (
              <details className="surface study-material-summary">
                <summary>教材重點與來源</summary>
                {materialCard}
              </details>
            )}
            <div className="study-current-action" id="assessment-panel">
              {completed && !data.selectedSet ? <StateView title="學習進度已保存" description="本次學習已結束，可以回到知識地圖查看進度或選擇其他概念。" tone="empty" icon="check"
                action={<button className="primary-button" onClick={back}>查看知識地圖</button>} /> : noAssessment && !currentCycle?.active_set_id ? (
                <section className="surface assessment-cycle">
                  <h2>此概念目前無法提供可靠檢測</h2>
                  <p>這是系統目前無法驗證，不代表你答錯。可以繼續學習其他概念。</p>
                  {continuation && <button className="primary-button" disabled={continuation.busy}
                    onClick={() => void continuation.onContinue()}>{continuation.label}</button>}
                </section>
              ) : inheritedCheck ? (
                <section className="surface assessment-cycle inherited-check" aria-label="承接的檢測結果">
                  <h2>已完成</h2>
                  <p>重點與來源皆未變更，已承接先前版本的完成紀錄。</p>
                  <div className="assessment-set-actions assessment-result-navigation">
                    <button className="secondary-button" onClick={() => writeRoute({
                      name: "study-session",
                      materialId: route.materialId,
                      runId: inheritedCheck.inherited_from!.run_id,
                      structureRevision: inheritedCheck.inherited_from!.knowledge_structure_revision,
                      studySessionId: inheritedCheck.inherited_from!.study_session_id,
                      assessmentSetId: inheritedCheck.diagnostic_set_id,
                    })}>查看原始結果</button>
                    {continuation && (
                      <button className="primary-button" disabled={continuation.busy}
                        onClick={() => void continuation.onContinue()}>{continuation.label}</button>
                    )}
                  </div>
                </section>
              ) : data.selectedSet && data.selectedSet.kind !== "material_diagnostic" && data.selectedSetId ? <AssessmentSetPanel
                apiClient={apiClient}
                materialId={route.materialId}
                studySessionId={route.studySessionId}
                selectedSetId={data.selectedSetId}
                initialSet={data.selectedSet}
                concept={visibleConcept}
                view={data.context!}
                completed={completed}
                conceptCompleted={conceptState?.status === "completed"}
                onSetSelected={(id) => writeRoute({ ...route, assessmentSetId: id })}
                onProgressChanged={refresh}
                onBackToMap={back}
                initialPhase={initialSetPhase}
                onPhaseChange={setPanelPhase}
                continuation={continuation}
              /> : <StateView title="尚未開始這個概念" description="按下開始，依目前的起始程度接續學習。" tone="empty"
                action={<button className="primary-button" disabled={launch.busy} onClick={() => void launch.start(route)}>開始學習</button>} />}
            </div>
          </div>}
        </div>
        {showRail && history}
      </div>
    </section>
  );
}
