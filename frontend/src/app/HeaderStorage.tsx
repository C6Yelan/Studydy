import { Progress } from "../ui/Progress";
import { useEffect, useId, useRef, useState } from "react";
import { formatBytes, type StudydyApiClient } from "../api/client";
import type { AccountStorage } from "../api/contracts";
import { routePath, writeRoute } from "./routes";
import "./storage-breakdown.css";

const categories = [
  { key: "material_files_bytes", label: "教材原檔與轉換檔", color: "#2563eb" },
  { key: "podcast_audio_bytes", label: "Podcast 音訊", color: "#0f8278" },
  { key: "podcast_video_bytes", label: "Podcast 影片", color: "#b77900" },
  { key: "analysis_bytes", label: "分析資料", color: "#7c3aed" },
  { key: "reserved_bytes", label: "處理中預留", color: "#c2416c" },
] as const;

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
        <Progress className="header-storage-progress" tone={full ? "warning" : "default"} value={storage.used_bytes} max={storage.limit_bytes} aria-label="已用帳號空間" />
        <span>{used}／{limit}</span>
      </span>
      <span className="header-storage-compact" aria-hidden="true">{percentage > 0 && percentage < 1 ? "<1" : Math.round(percentage)}%</span>
    </button>
    {open && <section id={panelId} role="region" aria-label="帳號空間" className="header-storage-panel storage-breakdown-panel">
      <header className="storage-breakdown-heading"><h2>帳號空間</h2><span>{used}／{limit}</span></header>
      <div className="storage-breakdown-bar" role="img" aria-label={`空間分類：已用 ${used}，剩餘 ${remaining}`}>
        {categories.map(category => <span key={category.key} aria-hidden="true" style={{ width: `${storage.breakdown[category.key] / Math.max(storage.limit_bytes, storage.used_bytes) * 100}%`, backgroundColor: category.color }} />)}
      </div>
      <dl className="storage-breakdown-legend">{categories.map(category => <div key={category.key}>
        <dt><span aria-hidden="true" style={{ backgroundColor: category.color }}/>{category.label}
          {category.key === "reserved_bytes" && storage.reserved_bytes > 0 && <span className="storage-reservation-info">
            <button type="button" aria-label="處理中預留說明" aria-describedby={`${panelId}-reservation`}>ⓘ</button>
            <span role="tooltip" id={`${panelId}-reservation`}>已用包含 {formatBytes(storage.reserved_bytes)} 預留給上傳中的檔案與生成中的 Podcast／影片。</span>
          </span>}
        </dt>
        <dd>{formatBytes(storage.breakdown[category.key])}</dd>
      </div>)}</dl>
      <div className="storage-largest">
        <h3>佔用最多</h3>
        {storage.largest_materials.length ? <ol>{storage.largest_materials.slice(0, 3).map(material => <li key={material.material_id}>
          <a className="header-storage-material-link" href={routePath({ name: "material", materialId: material.material_id })} onClick={event => {
            if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
            event.preventDefault(); setOpen(false); writeRoute({ name: "material", materialId: material.material_id });
          }} title={material.display_name}>{material.display_name}</a>
          <span>{formatBytes(material.used_bytes)}</span>
        </li>)}</ol> : <p>目前沒有佔用空間的教材。</p>}
      </div>
      {full && <p className="header-storage-warning">空間已滿：無法上傳教材、建立 Podcast 或生成影片；已有內容仍可閱讀、播放與刪除。</p>}
    </section>}
  </div>;
}
