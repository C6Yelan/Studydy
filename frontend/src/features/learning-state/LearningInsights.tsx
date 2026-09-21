import type { LearnerProgressView } from "../../api/contracts";
import "./styles.css";

const status = {
  not_started: "尚未開始",
  learning: "學習中",
  needs_review: "需要複習",
  mastered: "已掌握",
} as const;

export function LearningInsights({ currentConceptId, totalClaimCount, progress }: {
  currentConceptId: string;
  totalClaimCount: number;
  progress: LearnerProgressView;
}) {
  const current = progress.concept_states.find((state) => state.concept_id === currentConceptId);
  if (!current) return null;
  const finding = progress.weaknesses.find((item) => item.concept_id === currentConceptId);
  const cycle = progress.assessment_cycles.find(cycle => cycle.concept_id === currentConceptId);
  if (current.attempts === 0 && !finding && !cycle) return null;
  return (
    <section className="learning-insights" aria-labelledby="learning-insights-title">
      <div className="insights-heading">
        <h2 id="learning-insights-title">學習進度</h2>
        <span className={`learning-status is-${current.status}`}>{status[current.status]}</span>
      </div>
      <p className="insights-summary"><span>作答 {current.attempts} 次</span><span className="insights-separator" aria-hidden="true"> · </span><span>答對 {current.correct_answers} 次</span><span className="insights-separator" aria-hidden="true"> · </span><span>已練習 {current.covered_claim_ids.length} 個重點</span></p>
      {cycle && <p className="cycle-insight">{cycle.outcome === "passed" ? "本輪檢測通過" : cycle.pending_count ? `仍有 ${cycle.pending_count} 個重點待補強` : "本輪檢測進行中"}
        {cycle.remediation_passed_count > 0 && ` · ${cycle.remediation_passed_count} 個重點補強通過`}</p>}
      <p>已掌握 {current.mastered_claim_ids.length} / {totalClaimCount} 個教材重點</p>
      {current.attempts > 0 && <details className="mastery-explanation"><summary>如何判斷已掌握？</summary><p>每個教材重點需答對 2 道不同且通過檢查的題目，最近一次獨立作答也需答對。複習後的補強答案只用於確認本次改善，不累計為獨立掌握證據。</p></details>}

    </section>
  );
}
