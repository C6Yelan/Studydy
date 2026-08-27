import { useEffect, useState } from "react";

import { errorMessage, type StudydyApiClient } from "../../api/client";
import type { KnowledgeMapView, StudyContextView, StudySessionView } from "../../api/contracts";
import { writeRoute, type AppRoute } from "../../app/routes";
import { Icon } from "../../ui/Icon";
import { StateView } from "../../ui/StateView";
import { AssessmentPanel } from "../assessment/AssessmentPanel";
import { safeExternalUrl } from "../knowledge-map/knowledge-map";
import "./styles.css";

type StudyData = {
  context: StudyContextView;
  session: StudySessionView;
  sourceArtifactId: string;
  view: KnowledgeMapView;
};

function assertRouteBinding(route: Extract<AppRoute, { name: "study-session" }>, data: StudyData) {
  if (
    data.session.study_session_id !== route.studySessionId
    || data.session.material_id !== route.materialId
    || data.session.knowledge_map_revision !== route.mapRevision
    || data.context.study_session_id !== route.studySessionId
    || data.context.base_knowledge_map_revision !== route.mapRevision
    || data.view.knowledge_map_revision !== route.mapRevision
    || data.context.current_formal_concept_id !== data.session.current_formal_concept_id
    || data.context.deferred_formal_concept_id !== data.session.deferred_formal_concept_id
  ) throw new Error("STUDY_ROUTE_BINDING_MISMATCH");
}

function SessionPath({ context }: { context: StudyContextView }) {
  return (
    <section className="surface session-path" aria-labelledby="session-path-title">
      <p className="eyebrow">Canonical map</p>
      <h2 id="session-path-title">教材建議學習順序</h2>
      <p>這份順序來自 Knowledge Map，不會因本次作答而改寫。</p>
      <ol>
        {context.initial_learning_path.map((concept, index) => {
          const isCurrent = concept.formal_concept_id === context.current_formal_concept_id;
          const isDeferred = concept.formal_concept_id === context.deferred_formal_concept_id;
          return (
            <li className={isCurrent ? "is-current" : isDeferred ? "is-deferred" : undefined} key={concept.formal_concept_id}>
              <span>{index + 1}</span>
              <div><strong>{concept.label}</strong>{isCurrent && <small>目前概念</small>}{isDeferred && <small>稍後回到這裡</small>}</div>
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
      description="這次學習已結束。結果只屬於這次 StudySession；回到地圖後可以開始一個全新的本次學習。"
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

  useEffect(() => {
    let cancelled = false;
    void Promise.all([
      apiClient.getStudySession(route.studySessionId),
      apiClient.getStudyContext(route.studySessionId),
      apiClient.getKnowledgeMap({ materialId: route.materialId, runId: route.runId, mapRevision: route.mapRevision }),
      apiClient.getMaterialRun(route.runId),
    ]).then(
      ([session, context, view, run]) => {
        if (cancelled) return;
        const next = { context, session, sourceArtifactId: run.source_artifact_id, view };
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
      description="這個 StudySession 目前沒有可安全顯示的學習概念。"
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

  return (
    <section className="study-session-page">
      <header className="study-header">
        <div><p className="eyebrow">本次學習</p><h1>{currentConcept.label}</h1><p>學習狀態與作答紀錄只屬於目前這次學習。</p></div>
        <button className="secondary-button" disabled={isCompleting} type="button" onClick={() => void complete()}>
          <Icon name="check" />{isCompleting ? "正在完成…" : "完成本次學習"}
        </button>
      </header>
      {completeMessage && <p className="study-error" role="alert">{completeMessage}</p>}
      <div className="study-layout">
        <SessionPath context={data.context} />
        <div className="study-main-column">
          <article className="surface current-concept-card">
            <div className="current-concept-copy">
              <p className="eyebrow">目前概念</p>
              <h2>{currentConcept.label}</h2>
              {currentConcept.claims.map((claim) => (
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

          <AssessmentPanel
            apiClient={apiClient}
            concept={currentConcept}
            onSubmitted={() => undefined}
            sourceArtifactId={data.sourceArtifactId}
            studySessionId={route.studySessionId}
            view={data.view}
          />
        </div>
      </div>
    </section>
  );
}
