import { useEffect, useRef, useState } from "react";
import { errorMessage, type StudydyApiClient } from "../../api/client";
import type { MaterialLibraryItem, MaterialDiscardView } from "../../api/contracts";

export function MaterialManagement({ item, apiClient, deleting, onRenamed, onDeleted }: {
  item: MaterialLibraryItem; apiClient: StudydyApiClient; deleting: boolean;
  onRenamed: (item: MaterialLibraryItem) => void;
  onDeleted: (state: MaterialDiscardView["state"]) => void;
}) {
  const [mode, setMode] = useState<"rename" | "delete" | null>(null);
  const [name, setName] = useState(item.display_name);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const menu = useRef<HTMLDetailsElement>(null);
  const opener = useRef<HTMLElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const cancel = useRef<HTMLButtonElement>(null);
  const inFlight = useRef(false);
  const mounted = useRef(true);
  const interacted = useRef(false);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => {
    if (!interacted.current || deleting) return;
    if (mode === "rename") { input.current?.focus(); input.current?.select(); }
    else if (mode === "delete") cancel.current?.focus();
    else opener.current?.focus({ preventScroll: true });
  }, [mode, deleting]);
  useEffect(() => { if (error && !busy) (mode === "rename" ? input.current : cancel.current)?.focus(); }, [error, busy, mode]);
  const choose = (next: "rename" | "delete") => {
    if (menu.current) menu.current.open = false;
    interacted.current = true; setError(null); setName(item.display_name); setMode(next);
  };
  const close = () => { if (!inFlight.current) { setError(null); setMode(null); } };
  const valid = name.trim().length > 0 && Array.from(name.trim()).length <= 200 && !/[\p{Cc}\p{Cs}]/u.test(name);
  const submit = async () => {
    if (inFlight.current || !mode || (mode === "rename" && !valid)) return;
    inFlight.current = true; setBusy(true); setError(null);
    try {
      if (mode === "rename") onRenamed(await apiClient.renameMaterial(item.material_id, name.trim()));
      else onDeleted((await apiClient.discardMaterial(item.material_id)).state);
      if (mounted.current) setMode(null);
    } catch (failure) {
      if (mounted.current) setError(`${mode === "rename" ? "無法重新命名教材。" : "無法刪除教材。"}${errorMessage(failure)}`);
    } finally {
      inFlight.current = false;
      if (mounted.current) setBusy(false);
    }
  };
  const published = item.available_structures.length > 0 || item.study_sessions.length > 0;
  const processing = item.latest_attempt?.status === "pending" || item.latest_attempt?.status === "running" || item.source?.status === "pending" || item.source?.status === "running";
  return <>
    {!deleting && <details ref={menu} className="material-management-menu" hidden={mode !== null} onKeyDown={event => {
      if (event.key === "Escape") { event.preventDefault(); menu.current!.open = false; opener.current?.focus(); }
    }}>
      <summary ref={opener} role="button" tabIndex={0} aria-label={`管理「${item.display_name}」`}>⋯</summary>
      <div><button type="button" onClick={() => choose("rename")}>重新命名</button><button type="button" onClick={() => choose("delete")}>刪除教材</button></div>
    </details>}
    {mode !== "rename" && <h2>{item.display_name}</h2>}
    {deleting ? <p role="status" className="material-deleting">正在刪除…</p> : mode && <form className="material-management-form" aria-label={mode === "rename" ? "重新命名教材" : "刪除教材確認"}
      onSubmit={event => { event.preventDefault(); void submit(); }} onKeyDown={event => { if (event.key === "Escape") { event.preventDefault(); close(); } }}>
      {mode === "rename" ? <>
        <label>教材名稱<input ref={input} type="text" value={name} disabled={busy} onChange={event => setName(event.target.value)} /></label>
        {!valid && <p>名稱需為 1–200 個字，且不可包含控制字元。</p>}
      </> : <>
        <h3>確定要刪除這份教材嗎？</h3>
        {processing && <p>目前處理會先安全停止，之後刪除教材與相關資料。</p>}
        <p>{item.ingestion_kind ? "將一併刪除原始教材、轉換產物、處理紀錄，以及知識地圖、學習進度、題目與作答紀錄。此操作無法復原。" : published ? "將一併刪除原始 PDF、知識地圖、學習進度、題目與作答紀錄。此操作無法復原。" : "將刪除原始 PDF 與處理紀錄。此操作無法復原。"}</p>
      </>}
      <div className="material-management-actions">
        <button ref={cancel} className="secondary-button" type="button" disabled={busy} onClick={close}>取消</button>
        <button className={`secondary-button${mode === "delete" ? " cancel-confirm-button" : ""}`} type="submit" disabled={busy || (mode === "rename" && !valid)}>{busy ? mode === "rename" ? "正在儲存…" : "正在刪除…" : mode === "rename" ? "儲存" : "確認刪除"}</button>
      </div>
      {busy && <p role="status">{mode === "rename" ? "正在儲存…" : "正在送出刪除要求…"}</p>}
      {error && <p className="form-error" role="alert">{error}</p>}
    </form>}
  </>;
}
