import type { KnowledgeStructureView, LearnerProgressView } from "../../api/contracts";
import { Icon } from "../../ui/Icon";
import "./styles.css";

const copy = {
  assess: ["練習目前概念", "開始練習"],
  review_prerequisite: ["先補強前置概念", "前往前置概念"],
  advance: ["前往下一個教材重點", "繼續學習"],
  defer: ["先學下一個安全重點", "暫緩並繼續"],
  resume: ["回到先前暫緩的重點", "回到暫緩重點"],
  no_safe: ["目前沒有安全題目", ""],
  complete: ["本次內容已完成", "完成學習"],
} as const;

export function GuidanceNextStep({ progress, view, isApplying, onApply }: {
  progress: LearnerProgressView;
  view: KnowledgeStructureView;
  isApplying: boolean;
  onApply: () => void;
}) {
  const step = progress.next_action;
  const target = view.concepts.find((concept) => concept.concept_id === step.target_concept_id);
  return (
    <section className="adaptive-card" aria-labelledby="adaptive-title">
      <div className="adaptive-icon"><Icon name="learning" size={28} /></div>
      <div className="adaptive-copy">
        <p className="eyebrow">本次學習指引</p>
        <h2 id="adaptive-title">{copy[step.action][0]}</h2>
        <p>{step.reason === "canonical_prerequisite_gap" ? "這項建議只使用教材已發布的 prerequisite 關係。" : step.reason === "no_safe_assessment" ? "目前題目未通過安全檢查，先保留進度。" : "依目前作答與教材學習順序安排。"}</p>
        {target && <div className="adaptive-meta"><span>目標：{target.label}</span></div>}
        <small>指引只屬於本次 Session，不會改寫教材 Map 或 Path。</small>
      </div>
      {step.action !== "assess" && step.action !== "no_safe" && (
        <button className="primary-button" disabled={isApplying} type="button" onClick={onApply}>{isApplying ? "正在調整…" : copy[step.action][1]}<Icon name="chevron-right" /></button>
      )}
    </section>
  );
}
