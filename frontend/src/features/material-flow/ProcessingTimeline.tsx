import { Icon, type IconName } from "../../ui/Icon";

/** 依序流程用 currentIndex；各階段同時推進（如 Podcast 多集）時由 stage.state 與 stage.status 指定。 */
export function ProcessingTimeline({ stages, currentIndex, activity = "進行中", title = "處理流程" }: {
  stages: { label: string; icon: IconName; state?: "complete" | "active" | "waiting"; status?: string }[]; currentIndex: number; activity?: string; title?: string;
}) {
  return <section className="surface processing-card processing-timeline">
    <header className="processing-timeline-heading"><h2>{title}</h2><img src="/assets/studydy/processing-laptop.png" alt="" /></header>
    <ol className="status-timeline">{stages.map((stage, index) => {
      const state = stage.state ?? (index < currentIndex ? "complete" : index === currentIndex ? "active" : "waiting");
      return <li key={stage.label} aria-current={index === currentIndex ? "step" : undefined}
        className={state === "complete" ? "is-complete" : state === "active" ? "is-active" : undefined}>
        <span><Icon name={state === "complete" ? "check" : stage.icon} /></span>
        <div><strong>{stage.label}</strong><p>{stage.status ?? (state === "complete" ? "已完成" : state === "active" ? activity : "尚未開始")}</p></div>
      </li>;
    })}</ol>
  </section>;
}
