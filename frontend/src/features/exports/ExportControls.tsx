import { useEffect, useId, useRef, useState } from "react";
import { errorMessage } from "../../api/client";
import "./styles.css";

type Option = { value: string; label: string; hint?: string };
// 匯出入口：多種格式時在按鈕下方展開小視窗，點格式就直接匯出；只有一種格式時按鈕直接下載。
export function ExportControls({ label, triggerLabel = label, options, download }: {
  label: string; triggerLabel?: string; options: Option[]; download: (format: string) => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState("");
  const active = useRef(true);
  const entry = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const menuId = useId();
  useEffect(() => { active.current = true; return () => { active.current = false; }; }, []);
  useEffect(() => {
    if (!open) return;
    entry.current?.querySelector<HTMLButtonElement>(".export-popover-item")?.focus();
    const outside = (event: PointerEvent) => { if (!busy && !entry.current?.contains(event.target as Node)) setOpen(false); };
    const escape = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || busy) return;
      event.stopPropagation(); setOpen(false); trigger.current?.focus();
    };
    document.addEventListener("pointerdown", outside);
    document.addEventListener("keydown", escape, true);
    return () => { document.removeEventListener("pointerdown", outside); document.removeEventListener("keydown", escape, true); };
  }, [open, busy]);
  if (!options.length) return null;
  const start = async (format: string) => {
    setBusy(format); setError("");
    try {
      await download(format);
      if (active.current) setOpen(false);
    } catch (failure) {
      if (active.current) setError(errorMessage(failure));
    } finally {
      if (active.current) setBusy(null);
    }
  };
  const single = options.length === 1;
  return <div ref={entry} className="export-entry" role="group" aria-label={label}>
    <button ref={trigger} type="button" className="secondary-button" disabled={!!busy && single}
      aria-haspopup={single ? undefined : "menu"} aria-expanded={single ? undefined : open} aria-controls={single ? undefined : menuId}
      onClick={() => { setError(""); if (single) void start(options[0].value); else setOpen(value => !value); }}>
      {single ? (busy ? "正在匯出…" : `${triggerLabel}（${options[0].value.toUpperCase()}）`) : triggerLabel}
    </button>
    {/* 單一格式不另起一行顯示進度（會把所在標題列撐開）；進度寫在按鈕上，只有錯誤才另外顯示。 */}
    {single && error && <p role="alert" className="form-error">{error}</p>}
    {open && <div className="export-popover" id={menuId} role="menu" aria-label={label}>
      {options.map(option => <button key={option.value} type="button" role="menuitem" className="export-popover-item" disabled={!!busy}
        onClick={() => void start(option.value)}>
        <b>{busy === option.value ? "正在匯出…" : option.label}</b>
        {option.hint && <small>{option.hint}</small>}
      </button>)}
      {error && <p role="alert" className="form-error">{error}</p>}
    </div>}
  </div>;
}
