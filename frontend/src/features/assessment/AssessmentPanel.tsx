import { SourceButton, sourceLinks } from "../../ui/SourceButton";
import { useEffect, useLayoutEffect, useRef, useState } from "react";

import { ApiClientError, errorMessage, type StudydyApiClient } from "../../api/client";
import type { AnswerFeedbackView, AssessmentRecordView, KnowledgeStructureView } from "../../api/contracts";
import { Icon } from "../../ui/Icon";
import "./styles.css";

type AssessmentError = { conflict: boolean; message: string; retryable: boolean };
function assessmentError(error: unknown): AssessmentError {
  return {
    conflict: error instanceof ApiClientError && error.reasonCode === "IDEMPOTENCY_CONFLICT",
    message: errorMessage(error),
    retryable: error instanceof ApiClientError ? error.retryable : true,
  };
}

export function AssessmentPanel({ apiClient, record, completed, isHistorical, historyQuestionNumber, onReturnLatest,
  onQuestionModeChange, onProgressChanged, onReloadSession, sourceArtifactId, studySessionId, view, embedded = false, answerSelection }: {
  embedded?: boolean;
  answerSelection?: { value: string | null; disabled: boolean; onChange: (optionId: string) => void };
  apiClient: StudydyApiClient;
  record: AssessmentRecordView | null;
  completed: boolean;
  isHistorical: boolean;
  historyQuestionNumber: number | null;
  onReturnLatest: () => void;
  onQuestionModeChange: (active: boolean) => void;
  onProgressChanged: () => Promise<void>;
  onReloadSession: () => void;
  sourceArtifactId: string;
  studySessionId: string;
  view: KnowledgeStructureView;
}) {
  const assessment = record?.assessment ?? null;
  const [selectedOptionId, setSelectedOptionId] = useState<string | null>(record?.feedback?.selected_option_id ?? null);
  const [submittedFeedback, setFeedback] = useState<AnswerFeedbackView | null>(record?.feedback ?? null);
  const feedback = record?.feedback ?? submittedFeedback;
  const selection = answerSelection ? answerSelection.value : selectedOptionId;
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [submissionError, setSubmissionError] = useState<AssessmentError | null>(null);
  const submissionIntent = useRef<{ optionId: string; key: string } | null>(null);
  const questionHeading = useRef<HTMLHeadingElement>(null);
  const historyContext = useRef<HTMLElement>(null);
  // 題組只更新有新回饋的題卡，其他題尚未提交的選取保持原樣。
  useEffect(() => {
    if (record?.feedback) {
      setFeedback(record.feedback);
      setSelectedOptionId(record.feedback.selected_option_id);
      setSubmissionError(null);
    }
  }, [record?.feedback]);

  useEffect(() => {
    if (isHistorical) historyContext.current?.focus();
  }, [isHistorical, record?.assessment.assessment_revision]);

  // Current questions remain readable after create/resume, independently of history mode.
  useEffect(() => {
    if (!embedded && !isHistorical && assessment && !feedback && !completed && record?.can_submit !== false) {
      questionHeading.current?.scrollIntoView({ block: "nearest" });
    }
  }, [assessment?.assessment_revision, feedback, completed, record?.can_submit, isHistorical]);


  const canAnswer = !completed && record?.can_submit === true;
  const questionMode = !isHistorical && !completed && !!assessment && !feedback && canAnswer;
  useLayoutEffect(() => {
    if (embedded) return;
    onQuestionModeChange(questionMode);
    return () => onQuestionModeChange(false);
  }, [questionMode, onQuestionModeChange, embedded]);

  const historicalContext = isHistorical && record ? <section className="assessment-history-context" aria-label="歷史作答" tabIndex={-1} ref={historyContext}>
    <div><p className="eyebrow">歷史作答</p><p>第 {historyQuestionNumber} 題 · {record.feedback ? "已作答" : "尚未作答"}</p></div>
    <button className="secondary-button" type="button" onClick={onReturnLatest}>返回最新進度</button>
  </section> : null;

  const submit = async () => {
    if (!assessment || !selectedOptionId || isSubmitting || !canAnswer) return;
    if (submissionIntent.current?.optionId !== selectedOptionId) {
      submissionIntent.current = { optionId: selectedOptionId, key: crypto.randomUUID() };
    }
    setIsSubmitting(true);
    setSubmissionError(null);
    try {
      const next = await apiClient.submitAssessmentAnswer(studySessionId, assessment.assessment_revision, {
        schema: "answer-submission-create/v2",
        question_id: assessment.question_id,
        selected_option_id: selectedOptionId,
      }, submissionIntent.current.key);
      setFeedback(next);
      onQuestionModeChange(false);
      await onProgressChanged();
    } catch (error) {
      setSubmissionError(assessmentError(error));
    } finally {
      setIsSubmitting(false);
    }
  };

  if (feedback && assessment) {
    const evidence = view.concepts
      .flatMap((item) => item.claims)
      .flatMap((claim) => claim.evidence)
      .filter((item) => feedback.source_evidence_ids.includes(item.evidence_id));
    const evidenceLinks = sourceLinks(evidence, view.source_resolver);
    return (
      <section className={`assessment-card feedback-card is-${feedback.is_correct ? "correct" : "incorrect"}`} aria-live={isHistorical ? "off" : "polite"}>
        {historicalContext}
        <header className="feedback-result">
          <span className="feedback-icon" aria-hidden="true"><Icon name={feedback.is_correct ? "check" : "warning"} size={24} /></span>
          <div><p className="eyebrow">作答回饋</p><h2>{feedback.is_correct ? "答對了" : "這題需要再想一下"}</h2></div>
        </header>
        <section className="feedback-section"><h3>題目</h3><p>{assessment.prompt}</p></section>
        <section className="feedback-section"><h3>你的答案</h3><p>{assessment.options.find(option => option.option_id === feedback.selected_option_id)?.text}</p></section>
        <section className="feedback-section"><h3>為什麼？</h3><p className="feedback-rationale">{feedback.rationale}</p></section>
        <div className="feedback-evidence">
          <h3>教材依據</h3>
          {evidenceLinks.map((item) => (
            <SourceButton apiClient={apiClient} artifactId={sourceArtifactId} page={item.page} key={item.evidence_id} resolver={view.source_resolver} evidenceId={item.evidence_id} evidence={item}>原始教材第 {item.page} 頁<Icon name="chevron-right" /></SourceButton>
          ))}
        </div>
      </section>
    );
  }

  if (!assessment) return <section className="assessment-card"><h2>學習成果</h2><p>可從題目與作答紀錄選擇已保存的內容。</p></section>;

  const questionId = embedded ? `assessment-question-${assessment.question_id}` : "assessment-question";
  return (
    <section className="assessment-card" aria-labelledby={questionId}>
      {historicalContext}
      <p className="eyebrow">單選題</p>
      <h2 id={questionId} ref={questionHeading}>{assessment.prompt}</h2>
      <fieldset className="assessment-options" disabled={isSubmitting || !canAnswer || !!submissionError?.retryable || answerSelection?.disabled}>
        <legend className="sr-only">請選擇一個答案</legend>
        {assessment.options.map((option, index) => (
          <label className={selection === option.option_id ? "is-selected" : undefined} key={option.option_id}>
            <input
              type="radio"
              name={embedded ? `assessment-option-${assessment.question_id}` : "assessment-option"}
              value={option.option_id}
              checked={selection === option.option_id}
              onChange={() => {
                if (answerSelection) { answerSelection.onChange(option.option_id); return; }
                setSelectedOptionId(option.option_id);
                setSubmissionError(null);
                submissionIntent.current = null;
              }}
            />
            <span>{String.fromCharCode(65 + index)}</span>
            <strong>{option.text}</strong>
          </label>
        ))}
      </fieldset>
      {submissionError && (
        <div className="assessment-error" role="alert">
          <span>{submissionError.message}</span>
          <button className="text-button" type="button" onClick={onReloadSession}>查回作答結果</button>
        </div>
      )}
      {!canAnswer && <p className="assessment-readonly">這題目前僅供回顧</p>}
      {canAnswer && !embedded && <button className="primary-button assessment-submit" aria-busy={isSubmitting} disabled={!selectedOptionId || isSubmitting || submissionError?.conflict} type="button" onClick={() => void submit()}>
        {isSubmitting ? "正在送出…" : submissionError?.retryable ? "重新送出" : "送出答案"}
      </button>}
    </section>
  );
}
