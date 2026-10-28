import { useRef, useState, type ReactNode } from "react";
import type { AssessmentRecordView, KnowledgeStructureView } from "../../api/contracts";
import type { StudydyApiClient } from "../../api/client";
import { Progress } from "../../ui/Progress";
import { AssessmentPanel } from "./AssessmentPanel";
import "./paper.css";

export function AssessmentPaper({ api, records, view, completed, selections, disabled, canSubmit, busy, submitLabel, onSelect, onSubmit, history, children }: {
  api: StudydyApiClient; records: AssessmentRecordView[];
  view: Pick<KnowledgeStructureView, "concepts" | "source_resolver">;
  completed: boolean; selections: Record<string, string>; disabled: boolean; canSubmit: boolean;
  busy: boolean; submitLabel: string; onSelect: (revision: string, option: string) => void;
  onSubmit: () => void; history?: ReactNode; children?: ReactNode;
}) {
  const [index, setIndex] = useState(0);
  const card = useRef<HTMLDivElement>(null);
  const current = records[index];
  const selected = current?.feedback?.selected_option_id ?? selections[current?.assessment.assessment_revision];
  const move = (next: number) => { setIndex(next); card.current?.focus(); };
  const renderQuestion = (record: AssessmentRecordView, position: number) => <article className="assessment-set-item" key={record.assessment.assessment_revision} aria-label={`第 ${position + 1} 題`}>
    {completed && <p className="assessment-set-number">第 {position + 1} 題</p>}
    <AssessmentPanel apiClient={api} record={record} completed={completed} view={view}
      answerSelection={{ value: record.feedback?.selected_option_id ?? selections[record.assessment.assessment_revision] ?? null,
        disabled, onChange: option => onSelect(record.assessment.assessment_revision, option) }} />
  </article>;
  if (completed) return <section className="assessment-review-list" aria-label="作答回顧">{records.map(renderQuestion)}</section>;
  if (!current) return null;
  return <form className="question-paper" aria-label="本組測驗" onSubmit={event => { event.preventDefault(); if (canSubmit) onSubmit(); }}
    onKeyDown={event => {
      if (event.altKey || event.ctrlKey || event.metaKey || (event.target as HTMLElement).closest('button, a, dialog')) return;
      const option = current.assessment.options[Number(event.key) - 1];
      if (option && !disabled && current.can_submit) { event.preventDefault(); onSelect(current.assessment.assessment_revision, option.option_id); }
      if (event.key === "Enter") {
        event.preventDefault();
        if (index < records.length - 1 && selected) move(index + 1);
        else if (index === records.length - 1 && canSubmit) onSubmit();
      }
    }}>
    <header className="question-paper-heading">
      <strong>{view.concepts.find(concept => concept.concept_id === current.assessment.target_concept_id)?.label}</strong>
      <span>第 {index + 1}／{records.length} 題</span>
      <Progress value={index + 1} max={records.length} aria-label="作答題目進度" />
      {history}
    </header>
    <div ref={card} className="question-paper-card" tabIndex={-1}>{renderQuestion(current, index)}</div>
    {children}
    <footer className="question-paper-navigation">
      <button className="secondary-button" type="button" disabled={index === 0 || busy} onClick={() => move(index - 1)}>上一題</button>
      <small>數字鍵 1–4 選答案 · Enter 下一題<br />交卷前可修改；尚未交卷的選取不會保存。</small>
      {index < records.length - 1
        ? <button className="primary-button" type="button" disabled={!selected || busy} onClick={() => move(index + 1)}>下一題</button>
        : <button className="primary-button" type="submit" disabled={!canSubmit} aria-busy={busy}>{submitLabel}</button>}
    </footer>
  </form>;
}

export function QuestionPreparation({ ready, total, label }: { ready: number; total: number; label: string }) {
  return <div className="question-preparation">
    <div className="question-preparation-message"><img src="/assets/studydy/processing-laptop.png" alt="" />
      <div><p>正在出題，準備好後會自動顯示。</p><p role="status">{total > 0 ? `已準備 ${ready} / ${total} 題` : "正在讀取準備進度…"}</p>
        <Progress aria-label={label} value={ready > 0 ? ready : undefined} max={Math.max(1, total)} aria-valuetext={total > 0 ? `已準備 ${ready} / ${total} 題` : "正在讀取準備進度…"} /></div>
    </div>
    <div className="question-skeleton" aria-hidden="true"><i />{[0, 1, 2, 3].map(key => <span key={key} />)}</div>
  </div>;
}
