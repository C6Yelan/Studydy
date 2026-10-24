import { useEffect, useRef, useState } from "react";
import { errorMessage, type StudydyApiClient } from "../../api/client";
import type { GoalConcept, LearningGoalView } from "../../api/contracts";
import { GoalEditor, type GoalFormValues } from "./GoalEditor";
import { useStudyStart } from "../study-session/StartingLevel";
import "./styles.css";

const dateText = (date: string) => date.replaceAll("-", "/");

export function LearningGoalPanel({ api, materialId }: { api: StudydyApiClient; materialId: string }) {
  const [view, setView] = useState<LearningGoalView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const [offset, setOffset] = useState(0);
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
    if (view) void launch.start({ materialId, runId: view.run_id, structureRevision: view.knowledge_structure_revision, conceptId: concept.concept_id });
  };
  return <section className="learning-goal-panel" aria-label="學習目標與每日關卡">
    {launch.dialog}
    <header><div><h2>學習目標</h2><p>用目標日期安排每天要學的概念，依真實作答更新進度。</p></div>
      {view && !error && <button className="secondary-button" type="button" disabled={acknowledging} onClick={() => setEditing(true)}>{goal ? "更改學習目標" : "設定學習目標"}</button>}
    </header>
    {error ? <p role="alert" className="form-error">目標暫時無法讀取。{error}<button className="text-button" onClick={() => setReload(value => value + 1)}>重新讀取目標</button></p>
      : !view ? <p role="status">正在讀取學習目標…</p>
      : !goal ? <p>設定日期與掌握目標後，這裡會顯示今天的關卡與後續預覽。</p>
      : <>
        {goal.update_notice && <div className="goal-update-notice" role="status">
          <p>教材已更新，重點數 {goal.update_notice.previous_points} → {goal.update_notice.current_points}。目標日期與比例保持不變。</p>
          <button type="button" className="text-button" disabled={acknowledging} onClick={() => void dismiss()}>{acknowledging ? "正在關閉…" : "關閉教材更新提示"}</button>
          {ackError && <p className="form-error" role="alert">{ackError}<button className="text-button" onClick={() => setReload(value => value + 1)}>重新讀取目標</button></p>}
        </div>}
        <dl className="goal-facts">
          <div><dt>目標日期</dt><dd><time dateTime={goal.target_date}>{dateText(goal.target_date)}</time></dd></div>
          <div><dt>掌握目標</dt><dd>{goal.target_percent}% · {goal.target_percent === 60 ? "及格" : goal.target_percent === 80 ? "熟練" : "精通"}
            {goal.assessable_points > 0 && <small>需完成 {goal.target_points} 個重點</small>}</dd></div>
          <div><dt>重點完成</dt><dd>{goal.completed_points} / {goal.assessable_points}<small>可檢測重點</small></dd></div>
          <div><dt>距離目標</dt><dd>{goal.assessable_points === 0 ? "尚無法計算" : goal.achieved ? "目標已達成" : `還差 ${goal.remaining_points} 個重點`}</dd></div>
        </dl>
        {goal.assessable_points === 0 && <p className="goal-warning" role="status">目前沒有可檢測重點，還不能判定整體目標達成或安排新的關卡。今天已取得的成果仍會保留，也可從地圖閱讀教材。</p>}
          <>
            {!goal.achieved && <p className="goal-timing">{goal.overdue_days > 0 ? `已逾期 ${goal.overdue_days} 天` : `包含今天，剩餘 ${goal.days_remaining} 天`}
              {goal.behind_points > 0 && <strong>落後 {goal.behind_points} 個重點</strong>}</p>}
            <section className="goal-today" aria-label="今天的關卡">
              <header><h3>今天的關卡</h3><time dateTime={view.today}>{dateText(view.today)}</time></header>
              <p className="goal-quota">今日目標 <strong>{goal.today_stage.quota_points}</strong> ／ 實際安排 <strong>{goal.today_stage.scheduled_points}</strong> 個重點</p>
              <p>今天已完成 {goal.today_stage.completed_points} 個重點{goal.today_stage.remaining_points > 0 ? `，還差 ${goal.today_stage.remaining_points} 個。` : "。"}</p>
              {goal.achieved ? <p>學習目標已達成。</p> : goal.today_stage.done && <p>今天的關卡已完成。</p>}
              {goal.today_stage.scheduled_points > goal.today_stage.remaining_points && <p className="goal-note">題組保留完整概念，實際安排可能多於今天尚需完成的量。</p>}
              {(goal.quota_over_limit || goal.scheduled_over_limit) && <div className="goal-warning" role="status">
                <p>{goal.quota_over_limit ? "今日目標" : "實際安排"}超過每日建議的 {goal.daily_limit} 個重點。可延後日期或降低目標；系統不會自行修改。</p>
                <button className="text-button" type="button" onClick={() => setEditing(true)}>調整學習目標</button>
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
    {editing && view && <GoalEditor today={view.today} current={goal ? { date: goal.target_date, percent: goal.target_percent } : null} onSave={save} onClose={() => setEditing(false)} />}
  </section>;
}
