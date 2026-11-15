import { useRef, useState, type ReactNode } from "react";
import type { AssessmentRecordView, KnowledgeStructureView } from "../../api/contracts";
import type { StudydyApiClient } from "../../api/client";
import { Icon } from "../../ui/Icon";
import { Progress } from "../../ui/Progress";
import { AssessmentPanel } from "./AssessmentPanel";
import "./paper.css";

export function AssessmentPaper({ api, records, view, completed, selections, disabled, canSubmit, busy, submitLabel, onSelect, onSubmit, onLeave, fresh, children }: {
  api: StudydyApiClient; records: AssessmentRecordView[];
  view: Pick<KnowledgeStructureView, "concepts" | "source_resolver">;
  completed: boolean; selections: Record<string, string>; disabled: boolean; canSubmit: boolean;
  busy: boolean; submitLabel: string; onSelect: (revision: string, option: string) => void;
  onSubmit: () => void; onLeave: () => void; fresh?: boolean; children?: ReactNode;
}) {
  const [index, setIndex] = useState(0);
  const card = useRef<HTMLDivElement>(null);
  const current = records[index];
  const selected = current?.feedback?.selected_option_id ?? selections[current?.assessment.assessment_revision];
  const move = (next: number) => { setIndex(next); card.current?.focus(); };
  const renderQuestion = (record: AssessmentRecordView, position: number) => <article className="assessment-set-item" key={record.assessment.assessment_revision} aria-label={`第 ${position + 1} 題`}>
    <AssessmentPanel apiClient={api} record={record} number={position + 1} completed={completed} view={view} fresh={fresh}
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
    <header className="quiz-top">
      <QuizLeave onLeave={onLeave} />
      <div className="quiz-segs" role="progressbar" aria-label="作答題目進度" aria-valuemin={1} aria-valuemax={records.length}
        aria-valuenow={index + 1} aria-valuetext={`第 ${index + 1} 題，共 ${records.length} 題`}>
        {records.map((record, position) => <i key={record.assessment.assessment_revision} className={position < index ? "on" : position === index ? "cur" : undefined} />)}
      </div>
      <span className="quiz-count">第 {index + 1}／{records.length} 題</span>
    </header>
    <div className="quiz-body">
      <div className="quiz-main">
        <p className="question-paper-heading">{view.concepts.find(concept => concept.concept_id === current.assessment.target_concept_id)?.label}</p>
        <div ref={card} className="question-paper-card" tabIndex={-1}>{renderQuestion(current, index)}</div>
        {children}
      </div>
      <aside className="quiz-side">
        <p>交卷前都可以回頭改，慢慢想沒關係。尚未交卷的選取不會保存。</p>
        <img src="/assets/studydy/brand-idle.png" alt="" />
      </aside>
    </div>
    <footer className="question-paper-navigation">
      <small><span className="keyboard-hint">按 1–4 選答案 · Enter 下一題 · </span>{records.length} 題答完一起交卷</small>
      <div className="quiz-nav-buttons">
        <button className="secondary-button" type="button" disabled={index === 0 || busy} onClick={() => move(index - 1)}>上一題</button>
        {index < records.length - 1
          ? <button className="primary-button" type="button" disabled={!selected || busy} onClick={() => move(index + 1)}>下一題</button>
          : <button className="primary-button" type="submit" disabled={!canSubmit} aria-busy={busy}>{submitLabel}</button>}
      </div>
    </footer>
  </form>;
}

// 專注模式的「×」：和進度條同一列；回到哪一頁由答題頁決定。
export function QuizLeave({ onLeave }: { onLeave: () => void }) {
  return <button className="study-focus-close" type="button" aria-label="離開作答" onClick={onLeave}><Icon name="close" size={22} /></button>;
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
