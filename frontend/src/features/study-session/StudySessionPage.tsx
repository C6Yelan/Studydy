import { useEffect, useState } from "react";

import { errorMessage, type StudydyApiClient } from "../../api/client";
import type {
  AdaptiveResponseView,
  AnswerFeedbackView,
  KnowledgeMapView,
  LearningStateView,
  StudyContextView,
  StudySessionView,
  WeaknessView,
} from "../../api/contracts";
import { writeRoute, type AppRoute } from "../../app/routes";
import { Icon } from "../../ui/Icon";
import { StateView } from "../../ui/StateView";
import { AssessmentPanel } from "../assessment/AssessmentPanel";
import { AdaptiveNextStep } from "../adaptive-learning/AdaptiveNextStep";
import { learningPathReason, safeExternalUrl } from "../knowledge-map/knowledge-map";
import { LearningInsights } from "../learning-state/LearningInsights";
import "./styles.css";

type StudyData = {
  adaptive: AdaptiveResponseView;
  context: StudyContextView;
  learningState: LearningStateView;
  session: StudySessionView;
  sourceArtifactId: string;
  view: KnowledgeMapView;
  weakness: WeaknessView;
};

function assertRouteBinding(route: Extract<AppRoute, { name: "study-session" }>, data: StudyData) {
  const conceptIds = data.view.concepts.map((concept) => concept.formal_concept_id);
  const contextIds = data.context.initial_learning_path.map((concept) => concept.formal_concept_id);
  const stateIds = data.learningState.concept_states.map((state) => state.formal_concept_id);
  const contextMatchesMap = data.context.initial_learning_path.every((contextConcept) => {
    const mapConcept = data.view.concepts.find((concept) => concept.formal_concept_id === contextConcept.formal_concept_id);
    return !!mapConcept
      && mapConcept.label === contextConcept.label
      && JSON.stringify(mapConcept.claims.map((claim) => claim.claim_id)) === JSON.stringify(contextConcept.claim_ids)
      && JSON.stringify(mapConcept.supplementary_resources.map((resource) => resource.promotion_id))
        === JSON.stringify(contextConcept.supplementary_resource_promotion_ids);
  });
  const gapsUsePublishedPrerequisites = data.weakness.immediate_prerequisite_gaps.every((gap) => {
    const relation = data.view.relations.find((item) => item.relation_id === gap.relation_id);
    return relation?.type === "prerequisite"
      && relation.source_formal_concept_id === gap.prerequisite_formal_concept_id
      && relation.target_formal_concept_id === gap.target_formal_concept_id
      && !relation.is_in_prerequisite_cycle;
  });
  const adaptiveTarget = data.adaptive.plan.primary_step.target_formal_concept_id;
  const adaptiveResource = data.adaptive.plan.primary_step.route.resource_promotion_id;
  if (
    data.session.study_session_id !== route.studySessionId
    || data.session.material_id !== route.materialId
    || data.session.knowledge_map_revision !== route.mapRevision
    || data.context.study_session_id !== route.studySessionId
    || data.context.base_knowledge_map_revision !== route.mapRevision
    || data.view.knowledge_map_revision !== route.mapRevision
    || data.context.current_formal_concept_id !== data.session.current_formal_concept_id
    || data.context.deferred_formal_concept_id !== data.session.deferred_formal_concept_id
    || data.learningState.study_session_id !== route.studySessionId
    || data.learningState.base_knowledge_map_revision !== route.mapRevision
    || data.learningState.event_watermark !== data.session.event_watermark
    || data.weakness.study_session_id !== route.studySessionId
    || data.weakness.base_knowledge_map_revision !== route.mapRevision
    || data.weakness.source_learning_state_revision !== data.learningState.state_revision
    || data.weakness.event_watermark !== data.learningState.event_watermark
    || data.weakness.current_formal_concept_id !== data.session.current_formal_concept_id
    || data.adaptive.plan.study_session_id !== route.studySessionId
    || data.adaptive.plan.base_knowledge_map_revision !== route.mapRevision
    || data.adaptive.plan.source_learning_state_revision !== data.learningState.state_revision
    || data.adaptive.plan.event_watermark !== data.learningState.event_watermark
    || JSON.stringify(contextIds) !== JSON.stringify(data.view.initial_learning_path)
    || !contextMatchesMap
    || new Set(stateIds).size !== conceptIds.length
    || !conceptIds.every((id) => stateIds.includes(id))
    || (data.session.current_formal_concept_id !== null && !conceptIds.includes(data.session.current_formal_concept_id))
    || (data.session.deferred_formal_concept_id !== null && !conceptIds.includes(data.session.deferred_formal_concept_id))
    || data.weakness.findings.some((finding) => !conceptIds.includes(finding.target_formal_concept_id))
    || !gapsUsePublishedPrerequisites
    || (adaptiveTarget !== null && !conceptIds.includes(adaptiveTarget))
    || (adaptiveResource !== null && !data.view.concepts.some((concept) =>
      concept.supplementary_resources.some((resource) => resource.promotion_id === adaptiveResource)))
  ) throw new Error("STUDY_ROUTE_BINDING_MISMATCH");
}

function SessionPath({ context, learningState, view }: {
  context: StudyContextView;
  learningState: LearningStateView;
  view: KnowledgeMapView;
}) {
  const mastered = new Set(learningState.concept_states
    .filter((state) => state.status === "mastered")
    .map((state) => state.formal_concept_id));
  const currentIndex = context.initial_learning_path.findIndex((concept) =>
    concept.formal_concept_id === context.current_formal_concept_id);
  const nextConceptId = context.initial_learning_path
    .slice(currentIndex + 1)
    .find((concept) => !mastered.has(concept.formal_concept_id))?.formal_concept_id;
  return (
    <section className="surface session-path" aria-labelledby="session-path-title">
      <p className="eyebrow">你的學習方向</p>
      <h2 id="session-path-title">教材建議學習順序</h2>
      <p>作答會更新本次進度，但不會改寫這份教材順序。</p>
      <ol>
        {context.initial_learning_path.map((concept, index) => {
          const isCurrent = concept.formal_concept_id === context.current_formal_concept_id;
          const isDeferred = concept.formal_concept_id === context.deferred_formal_concept_id;
          const isCompleted = mastered.has(concept.formal_concept_id);
          const isNext = concept.formal_concept_id === nextConceptId;
          return (
            <li className={isCurrent ? "is-current" : isCompleted ? "is-completed" : isDeferred ? "is-deferred" : undefined} key={concept.formal_concept_id}>
              <span>{index + 1}</span>
              <div>
                <strong>{concept.label}</strong>
                <small>{isCurrent ? "目前" : isCompleted ? "已完成" : isDeferred ? "稍後回到這裡" : isNext ? "下一步" : learningPathReason(view, concept.formal_concept_id)}</small>
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
  const [isApplyingPlan, setIsApplyingPlan] = useState(false);
  const [adaptiveMessage, setAdaptiveMessage] = useState<string | null>(null);
  const [hasNoSafeItem, setHasNoSafeItem] = useState(false);

  useEffect(() => {
    let cancelled = false;
    void Promise.all([
      apiClient.getStudySession(route.studySessionId),
      apiClient.getStudyContext(route.studySessionId),
      apiClient.getKnowledgeMap({ materialId: route.materialId, runId: route.runId, mapRevision: route.mapRevision }),
      apiClient.getMaterialRun(route.runId),
      apiClient.getLearningState(route.studySessionId),
      apiClient.getWeakness(route.studySessionId),
      apiClient.getAdaptivePlan(route.studySessionId),
    ]).then(
      ([session, context, view, run, learningState, weakness, adaptive]) => {
        if (cancelled) return;
        const next = {
          adaptive,
          context,
          learningState,
          session,
          sourceArtifactId: run.source_artifact_id,
          view,
          weakness,
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
  if (data.session.status === "completed") return <CompletedSession route={route} />;

  const currentConcept = data.view.concepts.find((concept) =>
    concept.formal_concept_id === data.session.current_formal_concept_id);
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
      const session = await apiClient.completeStudySession(route.studySessionId);
      setData((current) => current && { ...current, session });
    } catch (error) {
      setCompleteMessage(errorMessage(error));
      setIsCompleting(false);
    }
  };

  const refreshInsights = async () => {
    if (isRefreshingInsights) return;
    setIsRefreshingInsights(true);
    try {
      const [session, context, learningState, weakness, adaptive] = await Promise.all([
        apiClient.getStudySession(route.studySessionId),
        apiClient.getStudyContext(route.studySessionId),
        apiClient.getLearningState(route.studySessionId),
        apiClient.getWeakness(route.studySessionId),
        apiClient.getAdaptivePlan(route.studySessionId),
      ]);
      const next = { ...data, adaptive, context, learningState, session, weakness };
      assertRouteBinding(route, next);
      setData(next);
    } catch (error) {
      setAdaptiveMessage(errorMessage(error));
    } finally {
      setIsRefreshingInsights(false);
    }
  };

  const applyPlan = async () => {
    if (isApplyingPlan) return;
    setIsApplyingPlan(true);
    setAdaptiveMessage(null);
    const step = data.adaptive.plan.primary_step;
    try {
      await apiClient.applyAdaptivePlan(route.studySessionId, {
        schema: "adaptive-plan-apply/v1",
        adaptive_plan_revision: data.adaptive.plan.adaptive_plan_revision,
      });
      await refreshInsights();
      if (step.action === "use_resource" && step.route.resource_promotion_id) {
        const resource = data.view.concepts
          .flatMap((concept) => concept.supplementary_resources)
          .find((item) => item.promotion_id === step.route.resource_promotion_id);
        const url = resource && safeExternalUrl(resource.source_url);
        if (url) window.open(url, "_blank", "noopener,noreferrer");
      }
      if (["practice", "review", "collect_more_data", "continue", "start"].includes(step.action)) {
        window.setTimeout(() => document.getElementById("assessment-panel")?.scrollIntoView({ behavior: "smooth", block: "start" }), 0);
      }
    } catch (error) {
      setAdaptiveMessage(errorMessage(error));
    } finally {
      setIsApplyingPlan(false);
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
      {adaptiveMessage && <p className="study-error" role="alert">{adaptiveMessage}</p>}
      <div className="study-layout">
        <SessionPath context={data.context} learningState={data.learningState} view={data.view} />
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

          <AdaptiveNextStep
            adaptive={data.adaptive}
            context={data.context}
            hasNoSafeItem={hasNoSafeItem}
            isApplying={isApplyingPlan || isRefreshingInsights}
            onApply={() => void applyPlan()}
            onReviewEvidence={() => document.getElementById("assessment-panel")?.scrollIntoView({ behavior: "smooth", block: "start" })}
          />

          <LearningInsights
            currentConceptId={currentConcept.formal_concept_id}
            learningState={data.learningState}
            weakness={data.weakness}
          />

          <div id="assessment-panel">
            <AssessmentPanel
              apiClient={apiClient}
              attemptedClaimIds={data.learningState.concept_states.find((state) =>
                state.formal_concept_id === currentConcept.formal_concept_id)?.attempted_claim_ids ?? []}
              concept={currentConcept}
              onNoSafeItem={setHasNoSafeItem}
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
