import { useState } from 'react';

export type Candidate = {
  id: string; kind: string; title: string; authors: string; year: number | null;
  url: string; doi: string | null; license: string; license_url: string | null;
  version: string; eligible: boolean; reason: string; state: string; error_code?: string;
  acquisition?: { license_text?: string; acquired_at: string };
};
export const sourceReasons: Record<string, string> = {
  RESEARCH_SEARCH_FAILED: '搜尋暫時失敗，請稍後重試。',
  RESEARCH_DOWNLOAD_FAILED: '來源目前無法下載。',
  RESEARCH_LICENSE_UNCONFIRMED: '無法核對全文授權。',
  RESEARCH_IDENTITY_UNCONFIRMED: '下載文件與搜尋紀錄的身分尚無法核對。',
  RESEARCH_FORMAT_UNSUPPORTED: '目前無法處理此文件格式。',
  DUPLICATE_SOURCE: '這份文件已存在於教材。',
  RESEARCH_ITEMS_FAILED: '部分來源未完成。可重試，或只勾選已準備好的來源繼續。',
  RESEARCH_OFFICIAL_UNAVAILABLE: '部分官方文件搜尋暫時失敗；其他結果已保留。',
};
const states: Record<string, string> = { normalizing: '轉換中', ready: '已取得', failed: '取得失敗' };

export function SourceCandidates({ candidates, selected, disabled, onChange, readOnly = false, search }: {
  candidates: Candidate[]; selected: string[]; disabled: boolean;
  onChange: (ids: string[]) => void; readOnly?: boolean; search?: { value: string; onChange: (value: string) => void };
}) {
  const [localQuery, setLocalQuery] = useState('');
  const query = search?.value ?? localQuery;
  const setQuery = search?.onChange ?? setLocalQuery;
  const [kind, setKind] = useState('all');
  const [onlySelected, setOnlySelected] = useState(false);
  const visible = candidates.filter(c =>
    (kind === 'all' || c.kind === kind) && (!onlySelected || selected.includes(c.id)) &&
    `${c.title} ${c.authors}`.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()));
  return <div className={`source-picker${readOnly ? ' is-readonly' : ''}`}>
    {!readOnly && <>
      <div className={`source-picker-filters${search ? " is-external-search" : ""}`}>
        {!search && <input type="search" aria-label="搜尋來源" placeholder="搜尋來源名稱或作者…" value={query} onChange={e => setQuery(e.target.value)} />}
        <select aria-label="來源類型" value={kind} onChange={e => setKind(e.target.value)}>
          <option value="all">全部來源</option><option value="official">官方教學</option><option value="paper">學術論文</option>
        </select>
      </div>
      <div className="research-selection">
        <div><button className="text-button" disabled={disabled} onClick={() => onChange(candidates.filter(c => c.eligible).map(c => c.id))}>全選可用來源</button><span>／</span><button className="text-button" disabled={disabled} onClick={() => onChange([])}>取消全選</button></div>
        <label><input type="checkbox" checked={onlySelected} onChange={e => setOnlySelected(e.target.checked)} />只看已選</label>
        <span>已選 {selected.length} 份</span>
      </div>
    </>}
    <div className="research-candidates" tabIndex={0} aria-label={readOnly ? '已選來源' : '來源搜尋結果'}>
      {visible.map(c => <article key={c.id} className={selected.includes(c.id) ? 'is-selected' : undefined}>
        <div className="source-candidate-heading">
          <label>{!readOnly && <input type="checkbox" aria-label={`選取 ${c.title}`} disabled={!c.eligible || disabled} checked={selected.includes(c.id)} onChange={e => onChange(e.target.checked ? [...selected, c.id] : selected.filter(x => x !== c.id))} />}<strong>{c.title}</strong></label>
          <span className={`source-state${c.error_code ? ' is-failed' : ''}`}>{states[c.state] ?? (c.eligible ? '可加入' : '僅供查看')}</span>
        </div>
        <p className="source-byline" title={c.authors}>{c.kind === 'official' ? '官方教學' : '學術論文'} · {c.authors}{c.year ? ` · ${c.year}` : ''}</p>
        {c.error_code && <p className="source-error">{sourceReasons[c.error_code] ?? '取得失敗，可重試。'}</p>}
        <div className="source-candidate-links">
          <a href={c.url.startsWith('https://') ? c.url : undefined} target="_blank" rel="noreferrer">查看原始來源 ↗</a>
          <details><summary>授權與取得資訊</summary><div>
            <p>{c.license || '尚未確認授權'} · {c.reason}</p>
            {c.license_url && <a href={c.license_url} target="_blank" rel="noreferrer">查看授權說明 ↗</a>}
            <p>版本：{c.version}</p>
            {c.acquisition && <p>取得時間：{new Date(c.acquisition.acquired_at).toLocaleString('zh-TW')}</p>}
            {c.acquisition?.license_text && <pre>{c.acquisition.license_text}</pre>}
          </div></details>
        </div>
      </article>)}
      {!visible.length && candidates.length > 0 && <p className="source-picker-empty">{onlySelected ? '目前沒有符合篩選的已選來源。' : '沒有符合篩選的來源。'}<button className="text-button" onClick={() => { setQuery(''); setKind('all'); setOnlySelected(false); }}>清除篩選</button></p>}
    </div>
  </div>;
}
