import { useAssessmentPresence } from "./useAssessmentPresence";
import { AssessmentPaper, QuestionPreparation } from "./AssessmentPaper";
import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { ApiClientError, errorMessage, type StudydyApiClient } from "../../api/client";
import type {
  AssessmentRecordView,
  AssessmentSetAction,
  AssessmentSetAnswer,
  AssessmentSetView,
  ConceptAssessmentSetView,
  KnowledgeStructureView,
} from "../../api/contracts";
import { Icon } from "../../ui/Icon";
import { assessmentPhase, type AssessmentPhase } from "./assessment-phase";
import "./sets.css";

type Concept = KnowledgeStructureView["concepts"][number];

export function AssessmentSetPanel({
  apiClient,
  studySessionId,
  materialId,
  selectedSetId,
  initialSet,
  concept,
  view,
  completed,
  conceptCompleted = false,
  onSetSelected,
  onProgressChanged,
  onBackToMap,
  initialPhase,
  onPhaseChange,
  continuation,
  history,
}: {
  apiClient: StudydyApiClient;
  studySessionId: string;
  materialId: string;
  selectedSetId: string;
  initialSet: ConceptAssessmentSetView;
  concept: Concept;
  view: Pick<KnowledgeStructureView, "knowledge_structure_revision" | "source_resolver" | "concepts">;
  completed: boolean;
  conceptCompleted?: boolean;
  onSetSelected: (id: string) => void;
  onProgressChanged: () => Promise<void>;
  onBackToMap: () => void;
  initialPhase: AssessmentPhase;
  onPhaseChange: (phase: AssessmentPhase) => void;
  history?: ReactNode;
  continuation?: { label: string; busy: boolean; onContinue: () => Promise<void> };
}) {
  const [group, setGroup] = useState<ConceptAssessmentSetView | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [starting, setStarting] = useState(false);
  const preparationTitle = useRef<HTMLHeadingElement>(null);
  const [reload, setReload] = useState(0);
  const [selections, setSelections] = useState<Record<string, string>>({});
  const [submissionError, setSubmissionError] = useState<string | null>(null);
  const [submissionNeedsRefresh, setSubmissionNeedsRefresh] = useState(false);
  const rejectedSubmission = useRef(false);
  const latestGroup = useRef<ConceptAssessmentSetView | null>(null);
  const submissionIntent = useRef<{
    key: string;
    version: number;
    answers: AssessmentSetAnswer[];
  } | null>(null);
  const actionIntent = useRef<{
    id: string;
    action: AssessmentSetAction;
    key: string;
    version: number;
  } | null>(null);
  const cycleIntent = useRef<{ signature: string; key: string; version: number } | null>(null);
  const alive = useRef(true);
  const selected = useRef(selectedSetId);
  selected.current = selectedSetId;
  const scopeKey = `${studySessionId}/${concept.concept_id}/${selectedSetId ?? ""}`;
  const currentScope = useRef(scopeKey);
  currentScope.current = scopeKey;
  // 過期回應不得改變目前觀念或清除其他題組的選取。
  const isCurrent = () => alive.current && currentScope.current === scopeKey;

  const accept = (next: AssessmentSetView) => {
    if (!isCurrent() || (selected.current && next.set_id !== selected.current)) return false;
    if (
      next.knowledge_structure_revision !== view.knowledge_structure_revision ||
      next.target_concept_id !== concept.concept_id
    ) {
      throw new Error("題組與目前觀念不一致，請重新開啟學習紀錄。");
    }
    const previous = latestGroup.current;
    if (
      previous?.set_id === next.set_id &&
      (previous.set_version > next.set_version ||
        previous.cycle.set_version > next.cycle.set_version)
    )
      return false;
    latestGroup.current = next;
    if (rejectedSubmission.current || !next.can_complete) {
      submissionIntent.current = null;
      rejectedSubmission.current = false;
      setSubmissionNeedsRefresh(false);
      setSubmissionError(null);
    }
    setGroup(next);
    return true;
  };
  const refresh = async () => {
    if (!group) {
      setReload((value) => value + 1);
      await onProgressChanged();
      return;
    }
    try {
      const next = await apiClient.readAssessmentSet(studySessionId, group.set_id);
      if (accept(next)) {
        setMessage(null);
        return next;
      }
    } catch (error) {
      if (isCurrent()) setMessage(errorMessage(error));
    }
  };

  useEffect(() => {
    let cancelled = false;
    alive.current = true;
    setGroup(null);
    setMessage(null);
    setSelections({});
    setSubmissionError(null);
    submissionIntent.current = null;
    latestGroup.current = null;
    rejectedSubmission.current = false;
    setSubmissionNeedsRefresh(false);
    setBusy(false);
    setStarting(false);
    actionIntent.current = null;
    cycleIntent.current = null;
    void (async () => {
      try {
        if (selectedSetId) {
          // 恢復頁面已帶回同一 snapshot 的題組；重新整理題組時仍讀取最新版本。
          const restored = reload === 0 && initialSet?.set_id === selectedSetId
            ? initialSet : await apiClient.readAssessmentSet(studySessionId, selectedSetId);
          if (!cancelled) accept(restored);
        }
      } catch (error) {
        if (!cancelled) setMessage(errorMessage(error));
      }
    })();
    return () => {
      cancelled = true;
      alive.current = false;
    };
  }, [apiClient, studySessionId, selectedSetId, concept.concept_id, reload]);

  useEffect(() => {
    if (!group || group.status !== "preparing") return;
    const id = group.set_id;
    let cancelled = false,
      fetching = false;
    const timer = window.setInterval(async () => {
      if (fetching) return;
      fetching = true;
      try {
        const next = await apiClient.readAssessmentSet(studySessionId, id);
        if (!cancelled) {
          accept(next);
          setMessage(null);
          if (next.status !== "preparing") await onProgressChanged();
        }
      } catch (error) {
        if (!cancelled) setMessage(errorMessage(error));
      } finally {
        fetching = false;
      }
    }, 2000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [apiClient, studySessionId, group?.set_id, group?.status]);

  const phase = starting ? "preparing" : group ? assessmentPhase(group) : initialPhase;
  const answering = phase === "question";
  const presenceError = useAssessmentPresence(apiClient, materialId, group?.set_id ?? null, answering);
  useLayoutEffect(() => {
    onPhaseChange(phase);
  }, [phase, onPhaseChange]);
  useEffect(() => {
    preparationTitle.current?.focus();
  }, [phase]);

  const action = async (kind: AssessmentSetAction) => {
    if (!group || busy) return;
    setBusy(true);
    setStarting(kind === "retry");
    setMessage(null);
    if (actionIntent.current?.id !== group.set_id || actionIntent.current.action !== kind) {
      actionIntent.current = {
        id: group.set_id,
        action: kind,
        key: crypto.randomUUID(),
        version: group.set_version,
      };
    }
    const intent = actionIntent.current;
    try {
      accept(
        await apiClient.changeAssessmentSet(
          studySessionId,
          group.set_id,
          kind,
          intent.version,
          intent.key,
        ),
      );
      actionIntent.current = null;
      await onProgressChanged();
    } catch (error) {
      if (!isCurrent()) return;
      setMessage(errorMessage(error));
      if (error instanceof ApiClientError && error.reasonCode === "ASSESSMENT_SET_CONFLICT") {
        actionIntent.current = null;
        await refresh();
      }
    } finally {
      if (isCurrent()) {
        setBusy(false);
        setStarting(false);
      }
    }
  };

  const startRemediation = async () => {
    if (!group || busy) return;
    const cycle = group.cycle;
    const signature = cycle.diagnostic_set_id;
    if (cycleIntent.current?.signature !== signature) {
      cycleIntent.current = { signature, key: crypto.randomUUID(), version: cycle.set_version };
    }
    const intent = cycleIntent.current;
    setBusy(true);
    setStarting(true);
    setMessage(null);
    try {
      const next = await apiClient.createRemediationSet(
        studySessionId,
        cycle.diagnostic_set_id,
        intent.version,
        intent.key,
      );
      if (!isCurrent()) return;
      cycleIntent.current = null;
      onSetSelected(next.set_id);
    } catch (error) {
      if (!isCurrent()) return;
      setMessage(errorMessage(error));
      if (error instanceof ApiClientError && error.reasonCode === "ASSESSMENT_SET_CONFLICT") {
        cycleIntent.current = null;
        await refresh();
      }
    } finally {
      if (isCurrent()) {
        setBusy(false);
        setStarting(false);
      }
    }
  };

  const submit = async () => {
    if (!group?.can_complete || busy || submissionNeedsRefresh) return;
    if (!submissionIntent.current) {
      const answers = group.items.flatMap(({ assessment, feedback }) =>
        assessment
          ? [
              {
                assessment_revision: assessment.assessment_revision,
                question_id: assessment.question_id,
                selected_option_id:
                  feedback?.selected_option_id ?? selections[assessment.assessment_revision],
              },
            ]
          : [],
      );
      if (answers.some((answer) => !answer.selected_option_id)) {
        setSubmissionError("請先完成所有題目，再一起交卷。");
        return;
      }
      submissionIntent.current = { key: crypto.randomUUID(), version: group.set_version, answers };
    }
    const intent = submissionIntent.current;
    setBusy(true);
    setSubmissionError(null);
    try {
      accept(
        await apiClient.submitAssessmentSet(
          studySessionId,
          group.set_id,
          intent.answers,
          intent.version,
          intent.key,
        ),
      );
      // 交卷已由伺服器同交易解除；補送失敗不能把已保存成績當作交卷失敗。
      void apiClient.setAssessmentPresence(materialId, group.set_id, false).catch(() => undefined);
      submissionIntent.current = null;
      await onProgressChanged();
    } catch (error) {
      if (!isCurrent()) return;
      if (
        error instanceof ApiClientError &&
        error.status === 409 &&
        error.reasonCode === "ASSESSMENT_SET_CONFLICT"
      ) {
        // 明確拒絕後才能換掉舊提交意圖；網路結果未知時仍保留原 key／答案。
        rejectedSubmission.current = true;
        setSubmissionNeedsRefresh(true);
        const updated = await refresh();
        if (!isCurrent()) return;
        if (updated?.can_complete)
          setSubmissionError("題組已同步，答案選取已保留。確認後可再次交卷。");
        else if (!updated) setSubmissionError("尚未讀到最新題組，請先查回交卷結果再繼續。");
        if (updated) await onProgressChanged();
      } else setSubmissionError(errorMessage(error));
    } finally {
      if (isCurrent()) setBusy(false);
    }
  };

  const afterAnswer = async () => {
    await refresh();
    await onProgressChanged();
  };
  const closed = group?.status === "completed";
  const prepared = !!group && group.published_count > 0;
  const isRemediation = group?.kind === "remediation";
  const cycleFinished = !!group && ["passed", "incomplete"].includes(group.cycle.outcome);
  const questions: AssessmentRecordView[] =
    group?.items.flatMap((item) =>
      item.assessment && item.created_at
        ? [{ assessment: item.assessment, feedback: item.feedback, can_submit: item.can_submit }]
        : [],
    ) ?? [];
  const selectedCount = questions.filter(
    (record) => record.feedback || selections[record.assessment.assessment_revision],
  ).length;
  const unavailable = group
    ? group.requested_count - group.published_count + group.excluded_count
    : 0;
  const readError = message && (
    <div className="assessment-error" role="alert">
      <span>{message}</span>
      <button className="text-button" onClick={() => void refresh()}>
        重新讀取題組
      </button>
    </div>
  );
  const waiting = phase === "preparing" || phase === "intervention";
  const progressGroup = starting ? null : group;
  const total = progressGroup?.requested_count ?? 0;
  const ready = progressGroup?.verified_count ?? 0;
  const intervention = phase === "intervention";
  const paper = group && prepared && <AssessmentPaper key={group.set_id} api={apiClient} records={questions} view={view} history={history}
    completed={closed || completed} selections={selections} disabled={busy || !!submissionIntent.current}
    busy={busy} canSubmit={group.can_complete && !completed && !busy && !submissionNeedsRefresh && selectedCount === questions.length}
    submitLabel={busy ? "正在交卷…" : submissionError ? "重新交卷" : "交卷"}
    onSubmit={() => void submit()} onSelect={(revision, option) => {
      if (!submissionIntent.current) { setSelections(previous => ({ ...previous, [revision]: option })); setSubmissionError(null); }
    }}>
    {submissionError && <div className="assessment-error" role="alert"><span>{submissionError}</span>
      <button className="text-button" type="button" onClick={() => void afterAnswer()}>查回交卷結果</button></div>}
  </AssessmentPaper>;
  return (
    <section className="assessment-set-panel" aria-label={`${concept.label}的重點題組`}>
      {answering && presenceError && <p className="form-error" role="status">作答狀態暫時無法同步：{presenceError}</p>}
      {waiting && (
        <header className={`surface assessment-set-header${waiting ? " is-preparing" : ""}`}>
          {waiting ? (
            <>
              <div className="preparation-heading">
                <p className="eyebrow">{isRemediation ? "錯題重點補強" : "觀念重點檢測"}</p>
                <h2 ref={preparationTitle} tabIndex={-1}>
                  {starting
                    ? "正在開始本輪練習…"
                    : intervention
                      ? ready > 0
                        ? "部分題目已準備完成"
                        : "這次題目尚未準備完成。"
                      : "正在準備本輪練習"}
                </h2>
              </div>
              {intervention && (
                <p>
                  {ready > 0
                    ? `目前已準備 ${ready} 題${group?.can_publish_partial ? "，可以先開始練習" : ""}${group?.can_retry ? "，也可以再試著準備其餘題目" : ""}。`
                    : "你可以稍後再試，或先查看知識地圖。"}
                </p>
              )}
              {!intervention && <QuestionPreparation ready={ready} total={total} label="準備進度" />}
              {readError}
              {intervention && (
                <div className="assessment-set-actions">
                  {group?.can_publish_partial && (
                    <button
                      className="primary-button"
                      disabled={busy}
                      onClick={() => void action("publish-partial")}
                    >
                      先做已準備的 {ready} 題
                    </button>
                  )}
                  {group?.can_retry && (
                    <button
                      className="secondary-button"
                      disabled={busy}
                      onClick={() => void action("retry")}
                    >
                      再試一次
                    </button>
                  )}
                </div>
              )}
              {!intervention && (
                <p className="preparation-note">完成後會自動顯示題目；也可以先離開，稍後再回來。</p>
              )}
              <div className="assessment-set-actions preparation-navigation">
                <button className="text-button" onClick={onBackToMap}>
                  <Icon name="arrow-left" size={16} />
                  查看知識地圖
                </button>
              </div>
            </>
          ) : null}
        </header>
      )}
      {!waiting && group && closed && (
        <section className="surface assessment-cycle" aria-label="本輪檢測與補強">
          <p className="eyebrow">{isRemediation ? "錯題重點補強" : "觀念重點檢測"}</p>
          <h2>{isRemediation ? "本次補強結果" : "本輪檢測結果"}</h2>
          {readError}
          {group.cycle.outcome === "passed" ? (
            <p className="assessment-cycle-result">可檢測的重點皆已完成。</p>
          ) : group.cycle.outcome === "incomplete" ? (
            <p className="assessment-cycle-result">本輪有未作答或未檢測的重點。</p>
          ) : (
            group.cycle.pending_count > 0 && conceptCompleted && <p>本次練習有需要加強的重點，先前的完成紀錄會保留。</p>
          )}
          <p className="question-result-summary" aria-label="本輪檢測摘要">答對 {group.passed_count}／{group.published_count} 題 · {group.cycle.pending_count} 個重點需要補強</p>
          {(group.cycle.unanswered_count > 0 || unavailable > 0) && <p>未作答 {group.cycle.unanswered_count} 個重點 · 未檢測 {group.cycle.unavailable_count} 個重點</p>}
          <div className="assessment-set-actions">
            {group.cycle.active_set_id && group.cycle.active_set_id !== group.set_id
              ? <button className="primary-button" onClick={() => onSetSelected(group.cycle.active_set_id!)}>接續補強</button>
              : group.cycle.can_create_remediation
                ? <button className="primary-button" disabled={busy} onClick={() => void startRemediation()}>補強這 {group.cycle.pending_count} 個重點</button>
                : (cycleFinished || conceptCompleted) && !group.cycle.active_set_id && continuation
                  ? <button className="primary-button assessment-continue" disabled={busy || continuation.busy} aria-busy={continuation.busy} onClick={() => void continuation.onContinue()}>{continuation.label}</button>
                  : null}
            <button className="text-button" onClick={onBackToMap}>查看知識地圖</button>
          </div>
          {group.cycle.pending_count > 0 && <small>連續答對 2 道不同題，才算補強完成。</small>}
        </section>
      )}
      {!waiting && !closed && readError}
      {!waiting && paper}
    </section>
  );
}
