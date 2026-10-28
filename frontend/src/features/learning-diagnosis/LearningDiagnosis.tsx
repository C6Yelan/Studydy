import { MathText } from "../../ui/MathText";
import { ExportControls } from "../exports/ExportControls";
import { useCallback, useEffect, useState } from "react";
import { errorMessage, type StudydyApiClient } from "../../api/client";
import type { ConceptDifficulty, DiagnosisFinding, MapProgressView, KnowledgeOutlineView } from "../../api/contracts";
import { SourceButton, sourceLinks } from "../../ui/SourceButton";
import "./styles.css";

function useReport<T>(read: () => Promise<T>) {
  const [result, setResult] = useState<{ read: typeof read; data: T | null; error: string | null } | null>(null);
  const [reload, setReload] = useState(0);
  useEffect(() => {
    let cancelled = false;
    setResult(null);
    const load = async () => {
      try {
        const data = await read();
        if (cancelled) return;
        setResult({ read, data, error: null });
      } catch (error) {
        if (!cancelled) setResult({ read, data: null, error: errorMessage(error) });
      }
    };
    void load();
    return () => { cancelled = true; };
  }, [read, reload]);
  return { data: result?.read === read ? result.data : null, error: result?.read === read ? result.error : null,
    retry: () => setReload(value => value + 1) };
}

export function useConceptDifficulty(api: StudydyApiClient, materialId: string, revision: string) {
  const read = useCallback(() => api.getConceptDifficulty({ materialId, structureRevision: revision }), [api, materialId, revision]);
  return useReport(read);
}

export function DifficultyBadge({ concept, detail = false }: {
  concept?: ConceptDifficulty; detail?: boolean;
}) {
  if (!concept || (concept.level !== "hard" && !concept.personal_difficulty)) return null;
  return <span className={`concept-difficulty${detail ? " has-reason" : ""}`}>
    {concept.level === "hard" && <span className="difficulty-badge is-hard">預估偏難</span>}
    {concept.personal_difficulty && <span className="difficulty-badge is-personal">你的難點</span>}
    {detail && (concept.reason || concept.personal_difficulty) && <span className="difficulty-reason">
      <MathText text={concept.reason}/>
      {concept.personal_difficulty && <span>你的作答：答錯率 {Math.round(concept.incorrect_count / concept.answer_count * 100)}%（{concept.incorrect_count} / {concept.answer_count} 題）。</span>}
    </span>}
  </span>;
}

export function useLearningDiagnosis(api: StudydyApiClient, materialId: string, revision: string,
  difficulty: ReturnType<typeof useConceptDifficulty>, progress: MapProgressView | null, concepts: KnowledgeOutlineView["concepts"]) {
  const read = useCallback(() => api.getLearningDiagnosis({ materialId, structureRevision: revision }), [api, materialId, revision]);
  const report = useReport(read);
  const { data } = report;
  const pending = new Map<string, { concept_id: string; label: string }>();
  const completed = new Set([...(data?.strengths.map(item => item.concept_id) ?? []), ...(progress?.concept_states.filter(state => state.status === "completed").map(state => state.concept_id) ?? [])]);
  for (const finding of data?.weaknesses ?? []) pending.set(finding.concept_id, finding);
  for (const recommendation of data?.recommendations ?? []) pending.set(recommendation.concept_id, recommendation);
  for (const state of progress?.concept_states ?? []) {
    const concept = concepts.find(item => item.concept_id === state.concept_id);
    if (concept && state.attempts > 0 && state.status !== "completed") pending.set(state.concept_id, concept);
  }
  for (const concept of difficulty.data?.concepts ?? []) {
    if (concept.personal_difficulty && !data?.strengths.some(item => item.concept_id === concept.concept_id)) pending.set(concept.concept_id, concept);
  }
  for (const id of completed) pending.delete(id);
  return { ...report, pending: [...pending.values()] };
}

export function LearningDiagnosis({ api, materialId, revision, onReview, disabled = false, difficulty, progress, concepts, report }: {
  api: StudydyApiClient; materialId: string; revision: string;
  onReview: (id: string) => void; disabled?: boolean;
  difficulty: ReturnType<typeof useConceptDifficulty>; progress: MapProgressView | null; concepts: KnowledgeOutlineView["concepts"];
  report: ReturnType<typeof useLearningDiagnosis>;
}) {
  const { data, error, retry, pending } = report;
  const counts = (id: string) => {
    const value = difficulty.data?.concepts.find(item => item.concept_id === id);
    return value ? `累計答對 ${value.answer_count - value.incorrect_count}／${value.answer_count} 題` : difficulty.error ? "累計作答暫時無法讀取" : difficulty.data ? "尚無累計作答資料" : "累計作答讀取中";
  };
  const points = (finding?: DiagnosisFinding) => finding && finding.points.length > 0 && <details className="diagnosis-point-details">
    <summary>查看 {finding.points.length} 個重點與來源</summary>
    <ul className="diagnosis-claims">{finding.points.map(point => <li key={point.claim_id}>
      <p><MathText text={point.title}/></p>
      <p>累計答對 {point.correct_count}／{point.answer_count} 題</p>
      {!point.completed && <p>需連續答對 {point.required_consecutive_correct} 題才算補強完成，目前連續 {point.consecutive_correct_count} 題。</p>}
      <div className="diagnosis-sources">{sourceLinks(point.evidence).map(evidence => <SourceButton key={evidence.evidence_id} apiClient={api} resolver={data!.source_resolver} evidence={evidence} />)}</div>
    </li>)}</ul>
  </details>;
  return <section className="learning-diagnosis" aria-label="學習診斷">
    <header className="diagnosis-heading"><h2>待加強</h2>
      {data && <details className="diagnosis-export"><summary aria-label="更多診斷操作">⋯</summary>
        <ExportControls label="匯出學習診斷" options={[{ value: "pdf", label: "PDF" }]}
          download={() => api.downloadExport(`/v1/materials/${materialId}/knowledge-structures/${revision}/learning-diagnosis/export/pdf`)} />
      </details>}
    </header>
    {error ? <p role="alert">診斷暫時無法讀取。{error}<button className="text-button" type="button" onClick={retry}>重新讀取診斷</button></p>
      : !data ? <p role="status">正在讀取學習診斷…</p> : <>
        {difficulty.error && <p role="alert">累計作答暫時無法讀取。{difficulty.error}<button className="text-button" type="button" onClick={difficulty.retry}>重新讀取難度</button></p>}
        {pending.length === 0 ? <p>目前沒有待加強的概念。</p> : <ul className="diagnosis-point-list">{pending.map(concept => {
          const finding = data.weaknesses.find(item => item.concept_id === concept.concept_id);
          const personal = difficulty.data?.concepts.find(item => item.concept_id === concept.concept_id)?.personal_difficulty;
          const recommendation = data.recommendations.find(item => item.concept_id === concept.concept_id);
          return <li key={concept.concept_id}><div><strong>{concept.label}</strong>{personal && <span className="difficulty-badge is-personal">常答錯</span>}
            <p>{finding ? `${finding.points.length} 個重點待加強 · ` : "尚未完成 · "}{counts(concept.concept_id)}</p>
            {recommendation && <p><MathText text={recommendation.reason}/></p>}
            {points(finding)}
          </div><button type="button" className="secondary-button" disabled={disabled} onClick={() => onReview(concept.concept_id)}>複習 {concept.label}</button></li>;
        })}</ul>}
        <details className="diagnosis-mastered"><summary>已掌握（{data.strengths.length} 個概念）</summary>
          {data.strengths.length === 0 ? <p>依現行完成規則，目前還沒有已掌握的概念。</p> : <ul className="diagnosis-point-list">{data.strengths.map(finding => <li key={finding.concept_id}><div><strong>{finding.label}</strong>{difficulty.data?.concepts.find(item => item.concept_id === finding.concept_id)?.personal_difficulty && <span className="difficulty-badge is-personal">常答錯</span>}<p>{counts(finding.concept_id)}</p>{points(finding)}</div>
            <button className="secondary-button" type="button" disabled={disabled} onClick={() => onReview(finding.concept_id)}>再次練習 {finding.label}</button></li>)}</ul>}
        </details>
      </>}
        <details className="review-records"><summary>測驗紀錄</summary>
          {!progress?.concept_states.some(state => state.attempts > 0) ? <p>還沒有測驗紀錄。</p> : <ul className="diagnosis-point-list">{progress.concept_states.filter(state => state.attempts > 0).map(state => <li key={state.concept_id}>
            <div><strong>{concepts.find(concept => concept.concept_id === state.concept_id)?.label}</strong><p>{counts(state.concept_id)} · {state.status === "completed" ? "已完成" : "尚未完成"}</p></div>
            <button className="secondary-button" type="button" disabled={disabled} onClick={() => onReview(state.concept_id)}>查看與練習</button>
          </li>)}</ul>}
        </details>
  </section>;
}
