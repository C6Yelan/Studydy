import { useEffect, useRef, useState } from 'react';
import { errorMessage, type StudydyApiClient } from '../../api/client';
import { useDismissibleMenu } from '../material-flow/useDismissibleMenu';
import type { ResearchSummary } from './research';

export function ResearchManagement({ api, item, onDeleted }: { api: StudydyApiClient; item: ResearchSummary; onDeleted: () => void }) {
  const [confirming, setConfirming] = useState(false), [busy, setBusy] = useState(false), [error, setError] = useState('');
  const menu = useRef<HTMLDetailsElement>(null), opener = useRef<HTMLElement>(null), cancel = useRef<HTMLButtonElement>(null);
  const alive = useRef(false), inFlight = useRef(false), interacted = useRef(false);
  useDismissibleMenu(menu, opener);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useEffect(() => {
    if (!interacted.current) return;
    if (confirming) cancel.current?.focus(); else opener.current?.focus({ preventScroll: true });
  }, [confirming]);
  const close = () => { if (!inFlight.current) { setConfirming(false); setError(''); } };
  const remove = async () => {
    if (inFlight.current) return;
    inFlight.current = true; setBusy(true); setError('');
    try {
      await api.studyTools(`/v1/research/${item.research_id}`, undefined, undefined, 'DELETE');
      if (alive.current) onDeleted();
    } catch (failure) { if (alive.current) setError(errorMessage(failure)); }
    finally { inFlight.current = false; if (alive.current) setBusy(false); }
  };
  return <>
    <details ref={menu} className="material-management-menu" name="research-management" hidden={confirming}>
      <summary ref={opener} role="button" tabIndex={0} aria-label={`管理查詢「${item.query}」`}>⋯</summary>
      <div><button className="material-delete-action" type="button" onClick={() => {
        if (menu.current) menu.current.open = false;
        interacted.current = true; setError(''); setConfirming(true);
      }}>刪除查詢</button></div>
    </details>
    {confirming && <form className="material-management-form" aria-label={`刪除查詢「${item.query}」確認`} aria-busy={busy}
      onSubmit={event => { event.preventDefault(); void remove(); }}
      onKeyDown={event => { if (event.key === 'Escape') { event.preventDefault(); close(); } }}>
      <h3>確定刪除這筆查詢？</h3>
      <p>尚未送出的查詢處理會停止。已加入的教材、來源檔案與已送出的教材分析會保留。</p>
      <div className="material-management-actions"><button ref={cancel} className="secondary-button" type="button" disabled={busy} onClick={close}>取消</button>
        <button className="secondary-button cancel-confirm-button" type="submit" aria-label="確認刪除查詢" disabled={busy}>{busy ? '正在刪除…' : '刪除'}</button></div>
      {error && <p className="form-error" role="alert">{error}</p>}
    </form>}
  </>;
}
