import { progressThickness } from "../Progress";
/** 圓形進度；ratio 為 0–1，children 顯示在圓心。 */
export function ProgressRing({ ratio, tone = "default", size = 38, children }: {
  ratio: number; tone?: "default" | "warning" | "complete"; size?: number; children?: React.ReactNode;
}) {
  const stroke = progressThickness.small;
  const radius = (size - stroke) / 2;
  const circumference = 2 * Math.PI * radius;
  const filled = Math.max(0, Math.min(1, ratio)) * circumference;
  return <span className={`progress-ring is-${tone}`} style={{ width: size, height: size }}>
    <svg viewBox={`0 0 ${size} ${size}`} width={size} height={size} aria-hidden="true">
      <circle cx={size / 2} cy={size / 2} r={radius} fill="none" className="progress-ring-track" strokeWidth={stroke} />
      {filled > 0 && <circle cx={size / 2} cy={size / 2} r={radius} fill="none" className="progress-ring-fill" strokeWidth={stroke} strokeLinecap="round"
        strokeDasharray={`${filled} ${circumference}`} transform={`rotate(-90 ${size / 2} ${size / 2})`} />}
    </svg>
    {children != null && <em>{children}</em>}
  </span>;
}
