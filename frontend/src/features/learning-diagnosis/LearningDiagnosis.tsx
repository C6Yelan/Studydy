import { useCallback, useEffect, useState } from "react";
import { errorMessage, type StudydyApiClient } from "../../api/client";
import type { ConceptDifficulty, DiagnosisFinding } from "../../api/contracts";
import { SourceButton } from "../../ui/SourceButton";
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
      {concept.reason}
      {concept.personal_difficulty && <span>你的作答：答錯率 {Math.round(concept.incorrect_count / concept.answer_count * 100)}%（{concept.incorrect_count} / {concept.answer_count} 題）。</span>}
    </span>}
  </span>;
}

export function DifficultyList({ report, onReview, disabled = false }: {
  report: ReturnType<typeof useConceptDifficulty>; onReview: (id: string) => void; disabled?: boolean;
}) {
  const hard = report.data?.concepts.filter(concept => concept.level === "hard" || concept.personal_difficulty) ?? [];
  return <section className="diagnosis-difficulties" aria-label="教材難點">
    <h3>教材難點</h3>
    {report.error ? <p role="alert">難度暫時無法讀取。{report.error}<button type="button" className="text-button" onClick={report.retry}>重新讀取難度</button></p>
      : !report.data ? <p role="status">正在讀取難度…</p>
      : <>
        {hard.length === 0 && <p>目前沒有標記為難點的概念。</p>}
        {hard.length > 0 && <ul className="diagnosis-point-list">{hard.map(concept => <li key={concept.concept_id}>
        <div><strong>{concept.label}</strong><DifficultyBadge concept={concept} detail /></div>
        <button className="secondary-button" type="button" disabled={disabled} onClick={() => onReview(concept.concept_id)}>複習 {concept.label}</button>
      </li>)}</ul>}
      </>}
  </section>;
}

export function LearningDiagnosis({ api, materialId, revision, setId, onReview, disabled = false }: {
  api: StudydyApiClient; materialId: string; revision: string; setId?: string;
  onReview: (id: string) => void; disabled?: boolean;
}) {
  const read = useCallback(() => api.getLearningDiagnosis({ materialId, structureRevision: revision }, setId), [api, materialId, revision, setId]);
  const { data, error, retry } = useReport(read);
  if (data && !data.strengths.length && !data.weaknesses.length && !data.recommendations.length) return null;
  const findings = (title: string, items: DiagnosisFinding[]) => items.length > 0 && <section>
    <h3>{title}</h3>
    <ul className="diagnosis-point-list">{items.map(finding => <li key={finding.concept_id}>
      <div><strong>{finding.label}</strong>
        <ul className="diagnosis-claims">{finding.points.map(point => <li key={point.claim_id}>
          <p>{point.title}</p>
          <p className="diagnosis-answer-count">{point.answer_count > 0 ? `答對 ${point.correct_count} / ${point.answer_count} 次` : "尚未作答"}
            {point.latest_is_correct !== null && <span>{point.latest_is_correct ? "最近答對" : "最近答錯"}</span>}
          </p>
          <div className="diagnosis-sources">{point.evidence.map(evidence => <SourceButton key={evidence.evidence_id} apiClient={api} resolver={data!.source_resolver} evidence={evidence} />)}</div>
        </li>)}</ul>
      </div>
    </li>)}</ul>
  </section>;
  return <section className="learning-diagnosis" aria-label={setId ? "本組學習診斷" : "學習診斷"}>
    <h2>{setId ? "本組學習診斷" : "學習診斷"}</h2>
    {error ? <p role="alert">診斷暫時無法讀取。{error}<button className="text-button" type="button" onClick={retry}>重新讀取診斷</button></p>
      : !data ? <p role="status">正在讀取學習診斷…</p>
      : <div className="diagnosis-sections">
        {findings("優勢", data.strengths)}
        {findings("弱點", data.weaknesses)}
        {data.recommendations.length > 0 && <section aria-label="建議先複習">
          <h3>建議先複習</h3>
          <ul className="diagnosis-point-list">{data.recommendations.map(recommendation => <li key={recommendation.concept_id}>
            <div><strong>{recommendation.label}</strong><p>{recommendation.reason}</p></div>
            <button className="secondary-button" type="button" disabled={disabled} onClick={() => onReview(recommendation.concept_id)}>複習 {recommendation.label}</button>
          </li>)}</ul>
        </section>}
      </div>}
  </section>;
}
