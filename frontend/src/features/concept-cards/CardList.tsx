import { useEffect, useRef, useState } from "react";

export type CardListItem = { key: string; label: string; current: boolean; status: string | null };

/** 卡片左側的清單欄：列出所有卡片與標記狀態，點選跳到該卡；目前卡自動捲到可見，概念多時可搜尋。 */
export function CardList({ items, disabled, onSelect }: { items: CardListItem[]; disabled?: boolean; onSelect: (index: number) => void }) {
  const [query, setQuery] = useState("");
  const scroller = useRef<HTMLDivElement>(null);
  const needle = query.trim().toLocaleLowerCase();
  const shown = items.flatMap((item, index) => !needle || item.label.toLocaleLowerCase().includes(needle) ? [{ item, index }] : []);
  const currentKey = items.find(item => item.current)?.key;
  useEffect(() => {
    // 只捲動清單本身，不帶動整個頁面。
    const box = scroller.current, row = box?.querySelector<HTMLElement>("[aria-current=true]");
    if (!box || !row) return;
    const boxRect = box.getBoundingClientRect(), rowRect = row.getBoundingClientRect();
    if (rowRect.top < boxRect.top) box.scrollTop += rowRect.top - boxRect.top - 8;
    else if (rowRect.bottom > boxRect.bottom) box.scrollTop += rowRect.bottom - boxRect.bottom + 8;
    if (rowRect.left < boxRect.left) box.scrollLeft += rowRect.left - boxRect.left - 8;
    else if (rowRect.right > boxRect.right) box.scrollLeft += rowRect.right - boxRect.right + 8;
  }, [currentKey, needle]);
  return <nav className="card-list" aria-label="選擇概念卡">
    {items.length > 12 && <input type="search" aria-label="搜尋概念卡" placeholder="搜尋概念…" value={query} onChange={event => setQuery(event.target.value)} />}
    <div ref={scroller} className="card-list-scroll">
      {shown.map(({ item, index }) => <button type="button" key={item.key} disabled={disabled} aria-current={item.current ? "true" : undefined} onClick={() => onSelect(index)}>
        <i className="card-list-dot" data-status={item.status ?? undefined} {...(item.status ? { role: "img", "aria-label": item.status } : { "aria-hidden": true })} />
        <span className="card-list-no" aria-hidden="true">{index + 1}</span><span className="card-list-label">{item.label}</span>
      </button>)}
      {needle && !shown.length && <p>找不到符合的概念。</p>}
    </div>
  </nav>;
}
