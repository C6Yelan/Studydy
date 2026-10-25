import { useEffect, useState } from "react";
import { errorMessage, formatBytes, type StudydyApiClient } from "../../api/client";
import type { AccountStorage } from "../../api/contracts";

// 帳號空間：教材原檔與轉換檔、Podcast 音訊與影片、分析檔及未收齊的分段上傳合計。
export function StorageCard({ apiClient }: { apiClient: StudydyApiClient }) {
  const [storage, setStorage] = useState<AccountStorage | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let cancelled = false;
    void apiClient.getAccountStorage().then(
      (value) => { if (!cancelled) setStorage(value); },
      (failure) => { if (!cancelled) setError(errorMessage(failure)); },
    );
    return () => { cancelled = true; };
  }, [apiClient]);
  const full = !!storage && storage.remaining_bytes === 0;
  return (
    <section className="profile-storage surface" aria-labelledby="account-storage-heading">
      <h2 id="account-storage-heading">帳號空間</h2>
      {error ? <p className="form-error" role="alert">{error}</p>
        : !storage ? <p className="profile-help" role="status">正在計算已用空間…</p>
        : <>
          <p className="profile-storage-numbers"><strong>已用 {formatBytes(storage.used_bytes)}</strong>
            <span>／上限 {formatBytes(storage.limit_bytes)} · 剩餘 {formatBytes(storage.remaining_bytes)}</span></p>
          {storage.reserved_bytes > 0 && <p className="profile-help">已用包含 {formatBytes(storage.reserved_bytes)} 預留給上傳中的檔案與生成中的 Podcast／影片。</p>}
          <progress className="profile-storage-meter" value={Math.min(storage.used_bytes, storage.limit_bytes)} max={storage.limit_bytes} aria-label="已用帳號空間" />
          <p className={full ? "form-error" : "profile-help"}>{full
            ? "空間已滿：無法上傳教材、建立 Podcast 或生成影片；已有內容仍可閱讀、播放與刪除。"
            : "包含教材原檔與轉換檔、Podcast 音訊與影片、分析資料，以及上傳中與生成中內容的預留空間。刪除教材、Podcast 或影片可釋出空間。"}</p>
        </>}
    </section>
  );
}
