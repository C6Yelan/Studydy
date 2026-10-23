import { useState } from 'react';
import { InlineConfirm } from '../../ui/InlineConfirm';

export function PodcastCancelControl({busy, onConfirm}: {busy:boolean; onConfirm:()=>void}) {
  const [confirming, setConfirming] = useState(false);
  if (!confirming) return <button type="button" className="secondary-button processing-destructive" data-inline-confirm-trigger disabled={busy}
    onClick={() => setConfirming(true)}>取消並刪除</button>;
  return <InlineConfirm message="取消生成並刪除整份 Podcast？" label="取消並刪除 Podcast 確認"
    detail="所有分集的生成會停止，已產生的講稿、音訊與影片也會刪除。原教材會保留。" cancelLabel="保留 Podcast"
    confirmLabel="取消並刪除" busyLabel="正在取消並刪除…" busy={busy} onCancel={() => setConfirming(false)} onConfirm={onConfirm} />;
}
