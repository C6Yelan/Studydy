import { MathText } from "../../ui/MathText";
import { SourceButton, sourceLinks } from "../../ui/SourceButton";
import type { StudydyApiClient } from "../../api/client";
import type { AssessmentRecordView, KnowledgeStructureView } from "../../api/contracts";
import { Icon } from "../../ui/Icon";
import "./styles.css";

export function AssessmentPanel({
  apiClient,
  record,
  completed,
  view,
  answerSelection,
}: {
  apiClient: StudydyApiClient;
  record: AssessmentRecordView;
  completed: boolean;
  view: Pick<KnowledgeStructureView, "source_resolver" | "concepts">;
  answerSelection: {
    value: string | null;
    disabled: boolean;
    onChange: (optionId: string) => void;
  };
}) {
  const assessment = record.assessment;
  const feedback = record.feedback;
  const selection = answerSelection.value;
  const canAnswer = !completed && record.can_submit;
  if (feedback && completed) {
    const claim = view.concepts.flatMap(item => item.claims).find(item => item.claim_id === assessment.target_claim_id);
    const evidence = view.concepts
      .flatMap((item) => item.claims)
      .flatMap((claim) => claim.evidence)
      .filter((item) => feedback.source_evidence_ids.includes(item.evidence_id));
    const evidenceLinks = sourceLinks(evidence);
    return (
      <section
        className={`assessment-card feedback-card is-${feedback.is_correct ? "correct" : "incorrect"}`}
        aria-live="polite"
      >
        <header className="feedback-result">
          <span className="feedback-icon" aria-hidden="true">
            <Icon name={feedback.is_correct ? "check" : "warning"} size={24} />
          </span>
          <h2>{feedback.is_correct ? "答對了" : "這題需要再想一下"}</h2>
        </header>
        <section className="feedback-section">
          <h3>題目</h3>
          <p><MathText text={assessment.prompt}/></p>
        </section>
        <section className="feedback-section">
          <h3>你的答案</h3>
          <p><MathText text={assessment.options.find(option => option.option_id === feedback.selected_option_id)?.text ?? ""}/></p>
        </section>
        <section className="feedback-section">
          <h3>正確答案</h3>
          <p><MathText text={assessment.options.find(option => option.option_id === feedback.correct_option_id)?.text ?? "這份作答紀錄尚未提供正確答案。"}/></p>
        </section>
        <section className="feedback-section">
          <h3>解析</h3>
          <p className="feedback-rationale"><MathText text={feedback.rationale}/></p>
        </section>
        <div className="feedback-evidence">
          <h3>對應重點與原文來源</h3>
          {claim && <p><MathText text={claim.text}/></p>}
          {evidenceLinks.map((item) => (
            <SourceButton
              apiClient={apiClient}
              key={item.evidence_id}
              resolver={view.source_resolver}
              evidence={item}
            />
          ))}
        </div>
      </section>
    );
  }

  const questionId = `assessment-question-${assessment.question_id}`;
  return (
    <section className="assessment-card" aria-labelledby={questionId}>
      <p className="eyebrow">單選題</p>
      <h2 id={questionId}><MathText text={assessment.prompt}/></h2>
      <fieldset className="assessment-options" disabled={!canAnswer || answerSelection.disabled}>
        <legend className="sr-only">請選擇一個答案</legend>
        {assessment.options.map((option, index) => (
          <label
            className={selection === option.option_id ? "is-selected" : undefined}
            key={option.option_id}
          >
            <input
              className="question-option-input"
              type="radio"
              name={`assessment-option-${assessment.question_id}`}
              value={option.option_id}
              checked={selection === option.option_id}
              onChange={() => answerSelection.onChange(option.option_id)}
            />
            <span>{String.fromCharCode(65 + index)}</span>
            <strong><MathText text={option.text}/></strong>
          </label>
        ))}
      </fieldset>
      {!canAnswer && <p className="assessment-readonly">這題目前僅供回顧</p>}
    </section>
  );
}
