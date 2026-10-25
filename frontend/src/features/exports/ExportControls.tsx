import { useEffect, useRef, useState } from "react";
import { errorMessage } from "../../api/client";
import "./styles.css";

type Option = { value: string; label: string };
export function ExportControls({ label, options, download }: {
  label: string; options: Option[]; download: (format: string) => Promise<void>;
}) {
  const [format, setFormat] = useState(options[0]?.value ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const active = useRef(true);
  useEffect(() => { active.current = true; return () => { active.current = false; }; }, []);
  const selected = options.some(o => o.value === format) ? format : options[0]?.value;
  if (!options.length) return null;
  return <div className="export-controls" role="group" aria-label={label}>
    <label>{label}<select value={selected} disabled={busy} onChange={e => setFormat(e.target.value)}>
      {options.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
    </select></label>
    <button type="button" className="secondary-button" disabled={busy} onClick={() => {
      setBusy(true); setError("");
      void download(selected).catch(e => { if (active.current) setError(errorMessage(e)); })
        .finally(() => { if (active.current) setBusy(false); });
    }}>{busy ? "正在匯出…" : "匯出"}</button>
    {busy && <span role="status">正在準備下載檔案，請保持此頁開啟。</span>}
    {error && <p role="alert" className="form-error">{error}</p>}
  </div>;
}
