import { MathText } from "../../ui/MathText";
import { SourceButton, evidencePlace, sourceLinks } from "../../ui/SourceButton";
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
    const rationale = feedback.rationale || claim?.text;
    const evidence = view.concepts
      .flatMap((item) => item.claims)
      .flatMap((claim) => claim.evidence)
      .filter((item) => feedback.source_evidence_ids.includes(item.evidence_id));
    const evidenceLinks = sourceLinks(evidence);
    const correct = feedback.is_correct;
    const optionText = (id: string) => assessment.options.find(option => option.option_id === id)?.text;
    const selectedText = feedback.selected_option_id ? optionText(feedback.selected_option_id) : undefined;
    const correctText = feedback.correct_option_id ? optionText(feedback.correct_option_id) : undefined;
    // 一題一列：對錯、題目、一句解析、教材頁碼；完整的答案與解析在展開區。
    return (
      <details
        className={`assessment-card feedback-card is-${correct ? "correct" : "incorrect"}${fresh ? " feedback-enter" : ""}`}
      >
        <summary className="feedback-summary">
          <span className="feedback-icon">
            <Icon name={correct ? "check" : "warning"} size={18} />
            <span className="sr-only">{correct ? "答對了" : "這題需要再想一下"}</span>
          </span>
          <span className="feedback-line">
            <b className="feedback-prompt">{number}. <MathText text={assessment.prompt}/></b>
            {!correct && selectedText && (
              <span className="feedback-pick">你選了「<MathText text={selectedText}/>」{correctText && <>，正確是「<MathText text={correctText}/>」</>}</span>
            )}
            {rationale && <span className="feedback-brief"><MathText text={rationale}/></span>}
          </span>
          {evidenceLinks[0] && <span className="feedback-page">教材{evidencePlace(evidenceLinks[0])}</span>}
          <span className="feedback-chevron" aria-hidden="true"><Icon name="chevron-right" size={16} /></span>
        </summary>
        <div className="feedback-detail">
          <section className="feedback-section feedback-yours">
            <h3>你的答案</h3>
            <p><MathText text={selectedText ?? ""}/></p>
          </section>
          <section className="feedback-section feedback-correct">
            <h3>正確答案</h3>
            <p><MathText text={correctText ?? "這份作答紀錄尚未提供正確答案。"}/></p>
          </section>
          <section className="feedback-section feedback-evidence">
            <h3>解析</h3>
            {rationale && <p><MathText text={rationale}/></p>}
            {evidenceLinks.map((item) => (
              <SourceButton
                apiClient={apiClient}
                key={item.evidence_id}
                resolver={view.source_resolver}
                evidence={item}
              />
            ))}
          </section>
        </div>
      </details>
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
            <span>{index + 1}</span>
            <strong><MathText text={option.text}/></strong>
          </label>
        ))}
      </fieldset>
      {!canAnswer && <p className="assessment-readonly">這題目前僅供回顧</p>}
    </section>
  );
}
