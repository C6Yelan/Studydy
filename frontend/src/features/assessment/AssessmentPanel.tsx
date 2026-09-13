import { useEffect, useRef, useState } from "react";

import { ApiClientError, errorMessage, type StudydyApiClient } from "../../api/client";
import type { AnswerFeedbackView, AssessmentRecordView, AssessmentView, KnowledgeStructureView } from "../../api/contracts";
import { Icon } from "../../ui/Icon";
import "./styles.css";

type Concept = KnowledgeStructureView["concepts"][number];
type AssessmentError = { conflict: boolean; message: string; noSafeItem: boolean; retryable: boolean };

function assessmentError(error: unknown): AssessmentError {
  if (error instanceof ApiClientError && error.reasonCode === "NO_SAFE_ASSESSMENT") {
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

export function AssessmentPanel({ apiClient, record, completed, onAssessmentCreated, concept, recommendedClaimId, onProgressChanged, onReloadSession, sourceArtifactId, studySessionId, view }: {
  apiClient: StudydyApiClient;
  record: AssessmentRecordView | null;
  completed: boolean;
  onAssessmentCreated: (revision: string) => void;
  concept: Concept;
  recommendedClaimId: string | null;
  onProgressChanged: () => void;
  onReloadSession: () => void;
  sourceArtifactId: string;
  studySessionId: string;
  view: KnowledgeStructureView;
}) {
  const [selectedClaimId, setSelectedClaimId] = useState(concept.claims[0].claim_id);
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

  // Creating or resuming an unanswered question remounts this panel at its exact route.
  useEffect(() => {
    if (assessment && !feedback && !completed && record?.can_submit !== false) {
      questionHeading.current?.scrollIntoView({ block: "nearest" });
    }
  }, [assessment?.assessment_revision, feedback, completed, record?.can_submit]);


  useEffect(() => {
    if (recommendedClaimId && concept.claims.some((claim) => claim.claim_id === recommendedClaimId)) {
      setSelectedClaimId(recommendedClaimId);
      assessmentIntent.current = null;
    }
  }, [concept.concept_id, recommendedClaimId]);

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
    if (isLoading || completed) return;
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
      const next = await createForClaim(selectedClaimId, newIntent);
      setAssessment(next);
      onAssessmentCreated(next.assessment_revision);
      submissionIntent.current = null;
    } catch (error) {
      const nextError = assessmentError(error);
      setRequestError(nextError);
      if (nextError.noSafeItem) onProgressChanged();
    } finally {
      setIsLoading(false);
    }
  };

  const submit = async () => {
    if (!assessment || !selectedOptionId || isSubmitting || completed || record?.can_submit === false) return;
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
      onProgressChanged();
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
    const evidencePages = [...new Set(evidence.map((item) => item.page))];
    return (
      <section className={`assessment-card feedback-card is-${feedback.is_correct ? "correct" : "incorrect"}`} aria-live="polite">
        <span className="feedback-icon"><Icon name={feedback.is_correct ? "check" : "warning"} size={28} /></span>
        <p className="eyebrow">作答回饋</p>
        <h2>{feedback.is_correct ? "答對了" : "這題需要再想一下"}</h2>
        <p>{assessment.prompt}</p>
        <p>你的作答：{assessment.options.find(option => option.option_id === feedback.selected_option_id)?.text}</p>
        <p className="feedback-rationale">{feedback.rationale}</p>
        <div className="feedback-evidence">
          <h3>教材依據</h3>
          {evidencePages.map((page) => (
            <button
              className="text-button"
              key={page}
              type="button"
              onClick={() => window.open(
                apiClient.sourceArtifactUrl(sourceArtifactId, page),
                "_blank",
                "noopener,noreferrer",
              )}
            >原始教材第 {page} 頁<Icon name="chevron-right" /></button>
          ))}
        </div>
        <div className="assessment-actions">
          <button className="secondary-button" type="button" onClick={() => {
            setAssessment(null);
            setFeedback(null);
            setSelectedOptionId(null);
          }}>回到教材</button>
          {!completed && <button className="secondary-button" type="button" onClick={() => void requestAssessment(true)}><Icon name="refresh" />取得目前概念的新題目</button>}
        </div>
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
                  <button
                    className="text-button"
                    key={evidence.evidence_id}
                    type="button"
                    onClick={() => window.open(
                      apiClient.sourceArtifactUrl(sourceArtifactId, evidence.page),
                      "_blank",
                      "noopener,noreferrer",
                    )}
                  >查看教材第 {evidence.page} 頁<Icon name="chevron-right" /></button>
                ))}
              </div>
            </article>
          ))}
        </div>
      )}
      <div className="assessment-actions">
        <button className="secondary-button" type="button" onClick={() => setRequestError(null)}>{requestError.noSafeItem ? "完成本次回顧" : "回到教材"}</button>
        {requestError.conflict && <button className="secondary-button" type="button" onClick={onReloadSession}>重新整理本次學習</button>}
        {requestError.retryable && <button className="primary-button" type="button" onClick={() => void requestAssessment(false)}>再試一次</button>}
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

  if (!assessment && completed) return <section className="assessment-card"><h2>本次學習已結束</h2><p>可從題目與作答紀錄選擇已保存的內容。</p></section>;

  if (!assessment) return (
    <section className="assessment-card assessment-ready">
      <div><p className="eyebrow">理解練習</p><h2>準備好練習「{concept.label}」了嗎？</h2><p>系統會依目前教材重點準備一道題目。</p></div>
      {concept.claims.length > 1 && (
        <details className="assessment-claim-picker"><summary>更換練習重點</summary><fieldset className="claim-picker">
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
        </fieldset></details>
      )}
      <button className="primary-button" type="button" onClick={() => void requestAssessment(true)}><Icon name="learning" />開始練習</button>
    </section>
  );

  return (
    <section className="assessment-card" aria-labelledby="assessment-question">
      <p className="eyebrow">單選題</p>
      <h2 id="assessment-question" ref={questionHeading}>{assessment.prompt}</h2>
      <fieldset className="assessment-options" disabled={isSubmitting || completed || record?.can_submit === false}>
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
      {(completed || record?.can_submit === false) && <p>這題已不在可作答的學習位置，僅供回顧。</p>}
      <button className="primary-button assessment-submit" disabled={!selectedOptionId || isSubmitting || submissionError?.conflict || completed || record?.can_submit === false} type="button" onClick={() => void submit()}>
        {isSubmitting ? "正在送出…" : submissionError?.retryable ? "重新送出" : "送出答案"}
      </button>
    </section>
  );
}
