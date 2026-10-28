import { useEffect, useRef, useState } from "react";
import { Icon } from "../../ui/Icon";

export type CardListItem = { key: string; label: string; current: boolean; status: string | null };

/** 標題列的「卡片清單」下拉：跳到指定卡片，並顯示每張的標記狀態；概念多時可搜尋。 */
export function CardListMenu({ items, disabled, onSelect }: { items: CardListItem[]; disabled?: boolean; onSelect: (index: number) => void }) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const root = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (event: Event) => { if (!root.current?.contains(event.target as Node)) setOpen(false); };
    document.addEventListener("pointerdown", close);
    return () => document.removeEventListener("pointerdown", close);
  }, [open]);
  const needle = query.trim().toLocaleLowerCase();
  const shown = items.flatMap((item, index) => !needle || item.label.toLocaleLowerCase().includes(needle) ? [{ item, index }] : []);
  return <div ref={root} className="card-list-menu" onKeyDown={event => { if (event.key === "Escape" && open) { event.stopPropagation(); setOpen(false); } }}>
    <button type="button" className="secondary-button cards-head-button" aria-haspopup="true" aria-expanded={open} disabled={disabled} onClick={() => setOpen(value => !value)}>
      卡片清單<Icon name="chevron-right" size={14} />
    </button>
    {open && <nav className="card-list-popover" aria-label="選擇概念卡">
      {items.length > 12 && <input type="search" aria-label="搜尋概念卡" placeholder={`搜尋 ${items.length} 張概念…`} value={query} onChange={event => setQuery(event.target.value)} />}
      {shown.map(({ item, index }) => <button type="button" key={item.key} aria-current={item.current ? "true" : undefined}
        onClick={() => { setOpen(false); onSelect(index); }}>
        <span><span aria-hidden="true">{index + 1}. </span>{item.label}</span>{item.status && <small>{item.status}</small>}
      </button>)}
      {needle && !shown.length && <p>找不到符合的概念。</p>}
    </nav>}
  </div>;
}
