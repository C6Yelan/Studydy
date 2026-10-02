import { useEffect, useState } from "react";
import { errorMessage, type StudydyApiClient } from "../../api/client";
import type { KnowledgeStructureView, LearnerProgressView } from "../../api/contracts";
import { recommendCards, type CardRecommendation, type RecommendationMode } from "./recommendations";

type ProgressRead =
  | { status: "loading" }
  | { status: "ready"; progress: LearnerProgressView | null }
  | { status: "error"; message: string };

export function CardRecommendations({ apiClient, view, studySessionId, disabled, onApply }: {
  apiClient: StudydyApiClient;
  view: KnowledgeStructureView;
  studySessionId: string | null;
  disabled: boolean;
  onApply: (items: CardRecommendation[]) => void;
}) {
  const [read, setRead] = useState<ProgressRead>({ status: "loading" });
  const [mode, setMode] = useState<RecommendationMode>("weakness");
  const [count, setCount] = useState(String(Math.min(5, view.concepts.length)));
  const [reload, setReload] = useState(0);
  const [applied, setApplied] = useState(false);
  useEffect(() => {
    let cancelled = false;
    setApplied(false);
    if (!studySessionId) {
      setRead({ status: "ready", progress: null });
      return;
    }
    setRead({ status: "loading" });
    void apiClient.readProgress(studySessionId, view.knowledge_structure_revision).then(
      (progress) => { if (!cancelled) setRead({ status: "ready", progress }); },
      (failure) => { if (!cancelled) setRead({ status: "error", message: errorMessage(failure) }); },
    );
    return () => { cancelled = true; };
  }, [apiClient, studySessionId, view.knowledge_structure_revision, reload]);

  const recommendation = read.status === "ready" ? recommendCards(view, read.progress, mode, Number(count)) : null;
  const labels = new Map(view.concepts.map((concept) => [concept.concept_id, concept.label]));
  return <section className="cards-recommendations" aria-label="幫我選卡">
    <div className="cards-recommendation-controls">
      <label>推薦方式<select value={mode} disabled={disabled} onChange={(event) => {
        setMode(event.target.value as RecommendationMode); setApplied(false);
      }}>
        <option value="weakness">優先複習弱點</option>
        <option value="path">依學習路徑</option>
      </select></label>
      <label>最多張數<input type="number" min={1} max={view.concepts.length} value={count} disabled={disabled} onChange={(event) => {
        setCount(event.target.value); setApplied(false);
      }} /></label>
    </div>
    {read.status === "loading" && <p role="status">正在讀取學習進度，尚未改變你的勾選。</p>}
    {read.status === "error" && <div role="alert">
      <p>無法讀取推薦所需的學習進度。{read.message}仍可手動選卡。</p>
      <button className="text-button" type="button" disabled={disabled} onClick={() => setReload((n) => n + 1)}>重新讀取進度</button>
    </div>}
    {recommendation && <>
      <p className="cards-recommendation-message" role="status">{recommendation.message}</p>
      {recommendation.items.length > 0 && <ol className="cards-recommendation-list">
        {recommendation.items.map((item) => <li key={item.conceptId}>
          <strong>{labels.get(item.conceptId)}</strong><span>{item.reason}</span>
        </li>)}
      </ol>}
    </>}
    <button className="secondary-button" type="button" disabled={disabled || !recommendation?.items.length} onClick={() => {
      if (!recommendation?.items.length) return;
      onApply(recommendation.items); setApplied(true);
    }}>套用推薦</button>
    <p className="cards-recommendation-hint">套用會取代目前勾選，保存前仍可手動調整。卡片依選取順序保存，手動加入的概念接在後面。</p>
    {applied && <p role="status">已套用推薦，可繼續調整或保存卡組。</p>}
  </section>;
}
