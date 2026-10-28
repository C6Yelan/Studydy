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
  fresh,
  number,
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
  fresh?: boolean;
  number: number;
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
    const correct = feedback.is_correct;
    const optionText = (id: string) => assessment.options.find(option => option.option_id === id)?.text;
    return (
      <section
        className={`assessment-card feedback-card is-${correct ? "correct" : "incorrect"}${fresh ? " feedback-enter" : ""}`}
      >
        <header className="feedback-result">
          <span className="feedback-icon" aria-hidden="true">
            <Icon name={correct ? "check" : "warning"} size={18} />
          </span>
          <span className="sr-only">{correct ? "答對了" : "這題需要再想一下"}</span>
          <b className="feedback-number">第 {number} 題</b>
        </header>
        <p className="feedback-prompt"><MathText text={assessment.prompt}/></p>
        <section className="feedback-section feedback-yours">
          <h3>你的答案</h3>
          <p><MathText text={(feedback.selected_option_id ? optionText(feedback.selected_option_id) : undefined) ?? ""}/></p>
        </section>
        <section className="feedback-section feedback-correct">
          <h3>正確答案</h3>
          <p><MathText text={(feedback.correct_option_id ? optionText(feedback.correct_option_id) : undefined) ?? "這份作答紀錄尚未提供正確答案。"}/></p>
        </section>
        {/* 解析只顯示對應重點（整理過的文字）；教材原文改由下方來源按鈕查看，不直接堆在卡片上。 */}
        <section className="feedback-section feedback-evidence">
          <h3>解析</h3>
          {claim && <p><MathText text={claim.text}/></p>}
          {evidenceLinks.map((item) => (
            <SourceButton
              apiClient={apiClient}
              key={item.evidence_id}
              resolver={view.source_resolver}
              evidence={item}
            />
          ))}
        </section>
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
