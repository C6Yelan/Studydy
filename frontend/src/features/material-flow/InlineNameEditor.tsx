import { useEffect, useRef, type RefObject } from "react";

// 教材、卡組與 Podcast 共用同一個原位置改名表單。
export function InlineNameEditor({ label, name, busy, error, onChange, onSave, onCancel, inputRef }: {
  label: string; name: string; busy: boolean; error: string | null;
  onChange: (value: string) => void; onSave: () => void; onCancel: () => void;
  inputRef?: RefObject<HTMLInputElement | null>;
}) {
  const ownInput = useRef<HTMLInputElement>(null);
  const input = inputRef ?? ownInput;
  const valid = name.trim().length > 0 && Array.from(name.trim()).length <= 200 && !/[\p{Cc}\p{Cs}]/u.test(name);
  useEffect(() => { input.current?.focus(); input.current?.select(); }, [input]);
  useEffect(() => { if (error && !busy) input.current?.focus(); }, [error, busy, input]);
  return <form className="material-management-form" aria-label={`重新命名${label.replace(/名稱$/, "").trim()}`}
    onSubmit={e => { e.preventDefault(); if (!busy && valid) onSave(); }}
    onKeyDown={e => { if (e.key === "Escape") { e.preventDefault(); if (!busy) onCancel(); } }}>
    <label>{label}<input ref={input} type="text" value={name} disabled={busy} onChange={e => onChange(e.target.value)} /></label>
    {!valid && <p>名稱需為 1–200 個字，且不可包含控制字元。</p>}
    <div className="material-management-actions"><button className="secondary-button" type="button" disabled={busy} onClick={onCancel}>取消</button>
      <button className="secondary-button" type="submit" disabled={busy || !valid}>{busy ? "正在儲存…" : "儲存"}</button></div>
    {busy && <p role="status">正在儲存…</p>}
    {error && <p className="form-error" role="alert">{error}</p>}
  </form>;
}
