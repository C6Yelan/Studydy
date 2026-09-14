import { useEffect, useId, useRef, useState } from "react";

import { errorMessage, type StudydyApiClient } from "../../api/client";
import type { MaterialDiscardView } from "../../api/contracts";

export function MaterialRemoveControl({ apiClient, materialId, onAccepted, inActionRow = false }: {
  apiClient: StudydyApiClient;
  materialId: string;
  onAccepted: (state: MaterialDiscardView["state"]) => void;
  inActionRow?: boolean;
}) {
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [removing, setRemoving] = useState(false);
  const inFlight = useRef(false);
  const mounted = useRef(true);
  const action = useRef<HTMLButtonElement>(null);
  const keep = useRef<HTMLButtonElement>(null);
  const title = useId();
  const interacted = useRef(false);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => {
    if (!interacted.current) return;
    if (confirming) keep.current?.focus(); else action.current?.focus();
  }, [confirming]);
  const submit = async () => {
    if (inFlight.current) return;
    inFlight.current = true; setBusy(true); setError(null);
    try {
      const result = await apiClient.discardMaterial(materialId);
      if (!mounted.current) return;
      setRemoving(true); onAccepted(result.state);
    } catch (failure) {
      if (mounted.current) setError(`無法刪除教材。${errorMessage(failure)}`);
    } finally {
      inFlight.current = false;
      if (mounted.current) setBusy(false);
    }
  };
  if (removing) return <p className="material-remove-message" role="status">正在刪除…</p>;
  const content = <>
    {(inActionRow || !confirming) && <button ref={action} className="secondary-button" type="button" disabled={confirming || busy} onClick={() => { interacted.current = true; setConfirming(true); }}>刪除教材</button>}
    {confirming && <section className="cancel-confirmation" aria-labelledby={title} onKeyDown={event => {
      if (event.key === "Escape" && !busy) { setConfirming(false); setError(null); }
    }}>
      <h3 id={title}>確定要刪除這份教材嗎？</h3>
      <p>原始 PDF、處理紀錄，以及既有的知識地圖、學習進度、題目與作答紀錄會一併刪除。此操作無法復原。</p>
      <div className="state-actions">
        <button ref={keep} className="secondary-button" type="button" disabled={busy} onClick={() => { setConfirming(false); setError(null); }}>取消</button>
        <button className="secondary-button cancel-confirm-button" type="button" disabled={busy} onClick={() => void submit()}>確認刪除</button>
      </div>
    </section>}
    {busy && <p className="material-remove-message" role="status">正在刪除…</p>}
    {error && <p className="form-error material-remove-message" role="alert">{error}</p>}
  </>;
  return inActionRow ? content : <div className="material-remove-control">{content}</div>;
}
