import { useEffect, useRef, useState, type ReactNode } from 'react';
import { BackLink } from '../../ui/BackLink';
import { errorMessage, isNotFound, type StudydyApiClient } from '../../api/client';
import { NotFoundView } from '../../ui/NotFoundView';
import { writeRoute } from '../../app/routes';
import { Icon } from '../../ui/Icon';
import { StateView } from '../../ui/StateView';
import { ResearchProgress } from './ResearchProgress';
import { SourceCandidates, sourceReasons as reasons } from './SourceCandidates';
import { researchActive, researchTaskInfo, researchSummary, type Research, type ResearchDraft } from './research';

export function ResearchDetail({ api, materialId, researchId, draft, onDraftChange, onMaterialUpdated, contentNavigation }: {
  api: StudydyApiClient; materialId: string; researchId: string; draft?: ResearchDraft;
  onDraftChange?: (draft: ResearchDraft) => void; onMaterialUpdated: () => void; contentNavigation?: ReactNode;
}) {
  const saved = draft?.tasks[researchId];
  const [stageRequest, setStageRequest] = useState<{ status: string; stage: number } | null>(null);
  const [view, setView] = useState<Research | null>(null), [error, setError] = useState(''), [notFound, setNotFound] = useState(false);
  const [selected, setSelected] = useState(saved?.selected ?? []);
  const [guidance, setGuidance] = useState('');
  useEffect(() => setGuidance(''), [researchId]);
  const [editing, setEditing] = useState(saved?.editing ?? false), [sourceQuery, setSourceQuery] = useState(saved?.sourceQuery ?? '');
  const [confirmed, setConfirmed] = useState(false), [busy, setBusy] = useState(false), [reload, setReload] = useState(0);
  const alive = useRef(false), inFlight = useRef(false), initialized = useRef(!!saved), notified = useRef<string | null>(null);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useEffect(() => {
    if (initialized.current) onDraftChange?.({ query: draft?.query ?? '', tasks: { ...draft?.tasks, [researchId]: { selected, editing, sourceQuery } } });
  }, [selected, editing, sourceQuery, view, researchId, onDraftChange]);
  useEffect(() => {
    let stopped = false; let timer: ReturnType<typeof setTimeout>;
    setError('');
    const read = async () => {
      try {
        const result = await api.studyTools<Research>(`/v1/research/${researchId}`);
        if (stopped) return;
        if (result.research_id !== researchId || result.material_id !== materialId) { setNotFound(true); setView(null); return; }
        if (!initialized.current) { initialized.current = true; setSelected(result.selection); }
        setView(result);
        if (researchActive.has(result.status) || result.status === 'submitted' && (!result.run || ['pending', 'running'].includes(result.run.status))) timer = setTimeout(read, 2000);
      } catch (failure) { if (!stopped) { if (isNotFound(failure)) setNotFound(true); else setError(errorMessage(failure)); } }
    };
    void read(); return () => { stopped = true; clearTimeout(timer); };
  }, [api, materialId, researchId, reload]);
  useEffect(() => {
    if (view?.run_id && ['succeeded', 'partial'].includes(view.run?.status ?? '') && notified.current !== view.run_id) {
      notified.current = view.run_id; onMaterialUpdated();
    }
  }, [view, onMaterialUpdated]);
  const perform = async (operation: string, ids?: string[]) => {
    if (inFlight.current) return;
    inFlight.current = true; setBusy(true); setError('');
    try {
      const result = await api.studyTools<Research>(`/v1/research/${researchId}/${operation === 'submit' ? 'submit' : 'actions'}`,
        operation === 'submit' ? { confirmed: true } : { action: operation, ...(ids ? { selected: ids } : {}), ...(operation === 'more' && guidance.trim() ? {guidance: guidance.trim()} : {}) });
      if (alive.current) { setView(result); setStageRequest(null); setConfirmed(false); setEditing(false); setReload(value => value + 1); }
    } catch (failure) { if (alive.current) setError(errorMessage(failure)); }
    finally { inFlight.current = false; if (alive.current) setBusy(false); }
  };
  const back = () => writeRoute({ name: 'material-content', materialId, kind: 'research' });
  const replan = () => {
    if(!view)return;
    onDraftChange?.({query:view.query,tasks:{...draft?.tasks,[researchId]:{selected,editing,sourceQuery}}});back();
  };
  const working = busy || !!view && researchActive.has(view.status), submitted = view?.status === 'submitted';
  const completed = ['succeeded', 'partial'].includes(view?.run?.status ?? '');
  const publishedRevision = completed ? view?.run?.output_binding?.knowledge_structure_revision : undefined;
  const selectionMatches = !!view && view.selection.length === selected.length && view.selection.every(id => selected.includes(id));
  const canConfirm = view?.status === 'ready' && !editing && selectionMatches;
  const info = view ? researchTaskInfo(researchSummary(view)) : null;
  const stageNames = [view?.progress ? '背景研究' : '搜尋資料', '選取來源', '加入教材'];
  const currentStage = submitted || canConfirm ? 2 : view?.progress && view.status === 'searching' || !view?.candidates.length || (['failed', 'cancelled'].includes(view?.status ?? '') && !view?.selection.length) ? 0 : 1;
  const editable = !!view && ['selecting', 'ready', 'failed'].includes(view.status);
  const canNavigate = [editable, editable && !!view?.candidates.length, view?.status === 'ready' && selectionMatches];
  // 切換只檢視已可用的階段；後端狀態變更時回到實際工作階段。
  const stage = stageRequest && stageRequest.status === view?.status && canNavigate[stageRequest.stage] ? stageRequest.stage : currentStage;
  const navigate = (next: number) => {
    if (!view || busy || !canNavigate[next] || next === stage) return;
    setStageRequest({ status: view.status, stage: next }); setConfirmed(false);
    if (next === 1 && view.status === 'ready') setEditing(true);
    if (next === 2) setEditing(false);
  };
  const readOnly = stage !== 1 || !editable && view?.status !== 'searching';
  const onlyAcquired = stage === 2 || stage === 1 && !editable && !!view?.selection.length;
  const displayed = view?.candidates.filter(candidate => (!onlyAcquired || view.selection.includes(candidate.id))) ?? [];
  const acquiredCount = view?.candidates.filter(candidate => view.selection.includes(candidate.id) && candidate.state === 'ready').length ?? 0;
  const selectedCount = submitted || ['acquiring', 'normalizing'].includes(view?.status ?? '') ? view?.selection.length ?? 0 : selected.length;
  const stepDone = [!!view && view.status !== 'searching' && (['selecting', 'ready', 'submitted'].includes(view.status) && view.candidates.length > 0 || view.selection.length > 0), !!submitted || !!canConfirm, !!completed];
  if (notFound) return <section className="cards-page supplementary-page research-detail">{contentNavigation}
    <NotFoundView title="找不到這筆補充學習" description="這筆查詢可能已被刪除，或不屬於這份教材。" back={{ label: '回到補充學習', route: { name: 'material-content', materialId, kind: 'research' } }} /></section>;
  return <section className="cards-page supplementary-page research-detail">
    {contentNavigation}
    <div className="research-panel" role="tabpanel" id="material-panel-research" aria-labelledby="material-tab-research">
      <header className="cards-page-header research-detail-header"><BackLink label="返回補充學習" route={{ name: 'material-content', materialId, kind: 'research' }}/><div><details className="research-request"><summary><h1>{view?.query ?? '查詢詳情'}</h1></summary><p>{view?.query}</p></details></div></header>
      {!view ? <StateView title={error ? '無法讀取查詢' : '正在讀取查詢'} description={error || '正在載入來源與處理進度。'} tone={error ? 'failure' : 'loading'} live={!error} skeleton="page"
        action={error ? <button type="button" className="secondary-button" onClick={() => setReload(value => value + 1)}>重新讀取</button> : undefined}/> : <>
        {error && <p className="form-error" role="alert">{error}</p>}
        <div className="research-workspace">
          <nav className="research-workflow" aria-label="補充學習流程"><ol>{stageNames.map((name, index) => <li key={name} className={`${stepDone[index] ? 'is-complete' : ''} ${currentStage === index ? 'is-current' : ''} ${stage === index ? 'is-viewing' : ''}`}>
            <button type="button" className="research-workflow-step" disabled={busy || !canNavigate[index]} aria-current={currentStage === index ? 'step' : undefined} aria-pressed={stage === index} aria-controls="research-stage-content" onClick={() => navigate(index)}>
              <span className="research-workflow-number" aria-hidden="true">{stepDone[index] ? <Icon name="check" size={14}/> : index + 1}</span>
              <strong>{name}</strong>
            </button>
          </li>)}</ol>{submitted && <p>已送入教材分析，來源不可再修改。</p>}</nav>
          <section id="research-stage-content" className="surface research-detail-body tool-stage" aria-label="查詢來源與進度">
      {view.error_code && <p className="tool-notice">{reasons[view.error_code] ?? '處理未完成，已保存取得的結果。'}</p>}
      {view.approved_scope && <details><summary>本次補充範圍</summary><ul>{view.approved_scope.topics.map((topic,i)=><li key={i}>{topic}</li>)}</ul></details>}
      {view.is_current_revision === false && !submitted && <p className="tool-notice">教材已更新，這次搜尋不能直接加入舊版本。請以目前教材重新搜尋。</p>}
      {stage === 2 && submitted && <div className={`tool-result${completed ? ' is-complete' : ''}`}>
        <span className="tool-result-icon"><Icon name={completed ? 'check' : 'process'} size={24} /></span>
        <div><h3>{view.run?.status === 'partial' ? '補充教材可查看' : completed ? '補充教材已建立' : view.run?.status === 'failed' ? '教材分析尚未完成' : view.run?.status === 'cancelled' ? '教材分析已取消' : '正在建立補充教材'}</h3>
          <p>{view.run?.status === 'partial' ? '已產生補充內容，可查看知識地圖與各項內容的原文來源。' : completed ? `已將 ${acquiredCount} 份來源加入教材，可查看更新後的知識地圖。` : '已選來源會保留。可到教材分析頁查看進度或接續處理。'}</p></div>
        {view.run_id && <button className="primary-button" onClick={() => { writeRoute(publishedRevision
          ? { name: 'knowledge-map', materialId, runId: view.run_id!, structureRevision: publishedRevision }
          : { name: 'material-run', materialId, runId: view.run_id! }); }}>{publishedRevision ? '查看補充教材' : '查看教材分析'}<Icon name="chevron-right" size={16} /></button>}
        {['succeeded','partial'].includes(view.run?.status ?? '') && view.candidates.some(c => view.selection.includes(c.id) && c.state === 'failed') && <button className="secondary-button" disabled={busy} onClick={() => void perform('retry')}>重試未完成來源</button>}
      </div>}
      {view.progress && stage !== 2 && <ResearchProgress progress={view.progress} running={view.status === 'searching'} />}
      {!view.progress && stage === 0 && view.status === 'searching' && <div className="tool-wait" role="status"><span className="loading-ring" /><p>正在尋找補充資料，搜尋結果會保存在這筆紀錄。</p></div>}
      {stage === 2 && canConfirm && <div className="tool-selection-heading"><h4>確認加入的來源 · {acquiredCount} 份</h4><button className="text-button" disabled={busy} onClick={() => navigate(1)}>調整來源</button></div>}
      {stage === 2 && submitted && <h4 className="tool-section-label">{completed ? '已加入的來源' : '已送出的來源'} · {acquiredCount} 份</h4>}
      {(!view.progress || view.status !== 'searching') && displayed.length > 0 && <SourceCandidates compact showAcquisitionDetails={false} candidates={displayed} selected={selected} disabled={working || !editable || stage !== 1} readOnly={readOnly} search={{value:sourceQuery,onChange:setSourceQuery}} inlineSearch onChange={ids => { setSelected(ids); setConfirmed(false); }} />}
      {view.progress && view.status === 'selecting' && !displayed.length && <p className="tool-empty">這次尚未整理出切題的正文來源，可查看未完成原因或依缺口繼續研究。</p>}
      {!view.candidates.length && !researchActive.has(view.status) && <p className="tool-empty">尚未取得可用來源。可查看原因、依缺口補查，或重新確認需求。</p>}
      {stage === 2 && canConfirm && <div className="research-confirm">{acquiredCount < view.selection.length && <p>部分來源處理未完成，本次先加入已準備好的 {acquiredCount} 份；未完成紀錄會保留。</p>}<p>這些來源目前尚未納入知識地圖；確認後會開始分析。加入後會更新教材與知識地圖，<strong>無法撤回新增內容</strong>。</p><label><input type="checkbox" checked={confirmed} onChange={e => setConfirmed(e.target.checked)} />我了解加入後不可逆</label></div>}
      {!submitted && <footer className="tool-stage-footer">
        <div className="tool-footer-secondary">
      {view.status === 'selecting' && view.approved_scope && <details><summary>調整補查方向</summary><label>補查方向（選填）<textarea rows={2} maxLength={1000} value={guidance} onChange={e => setGuidance(e.target.value)} placeholder="說明想補足的機制、例子或來源類型" disabled={busy}/></label></details>}
          {researchActive.has(view.status) && <button className="secondary-button" disabled={busy} onClick={() => void perform('cancel')}>停止處理</button>}
          {view.status === 'selecting' && <button className="text-button" disabled={busy} onClick={() => {
            if(view.approved_scope)void perform('more');
            else replan();
          }}>{view.approved_scope ? '依缺口繼續研究' : '重新確認搜尋範圍'}</button>}
          {['failed', 'cancelled'].includes(view.status) && <button className="secondary-button" disabled={busy} onClick={() => {
            if(view.approved_scope||view.selection.length)void perform('retry');else replan();
          }}>{view.approved_scope||view.selection.length?'重試未完成部分':'重新確認搜尋範圍'}</button>}
          {stage === 1 && editing && view.status === 'ready' && <button className="text-button" disabled={busy} onClick={() => { setSelected(view.selection); setEditing(false); setStageRequest(null); }}>取消調整</button>}
          {!researchActive.has(view.status) && <span>已選 {selected.length} 份來源</span>}
        </div>
        {stage === 0 ? canNavigate[1] && <button className="primary-button" disabled={busy} onClick={() => navigate(1)}>前往選取來源<Icon name="chevron-right" size={16}/></button>
          : stage === 2 && canConfirm ? <button className="primary-button" disabled={busy || !confirmed || view.is_current_revision === false} onClick={() => void perform('submit')}>確認加入教材<Icon name="chevron-right" size={16}/></button>
          : stage === 1 && view.status === 'ready' && selectionMatches ? <button className="primary-button" disabled={busy} onClick={() => navigate(2)}>前往加入教材<Icon name="chevron-right" size={16}/></button>
          : stage === 1 && editable && <button className="primary-button" disabled={working || !selected.length} onClick={() => void perform('acquire', selected)}>取得選取的 {selected.length} 份來源<Icon name="chevron-right" size={16}/></button>}

      </footer>}

        {!view.candidates.length && view.status === 'selecting' && <button className="secondary-button" type="button" onClick={() => {
          onDraftChange?.({ query: view.query, tasks: { ...draft?.tasks, [researchId]: { selected, editing, sourceQuery } } }); back();
        }}>調整搜尋</button>}
          </section>
        </div>
      </>}
    </div>
  </section>;
}
