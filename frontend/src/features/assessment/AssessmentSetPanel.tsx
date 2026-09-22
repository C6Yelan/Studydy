import { useEffect, useLayoutEffect, useRef, useState, type FormEvent } from "react";
import { ApiClientError, errorMessage, type StudydyApiClient } from "../../api/client";
import type { AssessmentPlanView, AssessmentRecordView, AssessmentSetAction, AssessmentSetAnswer, AssessmentSetView, KnowledgeStructureView } from "../../api/contracts";
import { AssessmentPanel } from "./AssessmentPanel";
import { SourceButton, sourceLinks } from "../../ui/SourceButton";
import { Icon } from "../../ui/Icon";
import { assessmentPhase, type AssessmentPhase } from "./assessment-phase";
import "./sets.css";

type Concept = KnowledgeStructureView["concepts"][number];

export function AssessmentSetPanel({ apiClient, studySessionId, selectedSetId, concept, view,
  completed, onSetSelected, onProgressChanged, onBackToMap, initialPhase, onPhaseChange }: {
  apiClient: StudydyApiClient; studySessionId: string; selectedSetId: string | null; concept: Concept;
  view: KnowledgeStructureView; completed: boolean;
  onSetSelected: (id: string) => void; onProgressChanged: () => Promise<void>;
  onBackToMap: () => void; initialPhase: AssessmentPhase;
  onPhaseChange: (phase: AssessmentPhase) => void;
}) {
  const [plan, setPlan] = useState<AssessmentPlanView | null>(null);
  const [group, setGroup] = useState<AssessmentSetView | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [starting, setStarting] = useState(false);
  const preparationTitle = useRef<HTMLHeadingElement>(null);
  const [reload, setReload] = useState(0);
  const [selections, setSelections] = useState<Record<string, string>>({});
  const [submissionError, setSubmissionError] = useState<string | null>(null);
  const [submissionNeedsRefresh, setSubmissionNeedsRefresh] = useState(false);
  const rejectedSubmission = useRef(false);
  const latestGroup = useRef<AssessmentSetView | null>(null);
  const submissionIntent = useRef<{ key: string; version: number; answers: AssessmentSetAnswer[] } | null>(null);
  const createIntent = useRef<string | null>(null);
  const actionIntent = useRef<{ id: string; action: AssessmentSetAction; key: string; version: number } | null>(null);
  const cycleIntent = useRef<{ signature: string; key: string; version: number } | null>(null);
  const alive = useRef(true);
  const selected = useRef(selectedSetId);
  selected.current = selectedSetId;
  const scopeKey = `${studySessionId}/${concept.concept_id}/${selectedSetId ?? ""}`;
  const currentScope = useRef(scopeKey);
  currentScope.current = scopeKey;
  // 舊頁面的晚到回應不可把使用者拉回前一個觀念，或清掉另一題組的選取。
  const isCurrent = () => alive.current && currentScope.current === scopeKey;

  const accept = (next: AssessmentSetView) => {
    if (!isCurrent() || selected.current && next.set_id !== selected.current) return false;
    if (next.knowledge_structure_revision !== view.knowledge_structure_revision || next.target_concept_id !== concept.concept_id) {
      throw new Error("題組與目前觀念不一致，請重新開啟學習紀錄。");
    }
    const previous = latestGroup.current;
    if (previous?.set_id === next.set_id && (previous.set_version > next.set_version || previous.cycle.set_version > next.cycle.set_version)) return false;
    latestGroup.current = next;
    if (rejectedSubmission.current || !next.can_complete) {
      submissionIntent.current = null; rejectedSubmission.current = false;
      setSubmissionNeedsRefresh(false); setSubmissionError(null);
    }
    setGroup(next);
    return true;
  };
  const refresh = async () => {
    if (!group) { setReload(value => value + 1); await onProgressChanged(); return; }
    try {
      const next = await apiClient.readAssessmentSet(studySessionId, group.set_id);
      if (accept(next)) { setMessage(null); return next; }
    } catch (error) { if (isCurrent()) setMessage(errorMessage(error)); }
  };

  useEffect(() => {
    let cancelled = false;
    alive.current = true;
    setGroup(null); setPlan(null); setMessage(null);
    setSelections({}); setSubmissionError(null); submissionIntent.current = null;
    latestGroup.current = null; rejectedSubmission.current = false; setSubmissionNeedsRefresh(false); setBusy(false); setStarting(false);
    createIntent.current = null; actionIntent.current = null; cycleIntent.current = null;
    void (async () => {
      try {
        if (selectedSetId) {
          const restored = await apiClient.readAssessmentSet(studySessionId, selectedSetId);
          if (!cancelled) accept(restored);
        } else if (!completed) {
          const selected = await apiClient.readAssessmentPlan(studySessionId, concept.concept_id);
          if (selected.knowledge_structure_revision !== view.knowledge_structure_revision) throw new Error("題組與教材版本不一致。");
          if (!cancelled) setPlan(selected);
        }
      } catch (error) { if (!cancelled) setMessage(errorMessage(error)); }
    })();
    return () => { cancelled = true; alive.current = false; };
  }, [apiClient, studySessionId, selectedSetId, concept.concept_id, reload]);

  useEffect(() => {
    if (!group || group.status !== "preparing") return;
    const id = group.set_id;
    let cancelled = false, fetching = false;
    const timer = window.setInterval(async () => {
      if (fetching) return;
      fetching = true;
      try {
        const next = await apiClient.readAssessmentSet(studySessionId, id);
        if (!cancelled) { accept(next); setMessage(null); if (next.status !== "preparing") await onProgressChanged(); }
      } catch (error) { if (!cancelled) setMessage(errorMessage(error)); }
      finally { fetching = false; }
    }, 2000);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [apiClient, studySessionId, group?.set_id, group?.status]);

  const phase = starting ? "preparing" : group ? assessmentPhase(group) : initialPhase;
  const answering = phase === "question";
  useLayoutEffect(() => { onPhaseChange(phase); }, [phase, onPhaseChange]);
  useEffect(() => { preparationTitle.current?.focus(); }, [phase]);

  const create = async () => {
    if (busy || completed) return;
    setBusy(true); setStarting(true); setMessage(null);
    if (!createIntent.current) createIntent.current = crypto.randomUUID();
    try {
      const next = await apiClient.createAssessmentSet(studySessionId, concept.concept_id, createIntent.current);
      if (!isCurrent()) return;
      accept(next); createIntent.current = null; onSetSelected(next.set_id);
    } catch (error) {
      if (!isCurrent()) return;
      if (error instanceof ApiClientError && error.reasonCode === "ASSESSMENT_SET_ACTIVE") {
        createIntent.current = null;
        try {
          const sets = await apiClient.listAssessmentSets(studySessionId);
          if (!isCurrent()) return;
          if (sets.knowledge_structure_revision !== view.knowledge_structure_revision) throw new Error("題組與教材版本不一致。");
          const existing = sets.sets.find(item => item.target_concept_id === concept.concept_id && sets.active_set_ids.includes(item.set_id));
          if (existing) { onSetSelected(existing.set_id); return; }
          setMessage("已讀取最新狀態，可以再次開始這個觀念的題組。");
        } catch (readError) { if (isCurrent()) setMessage(errorMessage(readError)); }
      } else setMessage(errorMessage(error));
    } finally { if (isCurrent()) { setBusy(false); setStarting(false); } }
  };

  const action = async (kind: AssessmentSetAction) => {
    if (!group || busy) return;
    setBusy(true); setStarting(kind === "retry"); setMessage(null);
    if (actionIntent.current?.id !== group.set_id || actionIntent.current.action !== kind) {
      actionIntent.current = { id: group.set_id, action: kind, key: crypto.randomUUID(), version: group.set_version };
    }
    const intent = actionIntent.current;
    try {
      accept(await apiClient.changeAssessmentSet(studySessionId, group.set_id, kind, intent.version, intent.key));
      actionIntent.current = null;
      await onProgressChanged();
    } catch (error) {
      if (!isCurrent()) return;
      setMessage(errorMessage(error));
      if (error instanceof ApiClientError && error.reasonCode === "ASSESSMENT_SET_CONFLICT") {
        actionIntent.current = null;
        await refresh();
      }
    }
    finally { if (isCurrent()) { setBusy(false); setStarting(false); } }
  };

  const cycleAction = async (kind: "review" | "defer" | "remediation" | "close-cycle", claim?: string) => {
    if (!group || busy) return;
    const cycle = group.cycle;
    const signature = `${cycle.diagnostic_set_id}/${kind}/${claim ?? ""}`;
    if (cycleIntent.current?.signature !== signature) {
      cycleIntent.current = { signature, key: crypto.randomUUID(), version: cycle.set_version };
    }
    const intent = cycleIntent.current;
    setBusy(true); setStarting(kind === "remediation"); setMessage(null);
    try {
      if (kind === "remediation") {
        const next = await apiClient.createRemediationSet(studySessionId, cycle.diagnostic_set_id, intent.version, intent.key);
        if (!isCurrent()) return;
        cycleIntent.current = null; onSetSelected(next.set_id);
      } else {
        if (kind === "close-cycle") {
          await apiClient.changeAssessmentSet(studySessionId, cycle.diagnostic_set_id, kind, intent.version, intent.key);
        } else {
          await apiClient.reviewAssessmentPoint(studySessionId, cycle.diagnostic_set_id, claim!, kind, intent.version, intent.key);
        }
        cycleIntent.current = null;
        accept(await apiClient.readAssessmentSet(studySessionId, group.set_id));
      }
      if (kind !== "remediation") await onProgressChanged();
    } catch (error) {
      if (!isCurrent()) return;
      setMessage(errorMessage(error));
      if (error instanceof ApiClientError && error.reasonCode === "ASSESSMENT_SET_CONFLICT") {
        cycleIntent.current = null;
        await refresh();
      }
    } finally { if (isCurrent()) { setBusy(false); setStarting(false); } }
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!group?.can_complete || busy || submissionNeedsRefresh) return;
    const questions = group.items.filter(item => item.assessment);
    if (!submissionIntent.current) {
      const answers = questions.map(item => ({ assessment_revision: item.assessment!.assessment_revision,
        question_id: item.assessment!.question_id,
        selected_option_id: item.feedback?.selected_option_id ?? selections[item.assessment!.assessment_revision] }));
      if (answers.some(answer => !answer.selected_option_id)) { setSubmissionError("請先完成所有題目，再一起交卷。"); return; }
      submissionIntent.current = { key: crypto.randomUUID(), version: group.set_version, answers };
    }
    const intent = submissionIntent.current;
    setBusy(true); setSubmissionError(null);
    try {
      accept(await apiClient.submitAssessmentSet(studySessionId, group.set_id, intent.answers, intent.version, intent.key));
      submissionIntent.current = null;
      await onProgressChanged();
    } catch (error) {
      if (!isCurrent()) return;
      if (error instanceof ApiClientError && error.status === 409 && error.reasonCode === "ASSESSMENT_SET_CONFLICT") {
        // 明確拒絕後才能換掉舊提交意圖；網路結果未知時仍保留原 key／答案。
        rejectedSubmission.current = true; setSubmissionNeedsRefresh(true);
        const updated = await refresh();
        if (!isCurrent()) return;
        if (updated?.can_complete) setSubmissionError("題組已同步，答案選取已保留。確認後可再次交卷。");
        else if (!updated) setSubmissionError("尚未讀到最新題組，請先查回交卷結果再繼續。");
        if (updated) await onProgressChanged();
      } else setSubmissionError(errorMessage(error));
    } finally { if (isCurrent()) setBusy(false); }
  };

  const afterAnswer = async () => { await refresh(); await onProgressChanged(); };
  const closed = group?.status === "completed";
  const prepared = !!group && group.published_count > 0;
  const questions = group?.items.filter(item => item.assessment && item.created_at) ?? [];
  const selectedCount = questions.filter(item => item.feedback || selections[item.assessment!.assessment_revision]).length;
  const unavailable = group ? group.requested_count - group.published_count + group.excluded_count : 0;
  const readError = message && <div className="assessment-error" role="alert"><span>{message}</span>
    <button className="text-button" onClick={() => void refresh()}>重新讀取題組</button></div>;
  const waiting = phase === "preparing" || phase === "intervention";
  const progressGroup = starting ? null : group;
  const total = progressGroup?.requested_count ?? 0;
  const ready = progressGroup?.verified_count ?? 0;
  const intervention = phase === "intervention";
  const progressText = total > 0 ? `已準備 ${ready} / ${total} 題` : "正在讀取準備進度…";
  return <section className="assessment-set-panel" aria-label={`${concept.label}的重點題組`}>
    <header className={`surface assessment-set-header${waiting ? " is-preparing" : ""}`}>
      {waiting ? <>
      <div className="preparation-heading">
        <p className="eyebrow">{group?.kind === "remediation" ? "錯題重點補強" : "觀念重點檢測"}</p>
      <h2 ref={preparationTitle} tabIndex={-1}>{starting ? "正在開始本輪練習…" : intervention
        ? ready > 0 ? "部分題目已準備完成" : "這次題目尚未準備完成。"
        : "正在準備本輪練習"}</h2>
      </div>
      <p>{intervention ? ready > 0
        ? `目前已準備 ${ready} 題${group?.can_publish_partial ? "，可以先開始練習" : ""}${group?.can_retry ? "，也可以再試著準備其餘題目" : ""}。`
        : "你可以稍後再試，或先回到知識地圖。"
        : `Studydy 正在準備「${concept.label}」的題目，完成後會自動顯示。`}</p>
      {!intervention && <div className="preparation-progress">
        <div className="preparation-progress-heading"><h3>準備進度</h3><p role="status">{total > 0 ? `${ready} / ${total} 題` : progressText}</p></div>
        <div className={`preparation-progress-track${total > 0 ? "" : " is-indeterminate"}`} role="progressbar" aria-label="準備進度"
          aria-valuemin={0} aria-valuemax={100} aria-valuenow={total > 0 ? Math.round(ready / total * 100) : undefined} aria-valuetext={progressText}>
          <span style={total > 0 ? { width: `${ready / total * 100}%` } : undefined} />
        </div>
      </div>}
      {readError}
      {intervention && <div className="assessment-set-actions">
        {group?.can_publish_partial && <button className="primary-button" disabled={busy} onClick={() => void action("publish-partial")}>先做已準備的 {ready} 題</button>}
        {group?.can_retry && <button className="secondary-button" disabled={busy} onClick={() => void action("retry")}>再試一次</button>}
        {group?.status === "failed" && !group.can_retry && group.requested_count > 0 && !completed && <details><summary>其他檢測操作</summary><button className="secondary-button" disabled={busy} onClick={() => void create()}>重新檢測整個觀念</button></details>}
      </div>}
      {!intervention && <p className="preparation-note">可以先離開，稍後回來會接續這次練習。</p>}
      <div className="assessment-set-actions preparation-navigation">
        <button className="text-button" onClick={onBackToMap}><Icon name="arrow-left" size={16} />回到知識地圖</button>
      </div>
      </> : <>
      <div><p className="eyebrow">{group?.kind === "remediation" ? "錯題重點補強" : "觀念重點檢測"}</p><h2>{group?.kind === "remediation" ? `針對「${concept.label}」的錯誤重點再確認` : `一起檢測「${concept.label}」的重點`}</h2>
        {answering && <p>選完本組所有題目後一起交卷，再查看結果與補強建議。交卷前可修改答案；尚未交卷的選取不會保存。</p>}</div>
      {readError}
      {!group && !plan && !message && !completed && <p role="status">正在讀取這個觀念的檢測範圍…</p>}
      {!group && completed && <p>此學習紀錄已結束，可以從題目與作答紀錄回顧。</p>}
      {plan && !group && <>
        <p>這個觀念包含 {plan.point_count} 個重點，本輪準備 {plan.requested_count} 題。</p>
        {plan.requested_count > 0 && <p>選完本組所有題目後一起交卷。</p>}
        {plan.excluded.length > 0 && <p>{plan.excluded.length} 個重點未列入本輪出題範圍，保留為未檢測。</p>}
        <button className="primary-button" disabled={busy || plan.requested_count === 0} aria-busy={busy} onClick={() => void create()}>
          <Icon name="learning" />{busy ? "正在建立題組…" : `開始本輪 ${plan.requested_count} 題`}</button>
      </>}
      {prepared && <p className="assessment-set-count">{closed ? "已交卷" : "本組"} {group!.published_count} 題
        {unavailable > 0 && ` · 原訂 ${group!.requested_count} 題，${unavailable} 個重點未檢測`}</p>}
      {closed && <p>{group!.kind === "remediation" ? "本組補強已結束，請查看本輪檢測結果。" : "初篩已結束，請查看下方結果與需要補強的重點。"}</p>}
      {group && <div className="assessment-set-actions">
        {(closed || group.status === "failed" && !group.can_retry && group.requested_count > 0) && !completed && <details><summary>其他檢測操作</summary><button className="secondary-button" disabled={busy} onClick={() => void create()}>重新檢測整個觀念</button></details>}
        <button className="text-button" onClick={onBackToMap}>回到知識地圖</button>
      </div>}
      </>}
    </header>
    {!waiting && group && closed && <section className="surface assessment-cycle" aria-label="本輪檢測與補強">
      <h2>本輪檢測結果</h2>
      {group.cycle.outcome === "passed" ? <p className="assessment-cycle-result">本輪檢測通過，僅代表這次檢測範圍的結果。</p>
        : group.cycle.closed_at ? <p className="assessment-cycle-result">本輪已結束，尚未通過的重點保留原狀，之後可以繼續。</p>
        : <p>答錯的重點先複習，再用新題確認。補強答對一題就結束該重點本次補強，不直接視為已掌握。</p>}
      <div className="assessment-set-summary" aria-label="本輪累計結果">
        <span>本次通過 <strong>{group.cycle.passed_count}</strong></span>
        <span>其中補強通過 <strong>{group.cycle.remediation_passed_count}</strong></span>
        <span>待補強 <strong>{group.cycle.pending_count}</strong></span>
        <span>未作答 <strong>{group.cycle.unanswered_count}</strong></span>
        <span>未檢測 <strong>{group.cycle.unavailable_count}</strong></span>
      </div>
      {group.kind === "remediation" && <button className="text-button" onClick={() => onSetSelected(group.cycle.diagnostic_set_id)}>查看原始初篩題組</button>}
      {group.cycle.active_set_id && group.cycle.active_set_id !== group.set_id && <button className="primary-button" onClick={() => onSetSelected(group.cycle.active_set_id!)}>接續進行中的題組</button>}
      {group.cycle.can_review && <div className="assessment-review-points">{group.cycle.points.filter(point => ["needs_review", "reviewed", "deferred"].includes(point.result)).map(point => {
        const claim = concept.claims.find(claim => claim.claim_id === point.claim_id);
        if (!claim) return null;
        const references = sourceLinks(claim.evidence);
        return <article className="assessment-review-point" key={point.claim_id} aria-label="待補強重點">
          <h3>{point.result === "reviewed" ? "已確認複習" : point.result === "deferred" ? "稍後補強" : "先複習這個重點"}</h3>
          <p>{claim.text}</p>
          <div className="assessment-set-actions">{references.map(reference => <SourceButton key={reference.evidence_id}
            apiClient={apiClient} resolver={view.source_resolver} evidence={reference} />)}</div>
          <div className="assessment-set-actions">
            {point.result !== "reviewed" && <button className="secondary-button" disabled={busy} onClick={() => void cycleAction("review", point.claim_id)}>已複習此重點</button>}
            {point.result !== "deferred" && <button className="text-button" disabled={busy} onClick={() => void cycleAction("defer", point.claim_id)}>這個重點稍後處理</button>}
            {point.latest_set_id && point.latest_set_id !== group.set_id && <button className="text-button" onClick={() => onSetSelected(point.latest_set_id!)}>查看最近作答</button>}
          </div>
        </article>;
      })}</div>}
      <div className="assessment-set-actions">
        {group.cycle.can_create_remediation && <button className="primary-button" disabled={busy} onClick={() => void cycleAction("remediation")}>開始補強 {group.cycle.points.filter(point => point.result === "reviewed").length} 題</button>}
        {group.cycle.can_close && <button className="secondary-button" disabled={busy} onClick={() => void cycleAction("close-cycle")}>結束本輪，保留待處理重點</button>}
        {(group.cycle.closed_at || group.cycle.outcome === "passed") && <button className="secondary-button" onClick={onBackToMap}>回到地圖繼續學習</button>}
      </div>
    </section>}
    {!waiting && prepared && <form className="assessment-paper" aria-label="本組測驗" onSubmit={event => void submit(event)}>
      {closed && <div className="assessment-set-summary surface" aria-label="本組作答結果">
        <span>本組答對 <strong>{group!.passed_count}</strong></span>
        <span>本組答錯 <strong>{group!.answered_count - group!.passed_count}</strong></span>
        {group!.answered_count < group!.published_count && <span>未作答 <strong>{group!.published_count - group!.answered_count}</strong></span>}
        {unavailable > 0 && <span>未檢測 <strong>{unavailable}</strong></span>}
      </div>}
      <div className="assessment-set-items">{questions.map((item, index) => {
        const assessment = item.assessment!;
        const record: AssessmentRecordView = { assessment, feedback: item.feedback, created_at: item.created_at!, can_submit: item.can_submit };
        return <article className="assessment-set-item" key={assessment.assessment_revision} aria-label={`第 ${index + 1} 題`}>
          <p className="assessment-set-number">第 {index + 1} 題{item.feedback ? " · 已保存" : selections[assessment.assessment_revision] ? " · 已選擇" : " · 未選擇"}</p>
          <AssessmentPanel apiClient={apiClient} record={record} completed={closed || completed}
            answerSelection={{ value: item.feedback?.selected_option_id ?? selections[assessment.assessment_revision] ?? null,
              disabled: busy || !!submissionIntent.current,
              onChange: optionId => { if (!submissionIntent.current) { setSelections(previous => ({ ...previous, [assessment.assessment_revision]: optionId })); setSubmissionError(null); } } }}
            view={view} />
        </article>;
      })}</div>
      {group!.can_complete && !completed && <footer className="assessment-set-submit surface">
        {submissionError && <div className="assessment-error" role="alert"><span>{submissionError}</span>
          <button className="text-button" type="button" onClick={() => void afterAnswer()}>查回交卷結果</button></div>}
        <div className="assessment-set-submit-row"><p>已選擇 <strong>{selectedCount}／{questions.length}</strong> 題<span>交卷後統一保存與顯示結果</span></p>
          <button className="primary-button" type="submit" disabled={busy || submissionNeedsRefresh || selectedCount !== questions.length} aria-busy={busy}>
            {busy ? "正在交卷…" : submissionError ? "重新交卷" : "交卷並查看結果"}</button>
        </div>
      </footer>}
    </form>}
  </section>;
}
