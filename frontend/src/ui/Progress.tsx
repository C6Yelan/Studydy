import type { CSSProperties } from "react";
import "./progress.css";

export const progressThickness = { small: 5, standard: 8 };

export function Progress({ value, max = 100, size = "standard", tone = "default", className = "", ...aria }: {
  value?: number; max?: number; size?: "small" | "standard"; tone?: "default" | "warning" | "complete";
  className?: string; "aria-label": string; "aria-valuetext"?: string;
}) {
  const known = value !== undefined && max > 0;
  const current = known ? Math.max(0, Math.min(max, value)) : undefined;
  return <span className={`studydy-progress is-${size} is-${tone}${known ? "" : " is-indeterminate"} ${className}`}
    role="progressbar" aria-valuemin={0} aria-valuemax={known ? max : undefined} aria-valuenow={current} {...aria}
    style={{ "--progress-thickness": `${progressThickness[size]}px`, "--progress-ratio": known ? `${current! / max * 100}%` : undefined } as CSSProperties}>
    <span className="studydy-progress-fill" />
  </span>;
}
