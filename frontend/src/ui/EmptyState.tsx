import type { ReactNode } from "react";

/** 清單與分頁共用的空狀態：吉祥物插圖＋標題＋說明＋主要按鈕。 */
export function EmptyState({ label, title, description, action, image = "/assets/studydy/empty-disappointed.png" }: {
  label?: string;
  title: string;
  description: string;
  action?: ReactNode;
  image?: string;
}) {
  return <section className="library-empty empty-state surface" aria-label={label ?? title}>
    <div className="library-empty-illustration"><img src={image} alt="" /></div>
    <h2>{title}</h2>
    <p>{description}</p>
    {action && <div className="empty-state-actions">{action}</div>}
  </section>;
}
