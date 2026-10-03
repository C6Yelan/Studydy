export function LearningSteps({ labels, current }: { labels: string[]; current: number }) {
  return <ol className="learning-steps" aria-label="建立流程">{labels.map((label, index) =>
    <li key={label} className={index < current ? 'is-complete' : index === current ? 'is-current' : ''} aria-current={index === current ? 'step' : undefined}>
      <span>{index < current ? '✓' : index + 1}</span>{label}
    </li>)}</ol>;
}
