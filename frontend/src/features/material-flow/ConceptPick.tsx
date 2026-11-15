import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import type { KnowledgeRegionsView } from "../../api/contracts";
import { mapRegions } from "../knowledge-map/knowledge-map";
import { LearningSteps } from "./Flow";
import { Modal } from "../../app/Modal";
import "./concept-pick.css";

/** 建立卡組、調整卡組、建立 Podcast 共用的「挑概念」頁：頁頂（麵包屑、標題、步驟、說明）、左右兩個面板、底部固定列。 */

export type SelectionFilter = "all" | "selected" | "unselected";
export type ChapterGroup = { id: string; label: string; ids: string[] };

/**
 * 依地圖章節分組。只有 basis 為 chapters、區域剛好涵蓋全部概念、且至少兩章時才分組；
 * 其餘（來源檔、學習順序、讀不到、與地圖不一致）回傳 null，呼叫端退回原本的清單。
 */
export function chapterGroups(concepts: { concept_id: string; label: string; aliases: string[] }[], regions: KnowledgeRegionsView | null): ChapterGroup[] | null {
  if (regions?.basis !== "chapters") return null;
  const mapped = mapRegions({ concepts }, regions);
  if (!mapped || mapped.regions.length < 2) return null;
  return mapped.regions.map((region) => ({ id: region.id, label: region.label, ids: region.conceptIds }));
}

export function PickHead({ crumb, title, sub, steps }: {
  crumb: ReactNode; title: ReactNode; sub?: ReactNode;
  steps?: { labels: string[]; current: number; label: string };
}) {
  return <header className="pick-head">
    {crumb}
    <div className="pick-head-row">
      <h1>{title}</h1>
      {steps && <LearningSteps labels={steps.labels} current={steps.current} label={steps.label} />}
    </div>
    {sub && <p className="pick-sub">{sub}</p>}
  </header>;
}

/** 左面板上方：搜尋、全選／取消全選、全部／已選／未選。 */
export function ConceptPickTools({ searchLabel, query, onQuery, selectAllLabel = "全選", selectNoneLabel = "取消全選", onSelectAll, onSelectNone, busy, filter, onFilter, total, selected }: {
  searchLabel: string; query: string; onQuery: (value: string) => void;
  selectAllLabel?: string; selectNoneLabel?: string; onSelectAll: () => void; onSelectNone: () => void; busy: boolean;
  filter: SelectionFilter; onFilter: (value: SelectionFilter) => void; total: number; selected: number;
}) {
  return <div className="cards-selection-heading">
    <input className="cards-concept-search" type="search" aria-label={searchLabel} placeholder="搜尋概念…" value={query} onChange={(event) => onQuery(event.target.value)} />
    <div className="cards-selection-actions"><button className="text-button" type="button" disabled={busy} onClick={onSelectAll}>{selectAllLabel}</button><span>／</span><button className="text-button" type="button" disabled={busy} onClick={onSelectNone}>{selectNoneLabel}</button></div>
    <div className="concept-selection-filters">
      {([["all", "全部", total], ["selected", "已選", selected], ["unselected", "未選", total - selected]] as const).map(([value, label, count]) =>
        <button key={value} type="button" className="secondary-button" aria-pressed={filter === value} onClick={() => onFilter(value)}>{label} {count}</button>)}
    </div>
  </div>;
}

function ChapterCheckbox({ label, selected, total, busy, onChange }: { label: string; selected: number; total: number; busy: boolean; onChange: (on: boolean) => void }) {
  const box = useRef<HTMLInputElement>(null);
  useEffect(() => { if (box.current) box.current.indeterminate = selected > 0 && selected < total; }, [selected, total]);
  return <label className="chapter-head">
    <input ref={box} type="checkbox" aria-label={`整章選取：${label}`} checked={selected === total} disabled={busy} onChange={(event) => onChange(event.target.checked)} />
    <strong>{label}</strong><small>已選 {selected} / {total} 個概念</small>
  </label>;
}

/** 照章節分組的概念清單：每章一個標題（可整章勾選）；搜尋與已選／未選篩選只影響章內列出哪些概念，整章勾選一律作用在那章全部概念。 */
export function ChapterGroups<C extends { concept_id: string }>({ groups, visible, selected, busy, onToggleChapter, itemsClassName, renderConcept }: {
  groups: ChapterGroup[]; visible: C[]; selected: Set<string>; busy: boolean;
  onToggleChapter: (ids: string[], on: boolean) => void; itemsClassName: string; renderConcept: (concept: C) => ReactNode;
}) {
  const byId = new Map(visible.map((concept) => [concept.concept_id, concept]));
  return <>{groups.map((group) => {
    const items = group.ids.flatMap((id) => byId.has(id) ? [byId.get(id)!] : []);
    if (!items.length) return null;
    return <section className="chapter-group" key={group.id}>
      <ChapterCheckbox label={group.label} selected={group.ids.filter((id) => selected.has(id)).length} total={group.ids.length} busy={busy} onChange={(on) => onToggleChapter(group.ids, on)} />
      <div className={itemsClassName}>{items.map(renderConcept)}</div>
    </section>;
  })}</>;
}

/** 教材原文每條只露出兩行；真的被截掉才出現「展開全文」，換一個概念（resetKey）就回到收合。 */
export function ClampedClaims({ resetKey, children }: { resetKey: string; children: ReactNode }) {
  const [expandedFor, setExpandedFor] = useState<string | null>(null);
  const expanded = expandedFor === resetKey;
  const box = useRef<HTMLDivElement>(null);
  const [clipped, setClipped] = useState(false);
  useLayoutEffect(() => {
    const element = box.current;
    if (!element) return;
    const check = () => setClipped([...element.querySelectorAll(".pick-claim-text")].some((text) => text.scrollHeight > text.clientHeight + 1));
    check();
    const observer = new ResizeObserver(check);
    observer.observe(element);
    return () => observer.disconnect();
  }, [resetKey, expanded]);
  return <div className={`pick-claims${expanded ? "" : " is-clamped"}`}>
    <div ref={box}>{children}</div>
    {(expanded || clipped) && <button type="button" className="text-button pick-claims-toggle" aria-expanded={expanded} onClick={() => setExpandedFor(expanded ? null : resetKey)}>{expanded ? "收合重點" : "展開全文"}</button>}
  </div>;
}

// 與 concept-pick.css 的手機斷點一致：手機上的概念預覽改成底部抽屜，不放在長清單下方。
const phoneQuery = "(max-width: 720px)";
export function usePhoneLayout() {
  const [phone, setPhone] = useState(() => window.matchMedia(phoneQuery).matches);
  useEffect(() => {
    const media = window.matchMedia(phoneQuery);
    const update = () => setPhone(media.matches);
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);
  return phone;
}

/** 手機的概念預覽抽屜：關閉鈕與 Esc 都會關；關閉後瀏覽器把焦點還給打開它的概念按鈕。 */
export function PreviewSheet({ title, open, onClose, children }: { title: string; open: boolean; onClose: () => void; children: ReactNode }) {
  return <Modal label={title} open={open} onClose={onClose} className="pick-preview-sheet">
    <header className="pick-preview-sheet-head">
      <strong>{title}</strong>
      <button className="header-dialog-close" type="button" aria-label="關閉預覽" onClick={onClose}>×</button>
    </header>
    <div className="pick-preview-sheet-body">{children}</div>
  </Modal>;
}
