import { useEffect, useRef, useState } from 'react';
import { InlineConfirm } from '../../ui/InlineConfirm';
import { errorMessage, type StudydyApiClient } from '../../api/client';
import { useDismissibleMenu } from '../material-flow/useDismissibleMenu';
import type { ResearchSummary } from './research';

export function ResearchManagement({ api, item, onDeleted }: { api: StudydyApiClient; item: ResearchSummary; onDeleted: () => void }) {
  const [confirming, setConfirming] = useState(false), [busy, setBusy] = useState(false), [error, setError] = useState('');
  const menu = useRef<HTMLDetailsElement>(null), opener = useRef<HTMLElement>(null);
  const alive = useRef(false), inFlight = useRef(false), interacted = useRef(false);
  useDismissibleMenu(menu, opener);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useEffect(() => {
    if (!interacted.current) return;
    if (!confirming) opener.current?.focus({ preventScroll: true });
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
    {confirming && <InlineConfirm message="確定刪除這筆查詢？" label={`刪除查詢「${item.query}」確認`}
      detail="尚未送出的查詢處理會停止。已加入的教材、來源檔案與已送出的教材分析會保留。" confirmLabel="刪除" confirmAriaLabel="確認刪除查詢"
      busyLabel="正在刪除…" busy={busy} error={error} onCancel={close} onConfirm={() => void remove()} />}
  </>;
}
