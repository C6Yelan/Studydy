import { useEffect, useId, useState } from "react";
import { formatBytes, type StudydyApiClient } from "../../api/client";
import type { AccountStorage } from "../../api/contracts";
import { Progress } from "../../ui/Progress";
import { RouteLink } from "../../ui/RouteLink";
import "./storage.css";

const categories = [
  { key: "material_files_bytes", label: "教材原檔與轉換檔", color: "#2563eb" },
  { key: "podcast_audio_bytes", label: "Podcast 音訊", color: "#0f8278" },
  { key: "podcast_video_bytes", label: "Podcast 影片", color: "#b77900" },
  { key: "analysis_bytes", label: "分析資料", color: "#7c3aed" },
  { key: "reserved_bytes", label: "預留空間", color: "#c2416c" },
] as const;

// 個人檔案的儲存空間卡：已用／上限與進度條，「各教材用量」展開分類與佔用最多的教材。讀不到時整張卡不顯示。
export function ProfileStorage({ apiClient }: { apiClient: StudydyApiClient }) {
  const [storage, setStorage] = useState<AccountStorage | null>(null);
  const [open, setOpen] = useState(false);
  const panelId = useId();
  useEffect(() => {
    let cancelled = false;
    void apiClient.getAccountStorage().then(
      (value) => { if (!cancelled) setStorage(value); },
      () => { if (!cancelled) setStorage(null); },
    );
    return () => { cancelled = true; };
  }, [apiClient]);
  if (!storage) return null;
  const full = storage.remaining_bytes === 0;
  const used = formatBytes(storage.used_bytes);
  const limit = formatBytes(storage.limit_bytes);
  const remaining = formatBytes(storage.remaining_bytes);
  return <section className={`profile-storage surface${full ? " is-full" : ""}`} aria-labelledby={`${panelId}-title`}>
    <header>
      <h2 id={`${panelId}-title`}>儲存空間</h2>
      <button className="text-button" type="button" aria-expanded={open} aria-controls={panelId} onClick={() => setOpen(value => !value)}>
        各教材用量
      </button>
    </header>
    <p className="profile-storage-total" aria-label={`已用 ${used}，上限 ${limit}，剩餘 ${remaining}`}><b>{used}</b><small>／{limit}</small></p>
    <Progress tone={full ? "warning" : "default"} value={storage.used_bytes} max={storage.limit_bytes} aria-label="已用帳號空間" />
    {full && <p className="profile-storage-warning">空間已滿：無法上傳教材、建立 Podcast 或生成影片；已有內容仍可閱讀、播放與刪除。</p>}
    {open && <div id={panelId} role="region" aria-label="各教材用量" className="storage-breakdown-panel">
      <div className="storage-breakdown-bar" role="img" aria-label={`空間分類：已用 ${used}，剩餘 ${remaining}`}>
        {categories.map(category => <span key={category.key} aria-hidden="true" style={{ width: `${storage.breakdown[category.key] / Math.max(storage.limit_bytes, storage.used_bytes) * 100}%`, backgroundColor: category.color }} />)}
      </div>
      <dl className="storage-breakdown-legend">{categories.map(category => <div key={category.key}>
        <dt><span aria-hidden="true" style={{ backgroundColor: category.color }}/>{category.label}
          {category.key === "reserved_bytes" && storage.reserved_bytes > 0 && <span className="storage-reservation-info">
            <button type="button" aria-label="預留空間說明" aria-describedby={`${panelId}-reservation`}>ⓘ</button>
            <span role="tooltip" id={`${panelId}-reservation`}>已用包含 {formatBytes(storage.reserved_bytes)} 預留給上傳中的檔案，以及生成中或生成失敗的 Podcast／影片；刪除生成失敗的 Podcast 會釋放這部分。</span>
          </span>}
        </dt>
        <dd>{formatBytes(storage.breakdown[category.key])}</dd>
      </div>)}</dl>
      <div className="storage-largest">
        <h3>佔用最多</h3>
        {storage.largest_materials.length ? <ol>{storage.largest_materials.slice(0, 3).map(material => <li key={material.material_id}>
          <RouteLink route={{ name: "material", materialId: material.material_id }} title={material.display_name}>{material.display_name}</RouteLink>
          <span>{formatBytes(material.used_bytes)}</span>
        </li>)}</ol> : <p>目前沒有佔用空間的教材。</p>}
      </div>
    </div>}
  </section>;
}
