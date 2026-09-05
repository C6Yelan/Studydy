import { useEffect, useState } from "react";

import { errorMessage, type StudydyApiClient } from "../../api/client";
import type { AnswerFeedbackView, KnowledgeStructureView, LearnerProgressView, StudySessionView } from "../../api/contracts";
import { writeRoute, type AppRoute } from "../../app/routes";
import { Icon } from "../../ui/Icon";
import { StateView } from "../../ui/StateView";
import { AssessmentPanel } from "../assessment/AssessmentPanel";
import { GuidanceNextStep } from "../adaptive-learning/AdaptiveNextStep";
import { LearningInsights } from "../learning-state/LearningInsights";
import "./styles.css";

type StudyData = {
  progress: LearnerProgressView;
  session: StudySessionView;
  sourceArtifactId: string;
  view: KnowledgeStructureView;
};

function validBinding(route: Extract<AppRoute, { name: "study-session" }>, data: StudyData): boolean {
  const concepts = new Set(data.view.concepts.map((concept) => concept.concept_id));
  return data.view.knowledge_structure_revision === route.structureRevision
    && data.session.knowledge_structure_revision === route.structureRevision
    && data.progress.knowledge_structure_revision === route.structureRevision
    && data.session.study_session_id === route.studySessionId
    && data.progress.study_session_id === route.studySessionId
    && data.session.material_id === route.materialId
    && data.progress.concept_states.length === concepts.size
    && data.progress.concept_states.every((state) => concepts.has(state.concept_id))
    && (data.progress.current_concept_id === null || concepts.has(data.progress.current_concept_id));
}

function SessionPath({ progress, view }: { progress: LearnerProgressView; view: KnowledgeStructureView }) {
  const mastered = new Set(progress.concept_states.filter((state) => state.status === "mastered").map((state) => state.concept_id));
  const deferred = new Set(progress.deferred_concept_ids);
  return (
    <section className="surface session-path" aria-labelledby="session-path-title">
      <p className="eyebrow">你的學習方向</p>
      <h2 id="session-path-title">教材建議學習順序</h2>
      <p>只有教材中的 prerequisite 關係能調整這份順序。</p>
      <ol>
        {view.initial_learning_path.map((step) => {
          const concept = view.concepts.find((item) => item.concept_id === step.concept_id)!;
          const current = step.concept_id === progress.current_concept_id;
          return (
            <li className={current ? "is-current" : mastered.has(step.concept_id) ? "is-completed" : deferred.has(step.concept_id) ? "is-deferred" : undefined} key={step.concept_id}>
              <span>{step.position}</span>
              <div><strong>{concept.label}</strong><small>{current ? "目前" : mastered.has(step.concept_id) ? "已掌握" : deferred.has(step.concept_id) ? "稍後回來" : step.reason === "prerequisite" ? "依前置概念安排" : "依教材順序"}</small></div>
            </li>
          );
        })}
      </ol>
    </section>
  );
}

export function StudySessionPage({ apiClient, route }: {
  apiClient: StudydyApiClient;
  route: Extract<AppRoute, { name: "study-session" }>;
}) {
  const [data, setData] = useState<StudyData | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [reload, setReload] = useState(0);

  const load = async () => {
    const [view, run, session, progress] = await Promise.all([
      apiClient.getKnowledgeStructure({ materialId: route.materialId, structureRevision: route.structureRevision }),
      apiClient.getMaterialRun(route.runId),
      apiClient.getStudySession(route.studySessionId),
      apiClient.getLearnerProgress(route.studySessionId),
    ]);
    const next = { view, sourceArtifactId: run.source_artifact_id, session, progress };
    if (run.material_id !== route.materialId || !validBinding(route, next)) throw new Error("STUDY_BINDING_MISMATCH");
    return next;
  };

  useEffect(() => {
    let cancelled = false;
    void load().then((next) => { if (!cancelled) { setData(next); setMessage(null); } }, (error) => { if (!cancelled) setMessage(errorMessage(error)); });
    return () => { cancelled = true; };
  }, [apiClient, reload, route]);

  const refresh = async () => {
    try { setData(await load()); } catch (error) { setMessage(errorMessage(error)); }
  };

  const back = () => writeRoute({
    name: "knowledge-map",
    materialId: route.materialId,
    runId: route.runId,
    structureRevision: route.structureRevision,
  });

  if (message) return <StateView action={<button className="primary-button" type="button" onClick={() => setReload((value) => value + 1)}><Icon name="refresh" />重新讀取</button>} description={message} image="/assets/studydy/failure-confused.png" title="無法開啟本次學習" tone="failure" />;
  if (!data) return <StateView description="正在復原教材結構與本次學習狀態。" live title="正在讀取本次學習" tone="loading" />;
  if (data.session.status === "completed") return <StateView action={<button className="primary-button" type="button" onClick={back}>回到知識地圖</button>} description="進度與作答紀錄已保存。" image="/assets/studydy/success-jump.png" title="本次學習已完成" tone="success" />;

  const current = data.view.concepts.find((concept) => concept.concept_id === data.progress.current_concept_id);
  if (!current) return <StateView action={<button className="secondary-button" type="button" onClick={back}>回到知識地圖</button>} description="目前沒有可安全顯示的教材概念。" image="/assets/studydy/empty-disappointed.png" title="目前沒有學習內容" tone="empty" />;

  const apply = async () => {
    if (busy) return;
    setBusy(true);
    try {
      await apiClient.applyGuidance(route.studySessionId, { schema: "guidance-apply/v2", guidance_revision: data.progress.guidance_revision });
      await refresh();
    } catch (error) { setMessage(errorMessage(error)); }
    finally { setBusy(false); }
  };

  const complete = async () => {
    if (busy) return;
    setBusy(true);
    try { await apiClient.completeStudySession(route.studySessionId); await refresh(); }
    catch (error) { setMessage(errorMessage(error)); }
    finally { setBusy(false); }
  };

  return (
    <section className="study-session-page">
      <header className="study-header">
        <div><p className="eyebrow">本次學習</p><h1>{current.label}</h1><p>指引只更新本次 Session，不會改寫教材 Map 或 Path。</p></div>
        <button className="secondary-button" disabled={busy} type="button" onClick={() => void complete()}><Icon name="check" />完成本次學習</button>
      </header>
      <div className="study-layout">
        <SessionPath progress={data.progress} view={data.view} />
        <div className="study-main-column">
          <article className="surface current-concept-card">
            <div className="current-concept-copy">
              <p className="eyebrow">目前概念</p><h2>{current.label}</h2>
              {current.claims.map((claim) => (
                <section className="study-claim" key={claim.claim_id}>
                  <p>{claim.text}</p>
                  <div>{claim.evidence.map((evidence) => (
                    <button className="text-button" key={evidence.evidence_id} type="button" onClick={() => window.open(apiClient.sourceArtifactUrl(data.sourceArtifactId, evidence.page), "_blank", "noopener,noreferrer")}>原始教材第 {evidence.page} 頁<Icon name="chevron-right" /></button>
                  ))}</div>
                </section>
              ))}
            </div>
            <img src="/assets/studydy/learning-guide.png" alt="" />
          </article>


          <GuidanceNextStep progress={data.progress} view={data.view} isApplying={busy} onApply={() => void apply()} />
          <LearningInsights currentConceptId={current.concept_id} progress={data.progress} />
          <div id="assessment-panel">
            <AssessmentPanel
              apiClient={apiClient}
              concept={current}
              onNoSafeItem={(isUnavailable) => { if (isUnavailable) void refresh(); }}
              onReloadSession={() => { void refresh(); }}
              onSubmitted={(_feedback: AnswerFeedbackView) => { void refresh(); }}
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
