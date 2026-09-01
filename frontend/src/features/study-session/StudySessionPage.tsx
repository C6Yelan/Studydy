import { useEffect, useState } from "react";

import { errorMessage, type StudydyApiClient } from "../../api/client";
import type {
  AnswerFeedbackView,
  KnowledgeMapView,
  LearnerProgressView,
} from "../../api/contracts";
import { writeRoute, type AppRoute } from "../../app/routes";
import { Icon } from "../../ui/Icon";
import { StateView } from "../../ui/StateView";
import { AssessmentPanel } from "../assessment/AssessmentPanel";
import { GuidanceNextStep } from "../adaptive-learning/AdaptiveNextStep";
import { safeExternalUrl } from "../knowledge-map/knowledge-map";
import { LearningInsights } from "../learning-state/LearningInsights";
import "./styles.css";

type StudyData = {
  progress: LearnerProgressView;
  sourceArtifactId: string;
  view: KnowledgeMapView;
};

function assertRouteBinding(route: Extract<AppRoute, { name: "study-session" }>, data: StudyData) {
  const conceptIds = data.view.concepts.map((concept) => concept.formal_concept_id);
  const stateIds = data.progress.concept_states.map((state) => state.formal_concept_id);
  const guidanceTarget = data.progress.next_action.target_formal_concept_id;
  const guidanceResource = data.progress.next_action.route.resource_promotion_id;
  if (
    data.view.knowledge_map_revision !== route.mapRevision
    || data.progress.study_session_id !== route.studySessionId
    || data.progress.material_id !== route.materialId
    || data.progress.base_knowledge_map_revision !== route.mapRevision
    || new Set(stateIds).size !== conceptIds.length
    || !conceptIds.every((id) => stateIds.includes(id))
    || (data.progress.current_formal_concept_id !== null && !conceptIds.includes(data.progress.current_formal_concept_id))
    || data.progress.weakness_findings.some((finding) => !conceptIds.includes(finding.target_formal_concept_id))
    || (guidanceTarget !== null && !conceptIds.includes(guidanceTarget))
    || (guidanceResource !== null && !data.view.concepts.some((concept) =>
      concept.supplementary_resources.some((resource) => resource.promotion_id === guidanceResource)))
  ) throw new Error("STUDY_ROUTE_BINDING_MISMATCH");
}

function SessionPath({ progress, view }: {
  progress: LearnerProgressView;
  view: KnowledgeMapView;
}) {
  const mastered = new Set(progress.concept_states
    .filter((state) => state.status === "mastered")
    .map((state) => state.formal_concept_id));
  const deferredNoSafe = new Set(progress.no_safe_deferred_formal_concept_ids);
  const path = view.initial_learning_path.map((step) => ({
    ...step,
    label: view.concepts.find((concept) => concept.formal_concept_id === step.formal_concept_id)?.label ?? step.formal_concept_id,
  }));
  const currentIndex = path.findIndex((concept) =>
    concept.formal_concept_id === progress.current_formal_concept_id);
  const nextConceptId = path
    .slice(currentIndex + 1)
    .find((concept) => !mastered.has(concept.formal_concept_id))?.formal_concept_id;
  return (
    <section className="surface session-path" aria-labelledby="session-path-title">
      <p className="eyebrow">你的學習方向</p>
      <h2 id="session-path-title">教材建議學習順序</h2>
      <p>作答會更新本次進度，但不會改寫這份教材順序。</p>
      <ol>
        {path.map((concept, index) => {
          const isCurrent = concept.formal_concept_id === progress.current_formal_concept_id;
          const isNoSafeDeferred = deferredNoSafe.has(concept.formal_concept_id);
          const isCompleted = mastered.has(concept.formal_concept_id);
          const isNext = concept.formal_concept_id === nextConceptId;
          const placementReason = view.initial_learning_path.find((step) =>
            step.formal_concept_id === concept.formal_concept_id)?.placement_reason;
          return (
            <li className={isCurrent ? "is-current" : isCompleted ? "is-completed" : isNoSafeDeferred ? "is-deferred" : undefined} key={concept.formal_concept_id}>
              <span>{index + 1}</span>
              <div>
                <strong>{concept.label}</strong>
                <small>{isCurrent ? "目前" : isCompleted ? "已完成" : isNoSafeDeferred ? "稍後回到這裡" : isNext ? "下一步" : placementReason}</small>
              </div>
            </li>
          );
        })}
      </ol>
    </section>
  );
}

function CompletedSession({ route }: { route: Extract<AppRoute, { name: "study-session" }> }) {
  return (
    <StateView
      action={<button className="primary-button" type="button" onClick={() => writeRoute({
        name: "knowledge-map",
        materialId: route.materialId,
        runId: route.runId,
        mapRevision: route.mapRevision,
      })}>回到知識地圖<Icon name="map" /></button>}
      description="這次學習已結束。回到地圖後，可以從任何概念開始新的學習。"
      image="/assets/studydy/success-jump.png"
      title="本次學習已完成"
      tone="success"
    />
  );
}

function NoSafeSession({ route }: { route: Extract<AppRoute, { name: "study-session" }> }) {
  return (
    <StateView
      action={<button className="primary-button" type="button" onClick={() => writeRoute({
        name: "knowledge-map",
        materialId: route.materialId,
        runId: route.runId,
        mapRevision: route.mapRevision,
      })}>回到知識地圖<Icon name="map" /></button>}
      description="目前沒有其他可安全前往的教材重點，這次進度已保留。"
      image="/assets/studydy/empty-disappointed.png"
      title="目前沒有可繼續的練習"
      tone="empty"
    />
  );
}

export function StudySessionPage({ apiClient, route }: {
  apiClient: StudydyApiClient;
  route: Extract<AppRoute, { name: "study-session" }>;
}) {
  const [data, setData] = useState<StudyData | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const [isCompleting, setIsCompleting] = useState(false);
  const [completeMessage, setCompleteMessage] = useState<string | null>(null);
  const [isRefreshingInsights, setIsRefreshingInsights] = useState(false);
  const [isApplyingGuidance, setIsApplyingGuidance] = useState(false);
  const [guidanceMessage, setGuidanceMessage] = useState<string | null>(null);
  const [hasNoSafeItem, setHasNoSafeItem] = useState(false);

  useEffect(() => {
    let cancelled = false;
    void Promise.all([
      apiClient.getKnowledgeMap({ materialId: route.materialId, runId: route.runId, mapRevision: route.mapRevision }),
      apiClient.getMaterialRun(route.runId),
      apiClient.getLearnerProgress(route.studySessionId),
    ]).then(
      ([view, run, progress]) => {
        if (cancelled) return;
        const next = {
          progress,
          sourceArtifactId: run.source_artifact_id,
          view,
        };
        if (run.material_id !== route.materialId) throw new Error("RUN_MATERIAL_MISMATCH");
        assertRouteBinding(route, next);
        setData(next);
        setMessage(null);
      },
      (error) => {
        if (!cancelled) setMessage(errorMessage(error));
      },
    ).catch(() => {
      if (!cancelled) setMessage("這次學習與目前教材不相符，無法安全開啟。");
    });
    return () => { cancelled = true; };
  }, [apiClient, reload, route]);

  if (message) return (
    <StateView
      action={<button className="primary-button" type="button" onClick={() => setReload((value) => value + 1)}><Icon name="refresh" />重新讀取</button>}
      description={message}
      image="/assets/studydy/failure-confused.png"
      title="無法開啟本次學習"
      tone="failure"
    />
  );
  if (!data) return (
    <StateView
      description="正在復原目前概念、教材內容與本次學習狀態。"
      live
      title="正在讀取本次學習"
      tone="loading"
    />
  );
  if (data.progress.status === "completed") return <CompletedSession route={route} />;
  if (data.progress.status === "no_safe") return <NoSafeSession route={route} />;

  const currentConcept = data.view.concepts.find((concept) =>
    concept.formal_concept_id === data.progress.current_formal_concept_id);
  if (!currentConcept) return (
    <StateView
      action={<button className="secondary-button" type="button" onClick={() => writeRoute({
        name: "knowledge-map",
        materialId: route.materialId,
        runId: route.runId,
        mapRevision: route.mapRevision,
      })}>回到知識地圖</button>}
      description="這次學習目前沒有可安全顯示的教材概念。"
      image="/assets/studydy/empty-disappointed.png"
      title="目前沒有學習內容"
      tone="empty"
    />
  );

  const complete = async () => {
    if (isCompleting) return;
    setIsCompleting(true);
    setCompleteMessage(null);
    try {
      await apiClient.completeStudySession(route.studySessionId);
      const progress = await apiClient.getLearnerProgress(route.studySessionId);
      setData((current) => current && { ...current, progress });
    } catch (error) {
      setCompleteMessage(errorMessage(error));
      setIsCompleting(false);
    }
  };

  const refreshInsights = async () => {
    if (isRefreshingInsights) return;
    setIsRefreshingInsights(true);
    try {
      const progress = await apiClient.getLearnerProgress(route.studySessionId);
      const next = { ...data, progress };
      assertRouteBinding(route, next);
      setData(next);
    } catch (error) {
      setGuidanceMessage(errorMessage(error));
    } finally {
      setIsRefreshingInsights(false);
    }
  };

  const applyCurrentGuidance = async () => {
    if (isApplyingGuidance) return;
    setIsApplyingGuidance(true);
    setGuidanceMessage(null);
    const step = data.progress.next_action;
    try {
      const progress = await apiClient.applyGuidance(route.studySessionId, {
        schema: "guidance-apply/v1",
        guidance_revision: data.progress.guidance_revision,
      });
      const next = { ...data, progress };
      assertRouteBinding(route, next);
      setData(next);
      if (step.action === "use_resource" && step.route.resource_promotion_id) {
        const resource = data.view.concepts
          .flatMap((concept) => concept.supplementary_resources)
          .find((item) => item.promotion_id === step.route.resource_promotion_id);
        const url = resource && safeExternalUrl(resource.source_url);
        if (url) window.open(url, "_blank", "noopener,noreferrer");
      }
      if (["practice", "review", "collect_more_data", "continue", "start", "defer", "resume"].includes(step.action)) {
        window.setTimeout(() => document.getElementById("assessment-panel")?.scrollIntoView({ behavior: "smooth", block: "start" }), 0);
      }
    } catch (error) {
      setGuidanceMessage(errorMessage(error));
    } finally {
      setIsApplyingGuidance(false);
    }
  };

  return (
    <section className="study-session-page">
      <header className="study-header">
        <div><p className="eyebrow">本次學習</p><h1>{currentConcept.label}</h1><p>學習狀態與作答紀錄只屬於目前這次學習。</p></div>
        <button className="secondary-button" disabled={isCompleting} type="button" onClick={() => void complete()}>
          <Icon name="check" />{isCompleting ? "正在完成…" : "完成本次學習"}
        </button>
      </header>
      {completeMessage && <p className="study-error" role="alert">{completeMessage}</p>}
      {guidanceMessage && <p className="study-error" role="alert">{guidanceMessage}</p>}
      <div className="study-layout">
        <SessionPath progress={data.progress} view={data.view} />
        <div className="study-main-column">
          <article className="surface current-concept-card">
            <div className="current-concept-copy">
              <p className="eyebrow">目前概念</p>
              <h2>{currentConcept.label}</h2>
              {currentConcept.claims.slice(0, 1).map((claim) => (
                <section className="study-claim" key={claim.claim_id}>
                  <p>{claim.text}</p>
                  <div>
                    {claim.evidence.map((evidence) => (
                      <button
                        className="text-button"
                        key={evidence.evidence_id}
                        type="button"
                        onClick={() => window.open(
                          apiClient.sourceArtifactUrl(data.sourceArtifactId, evidence.page_number),
                          "_blank",
                          "noopener,noreferrer",
                        )}
                      >原始教材第 {evidence.page_number} 頁<Icon name="chevron-right" /></button>
                    ))}
                  </div>
                </section>
              ))}
              {currentConcept.claims.length > 1 && (
                <details className="additional-claims">
                  <summary>查看另外 {currentConcept.claims.length - 1} 個教材重點</summary>
                  {currentConcept.claims.slice(1).map((claim) => (
                    <section className="study-claim" key={claim.claim_id}>
                      <p>{claim.text}</p>
                      <div>{claim.evidence.map((evidence) => (
                        <button
                          className="text-button"
                          key={evidence.evidence_id}
                          type="button"
                          onClick={() => window.open(
                            apiClient.sourceArtifactUrl(data.sourceArtifactId, evidence.page_number),
                            "_blank",
                            "noopener,noreferrer",
                          )}
                        >原始教材第 {evidence.page_number} 頁<Icon name="chevron-right" /></button>
                      ))}</div>
                    </section>
                  ))}
                </details>
              )}
            </div>
            <img src="/assets/studydy/learning-guide.png" alt="" />
          </article>

          {currentConcept.supplementary_resources.length > 0 && (
            <section className="surface study-resources">
              <h2>補充資源</h2>
              {currentConcept.supplementary_resources.map((resource) => {
                const url = safeExternalUrl(resource.source_url);
                return <article key={resource.promotion_id}><div><strong>{resource.title}</strong><p>{resource.citation}</p></div>{url && <a href={url} target="_blank" rel="noreferrer">開啟資源</a>}</article>;
              })}
            </section>
          )}

          <GuidanceNextStep
            progress={data.progress}
            view={data.view}
            hasNoSafeItem={hasNoSafeItem}
            isApplying={isApplyingGuidance || isRefreshingInsights}
            onApply={() => void applyCurrentGuidance()}
            onReviewEvidence={() => document.getElementById("assessment-panel")?.scrollIntoView({ behavior: "smooth", block: "start" })}
          />

          <LearningInsights
            currentConceptId={currentConcept.formal_concept_id}
            progress={data.progress}
          />

          <div id="assessment-panel">
            <AssessmentPanel
              apiClient={apiClient}
              concept={currentConcept}
              onNoSafeItem={(isUnavailable) => {
                setHasNoSafeItem(isUnavailable);
                if (isUnavailable) void refreshInsights();
              }}
              onReloadSession={() => window.location.reload()}
              onSubmitted={(_feedback: AnswerFeedbackView) => { void refreshInsights(); }}
              sourceArtifactId={data.sourceArtifactId}
              studySessionId={route.studySessionId}
              view={data.view}
            />
          </div>
        </div>
      </div>
    </section>
  );
}
