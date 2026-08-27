import { useEffect, useRef, useState } from "react";

import { ApiClientError, errorMessage, type StudydyApiClient } from "../../api/client";
import type { AnswerFeedbackView, AssessmentView, KnowledgeMapView } from "../../api/contracts";
import { Icon } from "../../ui/Icon";
import "./styles.css";

type Concept = KnowledgeMapView["concepts"][number];
type AssessmentError = { conflict: boolean; message: string; noSafeItem: boolean; retryable: boolean };

function assessmentError(error: unknown): AssessmentError {
  if (error instanceof ApiClientError && error.reasonCode === "RESOURCE_NOT_FOUND") {
    return {
      message: "目前沒有可安全提供的新題目。你可以先回到教材重點，或稍後再試。",
      conflict: false,
      noSafeItem: true,
      retryable: false,
    };
  }
  if (error instanceof ApiClientError && error.reasonCode === "IDEMPOTENCY_CONFLICT") {
    return {
      message: "這次操作與較新的學習狀態衝突，請重新整理本次學習。",
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

export function AssessmentPanel({ apiClient, concept, onNoSafeItem, onReloadSession, onSubmitted, sourceArtifactId, studySessionId, view }: {
  apiClient: StudydyApiClient;
  concept: Concept;
  onNoSafeItem: (isUnavailable: boolean) => void;
  onReloadSession: () => void;
  onSubmitted: (feedback: AnswerFeedbackView) => void;
  sourceArtifactId: string;
  studySessionId: string;
  view: KnowledgeMapView;
}) {
  const [selectedClaimId, setSelectedClaimId] = useState(concept.claims[0].claim_id);
  const [assessment, setAssessment] = useState<AssessmentView | null>(null);
  const [selectedOptionId, setSelectedOptionId] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<AnswerFeedbackView | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const [requestError, setRequestError] = useState<AssessmentError | null>(null);
  const [submissionError, setSubmissionError] = useState<AssessmentError | null>(null);
  const assessmentIntent = useRef<{ claimId: string; key: string } | null>(null);
  const submissionIntent = useRef<{ optionId: string; key: string } | null>(null);

  useEffect(() => {
    setSelectedClaimId(concept.claims[0].claim_id);
    setAssessment(null);
    setSelectedOptionId(null);
    setFeedback(null);
    setRequestError(null);
    setSubmissionError(null);
    onNoSafeItem(false);
    assessmentIntent.current = null;
    submissionIntent.current = null;
  }, [concept.formal_concept_id, concept.claims]);

  useEffect(() => {
    if (!isLoading) return;
    const startedAt = Date.now();
    setElapsedSeconds(0);
    const timer = window.setInterval(() => {
      setElapsedSeconds(Math.floor((Date.now() - startedAt) / 1000));
    }, 1000);
    return () => window.clearInterval(timer);
  }, [isLoading]);

  const requestAssessment = async (newIntent: boolean) => {
    if (isLoading) return;
    if (newIntent || assessmentIntent.current?.claimId !== selectedClaimId) {
      assessmentIntent.current = { claimId: selectedClaimId, key: crypto.randomUUID() };
    }
    setIsLoading(true);
    onNoSafeItem(false);
    setRequestError(null);
    setAssessment(null);
    setFeedback(null);
    setSelectedOptionId(null);
    try {
      const next = await apiClient.createAssessment(studySessionId, {
        schema: "assessment-create/v1",
        target_claim_id: selectedClaimId,
      }, assessmentIntent.current.key);
      setAssessment(next);
      submissionIntent.current = null;
    } catch (error) {
      const nextError = assessmentError(error);
      setRequestError(nextError);
      onNoSafeItem(nextError.noSafeItem);
    } finally {
      setIsLoading(false);
    }
  };

  const submit = async () => {
    if (!assessment || !selectedOptionId || isSubmitting) return;
    if (submissionIntent.current?.optionId !== selectedOptionId) {
      submissionIntent.current = { optionId: selectedOptionId, key: crypto.randomUUID() };
    }
    setIsSubmitting(true);
    setSubmissionError(null);
    try {
      const next = await apiClient.submitAssessmentAnswer(studySessionId, assessment.assessment_revision, {
        schema: "answer-submission-create/v1",
        question_id: assessment.question_id,
        selected_option_id: selectedOptionId,
      }, submissionIntent.current.key);
      setFeedback(next);
      onSubmitted(next);
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
    return (
      <section className={`assessment-card feedback-card is-${feedback.is_correct ? "correct" : "incorrect"}`} aria-live="polite">
        <span className="feedback-icon"><Icon name={feedback.is_correct ? "check" : "warning"} size={28} /></span>
        <p className="eyebrow">作答回饋</p>
        <h2>{feedback.is_correct ? "答對了" : "這題需要再想一下"}</h2>
        <p className="feedback-rationale">{feedback.rationale}</p>
        <div className="feedback-evidence">
          <h3>教材依據</h3>
          {evidence.map((item) => (
            <button
              className="text-button"
              key={item.evidence_id}
              type="button"
              onClick={() => window.open(
                apiClient.sourceArtifactUrl(sourceArtifactId, item.page_number),
                "_blank",
                "noopener,noreferrer",
              )}
            >原始教材第 {item.page_number} 頁<Icon name="chevron-right" /></button>
          ))}
        </div>
        <div className="assessment-actions">
          <button className="secondary-button" type="button" onClick={() => {
            setAssessment(null);
            setFeedback(null);
            setSelectedOptionId(null);
          }}>回到教材</button>
          <button className="primary-button" type="button" onClick={() => void requestAssessment(true)}><Icon name="refresh" />取得新題目</button>
        </div>
      </section>
    );
  }

  if (requestError) return (
    <section className="assessment-card assessment-unavailable" role="status">
      <span><Icon name={requestError.noSafeItem ? "book" : "warning"} size={28} /></span>
      <h2>{requestError.noSafeItem ? "目前沒有新的安全題目" : "暫時無法建立評量"}</h2>
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
                  <button
                    className="text-button"
                    key={evidence.evidence_id}
                    type="button"
                    onClick={() => window.open(
                      apiClient.sourceArtifactUrl(sourceArtifactId, evidence.page_number),
                      "_blank",
                      "noopener,noreferrer",
                    )}
                  >查看教材第 {evidence.page_number} 頁<Icon name="chevron-right" /></button>
                ))}
              </div>
            </article>
          ))}
        </div>
      )}
      <div className="assessment-actions">
        <button className="secondary-button" type="button" onClick={() => setRequestError(null)}>{requestError.noSafeItem ? "完成本次回顧" : "回到教材"}</button>
        {requestError.retryable && <button className="primary-button" type="button" onClick={() => void requestAssessment(false)}>再試一次</button>}
      </div>
    </section>
  );

  if (isLoading) return (
    <section className="assessment-card assessment-loading" aria-live="polite">
      <span className="loading-ring" aria-hidden="true" />
      <h2>正在準備評量</h2>
      <p>目前階段：後端正在根據教材內容產生並驗證題目。</p>
      <strong className="assessment-elapsed">已等待 {elapsedSeconds} 秒</strong>
    </section>
  );

  if (!assessment) return (
    <section className="assessment-card assessment-ready">
      <div><p className="eyebrow">理解練習</p><h2>用一題確認目前理解</h2><p>送出後由系統評分，作答前不會顯示正確選項。</p></div>
      {concept.claims.length > 1 && (
        <fieldset className="claim-picker">
          <legend>選擇要練習的教材重點</legend>
          {concept.claims.map((claim, index) => (
            <label key={claim.claim_id}>
              <input
                type="radio"
                name="target-claim"
                value={claim.claim_id}
                checked={selectedClaimId === claim.claim_id}
                onChange={() => {
                  setSelectedClaimId(claim.claim_id);
                  assessmentIntent.current = null;
                }}
              />重點 {index + 1}：{claim.text}
            </label>
          ))}
        </fieldset>
      )}
      <button className="primary-button" type="button" onClick={() => void requestAssessment(true)}><Icon name="learning" />開始評量</button>
    </section>
  );

  return (
    <section className="assessment-card" aria-labelledby="assessment-question">
      <p className="eyebrow">單選評量</p>
      <h2 id="assessment-question">{assessment.prompt}</h2>
      <fieldset className="assessment-options" disabled={isSubmitting}>
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
          {submissionError.conflict && <button className="text-button" type="button" onClick={onReloadSession}>重新整理本次學習</button>}
        </div>
      )}
      <button className="primary-button assessment-submit" disabled={!selectedOptionId || isSubmitting || submissionError?.conflict} type="button" onClick={() => void submit()}>
        {isSubmitting ? "正在送出…" : submissionError?.retryable ? "重新送出" : "送出答案"}
      </button>
    </section>
  );
}
