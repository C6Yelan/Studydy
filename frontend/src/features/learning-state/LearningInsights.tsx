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
  if (current.attempts === 0 && !finding) return null;
  return (
    <section className="learning-insights" aria-labelledby="learning-insights-title">
      <div className="insights-heading">
        <h2 id="learning-insights-title">本次進度</h2>
        <span className={`learning-status is-${current.status}`}>{status[current.status]}</span>
      </div>
      <p className="insights-summary">作答 {current.attempts} 次 · 答對 {current.correct_answers} 次 · 已練習 {current.covered_claim_ids.length} 個重點</p>
      {current.mastered_claim_ids.length > 0 && <p>已掌握 {current.mastered_claim_ids.length} 個教材重點</p>}
      {current.attempts > 0 && <details className="mastery-explanation"><summary>如何判斷已掌握？</summary><p>每個教材重點需答對 2 道不同且通過檢查的題目，最近一次作答也需答對。</p></details>}
      {finding && <div className="weakness-card is-warning"><Icon name="warning" /><div><strong>最近答案需要複習</strong><p>回顧教材重點與來源，再嘗試另一題。</p></div></div>}

    </section>
  );
}
