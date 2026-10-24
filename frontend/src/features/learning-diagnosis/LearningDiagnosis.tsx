import { useCallback, useEffect, useState } from "react";
import { errorMessage, type StudydyApiClient } from "../../api/client";
import type { ConceptDifficulty, DiagnosisFinding } from "../../api/contracts";
import { SourceButton } from "../../ui/SourceButton";
import "./styles.css";

// 只輪詢尚未完成的工作；重新開頁只讀快取，不請求重新生成。
function useReport<T extends { status: string }>(read: () => Promise<T>) {
  const [result, setResult] = useState<{ read: typeof read; data: T | null; error: string | null } | null>(null);
  const [reload, setReload] = useState(0);
  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    setResult(null);
    const load = async () => {
      try {
        const data = await read();
        if (cancelled) return;
        setResult({ read, data, error: null });
        if (data.status === "pending" || data.status === "running") timer = setTimeout(load, 2000);
      } catch (error) {
        if (!cancelled) setResult({ read, data: null, error: errorMessage(error) });
      }
    };
    void load();
    return () => { cancelled = true; clearTimeout(timer); };
  }, [read, reload]);
  return { data: result?.read === read ? result.data : null, error: result?.read === read ? result.error : null,
    retry: () => setReload(value => value + 1) };
}

export function useConceptDifficulty(api: StudydyApiClient, materialId: string, revision: string) {
  const read = useCallback(() => api.getConceptDifficulty({ materialId, structureRevision: revision }), [api, materialId, revision]);
  return useReport(read);
}

export function DifficultyBadge({ concept, detail = false, hardOnly = false }: {
  concept?: ConceptDifficulty; detail?: boolean; hardOnly?: boolean;
}) {
  if (!concept || (hardOnly && concept.level !== "hard" && !concept.personal_difficulty)) return null;
  const label = concept.level === "hard" ? "偏難" : concept.level === "standard" ? "一般" : null;
  if (!label && !concept.personal_difficulty) return null;
  return <span className={`concept-difficulty${detail ? " has-reason" : ""}`}>
    {label && <span className={`difficulty-badge${concept.level === "hard" ? " is-hard" : ""}`}>{label}</span>}
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
  const failureMessage = report.data?.failure_reason === "DIFFICULTY_CALL_BUDGET_EXCEEDED"
    ? "教材較大，難度評估尚未完成：需要的評估次數超過目前上限（5 次）。"
    : report.data?.failure_reason === "DIFFICULTY_INPUT_TOO_LARGE"
      ? "難度評估尚未完成：單一概念內容超過目前可處理的容量。"
      : "這份教材的難度評估失敗，可先繼續學習。";
  return <section className="diagnosis-difficulties" aria-label="教材難點">
    <h3>教材難點</h3>
    {report.error ? <p role="alert">難度暫時無法讀取。{report.error}<button type="button" className="text-button" onClick={report.retry}>重新讀取難度</button></p>
      : !report.data ? <p role="status">正在讀取難度…</p>
      : <>
        {report.data.status === "not_generated" ? <p>這個教材版本尚未產生難度評估。</p>
          : report.data.status === "failed" ? <p role="status">{failureMessage}</p>
          : report.data.status !== "ready" ? <p role="status">正在整理這份教材的難點，可先繼續學習。</p>
          : hard.length === 0 && <p>目前沒有標記為難點的概念。</p>}
        {hard.length > 0 && <ul className="diagnosis-point-list">{hard.map(concept => <li key={concept.concept_id}>
        <div><strong>{concept.label}</strong><DifficultyBadge concept={concept} detail /></div>
        <button className="secondary-button" type="button" disabled={disabled} onClick={() => onReview(concept.concept_id)}>複習 {concept.label}</button>
      </li>)}</ul>}
      </>}
  </section>;
}

export function LearningDiagnosis({ api, materialId, revision, setId, onReview, disabled = false }: {
  api: StudydyApiClient; materialId: string; revision: string; setId?: string;
  onReview?: (id: string) => void; disabled?: boolean;
}) {
  const read = useCallback(() => api.getLearningDiagnosis({ materialId, structureRevision: revision }, setId), [api, materialId, revision, setId]);
  const { data, error, retry } = useReport(read);
  if (data?.status === "ready" && !data.strengths.length && !data.weaknesses.length && !data.recommendations.length) return null;
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
      {onReview && title === "建議先複習" && <button className="secondary-button" type="button" disabled={disabled} onClick={() => onReview(finding.concept_id)}>複習 {finding.label}</button>}
    </li>)}</ul>
  </section>;
  return <section className="learning-diagnosis" aria-label={setId ? "本組學習診斷" : "學習診斷"}>
    <h2>{setId ? "本組學習診斷" : "學習診斷"}</h2>
    {error ? <p role="alert">診斷暫時無法讀取。{error}<button className="text-button" type="button" onClick={retry}>重新讀取診斷</button></p>
      : !data ? <p role="status">正在讀取學習診斷…</p>
      : data.status === "no_answers" ? <p>還沒有作答紀錄。完成題組後，這裡會整理優勢、弱點與複習建議。</p>
      : data.status === "not_generated" ? <p>既有作答尚未產生學習診斷，下次完成題組後會更新。</p>
      : data.status === "failed" ? <p role="status">這次學習診斷未能完成，作答結果已保留。</p>
      : data.status !== "ready" ? <p role="status">正在根據作答整理學習診斷，可先查看結果或繼續學習。</p>
      : <div className="diagnosis-sections">
        {findings("優勢", data.strengths)}
        {findings("弱點", data.weaknesses)}
        {findings("建議先複習", data.recommendations)}
      </div>}
  </section>;
}
