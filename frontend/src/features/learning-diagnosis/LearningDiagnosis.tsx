import { MathText } from "../../ui/MathText";
import { ExportControls } from "../exports/ExportControls";
import { useCallback, useEffect, useState } from "react";
import { errorMessage, type StudydyApiClient } from "../../api/client";
import type { ConceptDifficulty, MapProgressView, KnowledgeOutlineView } from "../../api/contracts";
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
    return value ? `答對 ${value.answer_count - value.incorrect_count}/${value.answer_count}` : difficulty.error ? "累計作答暫時無法讀取" : difficulty.data ? "尚無作答資料" : "讀取中";
  };
  const personal = (id: string) => difficulty.data?.concepts.find(item => item.concept_id === id)?.personal_difficulty && <span className="difficulty-badge is-personal">常答錯</span>;
  const labelOf = (id: string) => concepts.find(concept => concept.concept_id === id)?.label ?? id;
  const strengths = data?.strengths ?? [];
  const allIds = [...new Set([...pending.map(item => item.concept_id), ...strengths.map(item => item.concept_id),
    ...(progress?.concept_states.filter(state => state.attempts > 0).map(state => state.concept_id) ?? [])])];
  const [picked, setPicked] = useState<"pending" | "mastered" | "all" | null>(null);
  const [query, setQuery] = useState("");
  const tab = picked ?? (pending.length > 0 ? "pending" : "all");
  const match = (label: string) => label.toLowerCase().includes(query.trim().toLowerCase());
  const tabs = [{ id: "pending" as const, label: "待加強", count: pending.length }, { id: "mastered" as const, label: "已掌握", count: strengths.length },
    { id: "all" as const, label: "全部", count: allIds.length }];
  const failed = error ? <p role="alert">診斷暫時無法讀取。{error}<button className="text-button" type="button" onClick={retry}>重新讀取診斷</button></p>
    : !data && <p role="status">正在讀取學習診斷…</p>;
  const empty = tab === "pending" ? "目前沒有待加強的概念。" : tab === "mastered" ? "依現行完成規則，目前還沒有已掌握的概念。" : "還沒有測驗紀錄。";
  const rows = !data ? [] : tab === "pending" ? pending.filter(item => match(item.label)).map(concept => {
    const finding = data!.weaknesses.find(item => item.concept_id === concept.concept_id);
    return <li key={concept.concept_id}><div><strong>{concept.label}</strong>{personal(concept.concept_id)}
      <p>{finding ? "" : "尚未完成 · "}{counts(concept.concept_id)}</p>
    </div><button type="button" className="secondary-button" aria-label={`複習 ${concept.label}`} disabled={disabled} onClick={() => onReview(concept.concept_id)}>複習</button></li>;
  }) : tab === "mastered" ? strengths.filter(item => match(item.label)).map(finding => <li key={finding.concept_id}>
    <div><strong>{finding.label}</strong>{personal(finding.concept_id)}<p>{counts(finding.concept_id)}</p></div>
    <button className="secondary-button" type="button" aria-label={`再次練習 ${finding.label}`} disabled={disabled} onClick={() => onReview(finding.concept_id)}>練習</button></li>)
    : allIds.filter(id => match(labelOf(id))).map(id => {
      const done = strengths.some(item => item.concept_id === id) || progress?.concept_states.some(state => state.concept_id === id && state.status === "completed");
      return <li key={id}><div><strong>{labelOf(id)}</strong><p>{counts(id)} · {done ? "已完成" : "尚未完成"}</p></div>
        <button className="secondary-button" type="button" disabled={disabled} onClick={() => onReview(id)}>查看與練習</button></li>;
    });
  return <section className="review-block learning-diagnosis" aria-label="測驗紀錄">
    <header className="review-block-heading"><h2>測驗紀錄</h2>
      {data && <div className="review-block-action"><ExportControls label="匯出學習診斷" triggerLabel="匯出" options={[{ value: "pdf", label: "PDF" }]}
        download={() => api.downloadExport(`/v1/materials/${materialId}/knowledge-structures/${revision}/learning-diagnosis/export/pdf`)} /></div>}
    </header>
    {data && <div className="review-tabs" role="group" aria-label="紀錄類別">
      {tabs.map(item => <button key={item.id} type="button" className={tab === item.id ? "is-active" : ""} aria-pressed={tab === item.id} onClick={() => setPicked(item.id)}>{item.label} {item.count}</button>)}
    </div>}
    {data && allIds.length > 8 && <input className="review-search" type="search" aria-label="搜尋概念名稱" placeholder="搜尋概念名稱" value={query} onChange={event => setQuery(event.currentTarget.value)} />}
    <div className="review-block-body">
      {failed || <>
        {difficulty.error && <p role="alert">累計作答暫時無法讀取。{difficulty.error}<button className="text-button" type="button" onClick={difficulty.retry}>重新讀取難度</button></p>}
        {rows.length === 0 ? <p>{query.trim() ? "沒有符合的概念。" : empty}</p> : <ul className="diagnosis-point-list">{rows}</ul>}
      </>}
    </div>
  </section>;
}
