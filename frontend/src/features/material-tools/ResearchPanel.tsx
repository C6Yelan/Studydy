import { useEffect, useRef, useState, type ReactNode } from 'react';
import { errorMessage, type StudydyApiClient } from '../../api/client';
import { writeRoute } from '../../app/routes';
import { Icon } from '../../ui/Icon';
import { LearningSteps } from './LearningSteps';
import { SourceCandidates, sourceReasons as reasons, type Candidate } from './SourceCandidates';

export type ResearchDraft = { query: string; researchId: string | null; selected: string[]; editing: boolean; sourceQuery: string };

type ResearchSummary = { research_id: string; query: string; selection: string[]; status: string; run_id: string | null };

export type Research = {
  research_id: string; query: string; mode: string; status: string; candidates: Candidate[];
  selection: string[]; cursor: string | null; error_code: string | null; run_id: string | null;
  is_current_revision?: boolean; run?: { status: string; error_code: string | null };
};
const active = new Set(['searching', 'acquiring', 'normalizing']);
const labels: Record<string, string> = {
  searching: '正在搜尋來源', selecting: '選擇補充來源', acquiring: '正在取得選取來源',
  normalizing: '正在整理文件', ready: '來源已準備好', submitted: '已送入教材分析',
  failed: '部分來源尚未完成', cancelled: '已停止處理',
};

export function ResearchPanel({ api, materialId, draft, onDraftChange, onMaterialUpdated, contentNavigation }: {
  api: StudydyApiClient; materialId: string; draft?: ResearchDraft; onDraftChange?: (draft: ResearchDraft) => void; onMaterialUpdated: () => void; contentNavigation?: ReactNode;
}) {
  const [query, setQuery] = useState(draft?.query ?? '');
  const [sourceQuery, setSourceQuery] = useState(draft?.sourceQuery ?? '');
  const mode = 'review';
  const [list, setList] = useState<ResearchSummary[]>([]), [id, setId] = useState<string | null>(draft?.researchId ?? null);
  const [view, setView] = useState<Research | null>(null), [selected, setSelected] = useState<string[]>(draft?.selected ?? []);
  const [busy, setBusy] = useState(false), [error, setError] = useState('');
  const [confirmed, setConfirmed] = useState(false), [editing, setEditing] = useState(draft?.editing ?? false);
  useEffect(() => { onDraftChange?.({ query, researchId: id, selected, editing, sourceQuery }); }, [query, id, selected, editing, sourceQuery, onDraftChange]);
  const searchIntent = useRef<{ body: string; key: string } | null>(null);
  const alive = useRef(true), current = useRef<string | null>(null);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  const refresh = async () => {
    const r = await api.studyTools<{ researches: ResearchSummary[] }>(`/v1/materials/${materialId}/research`);
    if (alive.current) setList(r.researches);
  };
  useEffect(() => { void refresh().catch(e => { if (alive.current) setError(errorMessage(e)); }); }, [api, materialId]);
  useEffect(() => {
    current.current = id;
    setView(previous => previous?.research_id === id ? previous : null);
    if (!id) return;
    let stop = false; let timer: ReturnType<typeof setTimeout>;
    const read = async () => {
      try {
        const r = await api.studyTools<Research>(`/v1/research/${id}`);
        if (stop) return;
        setView(r);
        if (active.has(r.status) || (r.status === 'submitted' && r.run && ['pending', 'running'].includes(r.run.status))) timer = setTimeout(read, 2000);
      } catch (e) { if (!stop) setError(errorMessage(e)); }
    };
    void read(); return () => { stop = true; clearTimeout(timer); };
  }, [api, id, busy]);
  const perform = async (fn: () => Promise<unknown>) => {
    setBusy(true); setError('');
    try { await fn(); await refresh(); }
    catch (e) { if (alive.current) setError(errorMessage(e)); }
    finally { if (alive.current) setBusy(false); }
  };
  const action = (action: string, ids?: string[]) => perform(async () => {
    const r = await api.studyTools<Research>(`/v1/research/${id}/actions`, { action, ...(ids ? { selected: ids } : {}) });
    if (alive.current && current.current === r.research_id) { setView(r); setConfirmed(false); setEditing(false); }
  });
  const choose = (nextId: string) => {
    const r = list.find(x => x.research_id === nextId);
    setId(nextId || null); setSelected(r?.selection ?? []); setConfirmed(false); setEditing(false); setError(''); setSourceQuery(''); setQuery(r?.query??'');
  };
  const working = busy || !!view && active.has(view.status);
  const submitted = view?.status === 'submitted';
  const canConfirm = view?.status === 'ready' && !editing && view.selection.length === selected.length && view.selection.every(x => selected.includes(x));
  const showSelected = submitted || canConfirm || !!view && ['acquiring', 'normalizing'].includes(view.status);
  const notifiedRun = useRef<string | null>(null);
  const completed = !!view?.run && ['succeeded', 'partial'].includes(view.run.status);
  useEffect(() => {
    if (completed && view?.run_id && notifiedRun.current !== view.run_id) {
      notifiedRun.current = view.run_id; onMaterialUpdated();
    }
  }, [completed, view?.run_id, onMaterialUpdated]);
  const displayed = view?.candidates.filter(c => !showSelected || view.selection.includes(c.id)) ?? [];

  const searchMaterials = () => perform(async () => {
    const body = JSON.stringify({ query, mode });
    if (searchIntent.current?.body !== body) searchIntent.current = { body, key: crypto.randomUUID() };
    const r = await api.studyTools<Research>(`/v1/materials/${materialId}/research`, { query, mode }, searchIntent.current.key);
    searchIntent.current = null;
    if (alive.current) { setId(r.research_id); setView(r); setSelected([]); setConfirmed(false); setSourceQuery(''); }
  });
  return <section className="cards-page supplementary-page">
    {contentNavigation}
    <div className="research-panel" role="tabpanel" id="material-panel-research" aria-labelledby="material-tab-research">
    <header className="cards-page-header"><div><h1>補充學習</h1></div></header>
    <section className="surface research-compose" aria-labelledby="research-prompt"><h2 id="research-prompt">想補充什麼內容？</h2>
      <form onSubmit={event => { event.preventDefault(); if (query.trim() && !busy) void searchMaterials(); }}>
        <input type="search" aria-label="想多了解什麼？" placeholder="輸入想補充的內容…" maxLength={1000} value={query} onChange={event => setQuery(event.target.value)} />
        <button type="submit" className="primary-button" disabled={busy || !query.trim()}>{busy ? '正在建立搜尋…' : '搜尋補充資料'}<Icon name="chevron-right" size={16}/></button>
      </form>
    </section>
    {error && <p role="alert" className="form-error">{error}</p>}
    <div className="research-records" aria-label="補充搜尋紀錄">
    {list.map(record => <section className="surface research-record" key={record.research_id} aria-label={record.query}>
      <button type="button" aria-expanded={id === record.research_id} disabled={busy} onClick={() => choose(id === record.research_id ? '' : record.research_id)}><strong>{record.query}</strong><span>{labels[(id === record.research_id ? view?.status : null) ?? record.status]}</span></button>
      {id === record.research_id && (!view ? <p role="status">正在讀取搜尋紀錄…</p> : <div className="tool-stage">
      <LearningSteps labels={['搜尋資料', '選取來源', '加入教材']} current={submitted ? 3 : canConfirm ? 2 : view.status !== 'searching' ? 1 : 0} />
      <header className="tool-stage-heading"><div><h3>{view.query}</h3><p>{labels[view.status]}</p></div></header>
      {view.error_code && <p className="tool-notice">{reasons[view.error_code] ?? '處理未完成，已保存取得的結果。'}</p>}
      {view.is_current_revision === false && !submitted && <p className="tool-notice">教材已更新，這次搜尋不能直接加入舊版本。請以目前教材重新搜尋。</p>}
      {submitted && <div className={`tool-result${completed ? ' is-complete' : ''}`}>
        <span className="tool-result-icon"><Icon name={completed ? 'check' : 'process'} size={24} /></span>
        <div><h3>{completed ? '補充教材已建立' : view.run?.status === 'failed' ? '教材分析尚未完成' : view.run?.status === 'cancelled' ? '教材分析已取消' : '正在建立補充教材'}</h3>
          <p>{completed ? `已將 ${view.selection.length} 份來源加入教材，可查看更新後的知識地圖。` : '已選來源會保留。可到教材分析頁查看進度或接續處理。'}</p></div>
        {view.run_id && <button className="primary-button" onClick={() => { writeRoute({ name: 'material-run', materialId, runId: view.run_id! }); }}>查看教材分析<Icon name="chevron-right" size={16} /></button>}
      </div>}
      {view.status === 'searching' && <div className="tool-wait" role="status"><span className="loading-ring" /><p>正在尋找補充資料，搜尋結果會保存在這筆紀錄。</p></div>}
      {canConfirm && <div className="tool-selection-heading"><h4>確認加入的來源 · {selected.length} 份</h4><button className="text-button" disabled={busy} onClick={() => { setEditing(true); setConfirmed(false); }}>調整來源</button></div>}
      {submitted && <h4 className="tool-section-label">已加入的來源 · {view.selection.length} 份</h4>}
      {displayed.length > 0 && <SourceCandidates candidates={displayed} selected={selected} disabled={working || submitted} readOnly={showSelected} search={{value:sourceQuery,onChange:setSourceQuery}} inlineSearch onChange={ids => { setSelected(ids); setConfirmed(false); }} />}
      {!view.candidates.length && !active.has(view.status) && <p className="tool-empty">沒有找到來源。可以換個主題或英文關鍵字再搜尋。</p>}
      {canConfirm && <div className="research-confirm"><p>加入後會更新教材與知識地圖，<strong>無法撤回新增內容</strong>。</p><label><input type="checkbox" checked={confirmed} onChange={e => setConfirmed(e.target.checked)} />我了解加入後不可逆</label></div>}
      {!submitted && <footer className="tool-stage-footer">
        <div className="tool-footer-secondary">
          {view.status === 'selecting' && view.cursor && <button className="text-button" disabled={busy} onClick={() => void action('more')}>載入更多論文</button>}
          {active.has(view.status) && <button className="secondary-button" disabled={busy} onClick={() => void action('cancel')}>停止處理</button>}
          {['failed', 'cancelled'].includes(view.status) && <button className="secondary-button" disabled={busy} onClick={() => void action('retry')}>重試未完成部分</button>}
          {editing && view.status === 'ready' && <button className="text-button" disabled={busy} onClick={() => { setSelected(view.selection); setEditing(false); }}>取消調整</button>}
          {!active.has(view.status) && <span>已選 {selected.length} 份來源</span>}
        </div>
        {canConfirm ? <button className="primary-button" disabled={busy || !confirmed || view.is_current_revision === false} onClick={() => void perform(async () => {
          const r = await api.studyTools<Research>(`/v1/research/${id}/submit`, { confirmed: true });
          if (alive.current) setView(r);
        })}>確認加入教材<Icon name="chevron-right" size={16} /></button> : ['selecting', 'ready', 'failed'].includes(view.status) && <button className="primary-button" disabled={working || !selected.length} onClick={() => void action('acquire', selected)}>取得選取的 {selected.length} 份來源<Icon name="chevron-right" size={16} /></button>}
      </footer>}
    </div>)}
    </section>)}
    </div>
    </div>
  </section>;
}
