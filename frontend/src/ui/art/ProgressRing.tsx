/** 圓形進度；ratio 為 0–1，children 顯示在圓心。 */
export function ProgressRing({ ratio, color = "var(--studydy-blue)", size = 38, stroke = 5, children }: {
  ratio: number; color?: string; size?: number; stroke?: number; children?: React.ReactNode;
}) {
  const radius = (size - stroke) / 2;
  const circumference = 2 * Math.PI * radius;
  const filled = Math.max(0, Math.min(1, ratio)) * circumference;
  return <span className="progress-ring" style={{ width: size, height: size }}>
    <svg viewBox={`0 0 ${size} ${size}`} width={size} height={size} aria-hidden="true">
      <circle cx={size / 2} cy={size / 2} r={radius} fill="none" stroke="#eef2f8" strokeWidth={stroke} />
      {filled > 0 && <circle cx={size / 2} cy={size / 2} r={radius} fill="none" stroke={color} strokeWidth={stroke} strokeLinecap="round"
        strokeDasharray={`${filled} ${circumference}`} transform={`rotate(-90 ${size / 2} ${size / 2})`} />}
    </svg>
    {children != null && <em>{children}</em>}
  </span>;
}
