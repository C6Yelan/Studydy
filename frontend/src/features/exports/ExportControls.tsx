import { useEffect, useId, useRef, useState } from "react";
import { errorMessage } from "../../api/client";
import { Icon } from "../../ui/Icon";
import "./styles.css";

type Option = { value: string; label: string };
export function ExportControls({ label, triggerLabel = label, options, download }: {
  label: string; triggerLabel?: string; options: Option[]; download: (format: string) => Promise<void>;
}) {
  const [format, setFormat] = useState(options[0]?.value ?? "");
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const active = useRef(true);
  const dialog = useRef<HTMLDialogElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const titleId = useId();
  useEffect(() => { active.current = true; return () => { active.current = false; }; }, []);
  useEffect(() => {
    if (!open) return;
    const element = dialog.current;
    element?.showModal();
    return () => { element?.close(); trigger.current?.focus(); };
  }, [open]);
  const selected = options.some(o => o.value === format) ? format : options[0]?.value;
  if (!options.length) return null;
  const start = async () => {
    setBusy(true); setError("");
    try {
      await download(selected);
      if (active.current) setOpen(false);
    } catch (failure) {
      if (active.current) setError(errorMessage(failure));
    } finally {
      if (active.current) setBusy(false);
    }
  };
  const status = <>
    {busy && <p role="status">正在準備下載檔案…</p>}
    {error && <p role="alert" className="form-error">{error}</p>}
  </>;
  return <div className="export-entry" role="group" aria-label={label}>
    <button ref={trigger} type="button" className="secondary-button" disabled={busy}
      aria-haspopup={options.length > 1 ? "dialog" : undefined}
      onClick={() => { setError(""); if (options.length > 1) setOpen(true); else void start(); }}>
      {options.length > 1 ? triggerLabel : `${triggerLabel}（${options[0].value.toUpperCase()}）`}
    </button>
    {!open && options.length === 1 && status}
    {open && <dialog ref={dialog} className="export-dialog" aria-labelledby={titleId}
      onCancel={event => { event.preventDefault(); event.stopPropagation(); if (!busy) setOpen(false); }}
      onKeyDown={event => { if (event.key === "Escape") event.stopPropagation(); }}>
      <header className="export-dialog-heading">
        <h2 id={titleId}>{label}</h2>
        <button type="button" className="secondary-button" aria-label="關閉匯出" disabled={busy} onClick={() => setOpen(false)}><Icon name="close" size={18}/></button>
      </header>
      <fieldset className="export-format-options" disabled={busy}>
        <legend>選擇格式</legend>
        {options.map(option => <label className="export-format-card" key={option.value}>
          <input type="radio" name={titleId} value={option.value} checked={selected === option.value} onChange={() => setFormat(option.value)}/>
          <span>{option.label}</span>
        </label>)}
      </fieldset>
      {status}
      <footer className="export-dialog-actions">
        <button type="button" className="secondary-button" disabled={busy} onClick={() => setOpen(false)}>取消</button>
        <button type="button" className="primary-button" disabled={busy} onClick={() => void start()}>{busy ? "正在匯出…" : "匯出"}</button>
      </footer>
    </dialog>}
  </div>;
}
