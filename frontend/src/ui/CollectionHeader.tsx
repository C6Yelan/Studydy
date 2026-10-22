import type { ReactNode, Ref } from "react";

/**
 * 我的教材、概念卡、Podcast 三個清單頁共用的頁首：標題＋說明，右側主要操作；
 * 下一列是搜尋欄（我的教材沒有，改用頁首全站搜尋）與「已保存 N …」計數。
 */
export function CollectionHeader({
  title, subtitle, actions, headingRef, search, count, countRef,
}: {
  title: string;
  subtitle: ReactNode;
  actions?: ReactNode;
  headingRef?: Ref<HTMLHeadingElement>;
  search?: { label: string; placeholder: string; value: string; onChange: (value: string) => void; inputRef?: Ref<HTMLInputElement> };
  count?: string;
  countRef?: Ref<HTMLDivElement>;
}) {
  return <>
    <header className="library-header collection-head">
      <div className="collection-head-text">
        <h1 ref={headingRef} tabIndex={-1}>{title}</h1>
        <p className="library-subtitle">{subtitle}</p>
      </div>
      {actions && <div className="state-actions collection-head-actions">{actions}</div>}
    </header>
    {(search || count !== undefined) && <div className="collection-toolbar collection-toolbar-row">
      {search && <form className="library-search" role="search" onSubmit={(event) => event.preventDefault()}>
        <input ref={search.inputRef} type="search" aria-label={search.label} placeholder={search.placeholder} value={search.value}
          onChange={(event) => search.onChange(event.target.value)}
          // Chromium 原生 search 會用 Escape 清空；僅阻止此預設行為，保留右側 ×。
          onKeyDown={(event) => { if (event.key === "Escape") event.preventDefault(); }} />
      </form>}
      {count !== undefined && <div className="collection-summary" ref={countRef} tabIndex={-1}><p>{count}</p></div>}
    </div>}
  </>;
}
