import type { LearnerProgressView } from "../../api/contracts";
import { Icon } from "../../ui/Icon";
import "./styles.css";

const status = {
  not_started: "尚未開始",
  learning: "學習中",
  needs_review: "需要複習",
  mastered: "本次已掌握",
} as const;

export function LearningInsights({ currentConceptId, progress }: {
  currentConceptId: string;
  progress: LearnerProgressView;
}) {
  const current = progress.concept_states.find((state) => state.concept_id === currentConceptId);
  if (!current) return null;
  const finding = progress.weaknesses.find((item) => item.concept_id === currentConceptId);
  return (
    <section className="learning-insights" aria-labelledby="learning-insights-title">
      <div className="insights-heading">
        <div><p className="eyebrow">依本次作答更新</p><h2 id="learning-insights-title">本次學習進度</h2></div>
        <span className={`learning-status is-${current.status}`}>{status[current.status]}</span>
      </div>
      <div className="state-dimensions">
        <article><span><Icon name="learning" /></span><small>作答次數</small><strong>{current.attempts}</strong></article>
        <article><span><Icon name="check" /></span><small>答對次數</small><strong>{current.correct_answers}</strong></article>
        <article><span><Icon name="book" /></span><small>不同安全題目</small><strong>{current.qualified_correct_items}</strong></article>
        <article><span><Icon name="file" /></span><small>已練習重點</small><strong>{current.covered_claim_ids.length}</strong></article>
      </div>
      {finding ? <div className="weakness-card is-warning"><Icon name="warning" /><div><strong>最近答案需要複習</strong><p>回到教材 Evidence，再嘗試另一題。</p></div></div> : <div className="no-weakness"><Icon name="check" /><span>目前沒有觀察到新的弱點。</span></div>}
    </section>
  );
}
