import { Progress } from "../../ui/Progress";
import { useEffect, useRef, useState } from "react";
import { errorMessage, type StudydyApiClient } from "../../api/client";
import type { GoalConcept, LearningGoalView } from "../../api/contracts";
import { GoalEditor, type GoalFormValues } from "./GoalEditor";
import { useStudyStart } from "../study-session/useStudyStart";
import "./styles.css";

const shortDate = (date: string) => `${Number(date.slice(5, 7))}/${Number(date.slice(8))}`;

/** 學習與測驗頁共用的目標狀態：頂列、今天的概念、目標區塊都讀這一份。 */
export function useLearningGoal(api: StudydyApiClient, materialId: string) {
  const [view, setView] = useState<LearningGoalView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const [adjusting, setAdjusting] = useState(false);
  const [adjustError, setAdjustError] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const active = useRef(true);
  const reads = useRef(0);
  const launch = useStudyStart(api, materialId);
  useEffect(() => {
    active.current = true; setView(null); setEditing(false);
    return () => { active.current = false; };
  }, [api, materialId]);
  useEffect(() => {
    let cancelled = false;
    const request = ++reads.current;
    setError(null);
    void api.getLearningGoal(materialId).then(result => {
      if (!cancelled && request === reads.current) setView(result);
    }, failure => { if (!cancelled && request === reads.current) setError(errorMessage(failure)); });
    return () => { cancelled = true; };
  }, [api, materialId, reload]);
  const save = async (values: GoalFormValues) => {
    const result = await api.setLearningGoal(materialId, values.date, values.percent);
    if (!active.current) return;
    reads.current++;
    setView(result); setEditing(false); setError(null);
  };
  const adjust = async (target: { target_date: string; target_percent: 60 | 80 | 100 }) => {
    setAdjusting(true); setAdjustError(null);
    try { await save({ date: target.target_date, percent: target.target_percent }); }
    catch (failure) { if (active.current) setAdjustError(errorMessage(failure)); }
    finally { if (active.current) setAdjusting(false); }
  };
  const begin = (concept: GoalConcept) => {
    if (view?.run_id && view.knowledge_structure_revision) void launch.start({ materialId, runId: view.run_id, structureRevision: view.knowledge_structure_revision, conceptId: concept.concept_id });
  };
  return { view, goal: view?.goal ?? null, error, adjusting, adjustError, editing, setEditing, launch, save, adjust, begin,
    retry: () => setReload(value => value + 1) };
}
export type LearningGoalState = ReturnType<typeof useLearningGoal>;

export function TodayConceptsBlock({ state }: { state: LearningGoalState }) {
  const { goal, view, launch, begin } = state;
  if (!goal || !view) return null;
  const concepts = goal.today_stage.concepts;
  return <section className="review-block goal-today" aria-label="今天的概念">
    <header className="review-block-heading"><h2>今天的概念</h2><span className="review-block-count">{concepts.length}</span></header>
    <div className="review-block-body">
      {goal.achieved && <p>學習目標已達成。</p>}
      {concepts.length === 0 && !goal.achieved && <p>今天沒有需要學習的概念。</p>}
      {concepts.length > 0 && <ul className="concept-rows">{concepts.map(concept => <li key={concept.concept_id}>
        <button type="button" className="concept-row" aria-label={`學習 ${concept.label}`} disabled={launch.busy} onClick={() => begin(concept)}>
          <span className={`concept-dot${concept.remaining_points === 0 ? " is-done" : ""}`} aria-hidden="true" />
          <span className="concept-row-name">{concept.label}</span>
          <small>尚需 {concept.remaining_points} 重點</small><span aria-hidden="true">›</span>
        </button>
      </li>)}</ul>}
      {goal.manual_review.length > 0 && <>
        <h3 title="這些概念雖已承接完成，仍有重點缺少逐點作答證據。它們不會自動排入關卡，可由你選擇複習。">仍需逐點檢測 <span aria-hidden="true">ⓘ</span></h3>
        <ul className="concept-rows">{goal.manual_review.map(concept => <li key={concept.concept_id}>
          <button type="button" className="concept-row" aria-label={`手動複習 ${concept.label}`} disabled={launch.busy} onClick={() => begin(concept)}>
            <span className="concept-dot" aria-hidden="true" />
            <span className="concept-row-name">{concept.label}</span>
            <small>還差 {concept.remaining_points} 重點</small><span aria-hidden="true">›</span>
          </button>
        </li>)}</ul>
      </>}
      {launch.error && <p role="alert" className="form-error">{launch.error}</p>}
    </div>
  </section>;
}

export function LearningGoalBlock({ state }: { state: LearningGoalState }) {
  const { view, goal, error, adjusting, adjustError, setEditing } = state;
  const timing = goal && (goal.achieved ? "目標已達成" : goal.overdue_days > 0 ? `已逾期 ${goal.overdue_days} 天` : goal.days_remaining <= 1 ? "今天到期" : `剩 ${goal.days_remaining} 天`);
  const over = goal && (goal.quota_over_limit || goal.scheduled_over_limit);
  const behind = goal && !goal.achieved && goal.behind_points > 0;
  return <section className="review-block learning-goal-panel" aria-label="學習目標">
    <header className="review-block-heading"><h2>學習目標</h2></header>
    <div className="review-block-body">
      {error ? <p role="alert" className="form-error">目標暫時無法讀取。{error}<button className="text-button" type="button" onClick={state.retry}>重新讀取目標</button></p>
        : !view ? <p role="status">正在讀取學習目標…</p>
        : !goal ? <p>尚未設定學習目標</p>
        : <>
          <p className="goal-title">{shortDate(goal.target_date)} 前掌握 {goal.target_percent}% · {timing}</p>
          {goal.assessable_points > 0 && <div className="goal-progress-line">
            <Progress value={goal.completed_points} max={goal.target_points || 1} tone={goal.achieved ? "complete" : "default"} aria-label="學習目標進度" aria-valuetext={`已完成 ${goal.completed_points}／需完成 ${goal.target_points} 個重點`} />
            <span>{goal.completed_points}/{goal.target_points}</span>
          </div>}
          {goal.assessable_points === 0 && <p className="goal-warning" role="status">目前沒有可檢測重點，尚無法計算目標進度，也不能判定整體目標達成或安排新的關卡。可從地圖閱讀教材。</p>}
          {(behind || over) && <div className="goal-warning" role="status">
            <p><strong>{behind ? `落後 ${goal.behind_points} 個重點，` : ""}今天需 {goal.today_stage.quota_points}{over ? `（建議 ${goal.daily_limit}）` : ""}</strong></p>
            {over && <div className="goal-suggestions">{goal.suggestions.map(suggestion => <button key={suggestion.kind} className="secondary-button" type="button" disabled={adjusting} onClick={() => void state.adjust(suggestion)}>
              {suggestion.kind === "extend_date" ? `延後到 ${shortDate(suggestion.target_date)}` : `改成 ${suggestion.target_percent}%`} · 每天約 {suggestion.daily_points} 個</button>)}</div>}
            {adjustError && <p role="alert">{adjustError}</p>}
          </div>}
        </>}
      {view && !error && <button className="secondary-button goal-edit" type="button" disabled={adjusting} onClick={() => setEditing(true)}>{goal ? "更改學習目標" : "設定學習目標"}</button>}
    </div>
  </section>;
}

export function GoalEditorHost({ state }: { state: LearningGoalState }) {
  const { view, goal, editing, setEditing, save } = state;
  if (!editing || !view) return null;
  return <GoalEditor today={view.today} current={goal ? { date: goal.target_date, percent: goal.target_percent } : null} onSave={save} onClose={() => setEditing(false)} />;
}
