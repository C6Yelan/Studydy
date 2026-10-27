import { useAssessmentPresence } from "./useAssessmentPresence";
import { assessmentPhase, type AssessmentPhase } from "./assessment-phase";
import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { ApiClientError, errorMessage, type StudydyApiClient } from "../../api/client";
import type { AssessmentSetAnswer, AssessmentSetView, KnowledgeConceptsView, MaterialDiagnosticSetView } from "../../api/contracts";
import { AssessmentPaper, QuestionPreparation } from "./AssessmentPaper";
import "./sets.css";

export function MaterialDiagnosticPanel({ api, initialSet, context, onProgressChanged, onBack, onContinue, continuing, sessionCompleted, completesSession, onPhaseChange, history }: {
  api: StudydyApiClient; initialSet: MaterialDiagnosticSetView; context: KnowledgeConceptsView;
  onProgressChanged: () => Promise<void>; onBack: () => void;
  onContinue: () => void; continuing: boolean;
  history?: ReactNode;
  sessionCompleted: boolean; completesSession: boolean; onPhaseChange: (phase: AssessmentPhase) => void;
}) {
  const [group, setGroup] = useState(initialSet);
  const [selections, setSelections] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [submissionFailed, setSubmissionFailed] = useState(false);
  const alive = useRef(true);
  const intent = useRef<{ key: string; version: number; answers: AssessmentSetAnswer[] } | null>(null);
  const actionIntent = useRef<{ key: string; action: "retry" | "publish-partial"; version: number } | null>(null);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useEffect(() => { setGroup(current => initialSet.set_version >= current.set_version ? initialSet : current); }, [initialSet]);
  const accept = (next: AssessmentSetView) => {
    if (next.kind !== "material_diagnostic" || next.knowledge_structure_revision !== context.knowledge_structure_revision) throw new Error("診斷與目前教材版本不一致。");
    if (alive.current) setGroup(current => next.set_version >= current.set_version ? next : current);
    return next;
  };
  const read = async () => {
    const next = accept(await api.readAssessmentSet(group.study_session_id, group.set_id));
    if (alive.current && next.status === "completed") { intent.current = null; setSubmissionFailed(false); }
    return next;
  };
  useEffect(() => {
    if (group.status !== "preparing") return;
    let cancelled = false, reading = false;
    const timer = window.setInterval(async () => {
      if (reading) return;
      reading = true;
      try {
        const next = await api.readAssessmentSet(group.study_session_id, group.set_id);
        if (!cancelled) { accept(next); setMessage(null); }
      } catch (failure) { if (!cancelled) setMessage(errorMessage(failure)); }
      finally { reading = false; }
    }, 2000);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [api, group.study_session_id, group.set_id, group.status]);
  const questions = group.items.filter(item => item.assessment && item.created_at);
  const done = group.status === "completed";
  const phase = assessmentPhase(group);
  const presenceError = useAssessmentPresence(api, group.material_id, group.set_id, phase === "question");
  useLayoutEffect(() => { onPhaseChange(phase); }, [phase, onPhaseChange]);
  const selectedCount = questions.filter(item => item.feedback || selections[item.assessment!.assessment_revision]).length;
  const submit = async () => {
    if (busy || done || selectedCount !== questions.length) return;
    setBusy(true); setMessage(null);
    if (!intent.current) intent.current = { key: crypto.randomUUID(), version: group.set_version, answers: questions.map(item => ({
      assessment_revision: item.assessment!.assessment_revision, question_id: item.assessment!.question_id,
      selected_option_id: item.feedback?.selected_option_id ?? selections[item.assessment!.assessment_revision],
    })) };
    try {
      const value = intent.current;
      accept(await api.submitAssessmentSet(group.study_session_id, group.set_id, value.answers, value.version, value.key));
      void api.setAssessmentPresence(group.material_id, group.set_id, false).catch(() => undefined);
      if (!alive.current) return;
      intent.current = null; setSubmissionFailed(false);
      await onProgressChanged();
    } catch (failure) {
      if (!alive.current) return;
      if (failure instanceof ApiClientError && failure.reasonCode === "ASSESSMENT_SET_CONFLICT") {
        intent.current = null;
        try { await read(); } catch (readFailure) { if (alive.current) setMessage(errorMessage(readFailure)); }
      }
      if (alive.current) { setSubmissionFailed(true); setMessage(errorMessage(failure)); }
    } finally { if (alive.current) setBusy(false); }
  };
  const change = async (action: "retry" | "publish-partial") => {
    if (busy) return;
    setBusy(true); setMessage(null);
    if (actionIntent.current?.action !== action || actionIntent.current.version !== group.set_version)
      actionIntent.current = { key: crypto.randomUUID(), action, version: group.set_version };
    try {
      const value = actionIntent.current;
      accept(await api.changeAssessmentSet(group.study_session_id, group.set_id, action, value.version, value.key));
      actionIntent.current = null;
    } catch (failure) { if (alive.current) setMessage(errorMessage(failure)); }
    finally { if (alive.current) setBusy(false); }
  };
  const paper = <AssessmentPaper key={group.set_id} api={api} records={questions.map(item => ({ assessment: item.assessment!, feedback: item.feedback, can_submit: item.can_submit }))}
    view={context} history={history} completed={done} selections={selections} disabled={busy || !!intent.current} busy={busy}
    canSubmit={!busy && selectedCount === group.published_count && group.can_complete}
    submitLabel={busy ? "正在交卷…" : submissionFailed ? "重新交卷" : "交卷"}
    onSubmit={() => void submit()} onSelect={(revision, option) => setSelections(current => ({ ...current, [revision]: option }))}>
    {submissionFailed && <button className="secondary-button" type="button" disabled={busy} onClick={() => void read().then(() => { setMessage(null); return onProgressChanged(); }).catch(failure => setMessage(errorMessage(failure)))}>查回交卷結果</button>}
  </AssessmentPaper>;
  return <section className="assessment-set-panel" aria-label="快速檢測">
    {phase === "question" && presenceError && <p className="form-error" role="status">作答狀態暫時無法同步：{presenceError}</p>}
    {message && <p role="alert" className="form-error">{message}</p>}
    {(group.status === "preparing" || ["partial_ready", "failed"].includes(group.status)) && <header className="surface assessment-set-header is-preparing">
      <h2>先找出這次學習的起點</h2>
      {group.status === "preparing" ? <QuestionPreparation ready={group.verified_count} total={group.requested_count} label="快速檢測準備進度" />
        : <div className="assessment-set-actions"><p>部分快速檢測題目尚未準備完成；沒有測到的概念會保留在後續學習。</p>
          {group.can_retry && <button className="secondary-button" disabled={busy} onClick={() => void change("retry")}>再試一次</button>}
          {group.can_publish_partial && <button className="primary-button" disabled={busy} onClick={() => void change("publish-partial")}>先做已準備的 {group.verified_count} 題</button>}
        </div>}
      <button className="text-button" onClick={onBack}>查看知識地圖</button>
    </header>}
    {done && <section className="surface assessment-cycle">
      <h2>快速檢測結果</h2>
      <p className="question-result-summary" aria-label="快速檢測摘要">答對 {group.passed_count}／{group.published_count} 題 · {new Set(questions.filter(item => item.feedback && !item.feedback.is_correct).map(item => item.target_claim_id)).size} 個重點需要補強</p>
      <p>先學答錯與未測的概念，再補齊已測概念中尚未完成的重點。</p>
      {sessionCompleted && <p role="status">這份教材已完成目前可檢測的重點。</p>}
      <div className="assessment-set-actions">{!sessionCompleted && <button className="primary-button" disabled={continuing || busy} onClick={onContinue}>{continuing ? "正在更新學習進度…" : completesSession ? "完成本次學習" : "接續學習"}</button>}
        <button className="text-button" onClick={onBack}>查看知識地圖</button></div>
    </section>}
    {group.published_count > 0 && paper}
  </section>;
}
