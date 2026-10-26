import type { ReactNode } from "react";
import "./selection-toggle.css";

export function SelectionToggle({ pressed, onChange, children }: {
  pressed: boolean; onChange: (pressed: boolean) => void; children: ReactNode;
}) {
  return <button type="button" className="selection-toggle" aria-pressed={pressed}
    title={pressed ? "顯示全部" : "只看已選"} onClick={() => onChange(!pressed)}>{children}</button>;
}
