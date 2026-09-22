import { SourceButton, sourceLinks } from "../../ui/SourceButton";
import { useEffect, useRef, useState } from "react";

import { errorMessage, type StudydyApiClient } from "../../api/client";
import type { AssessmentSetSummary, KnowledgeStructureView, LearnerProgressView, StudySessionView } from "../../api/contracts";
import { writeRoute, type AppRoute } from "../../app/routes";
import { Icon } from "../../ui/Icon";
import { StateView } from "../../ui/StateView";
import { assessmentPhase, type AssessmentPhase } from "../assessment/assessment-phase";
import { AssessmentSetPanel } from "../assessment/AssessmentSetPanel";
import { LearningInsights } from "../learning-state/LearningInsights";
import "./styles.css";

type StudyData = {
  progress: LearnerProgressView;
  session: StudySessionView;
  view: KnowledgeStructureView;
  assessmentSets: AssessmentSetSummary[];
  selectedSetId: string | null;
};

function validBinding(route: Extract<AppRoute, { name: "study-session" }>, data: StudyData): boolean {
  const concepts = new Set(data.view.concepts.map((concept) => concept.concept_id));
  return data.view.knowledge_structure_revision === route.structureRevision
    && data.session.knowledge_structure_revision === route.structureRevision
    && data.progress.knowledge_structure_revision === route.structureRevision
    && data.session.study_session_id === route.studySessionId
    && data.progress.study_session_id === route.studySessionId
    && data.session.material_id === route.materialId
    && data.assessmentSets.every(group => concepts.has(group.target_concept_id))
    && data.progress.assessment_cycles.every(cycle => concepts.has(cycle.concept_id))
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
  const [refreshMessage, setRefreshMessage] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const [setPhase, setSetPhase] = useState<AssessmentPhase | null>(null);
  const activePage = useRef(false);
  const pageVersion = useRef(0);

  const load = async () => {
    const restored = await apiClient.resumeStudy(route);
    const next = { view: restored.knowledge_structure,
      session: restored.session, progress: restored.progress,
      assessmentSets: restored.assessment_sets, selectedSetId: restored.selected_set_id };
    if (!validBinding(route, next)) throw new Error("STUDY_BINDING_MISMATCH");
    return next;
  };

  useEffect(() => {
    let cancelled = false;
    activePage.current = true;
    pageVersion.current += 1;
    setSetPhase(null);
    setData(null);
    setMessage(null);
    setRefreshMessage(null);
    void load().then((next) => {
      if (cancelled) return;
      if (next.selectedSetId && !route.assessmentSetId) {
        writeRoute({ ...route, assessmentSetId: next.selectedSetId }, true);
        return;
      }
      setData(next);

    }, (error) => { if (!cancelled) setMessage(errorMessage(error)); });
    return () => { cancelled = true; activePage.current = false; };
  }, [apiClient, reload, route]);

  const refresh = async () => {
    const version = pageVersion.current;
    try {
      const next = await load();
      if (!activePage.current || pageVersion.current !== version) return;
      setData(next);
      setRefreshMessage(null);

    } catch (error) { if (pageVersion.current === version) setRefreshMessage(errorMessage(error)); }
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
  const selectedGroup = data.assessmentSets.find(group => group.set_id === data.selectedSetId);
  const visibleConceptId = route.assessmentSetId ? selectedGroup?.target_concept_id : data.progress.current_concept_id;
  const current = data.view.concepts.find((concept) => concept.concept_id === visibleConceptId);
  if (!current) return <StateView action={<button className="secondary-button" type="button" onClick={back}>回到知識地圖</button>} description="目前沒有可安全顯示的教材概念。" image="/assets/studydy/empty-disappointed.png" title="目前沒有學習內容" tone="empty" />;

  const initialSetPhase = assessmentPhase(selectedGroup);
  const layoutMode = setPhase ?? initialSetPhase;
  const preparing = layoutMode === "preparing" || layoutMode === "intervention";
  const position = data.view.initial_learning_path.find(step => step.concept_id === current.concept_id)?.position;
  const sourceEvidence = sourceLinks(current.claims.flatMap(claim => claim.evidence));
  const materialCard = <article className="surface current-concept-card" aria-labelledby="study-content-title">
              <p className="eyebrow">教材重點</p><h2 id="study-content-title">{current.label}</h2>
              <ul className="study-claims">{current.claims.map(claim => <li key={claim.claim_id}>{claim.text}</li>)}</ul>
              <section className="study-sources" aria-label="教材來源"><h3>教材來源</h3><div>
                {sourceEvidence.map(evidence => <SourceButton key={evidence.evidence_id} apiClient={apiClient} resolver={data.view.source_resolver} evidence={evidence} />)}
              </div></section>
            </article>;
  return (
    <section className="study-session-page">
      <header className="study-header">
        <div><p className="eyebrow">學習進度</p><h1>{completed ? "學習已完成" : current.label}</h1>
          <p>{position !== undefined && `第 ${position} / ${data.view.initial_learning_path.length} 個概念`}{!preparing && layoutMode !== "question" && " · 學習進度會自動保存。"}</p></div>
      </header>
      {refreshMessage && <div className="assessment-error" role="alert">{refreshMessage}<button className="text-button" onClick={() => void refresh()}>重新讀取</button></div>}
      <div className={`study-workspace is-${layoutMode}-mode${preparing ? " without-rail" : ""}`}>
        <div className="study-main">
          <div className={`study-learning-grid is-${layoutMode}-mode is-set-mode`}>
            {!preparing && layoutMode !== "question" && (layoutMode === "preparation" ? materialCard : <details className="surface study-material-summary">
              <summary>教材重點與來源</summary>{materialCard}
            </details>)}
            <div className="study-current-action" id="assessment-panel">
              <AssessmentSetPanel apiClient={apiClient} studySessionId={route.studySessionId}
                selectedSetId={data.selectedSetId} concept={current} view={data.view}
                completed={completed} onSetSelected={id => writeRoute({ ...route, assessmentSetId: id })}
                onProgressChanged={() => refresh()} onBackToMap={back} initialPhase={initialSetPhase}
                onPhaseChange={setSetPhase} />
            </div>
          </div>
        </div>
        {!preparing && <aside className="study-rail" aria-label="學習資訊">
          <LearningInsights currentConceptId={current.concept_id} totalClaimCount={current.claims.length} progress={data.progress} />
          {data.assessmentSets.length > 0 && <details className="surface assessment-set-history">
            <summary>觀念題組紀錄（{data.assessmentSets.length}）</summary><ol>{data.assessmentSets.map((group, index) => {
              const label = data.view.concepts.find(concept => concept.concept_id === group.target_concept_id)?.label ?? "觀念";
              return <li key={group.set_id}><button className="text-button" aria-current={group.set_id === data.selectedSetId ? "true" : undefined}
                onClick={() => writeRoute({ ...route, assessmentSetId: group.set_id })}>
                第 {data.assessmentSets.length - index} 組 · {group.kind === "remediation" ? "補強" : "初篩"} · {label} · 已答 {group.answered_count}/{group.published_count}</button></li>;
            })}</ol>
          </details>}
        </aside>}
      </div>
    </section>
  );
}
