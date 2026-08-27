import type { AdaptiveAction, AdaptiveResponseView, StudyContextView } from "../../api/contracts";
import { Icon } from "../../ui/Icon";
import "./styles.css";

const actionCopy: Record<AdaptiveAction, { label: string; cta: string }> = {
  start: { label: "開始目前概念", cta: "前往目前步驟" },
  continue: { label: "繼續目前概念", cta: "繼續學習" },
  practice: { label: "練習目前概念", cta: "開始練習" },
  review: { label: "複習目前概念", cta: "開始複習" },
  relearn_prerequisite: { label: "先補強正式先備概念", cta: "前往補強" },
  use_resource: { label: "使用補充資源", cta: "開啟建議資源" },
  follow_path: { label: "回到教材建議順序", cta: "依順序繼續" },
  collect_more_data: { label: "取得更多作答資料", cta: "完成更多評量" },
  no_action: { label: "目前沒有下一個動作", cta: "" },
};

const confidenceCopy = {
  none: "目前沒有足夠依據",
  limited: "依據有限",
  supported: "已有足夠依據",
} as const;

function contextLabel(context: StudyContextView, conceptId: string | null): string | null {
  if (!conceptId) return null;
  return context.initial_learning_path.find((item) => item.formal_concept_id === conceptId)?.label ?? null;
}

export function AdaptiveNextStep({ adaptive, context, isApplying, onApply }: {
  adaptive: AdaptiveResponseView;
  context: StudyContextView;
  isApplying: boolean;
  onApply: () => void;
}) {
  const step = adaptive.plan.primary_step;
  const copy = actionCopy[step.action];
  const deferredLabel = contextLabel(context, adaptive.plan.deferred_formal_concept_id);
  const currentLabel = contextLabel(context, adaptive.plan.current_formal_concept_id);
  return (
    <section className="adaptive-card" aria-labelledby="adaptive-title">
      <div className="adaptive-icon"><Icon name="learning" size={28} /></div>
      <div className="adaptive-copy">
        <p className="eyebrow">目前為你調整</p>
        <h2 id="adaptive-title">{copy.label}</h2>
        <p>{step.reason}</p>
        <div className="adaptive-meta">
          <span>目標：{step.target_label ?? "目前沒有目標"}</span>
          <span>{confidenceCopy[step.confidence]}</span>
          <span>{step.claim_coverage_complete ? "教材重點已覆蓋" : "教材重點尚未完整覆蓋"}</span>
        </div>
        {deferredLabel && currentLabel && (
          <p className="deferred-copy"><Icon name="refresh" />現在先學「{currentLabel}」，完成後會回到「{deferredLabel}」。</p>
        )}
        <small>這個建議只屬於本次學習，不會修改正式 Relation 或教材建議學習順序。</small>
      </div>
      {step.action !== "no_action" && (
        <button className="primary-button" disabled={isApplying} type="button" onClick={onApply}>
          {isApplying ? "正在調整…" : copy.cta}<Icon name="chevron-right" />
        </button>
      )}
    </section>
  );
}
