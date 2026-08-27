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
  return (
    <section className="learning-insights" aria-labelledby="learning-insights-title">
      <div className="insights-heading">
        <div><p className="eyebrow">StudySession only</p><h2 id="learning-insights-title">本次學習狀態與弱點</h2></div>
        <span className={`learning-status is-${current.status}`}>{statusCopy[current.status]}</span>
      </div>
      <div className="state-dimensions">
        <article><span><Icon name="learning" /></span><small>理解狀態</small><strong>{bandCopy[current.mastery_band]}</strong></article>
        <article><span><Icon name="check" /></span><small>判斷依據</small><strong>{confidenceCopy[current.confidence]}</strong></article>
        <article><span><Icon name="book" /></span><small>教材重點覆蓋</small><strong>{current.claim_coverage_complete ? "已覆蓋" : "尚未完整"}</strong></article>
        <article><span><Icon name="file" /></span><small>Evidence 覆蓋</small><strong>{current.evidence_coverage_complete ? "已覆蓋" : "尚未完整"}</strong></article>
      </div>
      <p className="state-explanation">{current.explanation}</p>
      <div className="state-flags" aria-label="近期學習訊號">
        {current.needs_more_data && <span>需要更多作答資料</span>}
        {current.recent_result && <span>最近結果：{current.recent_result === "correct" ? "答對" : "答錯"}</span>}
        {current.repeated_error && <span>出現重複錯誤</span>}
        {current.post_error_improvement && <span>錯誤後已有改善</span>}
      </div>

      <div className="weakness-list">
        {weakness.findings.map((finding) => {
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
            <div><small>需要先補強的正式先備概念</small><strong>{gap.prerequisite_label}</strong><p>{gap.reason}</p></div>
          </article>
        ))}
        {weakness.findings.length === 0 && weakness.immediate_prerequisite_gaps.length === 0 && (
          <div className="no-weakness"><Icon name="check" /><span>目前沒有 backend 已發布的弱點；繼續依評量累積資料。</span></div>
        )}
      </div>
    </section>
  );
}
