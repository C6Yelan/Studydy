import { SourceButton } from "../../ui/SourceButton";
import { useEffect, useRef, useState } from "react";

import { errorMessage, type StudydyApiClient } from "../../api/client";
import type { AssessmentRecordView, KnowledgeStructureView, LearnerProgressView, StudySessionView } from "../../api/contracts";
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
  records: AssessmentRecordView[];
  selectedAssessmentRevision: string | null;
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
    && (data.progress.current_concept_id === null || concepts.has(data.progress.current_concept_id))
    && data.progress.next_action.prerequisite_concept_ids.every(id => concepts.has(id));
}

export function StudySessionPage({ apiClient, route }: {
  apiClient: StudydyApiClient;
  route: Extract<AppRoute, { name: "study-session" }>;
}) {
  const [data, setData] = useState<StudyData | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [reload, setReload] = useState(0);
  const [noSafeReviewActive, setNoSafeReviewActive] = useState(false);
  const [assessmentQuestionMode, setAssessmentQuestionMode] = useState(false);
  const activePage = useRef(false);

  const load = async () => {
    const restored = await apiClient.resumeStudy(route);
    const next = { view: restored.knowledge_structure, sourceArtifactId: restored.source_artifact_id,
      session: restored.session, progress: restored.progress, records: restored.assessments,
      selectedAssessmentRevision: restored.selected_assessment_revision };
    if (!validBinding(route, next)) throw new Error("STUDY_BINDING_MISMATCH");
    return next;
  };

  useEffect(() => {
    let cancelled = false;
    activePage.current = true;
    setNoSafeReviewActive(false);
    setAssessmentQuestionMode(false);
    setData(null);
    setMessage(null);
    void load().then((next) => {
      if (cancelled) return;
      setData(next);

    }, (error) => { if (!cancelled) setMessage(errorMessage(error)); });
    return () => { cancelled = true; activePage.current = false; };
  }, [apiClient, reload, route]);

  const refresh = async (activity?: "answer" | "no_safe") => {
    try {
      const next = await load();
      if (!activePage.current) return;
      setData(next);
      if (activity === "answer" && next.progress.next_action.action !== "assess") {
        writeRoute({ ...route, assessmentRevision: undefined }, true);
      }
    } catch (error) { setMessage(errorMessage(error)); }
  };

  const back = () => writeRoute({
    name: "knowledge-map",
    materialId: route.materialId,
    runId: route.runId,
    structureRevision: route.structureRevision,
  });

  if (message) return <StateView action={<><button className="primary-button" type="button" onClick={() => setReload((value) => value + 1)}><Icon name="refresh" />重新讀取</button><button className="secondary-button" type="button" onClick={() => writeRoute({ name: "materials" })}>返回教材庫</button></>} description={message} image="/assets/studydy/failure-confused.png" title="無法開啟學習進度" tone="failure" />;
  if (!data) return <StateView description="正在復原教材結構與學習進度狀態。" live title="正在讀取學習進度" tone="loading" />;

  const completed = data.session.status === "completed";
  // Only explicit history navigation writes an assessment revision into the route.
  const isHistorical = route.assessmentRevision !== undefined;
  const selectedRecord = data.records.find(record => record.assessment.assessment_revision === data.selectedAssessmentRevision) ?? null;
  const current = data.view.concepts.find((concept) => concept.concept_id === data.progress.current_concept_id);
  if (!current) return <StateView action={<button className="secondary-button" type="button" onClick={back}>回到知識地圖</button>} description="目前沒有可安全顯示的教材概念。" image="/assets/studydy/empty-disappointed.png" title="目前沒有學習內容" tone="empty" />;

  const apply = async () => {
    if (busy) return;
    setBusy(true);
    try {
      await apiClient.applyGuidance(route.studySessionId, { schema: "guidance-apply/v2", guidance_revision: data.progress.guidance_revision });
      writeRoute({ ...route, assessmentRevision: undefined }, true);
    } catch (error) { setMessage(errorMessage(error)); }
    finally { setBusy(false); }
  };

  const nextAction = data.progress.next_action;
  const assessmentTargetClaimId = nextAction.action === "assess"
    && nextAction.target_concept_id === current.concept_id
    && current.claims.some(claim => claim.claim_id === nextAction.target_claim_id)
    ? nextAction.target_claim_id : null;
  const prerequisiteConcepts = nextAction.action === "assess" && nextAction.target_concept_id === current.concept_id
    ? nextAction.prerequisite_concept_ids.map(id => data.view.concepts.find(concept => concept.concept_id === id)!) : [];
  const showAssessment = completed || isHistorical || nextAction.action === "assess" || noSafeReviewActive;
  const position = data.view.initial_learning_path.find(step => step.concept_id === data.progress.current_concept_id)?.position;
  const sourcePages = [...new Set(current.claims.flatMap(claim => claim.evidence.map(evidence => evidence.page)))];
  return (
    <section className="study-session-page">
      <header className="study-header">
        <div><p className="eyebrow">學習進度</p><h1>{completed ? "學習已完成" : current.label}</h1>
          <p>{position !== undefined && `第 ${position} / ${data.view.initial_learning_path.length} 個概念 · `}學習進度會自動保存。</p></div>
      </header>
      <div className="study-workspace">
        <div className="study-main">
          <div className={`study-learning-grid${assessmentQuestionMode ? " is-question-mode" : ""}`}>
            {!assessmentQuestionMode && <article className="surface current-concept-card" aria-labelledby="study-content-title">
              <p className="eyebrow">教材重點</p><h2 id="study-content-title">{current.label}</h2>
              <ul className="study-claims">{current.claims.map(claim => <li key={claim.claim_id}>{claim.text}</li>)}</ul>
              <section className="study-sources" aria-label="教材來源"><h3>教材來源</h3><div>
                {sourcePages.map(page => <SourceButton key={page} apiClient={apiClient} artifactId={data.sourceArtifactId} page={page} resolver={data.view.source_resolver} evidenceId={current.claims.flatMap(c=>c.evidence).find(e=>e.page===page)?.evidence_id}>第 {page} 頁<Icon name="chevron-right" /></SourceButton>)}
              </div></section>
            </article>}
            <div className="study-current-action" id="assessment-panel">
              {showAssessment ? <AssessmentPanel
                key={`${data.session.study_session_id}/${current.concept_id}/${selectedRecord?.assessment.assessment_revision ?? "new"}/${selectedRecord?.feedback?.answer_event_id ?? "unanswered"}`}
                apiClient={apiClient}
                record={selectedRecord}
                completed={completed}
                isHistorical={isHistorical}
                historyQuestionNumber={selectedRecord ? data.records.length - data.records.indexOf(selectedRecord) : null}
                onReturnLatest={() => writeRoute({ ...route, assessmentRevision: undefined }, true)}
                onAssessmentCreated={() => writeRoute({ ...route, assessmentRevision: undefined }, true)}
                concept={current}
                assessmentTargetClaimId={assessmentTargetClaimId}
                assessmentTargetInvalid={nextAction.action === "assess" && assessmentTargetClaimId === null}
                prerequisiteConcepts={prerequisiteConcepts}
                onNoSafeReviewChange={active => {
                  setNoSafeReviewActive(active);
                  if (!active && route.assessmentRevision && nextAction.action !== "assess") {
                    writeRoute({ ...route, assessmentRevision: undefined }, true);
                  }
                }}
                onQuestionModeChange={setAssessmentQuestionMode}
                onProgressChanged={refresh}
                onReloadSession={() => { void refresh(); }}
                sourceArtifactId={data.sourceArtifactId}
                studySessionId={route.studySessionId}
                view={data.view}
              /> : <GuidanceNextStep progress={data.progress} view={data.view} isApplying={busy} onApply={() => void apply()} />}
            </div>
          </div>
        </div>
        <aside className="study-rail" aria-label="學習資訊">
          <LearningInsights currentConceptId={current.concept_id} totalClaimCount={current.claims.length} progress={data.progress} />
          {data.records.length > 0 && <details className="surface study-record-picker">
            <summary>題目與作答紀錄（{data.records.length}）</summary>
            <ol className="study-history-list" aria-label="題目與作答紀錄">
              {data.records.map((record, index) => {
                const result = record.feedback ? record.feedback.is_correct ? "答對" : "答錯" : "未作答";
                const number = data.records.length - index;
                return <li key={record.assessment.assessment_revision}>
                  <button className="study-history-row" type="button" disabled={busy}
                    aria-current={isHistorical && route.assessmentRevision === record.assessment.assessment_revision ? "true" : undefined}
                    onClick={() => writeRoute({ ...route, assessmentRevision: record.assessment.assessment_revision })}>
                    <span className="study-history-number">第 {number} 題</span>
                    <span className="study-history-prompt">{record.assessment.prompt}</span>
                    <span className={`study-history-status is-${record.feedback ? record.feedback.is_correct ? "correct" : "incorrect" : "unanswered"}`}>{result}</span>
                    <span className="study-history-chevron" aria-hidden="true"><Icon name="chevron-right" size={18} /></span>
                  </button>
                </li>;
              })}
            </ol>
          </details>}
        </aside>
      </div>
    </section>
  );
}
