import { useEffect, useRef, useState } from 'react';

export function PodcastCancelControl({busy, onConfirm}: {busy:boolean; onConfirm:()=>void}) {
  const [confirming, setConfirming] = useState(false);
  const keep = useRef<HTMLButtonElement>(null), opener = useRef<HTMLButtonElement>(null);
  useEffect(() => { if (confirming) keep.current?.focus(); }, [confirming]);
  if (!confirming) return <button ref={opener} type="button" className="secondary-button processing-destructive" disabled={busy}
    onClick={() => setConfirming(true)}>取消並刪除</button>;
  return <div className="cancel-confirmation" role="group" aria-label="取消並刪除 Podcast 確認">
    <h3>取消生成並刪除整份 Podcast？</h3>
    <p>所有分集的生成會停止，已產生的講稿、音訊與影片也會刪除。原教材會保留。</p>
    <div className="state-actions">
      <button ref={keep} type="button" className="secondary-button" disabled={busy} onClick={() => {
        setConfirming(false); requestAnimationFrame(() => opener.current?.focus());
      }}>保留 Podcast</button>
      <button type="button" className="secondary-button cancel-confirm-button" disabled={busy} onClick={onConfirm}>
        {busy ? '正在取消並刪除…' : '取消並刪除'}
      </button>
    </div>
  </div>;
}
