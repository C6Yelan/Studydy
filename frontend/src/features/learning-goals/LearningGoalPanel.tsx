import { Progress } from "../../ui/Progress";
import { DailyGoalProgress } from "./DailyGoalProgress";
import { useEffect, useRef, useState } from "react";
import { errorMessage, type StudydyApiClient } from "../../api/client";
import type { GoalConcept, LearningGoalView } from "../../api/contracts";
import { GoalEditor, type GoalFormValues } from "./GoalEditor";
import { useStudyStart } from "../study-session/useStudyStart";
import "./styles.css";

const dateText = (date: string) => date.replaceAll("-", "/");

export function LearningGoalPanel({ api, materialId }: { api: StudydyApiClient; materialId: string }) {
  const [view, setView] = useState<LearningGoalView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const [offset, setOffset] = useState(0);
  const [adjusting, setAdjusting] = useState(false);
  const [adjustError, setAdjustError] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [acknowledging, setAcknowledging] = useState(false);
  const [ackError, setAckError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const active = useRef(true);
  const reads = useRef(0);
  const launch = useStudyStart(api, materialId);
  useEffect(() => {
    active.current = true; setView(null); setEditing(false); setAckError(null);
    return () => { active.current = false; };
  }, [api, materialId]);
  useEffect(() => {
    let cancelled = false;
    const request = ++reads.current;
    setError(null); setLoading(true);
    void api.getLearningGoal(materialId, offset).then(result => {
      if (!cancelled && request === reads.current) { setView(result); setAckError(null); }
    }, failure => { if (!cancelled && request === reads.current) setError(errorMessage(failure)); })
      .finally(() => { if (!cancelled && request === reads.current) setLoading(false); });
    return () => { cancelled = true; };
  }, [api, materialId, offset, reload]);
  const save = async (values: GoalFormValues) => {
    const result = await api.setLearningGoal(materialId, values.date, values.percent);
    if (!active.current) return;
    reads.current++;
    setView(result); setOffset(0); setEditing(false); setError(null); setLoading(false);
  };
  const dismiss = async () => {
    const notice = view?.goal?.update_notice;
    if (!notice || acknowledging) return;
    setAcknowledging(true); setAckError(null);
    try {
      const result = await api.acknowledgeGoalUpdate(materialId, notice.knowledge_structure_revision);
      if (active.current) { reads.current++; setView(result); setOffset(0); setLoading(false); }
    } catch (failure) { if (active.current) setAckError(errorMessage(failure)); }
    finally { if (active.current) setAcknowledging(false); }
  };
  const goal = view?.goal;
  const begin = (concept: GoalConcept) => {
    if (view?.run_id && view.knowledge_structure_revision) void launch.start({ materialId, runId: view.run_id, structureRevision: view.knowledge_structure_revision, conceptId: concept.concept_id });
  };
  return <section className="learning-goal-panel" aria-label="學習目標與每日關卡">

    <details className="goal-details">
      <summary className="goal-summary">
        <h2>{goal ? `${Number(goal.target_date.slice(5, 7))}/${Number(goal.target_date.slice(8))} 前掌握 ${goal.target_percent}%` : "尚未設定學習目標"}</h2>
        {goal && <><span>已完成 {goal.completed_points}／需完成 {goal.target_points} 個重點</span><span>{goal.achieved ? "目標已達成" : goal.overdue_days > 0 ? `已逾期 ${goal.overdue_days} 天` : goal.days_remaining <= 1 ? "今天到期" : `還有 ${goal.days_remaining} 天`}</span></>}
      </summary>
      <div className="goal-details-content"><header>
      {view && !error && <button className="secondary-button" type="button" disabled={acknowledging || adjusting} onClick={() => setEditing(true)}>{goal ? "更改學習目標" : "設定學習目標"}</button>}
    </header>
    {error ? <p role="alert" className="form-error">目標暫時無法讀取。{error}<button className="text-button" onClick={() => setReload(value => value + 1)}>重新讀取目標</button></p>
      : !view ? <p role="status">正在讀取學習目標…</p>
      : !goal ? null
      : <>
        {goal.update_notice && <div className="goal-update-notice" role="status">
          <p>教材已更新，重點數 {goal.update_notice.previous_points} → {goal.update_notice.current_points}。目標日期與比例保持不變。</p>
          <button type="button" className="text-button" disabled={acknowledging} onClick={() => void dismiss()}>{acknowledging ? "正在關閉…" : "關閉教材更新提示"}</button>
          {ackError && <p className="form-error" role="alert">{ackError}<button className="text-button" onClick={() => setReload(value => value + 1)}>重新讀取目標</button></p>}
        </div>}
        {goal.assessable_points > 0 && <Progress value={goal.completed_points} max={goal.target_points || 1} tone={goal.achieved ? "complete" : "default"} aria-label="學習目標進度" aria-valuetext={`已完成 ${goal.completed_points}／需完成 ${goal.target_points} 個重點`} />}
        <p>已完成 {goal.completed_points}／需完成 {goal.target_points} 個重點</p>
        <p>每天約 {goal.today_stage.quota_points} 個重點 · 今天 {goal.today_stage.completed_points}／{goal.today_stage.quota_points}</p>
        {goal.assessable_points === 0 && <p className="goal-warning" role="status">目前沒有可檢測重點，尚無法計算目標進度，也不能判定整體目標達成或安排新的關卡。今天已取得的成果仍會保留，也可從地圖閱讀教材。</p>}
          <>
            {!goal.achieved && <p className="goal-timing">{goal.overdue_days > 0 ? `已逾期 ${goal.overdue_days} 天` : goal.days_remaining <= 1 ? "今天到期" : `包含今天，剩餘 ${goal.days_remaining} 天`}
              {goal.behind_points > 0 && <strong>落後 {goal.behind_points} 個重點</strong>}</p>}
            <section className="goal-today" aria-label="今天的關卡">
              <header><h3>今天的關卡</h3><time dateTime={view.today}>{dateText(view.today)}</time></header>
              <DailyGoalProgress stage={goal.today_stage} stageKey={materialId} />
              {goal.achieved && <p>學習目標已達成。</p>}
              {(goal.quota_over_limit || goal.scheduled_over_limit) && <div className="goal-warning" role="status">
                <p>{goal.quota_over_limit ? "今日目標" : "實際安排"}超過每日建議的 {goal.daily_limit} 個重點。可延後日期或降低目標；系統不會自行修改。</p>
                <div className="goal-suggestions">{goal.suggestions.map(suggestion => <button key={suggestion.kind} className="secondary-button" type="button" disabled={adjusting || acknowledging} onClick={async () => {
                  setAdjusting(true); setAdjustError(null);
                  try { await save({ date: suggestion.target_date, percent: suggestion.target_percent }); }
                  catch (failure) { if (active.current) setAdjustError(errorMessage(failure)); }
                  finally { if (active.current) setAdjusting(false); }
                }}>{suggestion.kind === "extend_date" ? `延後到 ${Number(suggestion.target_date.slice(5, 7))}/${Number(suggestion.target_date.slice(8))}` : `改成 ${suggestion.target_percent}%`} · 每天約 {suggestion.daily_points} 個</button>)}</div>
                {adjustError && <p role="alert">{adjustError}</p>}
                <button className="text-button" type="button" disabled={adjusting} onClick={() => setEditing(true)}>自行調整目標</button>
              </div>}
              {goal.today_stage.concepts.length > 0 && <ul className="goal-concepts">{goal.today_stage.concepts.map(concept => <li key={concept.concept_id}>
                <div><strong>{concept.label}</strong><small>尚需完成 {concept.remaining_points} 個重點</small></div>
                <button className="secondary-button" disabled={launch.busy} onClick={() => begin(concept)}>學習 {concept.label}</button>
              </li>)}</ul>}
            </section>
            {goal.manual_review.length > 0 && <section className="goal-manual" aria-label="仍需逐點檢測">
              <h3>仍需逐點檢測</h3><p>這些概念雖已承接完成，仍有重點缺少逐點作答證據。它們不會自動排入關卡，可由你選擇複習。</p>
              <ul className="goal-concepts">{goal.manual_review.map(concept => <li key={concept.concept_id}>
                <div><strong>{concept.label}</strong><small>還差 {concept.remaining_points} 個重點</small></div>
                <button className="secondary-button" disabled={launch.busy} onClick={() => begin(concept)}>手動複習 {concept.label}</button>
              </li>)}</ul>
            </section>}
            {(goal.future_stages.length > 0 || goal.preview_offset > 0) && <section className="goal-preview" aria-label="未來關卡預覽">
              <h3>接下來的關卡</h3><p>以下是依目前進度推算的預覽，會隨作答調整，並非固定日程。</p>
              <ol>{goal.future_stages.map(stage => <li key={stage.date}>
                <div><time dateTime={stage.date}>{dateText(stage.date)}</time><span className="goal-preview-label">預覽</span></div>
                <p>目標 {stage.quota_points} ／ 安排 {stage.scheduled_points} 個重點</p>
                <p>{stage.concepts.map(concept => concept.label).join("、") || "沒有額外安排的概念"}</p>
              </li>)}</ol>
              <div className="goal-preview-actions">
                {loading && <span role="status">正在讀取預覽…</span>}
                <button className="secondary-button" disabled={loading || offset === 0} onClick={() => setOffset(value => Math.max(0, value - 7))}>前 7 天</button>
                <button className="secondary-button" disabled={loading || !goal.preview_has_more} onClick={() => setOffset(value => value + 7)}>後 7 天</button>
              </div>
            </section>}
          </>
      </>}
    {launch.error && <p role="alert" className="form-error">{launch.error}</p>}
      </div>
    </details>
    {editing && view && <GoalEditor today={view.today} current={goal ? { date: goal.target_date, percent: goal.target_percent } : null} onSave={save} onClose={() => setEditing(false)} />}
  </section>;
}
