import { SourceButton, sourceLinks } from "../../ui/SourceButton";
import { useEffect, useLayoutEffect, useRef, useState } from "react";

import { ApiClientError, errorMessage, type StudydyApiClient } from "../../api/client";
import type { AnswerFeedbackView, AssessmentRecordView, AssessmentView, KnowledgeStructureView } from "../../api/contracts";
import { Icon } from "../../ui/Icon";
import "./styles.css";

type Concept = KnowledgeStructureView["concepts"][number];
type AssessmentError = { conflict: boolean; message: string; noSafeItem: boolean; retryable: boolean };

function assessmentError(error: unknown): AssessmentError {
  if (error instanceof ApiClientError && error.reasonCode === "NO_SAFE_ASSESSMENT") {
    return {
      message: "目前沒有可安全提供的新題目。你可以先閱讀教材重點，或稍後再試。",
      conflict: false,
      noSafeItem: true,
      retryable: false,
    };
  }
  if (error instanceof ApiClientError && error.reasonCode === "IDEMPOTENCY_CONFLICT") {
    return {
      message: "這次操作與較新的學習狀態衝突，請重新整理學習進度。",
      conflict: true,
      noSafeItem: false,
      retryable: false,
    };
  }
  return {
    conflict: false,
    message: errorMessage(error),
    noSafeItem: false,
    retryable: error instanceof ApiClientError ? error.retryable : true,
  };
}

export function AssessmentPanel({ apiClient, record, completed, isHistorical, historyQuestionNumber, onReturnLatest, onAssessmentCreated, concept, assessmentTargetClaimId, assessmentTargetInvalid, prerequisiteConcepts, onNoSafeReviewChange, onQuestionModeChange, onProgressChanged, onReloadSession, sourceArtifactId, studySessionId, view }: {
  apiClient: StudydyApiClient;
  record: AssessmentRecordView | null;
  completed: boolean;
  isHistorical: boolean;
  historyQuestionNumber: number | null;
  onReturnLatest: () => void;
  onAssessmentCreated: (revision: string) => void;
  concept: Concept;
  assessmentTargetClaimId: string | null;
  assessmentTargetInvalid: boolean;
  prerequisiteConcepts: Concept[];
  onNoSafeReviewChange: (active: boolean) => void;
  onQuestionModeChange: (active: boolean) => void;
  onProgressChanged: (activity: "answer" | "no_safe") => Promise<void>;
  onReloadSession: () => void;
  sourceArtifactId: string;
  studySessionId: string;
  view: KnowledgeStructureView;
}) {
  const [previewPrerequisiteId, setPreviewPrerequisiteId] = useState<string | null>(null);
  const [assessment, setAssessment] = useState<AssessmentView | null>(record?.assessment ?? null);
  const [selectedOptionId, setSelectedOptionId] = useState<string | null>(record?.feedback?.selected_option_id ?? null);
  const [feedback, setFeedback] = useState<AnswerFeedbackView | null>(record?.feedback ?? null);
  const [isLoading, setIsLoading] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const [requestError, setRequestError] = useState<AssessmentError | null>(null);
  const [submissionError, setSubmissionError] = useState<AssessmentError | null>(null);
  const assessmentIntent = useRef<{ claimId: string; key: string } | null>(null);
  const submissionIntent = useRef<{ optionId: string; key: string } | null>(null);
  const questionHeading = useRef<HTMLHeadingElement>(null);
  const errorReloadFrom = useRef<KnowledgeStructureView | null>(null);
  const historyContext = useRef<HTMLElement>(null);
  const previewHeading = useRef<HTMLHeadingElement>(null);
  const prerequisiteButtons = useRef(new Map<string, HTMLButtonElement>());
  const previewOpenerId = useRef<string | null>(null);
  const previewPrerequisite = !isHistorical && !assessment && !record && !completed && !requestError && !isLoading
    ? prerequisiteConcepts.find(item => item.concept_id === previewPrerequisiteId) : undefined;

  useEffect(() => {
    if (previewPrerequisite) previewHeading.current?.focus();
    else if (previewOpenerId.current) {
      prerequisiteButtons.current.get(previewOpenerId.current)?.focus();
      previewOpenerId.current = null;
    }
  }, [previewPrerequisite?.concept_id]);


  // resumeStudy supplies a new view only after the requested refresh succeeds.
  useEffect(() => {
    if (errorReloadFrom.current && errorReloadFrom.current !== view) {
      errorReloadFrom.current = null;
      setRequestError(null);
    }
  }, [view]);


  useEffect(() => {
    if (isHistorical) historyContext.current?.focus();
  }, [isHistorical, record?.assessment.assessment_revision]);

  // Current questions remain readable after create/resume, independently of history mode.
  useEffect(() => {
    if (!isHistorical && assessment && !feedback && !completed && record?.can_submit !== false) {
      questionHeading.current?.scrollIntoView({ block: "nearest" });
    }
  }, [assessment?.assessment_revision, feedback, completed, record?.can_submit, isHistorical]);


  useEffect(() => {
    if (!isLoading) return;
    const startedAt = Date.now();
    setElapsedSeconds(0);
    const timer = window.setInterval(() => {
      setElapsedSeconds(Math.floor((Date.now() - startedAt) / 1000));
    }, 1000);
    return () => window.clearInterval(timer);
  }, [isLoading]);

  const canCreateAssessment = !!assessmentTargetClaimId && !completed && !isSubmitting && !isHistorical;
  const canAnswer = !completed && record?.can_submit !== false
    && (!isHistorical || (!!assessmentTargetClaimId && assessment?.target_concept_id === concept.concept_id));
  const questionMode = !isHistorical && !completed && !requestError
    && (isLoading || (!!assessment && !feedback && canAnswer));
  useLayoutEffect(() => {
    onQuestionModeChange(questionMode);
    return () => onQuestionModeChange(false);
  }, [questionMode, onQuestionModeChange]);

  const historicalContext = isHistorical && record ? <section className="assessment-history-context" aria-label="歷史作答" tabIndex={-1} ref={historyContext}>
    <div><p className="eyebrow">歷史作答</p><p>第 {historyQuestionNumber} 題 · {record.feedback ? "已作答" : "尚未作答"}</p></div>
    <button className="secondary-button" type="button" onClick={onReturnLatest}>返回最新進度</button>
  </section> : null;

  const requestAssessment = async (newIntent: boolean) => {
    if (isLoading || !canCreateAssessment || !assessmentTargetClaimId) return;
    onQuestionModeChange(true);
    setPreviewPrerequisiteId(null);
    setIsLoading(true);
    setRequestError(null);
    setAssessment(null);
    setFeedback(null);
    setSelectedOptionId(null);
    const createForClaim = async (claimId: string, forceNewIntent: boolean) => {
      if (forceNewIntent || assessmentIntent.current?.claimId !== claimId) {
        assessmentIntent.current = { claimId, key: crypto.randomUUID() };
      }
      return apiClient.createAssessment(studySessionId, {
        schema: "assessment-create/v2",
        target_claim_id: claimId,
      }, assessmentIntent.current.key);
    };
    try {
      const next = await createForClaim(assessmentTargetClaimId, newIntent);
      setAssessment(next);
      onAssessmentCreated(next.assessment_revision);
      submissionIntent.current = null;
    } catch (error) {
      const nextError = assessmentError(error);
      onQuestionModeChange(false);
      setRequestError(nextError);
      if (nextError.noSafeItem) {
        onNoSafeReviewChange(true);
        await onProgressChanged("no_safe");
      }
    } finally {
      setIsLoading(false);
    }
  };

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
      await onProgressChanged("answer");
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
        {canCreateAssessment && <div className="assessment-actions">
          <button className="primary-button" type="button" onClick={() => void requestAssessment(true)}><Icon name="refresh" />繼續練習</button>
        </div>}
      </section>
    );
  }

  if (requestError) return (
    <section className="assessment-card assessment-unavailable" role="status">
      <span><Icon name={requestError.noSafeItem ? "book" : "warning"} size={28} /></span>
      <h2>{requestError.noSafeItem ? "目前沒有新的安全題目" : "暫時無法準備練習題"}</h2>
      <p>{requestError.message}</p>
      {requestError.noSafeItem && (
        <div className="evidence-review-activity">
          <h3>改用教材回顧</h3>
          <p>閱讀目前重點並回查教材頁面。本活動不送出答案，也不會改變你的掌握狀態。</p>
          {concept.claims.map((claim) => (
            <article key={claim.claim_id}>
              <strong>{claim.text}</strong>
              <div>
                {claim.evidence.map((evidence) => (
                  <SourceButton apiClient={apiClient} artifactId={sourceArtifactId} page={evidence.page} key={evidence.evidence_id} resolver={view.source_resolver} evidenceId={evidence.evidence_id} evidence={evidence}>查看教材第 {evidence.page} 頁<Icon name="chevron-right" /></SourceButton>
                ))}
              </div>
            </article>
          ))}
        </div>
      )}
      <div className="assessment-actions">
        {requestError.noSafeItem
          ? <button className="secondary-button" disabled={isLoading} type="button" onClick={() => { setRequestError(null); onNoSafeReviewChange(false); }}>完成本次回顧</button>
          : requestError.retryable && canCreateAssessment
            ? <button className="primary-button" type="button" onClick={() => void requestAssessment(false)}>再試一次</button>
            : <button className="secondary-button" type="button" onClick={() => {
              errorReloadFrom.current = view;
              onReloadSession();
            }}>重新整理學習進度</button>}
      </div>
    </section>
  );

  if (isLoading) return (
    <section className="assessment-card assessment-loading" aria-live="polite">
      <span className="loading-ring" aria-hidden="true" />
      <h2>正在準備練習題</h2>
      <p>系統正在依教材內容準備並檢查題目。</p>
      <strong className="assessment-elapsed">已等待 {elapsedSeconds} 秒</strong>
    </section>
  );

  if (!assessment && completed) return <section className="assessment-card"><h2>學習成果</h2><p>可從題目與作答紀錄選擇已保存的內容。</p></section>;

  if (!assessment && (assessmentTargetInvalid || !canCreateAssessment)) return (
    <section className="assessment-card" role="status">
      <h2>暫時無法準備目前練習</h2>
      <p>學習進度與教材重點不同步，請重新讀取。</p>
      <button className="secondary-button" type="button" onClick={onReloadSession}>重新整理學習進度</button>
    </section>
  );


  if (previewPrerequisite) {
    const references = sourceLinks(previewPrerequisite.claims.flatMap(claim => claim.evidence), view.source_resolver);
    return <section className="assessment-card assessment-prerequisite-preview" aria-labelledby="prerequisite-preview-heading">
      <p className="eyebrow">前置概念</p>
      <h2 id="prerequisite-preview-heading" ref={previewHeading} tabIndex={-1}>{previewPrerequisite.label}</h2>
      <h3>教材重點</h3>
      <ul>{previewPrerequisite.claims.map(claim => <li key={claim.claim_id}>{claim.text}</li>)}</ul>
      <section className="assessment-prerequisite-sources" aria-label="前置概念教材來源">
        <h3>教材來源</h3><div>{references.map(item => <SourceButton apiClient={apiClient} artifactId={sourceArtifactId} page={item.page} key={item.evidence_id} resolver={view.source_resolver} evidenceId={item.evidence_id} evidence={item}>第 {item.page} 頁<Icon name="chevron-right" /></SourceButton>)}</div>
      </section>
      <div className="assessment-actions"><button className="secondary-button" type="button" onClick={() => setPreviewPrerequisiteId(null)}>回到目前練習</button></div>
    </section>;
  }

  if (!assessment) return (
    <section className="assessment-card assessment-ready">
      <div><p className="eyebrow">理解練習</p><h2>準備好練習「{concept.label}」了嗎？</h2><p>系統會依你的學習進度與目前教材重點準備一道題目。</p></div>
      {!record && prerequisiteConcepts.length > 0 && <aside className="assessment-prerequisites" aria-label="建議先了解">
        <h3>建議先了解</h3>
        <ul className="assessment-prerequisite-actions">{prerequisiteConcepts.map(item => <li key={item.concept_id}>
          <button className="text-button" type="button" ref={node => { if (node) prerequisiteButtons.current.set(item.concept_id, node); else prerequisiteButtons.current.delete(item.concept_id); }}
            onClick={() => { previewOpenerId.current = item.concept_id; setPreviewPrerequisiteId(item.concept_id); }}>查看「{item.label}」</button>
        </li>)}</ul>
        <p>{prerequisiteConcepts.length === 1 ? "這個內容" : "這些內容"}目前尚未在學習進度中掌握。如果你已經熟悉，可以直接開始練習；需要時也可以先查看內容。</p>
      </aside>}
      <button className="primary-button" type="button" onClick={() => void requestAssessment(true)}><Icon name="learning" />開始練習</button>
    </section>
  );

  return (
    <section className="assessment-card" aria-labelledby="assessment-question">
      {historicalContext}
      <p className="eyebrow">單選題</p>
      <h2 id="assessment-question" ref={questionHeading}>{assessment.prompt}</h2>
      <fieldset className="assessment-options" disabled={isSubmitting || !canAnswer}>
        <legend className="sr-only">請選擇一個答案</legend>
        {assessment.options.map((option, index) => (
          <label className={selectedOptionId === option.option_id ? "is-selected" : undefined} key={option.option_id}>
            <input
              type="radio"
              name="assessment-option"
              value={option.option_id}
              checked={selectedOptionId === option.option_id}
              onChange={() => {
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
      {canAnswer && <button className="primary-button assessment-submit" aria-busy={isSubmitting} disabled={!selectedOptionId || isSubmitting || submissionError?.conflict} type="button" onClick={() => void submit()}>
        {isSubmitting ? "正在送出…" : submissionError?.retryable ? "重新送出" : "送出答案"}
      </button>}
    </section>
  );
}
