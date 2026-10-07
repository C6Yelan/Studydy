import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import { errorMessage, type StudydyApiClient } from '../../api/client';
import { routePath, writeRoute } from '../../app/routes';
import { Icon } from '../../ui/Icon';
import { ResearchManagement } from './ResearchManagement';
import { researchActive, researchTaskInfo, type Research, type ResearchSummary, type ResearchDraft } from './research';

export function ResearchPanel({ api, materialId, draft, onDraftChange, onMaterialUpdated, contentNavigation }: {
  api: StudydyApiClient; materialId: string; draft?: ResearchDraft; onDraftChange?: (draft: ResearchDraft) => void;
  onMaterialUpdated: () => void; contentNavigation?: ReactNode;
}) {
  const [query, setQuery] = useState(draft?.query ?? ''), [list, setList] = useState<ResearchSummary[] | null>(null);
  const [busy, setBusy] = useState(false), [searchError, setSearchError] = useState(''), [listError, setListError] = useState('');
  const tasks = useRef(draft?.tasks ?? {}), deleted = useRef(new Set<string>()), notified = useRef(new Set<string>());
  const alive = useRef(false), creating = useRef(false), listRead = useRef(0), heading = useRef<HTMLDivElement>(null);
  const intent = useRef<{body:string;key:string}|null>(null);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useEffect(() => { onDraftChange?.({ query, tasks: tasks.current }); }, [query, onDraftChange]);
  const refresh = useCallback(async () => {
    const request = ++listRead.current;
    const result = await api.studyTools<{ researches: ResearchSummary[] }>(`/v1/materials/${materialId}/research`);
    if (alive.current && request === listRead.current) { setList(result.researches.filter(item => !deleted.current.has(item.research_id))); setListError(''); }
  }, [api, materialId]);
  useEffect(() => { void refresh().catch(error => { if (alive.current) setListError(errorMessage(error)); }); }, [refresh]);
  const pending = list?.some(item => researchActive.has(item.status) || item.status === 'submitted' && !['succeeded', 'partial', 'failed', 'cancelled'].includes(item.run_status ?? ''));
  useEffect(() => {
    if (!pending) return;
    let reading = false;
    const timer = setInterval(async () => {
      if (reading) return;
      reading = true;
      try { await refresh(); } catch (error) { if (alive.current) setListError(errorMessage(error)); }
      finally { reading = false; }
    }, 2000);
    return () => clearInterval(timer);
  }, [pending, refresh]);
  useEffect(() => {
    const finished = list?.filter(item => item.run_id && ['succeeded','partial'].includes(item.run_status ?? '') && !notified.current.has(item.run_id));
    if (finished?.length) { for (const item of finished) notified.current.add(item.run_id!); onMaterialUpdated(); }
  }, [list, onMaterialUpdated]);
  const search = async () => {
    if (creating.current || !query.trim()) return;
    creating.current = true; setBusy(true); setSearchError('');
    const body = JSON.stringify({query,mode:'review'});
    if (intent.current?.body !== body) intent.current = {body,key:crypto.randomUUID()};
    try {
      const result = await api.studyTools<Research>(`/v1/materials/${materialId}/research`, {query,mode:'review'}, intent.current.key);
      if (alive.current) {
        intent.current = null;
        onDraftChange?.({query:'',tasks:tasks.current});
        writeRoute({name:'research',materialId,researchId:result.research_id});
      }
    } catch (error) { if (alive.current) setSearchError(errorMessage(error)); }
    finally { creating.current = false; if (alive.current) setBusy(false); }
  };
  return <section className="material-library is-collection supplementary-page research-library" aria-label="補充學習查詢">
    {contentNavigation}
    <div className="research-panel" role="tabpanel" id="material-panel-research" aria-labelledby="material-tab-research">
      <header className="library-header library-header-compact collection-toolbar"><div className="collection-summary" ref={heading} tabIndex={-1}><h1>補充學習</h1><p>{list === null ? '正在讀取查詢…' : `已保存 ${list.length} 筆查詢`}</p></div></header>
      <section className="surface research-compose" aria-labelledby="research-prompt"><h2 id="research-prompt">想補充什麼內容？</h2>
        <form onSubmit={event => { event.preventDefault(); void search(); }}><input type="search" aria-label="想多了解什麼？" placeholder="輸入想補充的內容…" maxLength={1000} value={query} onChange={event => setQuery(event.target.value)}/>
          <button className="primary-button" type="submit" disabled={busy || !query.trim()}>{busy ? '正在建立搜尋…' : '搜尋補充資料'}<Icon name="chevron-right" size={16}/></button></form>
        {searchError && <p className="form-error" role="alert">{searchError}</p>}
      </section>
      {listError && <p className="form-error" role="alert">{listError}<button className="text-button" type="button" onClick={() => void refresh().catch(error => setListError(errorMessage(error)))}>重新讀取查詢</button></p>}
      {list?.length === 0 && <p className="research-empty">還沒有補充查詢。建立查詢後，可從卡片開啟來源與處理進度。</p>}
      <div className="library-grid research-records" role="region" tabIndex={0} aria-label="補充搜尋紀錄">{list?.map(item => {
        const info = researchTaskInfo(item), route = {name:'research' as const,materialId,researchId:item.research_id};
        return <article className={`surface library-item research-record is-${info.tone}`} key={item.research_id} aria-label={item.query}>
          <span className={`research-status is-${info.tone}`}>{info.label}</span>
          <ResearchManagement api={api} item={item} onDeleted={() => {
            deleted.current.add(item.research_id); delete tasks.current[item.research_id];
            onDraftChange?.({query,tasks:tasks.current});
            setList(previous => previous?.filter(record => record.research_id !== item.research_id) ?? null); heading.current?.focus();
          }}/>
          <a className="research-card-link" href={routePath(route)} onClick={event => {
            if (!event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey) { event.preventDefault(); writeRoute(route); }
          }}><h2 title={item.query}>{item.query}</h2></a>
          <p className="library-metadata research-record-counts">{item.candidate_count} 個候選來源 · {item.run_status === 'succeeded' ? `已加入 ${item.selection.length} 份` : item.status === 'submitted' ? `已送出 ${item.selection.length} 份` : `已選 ${tasks.current[item.research_id]?.selected.length ?? item.selection.length} 份`}</p>
          <p className="research-record-description">{info.description}</p>
          {item.created_at && <time className="library-metadata" dateTime={item.created_at}>{new Date(item.created_at).toLocaleString('zh-TW',{year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'})} 建立</time>}
          <button className={info.tone === 'complete' ? 'secondary-button' : 'primary-button'} type="button" onClick={() => writeRoute(route)}>{info.cta}<Icon name="chevron-right" size={16}/></button>
        </article>;
      })}</div>
    </div>
  </section>;
}
