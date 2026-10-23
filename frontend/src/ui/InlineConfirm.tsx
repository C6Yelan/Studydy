import { useEffect, useRef } from "react";

// 在原位置確認（取代瀏覽器原生 confirm）：一句說明＋「取消」＋確認按鈕。
// 開啟時焦點落在「取消」；Esc 或「取消」關閉，焦點交還給同一區塊內標了 data-inline-confirm-trigger 的按鈕
//（觸發按鈕在確認期間通常被換掉，重新出現的是新元素）。
// label：群組的無障礙名稱（預設同 message）；confirmAriaLabel：確認按鈕文字太短時的完整說法；
// detail：一句補充說明；error：失敗訊息，留在確認區內讓使用者重試或取消。
export function InlineConfirm({ message, label, detail, error, confirmLabel, confirmAriaLabel, busyLabel, cancelLabel = "取消", busy = false, danger = true, onConfirm, onCancel }: {
  message: string; label?: string; detail?: string; error?: string | null; confirmLabel: string; confirmAriaLabel?: string; busyLabel?: string;
  cancelLabel?: string; busy?: boolean; danger?: boolean; onConfirm: () => void; onCancel: () => void;
}) {
  const root = useRef<HTMLDivElement>(null);
  const keep = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    const container = root.current?.parentElement;
    keep.current?.focus();
    return () => {
      // 等觸發按鈕重新渲染後再交還焦點；焦點已移到別處（例如導頁）就不搶。
      window.setTimeout(() => {
        if (document.activeElement && document.activeElement !== document.body) return;
        container?.querySelector<HTMLElement>("[data-inline-confirm-trigger]")?.focus();
      });
    };
  }, []);
  // 失敗後按鈕重新可用時，焦點回到「取消」，可以直接重試或離開。
  useEffect(() => { if (error && !busy) keep.current?.focus(); }, [error, busy]);
  return <div ref={root} className="inline-confirm" role="group" aria-label={label ?? message} aria-busy={busy}
    onKeyDown={(event) => { if (event.key === "Escape" && !busy) { event.preventDefault(); event.stopPropagation(); onCancel(); } }}>
    <span>{message}{detail && <small>{detail}</small>}</span>
    <div className="inline-confirm-actions">
      <button ref={keep} type="button" className="secondary-button" disabled={busy} onClick={onCancel}>{cancelLabel}</button>
      <button type="button" className={danger ? "secondary-button cancel-confirm-button" : "primary-button"} aria-label={busy ? undefined : confirmAriaLabel} disabled={busy} onClick={onConfirm}>
        {busy ? busyLabel ?? confirmLabel : confirmLabel}
      </button>
    </div>
    {error && <p className="form-error" role="alert">{error}</p>}
  </div>;
}
