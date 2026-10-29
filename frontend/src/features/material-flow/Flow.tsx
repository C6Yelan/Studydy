import type { ReactNode, Ref } from "react";
import { Icon, type IconName } from "../../ui/Icon";
import "./flow.css";

/**
 * 建立教材的共用版型：上傳教材（上傳 → 確認教材 → 建立知識地圖）與從主題建立教材
 * （學習需求 → 確認觀念與細節 → 選取來源 → 建立教材）使用同一套頁首、步驟列與階段卡片。
 */

/** 上傳教材流程的三個步驟（處理中與完成頁沿用）。 */
export const uploadSteps = ["上傳教材", "確認教材", "建立知識地圖"];
/** 從主題建立教材的四個步驟；教材重試後回到處理頁時沿用。 */
export const topicSteps = ["學習需求", "確認觀念與細節", "選取來源", "建立教材"];
/** 建立 Podcast 的三個步驟；生成中與中斷頁沿用。 */
export const podcastSteps = ["選擇教材", "挑選概念與形式", "生成 Podcast"];

/**
 * 頁首：返回、標題與一句說明；右側可放插圖或補充資訊（aside）。
 * 建立教材、卡組與 Podcast 的步驟列由呼叫端放在頁首下方，獨立占一整排。
 */
export function FlowHeader({ title, description, back, image, headingId, aside, className = "" }: {
  title: ReactNode; description?: ReactNode; back?: ReactNode; image?: string; headingId?: string;
  aside?: ReactNode; className?: string;
}) {
  return <header className={`flow-head ${className}`.trim()}>
    {back}
    <div className="flow-head-row">
      <div className="flow-head-text">
        <h1 id={headingId}>{title}</h1>
        {description && <p>{description}</p>}
      </div>
      {image && <img className="flow-head-art" src={image} alt="" />}
      {aside && <div className="flow-head-aside">{aside}</div>}
    </div>
  </header>;
}

export function LearningSteps({ labels, current, label = "建立流程" }: { labels: string[]; current: number; label?: string }) {
  return <ol className="learning-steps" aria-label={label}>{labels.map((name, index) =>
    <li key={name} className={index < current ? "is-complete" : index === current ? "is-current" : undefined} aria-current={index === current ? "step" : undefined}>
      <span className="learning-step-mark" aria-hidden="true">{index < current ? <Icon name="check" size={15} /> : index + 1}</span>
      <span className="learning-step-label">{name}</span>
    </li>)}</ol>;
}

/** 一個階段一張卡片：圖示＋標題＋說明，內容，頁尾左側次要操作、右側主要操作。 */
export function FlowStage({ icon, title, description, aside, children, footer, className = "", label, tone, headingRef }: {
  icon: IconName; title: ReactNode; description?: ReactNode; aside?: ReactNode; children?: ReactNode;
  footer?: ReactNode; className?: string; label?: string; tone?: "success" | "attention" | "failure";
  headingRef?: Ref<HTMLHeadingElement>;
}) {
  return <section className={`surface flow-stage${tone ? ` is-${tone}` : ""} ${className}`.trim()} aria-label={label}>
    <header className="flow-stage-head">
      <span className="flow-stage-icon" aria-hidden="true"><Icon name={icon} size={22} /></span>
      <div className="flow-stage-title">
        <h2 ref={headingRef} tabIndex={headingRef ? -1 : undefined}>{title}</h2>
        {description && <p>{description}</p>}
      </div>
      {aside && <div className="flow-stage-aside">{aside}</div>}
    </header>
    {children}
    {footer}
  </section>;
}

/** 階段卡片頁尾：左側說明與次要操作，右側主要操作。 */
export function FlowFooter({ secondary, primary, className = "" }: { secondary?: ReactNode; primary?: ReactNode; className?: string }) {
  return <footer className={`tool-stage-footer ${className}`.trim()}>
    <div className="tool-footer-secondary">{secondary}</div>
    {primary}
  </footer>;
}
