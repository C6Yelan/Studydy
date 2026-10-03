import { Icon, type IconName } from "../../ui/Icon";

export function ProcessingTimeline({ stages, currentIndex, activity = "進行中" }: {
  stages: { label: string; icon: IconName }[]; currentIndex: number; activity?: string;
}) {
  return <section className="surface processing-card processing-timeline">
    <header className="processing-timeline-heading"><h2>處理流程</h2><img src="/assets/studydy/processing-laptop.png" alt="" /></header>
    <ol className="status-timeline">{stages.map((stage, index) => <li key={stage.label}
      aria-current={index === currentIndex ? "step" : undefined}
      className={index < currentIndex ? "is-complete" : index === currentIndex ? "is-active" : undefined}>
      <span><Icon name={index < currentIndex ? "check" : stage.icon} /></span>
      <div><strong>{stage.label}</strong><p>{index < currentIndex ? "已完成" : index === currentIndex ? activity : "尚未開始"}</p></div>
    </li>)}</ol>
  </section>;
}
