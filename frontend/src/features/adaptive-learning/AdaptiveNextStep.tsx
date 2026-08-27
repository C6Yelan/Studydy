import type { AdaptiveAction, AdaptiveResponseView, StudyContextView } from "../../api/contracts";
import { Icon } from "../../ui/Icon";
import "./styles.css";

const actionCopy: Record<AdaptiveAction, { label: string; cta: string }> = {
  start: { label: "開始目前概念", cta: "前往目前步驟" },
  continue: { label: "繼續目前概念", cta: "繼續學習" },
  practice: { label: "練習目前概念", cta: "開始練習" },
  review: { label: "複習目前概念", cta: "開始複習" },
  relearn_prerequisite: { label: "先補強先備概念", cta: "前往補強" },
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

export function AdaptiveNextStep({ adaptive, context, hasNoSafeItem, isApplying, onApply, onReviewEvidence }: {
  adaptive: AdaptiveResponseView;
  context: StudyContextView;
  hasNoSafeItem: boolean;
  isApplying: boolean;
  onApply: () => void;
  onReviewEvidence: () => void;
}) {
  const step = adaptive.plan.primary_step;
  const useReviewFallback = hasNoSafeItem && step.action === "collect_more_data";
  const copy = useReviewFallback
    ? { label: "先回顧目前教材重點", cta: "回顧教材" }
    : actionCopy[step.action];
  const deferredLabel = contextLabel(context, adaptive.plan.deferred_formal_concept_id);
  const currentLabel = contextLabel(context, adaptive.plan.current_formal_concept_id);
  return (
    <section className="adaptive-card" aria-labelledby="adaptive-title">
      <div className="adaptive-icon"><Icon name="learning" size={28} /></div>
      <div className="adaptive-copy">
        <p className="eyebrow">目前為你調整</p>
        <h2 id="adaptive-title">{copy.label}</h2>
        <p>{useReviewFallback ? "目前沒有新的安全題目，先回查教材內容，不重複要求同一項評量。" : step.reason}</p>
        <div className="adaptive-meta">
          <span>目標：{step.target_label ?? "目前沒有目標"}</span>
          <span>{confidenceCopy[step.confidence]}</span>
          <span>{step.claim_coverage_complete ? "核心重點已練習" : "核心重點仍可練習"}</span>
        </div>
        {deferredLabel && currentLabel && (
          <p className="deferred-copy"><Icon name="refresh" />現在先學「{currentLabel}」，完成後會回到「{deferredLabel}」。</p>
        )}
        <small>這個建議只影響本次學習，不會改寫教材的概念連結或建議順序。</small>
      </div>
      {step.action !== "no_action" && (
        <button className="primary-button" disabled={isApplying} type="button" onClick={useReviewFallback ? onReviewEvidence : onApply}>
          {isApplying ? "正在調整…" : copy.cta}<Icon name="chevron-right" />
        </button>
      )}
    </section>
  );
}
