import { useEffect, useId, useRef, useState } from "react";
import { formatBytes, type StudydyApiClient } from "../api/client";
import type { AccountStorage } from "../api/contracts";

export function HeaderStorage({ apiClient }: { apiClient: StudydyApiClient }) {
  const [storage, setStorage] = useState<AccountStorage | null>(null);
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  const panelId = useId();
  useEffect(() => {
    let cancelled = false;
    let request = 0;
    // 換頁或回到視窗時重新讀取；讀取中沿用上次的數字，避免頁首元件消失又出現造成跳動。
    const load = () => {
      const current = ++request;
      setOpen(false);
      void apiClient.getAccountStorage().then(
        (value) => { if (!cancelled && current === request) setStorage(value); },
        () => { if (!cancelled && current === request) setStorage(null); },
      );
    };
    load();
    window.addEventListener("popstate", load);
    window.addEventListener("focus", load);
    return () => {
      cancelled = true;
      window.removeEventListener("popstate", load);
      window.removeEventListener("focus", load);
    };
  }, [apiClient]);
  useEffect(() => {
    if (!open) return;
    const close = () => { setOpen(false); button.current?.focus(); };
    const outside = (event: MouseEvent) => {
      if (!root.current?.contains(event.target as Node)) close();
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape") { event.preventDefault(); close(); }
    };
    document.addEventListener("click", outside);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("click", outside);
      document.removeEventListener("keydown", escape);
    };
  }, [open]);
  if (!storage) return null;
  const full = storage.remaining_bytes === 0;
  const percentage = storage.used_bytes / storage.limit_bytes * 100;
  const used = formatBytes(storage.used_bytes);
  const limit = formatBytes(storage.limit_bytes);
  const remaining = formatBytes(storage.remaining_bytes);
  return <div ref={root} className={`header-storage${full ? " header-storage-full" : ""}`}>
    <button ref={button} type="button" className="header-storage-button"
      aria-label={`帳號空間：已用 ${used}，上限 ${limit}，剩餘 ${remaining}`}
      aria-expanded={open} aria-controls={panelId} onClick={() => setOpen(value => !value)}>
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
        <ellipse cx="12" cy="5" rx="8" ry="3" /><path d="M4 5v14c0 4 16 4 16 0V5M4 12c0 4 16 4 16 0" />
      </svg>
      <span className="header-storage-wide" aria-hidden="true">
        <span className="header-storage-bar"><i style={{ width: `${Math.min(100, percentage)}%` }} /></span>
        <span>{used}／{limit}</span>
      </span>
      <span className="header-storage-compact" aria-hidden="true">{percentage > 0 && percentage < 1 ? "<1" : Math.round(percentage)}%</span>
    </button>
    {open && <section id={panelId} role="region" aria-label="帳號空間" className="header-storage-panel">
      <h2>帳號空間</h2>
      <p className="header-storage-numbers"><strong>已用 {used}</strong><span>／上限 {limit} · 剩餘 {remaining}</span></p>
      {storage.reserved_bytes > 0 && <p>已用包含 {formatBytes(storage.reserved_bytes)} 預留給上傳中的檔案與生成中的 Podcast／影片。</p>}
      <progress className="header-storage-meter" value={Math.min(storage.used_bytes, storage.limit_bytes)} max={storage.limit_bytes} aria-label="已用帳號空間" />
      <p className={full ? "header-storage-warning" : undefined}>{full
        ? "空間已滿：無法上傳教材、建立 Podcast 或生成影片；已有內容仍可閱讀、播放與刪除。"
        : "包含教材原檔與轉換檔、Podcast 音訊與影片、分析資料，以及上傳中與生成中內容的預留空間。刪除教材、Podcast 或影片可釋出空間。"}</p>
    </section>}
  </div>;
}
