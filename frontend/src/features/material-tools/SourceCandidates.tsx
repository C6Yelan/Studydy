import { SelectionToggle } from "../../ui/SelectionToggle";
import './research.css';
import { useEffect, useRef, useState } from 'react';
import { Icon } from '../../ui/Icon';

// 後端只送可選或已選的來源與學習者需要的欄位；搜尋管道、初篩與存取紀錄不送到前端。
export type Candidate = {
  id: string; kind: string; title: string; authors: string; year: number | null;
  url: string; license: string; license_url: string | null; eligible: boolean; state: string; error_code?: string;
  acquisition?: { license_text?: string; acquired_at: string };
  review?: { phase: string; relevance: string; reason: string; scope?: string };
};
export const sourceReasons: Record<string, string> = {
  RESEARCH_SOURCE_TYPE_UNCONFIRMED: '尚未核對為論文或 Wikipedia，請接續研究。',
  RESEARCH_SCOPE_REQUIRED: '請先確認要查找的觀念與細節，再開始搜尋。已保存的來源仍可查看。',
  RESEARCH_SNAPSHOT_UNAVAILABLE: '無法讀取這次研究保留的原文，請重新研究。',
  RESEARCH_PARTIAL_SEARCH: '已保留搜尋結果；部分觀念還沒找到合適來源，或有來源未完成。',
  RESEARCH_BODY_UNAVAILABLE: '入口只有標題或導覽，尚未取得正文。',
  RESEARCH_AUTH_REQUIRED: '此入口要求登入，未取得正文。',
  RESEARCH_SOURCE_BLOCKED: '來源網站拒絕讀取，未取得正文。',
  RESEARCH_RATE_LIMITED: '來源網站暫時限制請求，請稍後重試。',
  RESEARCH_SEARCH_FAILED: '搜尋暫時失敗，請稍後重試。',
  RESEARCH_DOWNLOAD_FAILED: '來源目前無法下載。',
  RESEARCH_LICENSE_UNCONFIRMED: '尚未確認全文的使用條件，未匯入此來源。',
  RESEARCH_IDENTITY_UNCONFIRMED: '目前無法確認這份文件，請改選其他來源。',
  RESEARCH_FORMAT_UNSUPPORTED: '目前無法處理此文件格式。',
  DUPLICATE_SOURCE: '這份文件已存在於教材。',
  RESEARCH_ITEMS_FAILED: '部分來源未完成。可重試，或只勾選已準備好的來源繼續。',
  RESEARCH_OFFICIAL_UNAVAILABLE: '部分官方文件搜尋暫時失敗；其他結果已保留。',
  RESEARCH_REVIEW_FAILED: '研究整理未完成，已保留進度，可接續處理。',
  RESEARCH_CONNECTION_TIMEOUT: '連線逾時', RESEARCH_CONNECTION_FAILED: '連線失敗', RESEARCH_DNS_FAILED: '無法解析來源網址',
  RESEARCH_CHALLENGE_REQUIRED: '網站回傳驗證頁', RESEARCH_HTTP_FAILED: '此入口未成功回應',
  RESEARCH_FEED_ARTICLE_MISSING: '公開 feed 沒有這篇文章', RESEARCH_TEXT_UNAVAILABLE: 'PDF 尚無可直接讀取的文字',
  RESEARCH_SOURCE_CHANGED: '來源正文已變更，請重新研究核對後再匯入。',
};
const states: Record<string, string> = { normalizing: '轉換中', ready: '已取得', failed: '取得失敗' };

export function sourceKindLabel(kind: string) {
  return kind === 'wikipedia' ? 'Wikipedia' : kind === 'official' ? '官方教學' : kind === 'web' ? '網頁文章' : '學術論文';
}

export function sourceHost(url: string) {
  try { return new URL(url).hostname.replace(/^www\./, ''); } catch { return ''; }
}

const PAGE_SIZE = 10;

function relevanceLabel(relevance: string) {
  return relevance === 'direct' ? '直接相關' : relevance === 'related' ? '背景參考' : '不相關';
}

// 來源的閱讀判斷與授權資訊集中在一個側邊面板（手機為底部面板），卡片本身維持固定高度。
function SourceDetail({ candidate: c, showAcquisitionDetails, onClose }: { candidate: Candidate; showAcquisitionDetails: boolean; onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const opener = useRef<Element | null>(null);
  useEffect(() => {
    opener.current = document.activeElement;
    dialog.current?.showModal();
    return () => { (opener.current as HTMLElement | null)?.focus?.(); };
  }, []);
  const link = /^https?:\/\//.test(c.url) ? c.url : undefined;
  const checked = c.review && `${c.review.scope === 'sections' ? '已閱讀相關段落。' : c.review.phase === 'body' ? '已閱讀全文。' : '依摘要判斷。'}${c.review.reason}`;
  return <dialog ref={dialog} className="source-detail-panel" aria-labelledby="source-detail-title"
    onCancel={event => { event.preventDefault(); event.stopPropagation(); onClose(); }}
    onKeyDown={event => { if (event.key === 'Escape') event.stopPropagation(); }}
    onClick={event => { if (event.target === dialog.current) onClose(); }}>
    <header>
      <div>
        <h2 id="source-detail-title">{c.title}</h2>
        <p>{[sourceKindLabel(c.kind), c.kind === 'wikipedia' ? null : c.authors, c.year].filter(Boolean).join(' · ')}</p>
      </div>
      <button type="button" className="source-detail-close" aria-label="關閉詳細資訊" onClick={onClose}><Icon name="close" size={18} /></button>
    </header>
    <div className="source-detail-body">
      {link && <a className="source-host-link" href={link} target="_blank" rel="noreferrer" title={c.url}>{sourceHost(c.url) || '原始來源'} ↗</a>}
      {checked && <section><h3>閱讀判斷{c.review && <span className={`source-relevance is-${c.review.relevance}`}>{relevanceLabel(c.review.relevance)}</span>}</h3>{/* 多個區段的判斷以「。」加空白接在一起，拆回段落。 */}{checked.split(/(?<=[。！？])\s+/).map((paragraph, i) => <p key={i}>{paragraph}</p>)}</section>}
      {showAcquisitionDetails && <section className="source-license"><h3>授權與取得資訊</h3>
        <p>{c.license || '尚未確認授權'}</p>
        {c.license_url && <a href={c.license_url} target="_blank" rel="noreferrer">查看授權說明 ↗</a>}
        {c.acquisition && <p>取得時間：{new Date(c.acquisition.acquired_at).toLocaleString('zh-TW')}</p>}
        {c.acquisition?.license_text && <pre>{c.acquisition.license_text}</pre>}
      </section>}
    </div>
  </dialog>;
}

export function SourceCandidates({ candidates, selected, disabled, onChange, readOnly = false, search, inlineSearch = false, showAcquisitionDetails = true, compact = false, previewOnly = false }: {
  candidates: Candidate[]; selected: string[]; disabled: boolean; compact?: boolean; previewOnly?: boolean;
  onChange: (ids: string[]) => void; readOnly?: boolean; inlineSearch?: boolean; showAcquisitionDetails?: boolean; search?: { value: string; onChange: (value: string) => void };
}) {
  const [localQuery, setLocalQuery] = useState('');
  const query = search?.value ?? localQuery;
  const setQuery = search?.onChange ?? setLocalQuery;
  const [kind, setKind] = useState('all');
  const [onlySelected, setOnlySelected] = useState(false);
  const [page, setPage] = useState(1);
  const [detail, setDetail] = useState<Candidate | null>(null);
  const list = useRef<HTMLDivElement>(null);
  const selectAll = useRef<HTMLInputElement>(null);
  const eligible = candidates.filter(candidate => candidate.eligible);
  const allSelected = eligible.length > 0 && eligible.every(candidate => selected.includes(candidate.id));
  const partiallySelected = !allSelected && eligible.some(candidate => selected.includes(candidate.id));
  useEffect(() => { if (selectAll.current) selectAll.current.indeterminate = partiallySelected; }, [partiallySelected, readOnly]);
  const available = readOnly ? candidates : eligible;
  const visible = available.filter(c =>
    (kind === 'all' || c.kind === kind) && (readOnly || !onlySelected || selected.includes(c.id)) &&
    `${c.title} ${c.authors}`.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()));
  const pageCount = Math.max(1, Math.ceil(visible.length / PAGE_SIZE));
  const currentPage = Math.min(page, pageCount);
  const pageItems = visible.slice((currentPage - 1) * PAGE_SIZE, currentPage * PAGE_SIZE);
  const pageNumbers = Array.from({ length: pageCount }, (_, i) => i + 1)
    .filter(number => number === 1 || number === pageCount || Math.abs(number - currentPage) <= 1);
  useEffect(() => { setPage(1); }, [query, kind, onlySelected, readOnly]);
  useEffect(() => { if (page > pageCount) setPage(current => Math.min(current, pageCount)); }, [page, pageCount]);
  useEffect(() => { list.current?.scrollTo({ top: 0 }); }, [currentPage, query, kind, onlySelected]);
  return <div className={`source-picker${readOnly ? ' is-readonly' : ''}${compact ? ' is-compact' : ''}`}>
    {(!readOnly || inlineSearch) && <>
      <div className={`source-picker-filters${search && !inlineSearch ? " is-external-search" : ""}`}>
        {(!search || inlineSearch) && <input type="search" aria-label={inlineSearch ? "篩選來源" : "搜尋來源"} placeholder={inlineSearch ? "篩選目前結果的來源名稱或作者…" : "搜尋來源名稱或作者…"} value={query} onChange={e => setQuery(e.target.value)} />}
        <select aria-label="來源類型" value={kind} onChange={e => setKind(e.target.value)}>
          <option value="all">全部來源</option><option value="paper">學術論文</option><option value="wikipedia">Wikipedia</option>
        </select>
        {compact && !readOnly && <>
          <SelectionToggle pressed={onlySelected} onChange={setOnlySelected}>已選 {selected.length} 個</SelectionToggle>
          <label className="source-toolbar-check" title="全選或取消全部可用來源，包含篩選外的來源"><input ref={selectAll} type="checkbox" aria-label="全選可用來源" checked={allSelected} disabled={disabled || !eligible.length} onChange={event => onChange(event.target.checked ? eligible.map(candidate => candidate.id) : [])}/>全選</label>
        </>}
      </div>
      {!readOnly && !compact && <div className="research-selection">
        <div><button className="text-button" disabled={disabled || !eligible.length} onClick={() => onChange(eligible.map(c => c.id))}>全選可用來源</button><span>／</span><button className="text-button" disabled={disabled} onClick={() => onChange([])}>取消全選</button></div>
        <SelectionToggle pressed={onlySelected} onChange={setOnlySelected}>已選 {selected.length} 個</SelectionToggle>
      </div>}
    </>}
    <div ref={list} className={`research-candidates${previewOnly ? ' research-preview-candidates' : ''}`} tabIndex={0} aria-label={previewOnly ? '搜尋中已核對的來源' : readOnly ? '已選來源' : '來源搜尋結果'}>
      {pageItems.map(c => {
        if(previewOnly)return <article key={c.id} className="research-source-preview">
          <button type="button" aria-haspopup="dialog" onClick={()=>setDetail(c)}>
            <Icon name={c.kind==='paper'?'file':c.kind==='web'?'search':'book'} size={20}/>
            <span><small>{sourceKindLabel(c.kind)}</small><strong>{c.title}</strong></span><Icon name="chevron-right" size={16}/>
          </button>
        </article>;
        // 只剩來源類型時（Wikipedia 沒有個別作者與年份）與下方網址重複，不另列。
        const metadata = [c.kind === 'wikipedia' ? null : c.authors, c.year].some(Boolean)
          ? [sourceKindLabel(c.kind), c.kind === 'wikipedia' ? null : c.authors, c.year].filter(Boolean).join(' · ') : '';
        const host = sourceHost(c.url);
        const link = /^https?:\/\//.test(c.url) ? c.url : undefined;
        const state = (readOnly && !compact || !c.eligible || c.error_code || c.state === 'failed' || readOnly && c.state === 'normalizing') && <span className={`source-state${c.error_code || c.state === 'failed' ? ' is-failed' : !c.eligible ? ' is-muted' : c.state === 'ready' ? ' is-ready' : ''}`}>{!c.eligible ? '目前無法加入' : c.error_code ? '取得失敗' : states[c.state] ?? '可加入'}</span>;
        return <article key={c.id} className={selected.includes(c.id) && !readOnly ? 'is-selected' : undefined}>
        <div className="source-candidate-heading">
          <label>{!readOnly && <input type="checkbox" aria-label={`選取 ${c.title}`} disabled={!c.eligible || disabled} checked={selected.includes(c.id)} onChange={e => onChange(e.target.checked ? [...selected, c.id] : selected.filter(x => x !== c.id))} />}<strong>{c.title}</strong></label>
          {(c.review || state) && <span className="source-badges">{c.review && <span className={`source-relevance is-${c.review.relevance}`}>{relevanceLabel(c.review.relevance)}</span>}{state}</span>}
        </div>
        {metadata && <p className="source-byline" title={metadata}>{metadata}</p>}
        {!readOnly && c.review?.phase === 'body' && <p className="source-purpose">{c.review.reason.split(/(?<=[。！？])/).slice(0, 2).join('')}</p>}
        {c.error_code && <p className="source-error">{sourceReasons[c.error_code] ?? '取得失敗，可重試。'}</p>}
        <div className="source-candidate-links">
          {link ? <a className="source-host-link" href={link} target="_blank" rel="noreferrer" title={c.url}>{host || '原始來源'} ↗</a> : <span className="source-host-link">{host || '原始來源'}</span>}
          {(c.review || showAcquisitionDetails) && <button type="button" className="text-button source-detail-open" aria-haspopup="dialog" onClick={() => setDetail(c)}>詳細資訊<Icon name="chevron-right" size={14} /></button>}
        </div>
      </article>; })}
      {!visible.length && available.length > 0 && <p className="source-picker-empty">{onlySelected ? '目前沒有符合篩選的已選來源。' : '沒有符合篩選的來源。'}<button className="text-button" onClick={() => { setQuery(''); setKind('all'); setOnlySelected(false); }}>清除篩選</button></p>}
    </div>
    {pageCount > 1 && <nav className="source-pagination" aria-label="來源分頁">
      <p role="status">第 {currentPage} / {pageCount} 頁 · {visible.length ? `${(currentPage - 1) * PAGE_SIZE + 1}–${Math.min(currentPage * PAGE_SIZE, visible.length)}` : '0'} / 已載入 {visible.length} 份</p>
      <div className="source-page-buttons">
        <button type="button" className="text-button" disabled={currentPage === 1} onClick={() => { setPage(currentPage - 1); }}>上一頁</button>
        {pageNumbers.map((number, i) => <span key={number} className="source-page-number">
          {i > 0 && number - pageNumbers[i - 1] > 1 && <span aria-hidden="true">…</span>}
          <button type="button" className="text-button" aria-label={`第 ${number} 頁`} aria-current={number === currentPage ? 'page' : undefined} onClick={() => { setPage(number); }}>{number}</button>
        </span>)}
        <button type="button" className="text-button" disabled={currentPage === pageCount} onClick={() => setPage(currentPage + 1)}>下一頁</button>
      </div>
    </nav>}
    {detail && <SourceDetail candidate={detail} showAcquisitionDetails={showAcquisitionDetails} onClose={() => setDetail(null)} />}
  </div>;
}
