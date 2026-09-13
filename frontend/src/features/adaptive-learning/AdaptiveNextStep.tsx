import type { KnowledgeStructureView, LearnerProgressView } from "../../api/contracts";
import { Icon } from "../../ui/Icon";
import "./styles.css";

const copy = {
  review_prerequisite: ["建議先補強前置概念", "前往前置概念", "完成這個重點後，再回來學目前內容。"],
  advance: ["可以繼續下一個重點", "繼續學習", "你已完成目前這一步。"],
  defer: ["先前往下一個可學習的重點", "暫緩並繼續", "目前內容會保留，之後可以再回來。"],
  resume: ["回到先前保留的重點", "回到保留重點", "接著學習先前留下的教材內容。"],
  no_safe: ["目前沒有適合的新題目", "", "可以先回顧教材內容。"],
  complete: ["本次學習內容已完成", "完成學習", "本次紀錄會保留，之後仍可回顧。"],
} as const;

export function GuidanceNextStep({ progress, view, isApplying, onApply }: {
  progress: LearnerProgressView;
  view: KnowledgeStructureView;
  isApplying: boolean;
  onApply: () => void;
}) {
  const step = progress.next_action;
  if (step.action === "assess") return null;
  const target = view.concepts.find((concept) => concept.concept_id === step.target_concept_id);
  return (
    <section className="adaptive-card" aria-labelledby="adaptive-title">
      <div className="adaptive-copy">
        <p className="eyebrow">下一步</p>
        <h2 id="adaptive-title">{copy[step.action][0]}</h2>
        <p>{copy[step.action][2]}</p>
        {target && <div className="adaptive-meta"><span>目標：{target.label}</span></div>}
      </div>
      {step.action !== "no_safe" && (
        <button className="primary-button" disabled={isApplying} type="button" onClick={onApply}>{isApplying ? "正在調整…" : copy[step.action][1]}<Icon name="chevron-right" /></button>
      )}
    </section>
  );
}
