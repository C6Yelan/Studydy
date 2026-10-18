import { useEffect, useRef, useState } from 'react';

export type Candidate = {
  id: string; kind: string; title: string; authors: string; year: number | null;
  url: string; doi: string | null; license: string; license_url: string | null;
  discovered_by?: string[]; version: string; eligible: boolean; reason: string; state: string; error_code?: string;
  duplicate_of?: string;
  screening?: { decision: string; reason: string; basis: string };
  source_type_review?: { type: string };
  acquisition?: { license_text?: string; acquired_at: string };
  review?: { phase: string; relevance: string; reason: string; topics: number[]; scope?: string };
  access?: { status: string; method?: string; fulltext_unavailable?: boolean; attempts: { url: string; outcome: string; error_code?: string; http_status?: number | null; method?: string }[] };
};
export const sourceReasons: Record<string, string> = {
  RESEARCH_SOURCE_TYPE_UNCONFIRMED: '尚未核對為論文或 Wikipedia，請接續研究。',
  RESEARCH_SCOPE_REQUIRED: '請先確認要查找的觀念與細節，再開始搜尋。已保存的來源仍可查看。',
  RESEARCH_SNAPSHOT_UNAVAILABLE: '無法讀取這次研究保留的原文，請重新研究。',
  RESEARCH_PARTIAL_SEARCH: '已保留搜尋結果；部分主題仍缺少正文依據，或有來源未完成。請查看涵蓋情況與各來源說明。',
  RESEARCH_BODY_UNAVAILABLE: '入口只有標題或導覽，尚未取得正文。',
  RESEARCH_AUTH_REQUIRED: '此入口要求登入，未取得正文。',
  RESEARCH_SOURCE_BLOCKED: '此入口回應 403，未取得正文。',
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

const PAGE_SIZE = 10;

function unavailableStatus(candidate: Candidate) {
  if (candidate.access?.fulltext_unavailable || candidate.access?.status === 'unavailable') return '未取得全文';
  if (candidate.source_type_review?.type === 'other') return '不符合來源範圍';
  if (candidate.source_type_review?.type === 'uncertain') return '來源類型未確認';
  if (candidate.screening?.basis === 'importability') return '匯入條件未確認';
  if (candidate.screening?.decision === 'exclude') return '未採用';
  if (candidate.review?.phase !== 'body') return '尚未核對正文';
  return '目前無法加入';
}

export function SourceCandidates({ candidates, selected, disabled, onChange, readOnly = false, search, inlineSearch = false, showAcquisitionDetails = true, compact = false }: {
  candidates: Candidate[]; selected: string[]; disabled: boolean; compact?: boolean;
  onChange: (ids: string[]) => void; readOnly?: boolean; inlineSearch?: boolean; showAcquisitionDetails?: boolean; search?: { value: string; onChange: (value: string) => void };
}) {
  const [localQuery, setLocalQuery] = useState('');
  const query = search?.value ?? localQuery;
  const setQuery = search?.onChange ?? setLocalQuery;
  const [kind, setKind] = useState('all');
  const [onlySelected, setOnlySelected] = useState(false);
  const [page, setPage] = useState(1);
  const list = useRef<HTMLDivElement>(null);
  const selectAll = useRef<HTMLInputElement>(null);
  const eligible = candidates.filter(candidate => candidate.eligible);
  const allSelected = eligible.length > 0 && eligible.every(candidate => selected.includes(candidate.id));
  const partiallySelected = !allSelected && eligible.some(candidate => selected.includes(candidate.id));
  useEffect(() => { if (selectAll.current) selectAll.current.indeterminate = partiallySelected; }, [partiallySelected, readOnly]);
  const available = readOnly ? candidates : candidates.filter(c => !c.duplicate_of && c.eligible);
  const otherResults = candidates.filter(c => !c.duplicate_of && !c.eligible);
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
          <label className="source-toolbar-check"><input type="checkbox" checked={onlySelected} onChange={event => setOnlySelected(event.target.checked)}/>只看已選</label>
          <label className="source-toolbar-check" title="全選或取消全部可用來源，包含篩選外的來源"><input ref={selectAll} type="checkbox" aria-label="全選可用來源" checked={allSelected} disabled={disabled || !eligible.length} onChange={event => onChange(event.target.checked ? eligible.map(candidate => candidate.id) : [])}/>全選</label>
        </>}
      </div>
      {!readOnly && !compact && <div className="research-selection">
        <div><button className="text-button" disabled={disabled || !eligible.length} onClick={() => onChange(eligible.map(c => c.id))}>全選可用來源</button><span>／</span><button className="text-button" disabled={disabled} onClick={() => onChange([])}>取消全選</button></div>
        <label><input type="checkbox" checked={onlySelected} onChange={e => setOnlySelected(e.target.checked)} />只看已選</label>
        <span>已選 {selected.length} 份</span>
      </div>}
    </>}
    <div ref={list} className="research-candidates" tabIndex={0} aria-label={readOnly ? '已選來源' : '來源搜尋結果'}>
      {pageItems.map(c => {
        const metadata = [c.kind === 'wikipedia' ? 'Wikipedia' : c.kind === 'official' ? '官方教學' : c.kind === 'web' ? '網頁文章' : '學術論文', c.authors, c.year].filter(Boolean).join(' · ');
        return <article key={c.id} className={selected.includes(c.id) ? 'is-selected' : undefined}>
        <div className="source-candidate-heading">
          <label>{!readOnly && <input type="checkbox" aria-label={`選取 ${c.title}`} disabled={!c.eligible || disabled} checked={selected.includes(c.id)} onChange={e => onChange(e.target.checked ? [...selected, c.id] : selected.filter(x => x !== c.id))} />}<strong>{c.title}</strong></label>
          {(!compact || !c.eligible || c.error_code || c.state === 'failed' || readOnly && c.state === 'normalizing') && <span className={`source-state${c.error_code ? ' is-failed' : ''}`}>{!c.eligible ? unavailableStatus(c) : c.error_code ? '取得失敗' : states[c.state] ?? '可加入'}</span>}
        </div>
        <p className="source-byline" title={metadata}>{metadata}</p>
        {c.review?.phase === 'body' && <p className="source-purpose">{c.review.reason.split(/(?<=[。！？])/).slice(0, 2).join('')}</p>}
        {c.review && <details className="source-review"><summary>{c.review.relevance === 'direct' ? '直接相關' : c.review.relevance === 'related' ? '背景參考' : '不相關'} · 查看閱讀判斷</summary><p>{c.review.scope === 'sections' ? '已核對相關區段。' : c.review.phase === 'body' ? '已核對全文。' : '摘要初篩。'}{c.review.reason}</p></details>}
        {!c.eligible && c.review && <p className="source-reference-note">{c.reason}</p>}
        {c.error_code && <p className="source-error">{sourceReasons[c.error_code] ?? '取得失敗，可重試。'}</p>}
        <div className="source-candidate-links">
          <a href={/^https?:\/\//.test(c.url) ? c.url : undefined} target="_blank" rel="noreferrer">{compact ? '原始來源 ↗' : '查看原始來源 ↗'}</a>
          {!!c.access?.attempts.length && <details><summary>查看存取紀錄</summary><ul>{c.access.attempts.map((attempt, i) => <li key={i}><a href={/^https?:\/\//.test(attempt.url) ? attempt.url : undefined} target="_blank" rel="noreferrer">{attempt.url}</a><span>{attempt.outcome === 'read' ? `已取得（${attempt.method ?? c.access?.method ?? '正文'}）` : `${attempt.http_status ?? ''} ${sourceReasons[attempt.error_code ?? ''] ?? '取得未完成'}`}</span></li>)}</ul></details>}
          {showAcquisitionDetails && <details><summary>授權與取得資訊</summary><div>
            {c.discovered_by?.length ? <p>搜尋來源：{c.discovered_by.join('、')}（同篇文件合併顯示）</p> : null}
            <p>{c.license || '尚未確認授權'} · {c.reason}</p>
            {c.license_url && <a href={c.license_url} target="_blank" rel="noreferrer">查看授權說明 ↗</a>}
            <p>版本：{c.version}</p>
            {c.acquisition && <p>取得時間：{new Date(c.acquisition.acquired_at).toLocaleString('zh-TW')}</p>}
            {c.acquisition?.license_text && <pre>{c.acquisition.license_text}</pre>}
          </div></details>}
        </div>
      </article>; })}
      {!readOnly && !eligible.length && candidates.length > 0 && <p className="source-picker-empty">目前尚無已核對可加入的來源，可查看其他搜尋結果的原因或接續研究。</p>}
      {!visible.length && available.length > 0 && <p className="source-picker-empty">{onlySelected ? '目前沒有符合篩選的已選來源。' : '沒有符合篩選的來源。'}<button className="text-button" onClick={() => { setQuery(''); setKind('all'); setOnlySelected(false); }}>清除篩選</button></p>}
    </div>
    {visible.length > 0 && <nav className="source-pagination" aria-label="來源分頁">
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
    {!readOnly && otherResults.length > 0 && <details className="source-review source-other-results">
      <summary>其他搜尋結果 · {otherResults.length} 份</summary>
      <p>這些項目尚未完成核對，或目前無法加入教材；不代表都與需求無關。上方只列本輪已核對可選的來源。</p>
      <ul>{otherResults.map(c => <li key={c.id}>
        <strong>{c.title}</strong><p>{unavailableStatus(c)} · {c.screening?.basis === 'importability' || c.access?.fulltext_unavailable || c.source_type_review ? c.reason : c.screening?.reason ?? '本輪尚未閱讀這份來源，未判定其內容是否可用。'}</p>
        <a href={/^https?:\/\//.test(c.url) ? c.url : undefined} target="_blank" rel="noreferrer">原始來源 ↗</a>
      </li>)}</ul>
    </details>}
  </div>;
}
