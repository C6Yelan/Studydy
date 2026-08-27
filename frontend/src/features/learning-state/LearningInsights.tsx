import type { LearningStateView, WeaknessView } from "../../api/contracts";
import { Icon } from "../../ui/Icon";
import "./styles.css";

const statusCopy = {
  not_started: "尚未開始",
  learning: "學習中",
  needs_review: "需要複習",
  mastered: "本次已掌握",
} as const;

const bandCopy = {
  no_evidence: "尚無作答證據",
  developing: "理解正在建立",
  demonstrated: "已展現理解",
} as const;

const confidenceCopy = {
  none: "尚無信心依據",
  limited: "依據有限",
  supported: "已有足夠依據",
} as const;

const findingCopy = {
  observed_weak: { title: "已觀察到的弱點", tone: "danger" },
  needs_review: { title: "近期結果需要複習", tone: "warning" },
  not_enough_data: { title: "目前資料不足", tone: "neutral" },
} as const;

export function LearningInsights({ currentConceptId, learningState, weakness }: {
  currentConceptId: string;
  learningState: LearningStateView;
  weakness: WeaknessView;
}) {
  const current = learningState.concept_states.find((state) => state.formal_concept_id === currentConceptId);
  if (!current) return null;
  const untouchedCount = learningState.concept_states.filter((state) =>
    state.formal_concept_id !== currentConceptId && state.status === "not_started").length;
  const findingPriority = { observed_weak: 0, needs_review: 1, not_enough_data: 2 } as const;
  const orderedFindings = [...weakness.findings].sort((left, right) =>
    Number(right.target_formal_concept_id === currentConceptId)
    - Number(left.target_formal_concept_id === currentConceptId)
    || findingPriority[left.category] - findingPriority[right.category]);
  return (
    <section className="learning-insights" aria-labelledby="learning-insights-title">
      <div className="insights-heading">
        <div><p className="eyebrow">依本次作答更新</p><h2 id="learning-insights-title">本次學習進度</h2></div>
        <span className={`learning-status is-${current.status}`}>{statusCopy[current.status]}</span>
      </div>
      <div className="state-dimensions">
        <article><span><Icon name="learning" /></span><small>理解狀態</small><strong>{bandCopy[current.mastery_band]}</strong></article>
        <article><span><Icon name="check" /></span><small>判斷依據</small><strong>{confidenceCopy[current.confidence]}</strong></article>
        <article><span><Icon name="book" /></span><small>核心重點練習</small><strong>{current.claim_coverage_complete ? "已完成" : "尚未完成"}</strong></article>
        <article><span><Icon name="file" /></span><small>教材來源回查</small><strong>{current.evidence_coverage_complete ? "已涵蓋" : "可繼續查看"}</strong></article>
      </div>
      <p className="state-explanation">{current.explanation}</p>
      <div className="state-flags" aria-label="近期學習訊號">
        {current.needs_more_data && <span>需要更多作答資料</span>}
        {current.recent_result && <span>最近結果：{current.recent_result === "correct" ? "答對" : "答錯"}</span>}
        {current.repeated_error && <span>出現重複錯誤</span>}
        {current.post_error_improvement && <span>錯誤後已有改善</span>}
      </div>

      {untouchedCount > 0 && (
        <p className="untouched-summary"><Icon name="book" />另有 {untouchedCount} 個概念尚未開始，開始練習後才會個別顯示學習觀察。</p>
      )}
      <details className="learning-findings">
        <summary>查看需要留意的學習觀察</summary>
        <div className="weakness-list">
        {orderedFindings.map((finding) => {
          const copy = findingCopy[finding.category];
          return (
            <article className={`weakness-card is-${copy.tone}`} key={`${finding.target_formal_concept_id}:${finding.category}`}>
              <span><Icon name={finding.category === "not_enough_data" ? "book" : "warning"} /></span>
              <div><small>{copy.title}</small><strong>{finding.target_label}</strong><p>{finding.reason}</p></div>
            </article>
          );
        })}
        {weakness.immediate_prerequisite_gaps.map((gap) => (
          <article className="weakness-card is-prerequisite" key={gap.relation_id}>
            <span><Icon name="arrow-left" /></span>
            <div><small>學習前可先補強</small><strong>{gap.prerequisite_label}</strong><p>{gap.reason}</p></div>
          </article>
        ))}
        {orderedFindings.length === 0 && weakness.immediate_prerequisite_gaps.length === 0 && (
          <div className="no-weakness"><Icon name="check" /><span>目前尚未觀察到需要複習的弱點。</span></div>
        )}
        </div>
      </details>
    </section>
  );
}
