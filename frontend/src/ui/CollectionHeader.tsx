import type { ReactNode, Ref } from "react";

/**
 * 我的教材、概念卡、Podcast 三個清單頁共用的頁首：標題＋說明（含數量），右側主要操作。
 * 各頁不另設搜尋框，統一使用頁首的全站搜尋。
 */
export function CollectionHeader({
  title, subtitle, actions, headingRef,
}: {
  title: string;
  subtitle: ReactNode;
  actions?: ReactNode;
  headingRef?: Ref<HTMLHeadingElement>;
}) {
  return <header className="library-header collection-head">
    <div className="collection-head-text">
      <h1 ref={headingRef} tabIndex={-1}>{title}</h1>
      <p className="library-subtitle">{subtitle}</p>
    </div>
    {actions && <div className="state-actions collection-head-actions">{actions}</div>}
  </header>;
}
