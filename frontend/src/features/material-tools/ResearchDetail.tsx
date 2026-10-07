import { useEffect, useRef, useState, type ReactNode } from 'react';
import { errorMessage, type StudydyApiClient } from '../../api/client';
import { writeRoute } from '../../app/routes';
import { Icon } from '../../ui/Icon';
import { StateView } from '../../ui/StateView';
import { SourceCandidates, sourceReasons as reasons } from './SourceCandidates';
import { researchActive, researchTaskInfo, researchSummary, type Research, type ResearchDraft } from './research';

export function ResearchDetail({ api, materialId, researchId, draft, onDraftChange, onMaterialUpdated, contentNavigation }: {
  api: StudydyApiClient; materialId: string; researchId: string; draft?: ResearchDraft;
  onDraftChange?: (draft: ResearchDraft) => void; onMaterialUpdated: () => void; contentNavigation?: ReactNode;
}) {
  const saved = draft?.tasks[researchId];
  const [stageRequest, setStageRequest] = useState<{ status: string; stage: number } | null>(null);
  const [view, setView] = useState<Research | null>(null), [error, setError] = useState('');
  const [selected, setSelected] = useState(saved?.selected ?? []);
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
        if (result.research_id !== researchId || result.material_id !== materialId) { setError('這筆查詢不屬於此教材。'); setView(null); return; }
        if (!initialized.current) { initialized.current = true; setSelected(result.selection); }
        setView(result);
        if (researchActive.has(result.status) || result.status === 'submitted' && (!result.run || ['pending', 'running'].includes(result.run.status))) timer = setTimeout(read, 2000);
      } catch (failure) { if (!stopped) setError(errorMessage(failure)); }
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
        operation === 'submit' ? { confirmed: true } : { action: operation, ...(ids ? { selected: ids } : {}) });
      if (alive.current) { setView(result); setStageRequest(null); setConfirmed(false); setEditing(false); setReload(value => value + 1); }
    } catch (failure) { if (alive.current) setError(errorMessage(failure)); }
    finally { inFlight.current = false; if (alive.current) setBusy(false); }
  };
  const back = () => writeRoute({ name: 'material-content', materialId, kind: 'research' });
  const working = busy || !!view && researchActive.has(view.status), submitted = view?.status === 'submitted';
  const completed = view?.run?.status === 'succeeded';
  const selectionMatches = !!view && view.selection.length === selected.length && view.selection.every(id => selected.includes(id));
  const canConfirm = view?.status === 'ready' && !editing && selectionMatches;
  const info = view ? researchTaskInfo(researchSummary(view)) : null;
  const stageNames = ['搜尋資料', '選取來源', '加入教材'];
  const currentStage = submitted || canConfirm ? 2 : view?.status === 'searching' || !view?.candidates.length || (['failed', 'cancelled'].includes(view?.status ?? '') && !view?.selection.length) ? 0 : 1;
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
  const readOnly = stage !== 1 || !editable || working;
  const onlyAcquired = stage === 2 || stage === 1 && !editable && !!view?.selection.length;
  const displayed = view?.candidates.filter(candidate => !onlyAcquired || view.selection.includes(candidate.id)) ?? [];
  const acquiredCount = view?.candidates.filter(candidate => view.selection.includes(candidate.id) && candidate.state === 'ready').length ?? 0;
  const selectedCount = submitted || ['acquiring', 'normalizing'].includes(view?.status ?? '') ? view?.selection.length ?? 0 : selected.length;
  const stepDone = [!!view && view.status !== 'searching' && (['selecting', 'ready', 'submitted'].includes(view.status) && view.candidates.length > 0 || view.selection.length > 0), !!submitted || !!canConfirm, !!completed];
  const summaries = [
    view?.status === 'searching' ? `已找到 ${view.candidates.length} 個候選，搜尋中…` : view?.candidates.length ? `${view.candidates.length} 個候選來源` : info?.label ?? '尚未搜尋',
    ['acquiring', 'normalizing'].includes(view?.status ?? '') ? `${info?.label} · 已選 ${selectedCount} 份 · 已取得 ${acquiredCount} 份` : selectedCount ? `已選 ${selectedCount} 份${stage === 1 && info?.tone === 'attention' ? ' · 需要處理' : ''}` : '尚未選取來源',
    submitted ? `${info?.label} · ${completed ? '已加入' : '已送出'} ${view?.selection.length} 份` : view?.status === 'ready' && selectionMatches ? `${view.selection.length} 份來源已準備好，待確認` : '選取並取得來源後才能加入',
  ];
  return <section className="cards-page supplementary-page research-detail">
    {contentNavigation}
    <div className="research-panel" role="tabpanel" id="material-panel-research" aria-labelledby="material-tab-research">
      <header className="cards-page-header research-detail-header"><button className="text-button" type="button" onClick={back}><Icon name="arrow-left" size={17}/>返回補充學習</button><div><h1>{view?.query ?? '查詢詳情'}</h1></div></header>
      {!view ? <StateView title={error ? '無法讀取查詢' : '正在讀取查詢'} description={error || '正在載入來源與處理進度。'} tone={error ? 'failure' : 'loading'} live={!error}
        action={error ? <button type="button" className="secondary-button" onClick={() => setReload(value => value + 1)}>重新讀取</button> : undefined}/> : <>
        {error && <p className="form-error" role="alert">{error}</p>}
        <div className="research-workspace">
          <nav className="research-workflow" aria-label="補充學習流程"><ol>{stageNames.map((name, index) => <li key={name} className={`${stepDone[index] ? 'is-complete' : ''} ${currentStage === index ? 'is-current' : ''} ${stage === index ? 'is-viewing' : ''}`}>
            <button type="button" className="research-workflow-step" disabled={busy || !canNavigate[index]} aria-current={currentStage === index ? 'step' : undefined} aria-pressed={stage === index} aria-controls="research-stage-content" onClick={() => navigate(index)}>
              <span className="research-workflow-number" aria-hidden="true">{stepDone[index] ? <Icon name="check" size={14}/> : index + 1}</span>
              <span><strong>{name}</strong><span className="research-workflow-state">{stepDone[index] ? '已完成' : currentStage === index ? '目前階段' : index === 1 && selectedCount ? '待續' : '未開始'}</span><small>{summaries[index]}</small></span>
            </button>
          </li>)}</ol>{submitted && <p>已送入教材分析，來源不可再修改。</p>}</nav>
          <section id="research-stage-content" className="surface research-detail-body tool-stage" aria-label="查詢來源與進度">
            <div className="research-stage-indicator" role="status" aria-label="目前階段"><span>{stage + 1} / 3</span><strong>{stageNames[stage]}</strong><small>{info!.label} · {view.candidates.length} 個候選</small></div>
            <header className="research-stage-heading"><div className="research-stage-title"><h2>{stageNames[stage]}</h2><span className={`research-status is-${info!.tone}`}>{stage === 0 && currentStage !== 0 ? '搜尋結果' : editing && view.status === 'ready' ? '調整來源' : info!.label}</span></div><p className="research-stage-counts">{view.candidates.length} 個候選來源</p></header>
      {view.error_code && <p className="tool-notice">{reasons[view.error_code] ?? '處理未完成，已保存取得的結果。'}</p>}
      {view.is_current_revision === false && !submitted && <p className="tool-notice">教材已更新，這次搜尋不能直接加入舊版本。請以目前教材重新搜尋。</p>}
      {stage === 2 && submitted && <div className={`tool-result${completed ? ' is-complete' : ''}`}>
        <span className="tool-result-icon"><Icon name={completed ? 'check' : 'process'} size={24} /></span>
        <div><h3>{completed ? '補充教材已建立' : view.run?.status === 'partial' ? '教材已部分更新' : view.run?.status === 'failed' ? '教材分析尚未完成' : view.run?.status === 'cancelled' ? '教材分析已取消' : '正在建立補充教材'}</h3>
          <p>{completed ? `已將 ${view.selection.length} 份來源加入教材，可查看更新後的知識地圖。` : view.run?.status === 'partial' ? '部分內容仍需確認，請查看教材分析結果。' : '已選來源會保留。可到教材分析頁查看進度或接續處理。'}</p></div>
        {view.run_id && <button className="primary-button" onClick={() => { writeRoute({ name: 'material-run', materialId, runId: view.run_id! }); }}>查看教材分析<Icon name="chevron-right" size={16} /></button>}
      </div>}
      {stage === 0 && view.status === 'searching' && <div className="tool-wait" role="status"><span className="loading-ring" /><p>正在尋找補充資料，搜尋結果會保存在這筆紀錄。</p></div>}
      {stage === 2 && canConfirm && <div className="tool-selection-heading"><h4>確認加入的來源 · {selected.length} 份</h4><button className="text-button" disabled={busy} onClick={() => navigate(1)}>調整來源</button></div>}
      {stage === 2 && submitted && <h4 className="tool-section-label">{completed ? '已加入的來源' : '已送出的來源'} · {view.selection.length} 份</h4>}
      {(displayed.length > 0 || view.status === 'selecting' && !!view.cursor) && <SourceCandidates compact listAction={view.status === 'selecting' && view.cursor ? <button className="text-button" type="button" disabled={busy} onClick={() => void perform('more')}>載入更多</button> : undefined} showAcquisitionDetails={false} candidates={displayed} selected={selected} disabled={working || !editable || stage !== 1} readOnly={readOnly} search={{value:sourceQuery,onChange:setSourceQuery}} inlineSearch onChange={ids => { setSelected(ids); setConfirmed(false); }} />}
      {!view.candidates.length && !researchActive.has(view.status) && <p className="tool-empty">沒有找到來源。可以換個主題或英文關鍵字再搜尋。</p>}
      {stage === 2 && canConfirm && <div className="research-confirm"><p>加入後會更新教材與知識地圖，<strong>無法撤回新增內容</strong>。</p><label><input type="checkbox" checked={confirmed} onChange={e => setConfirmed(e.target.checked)} />我了解加入後不可逆</label></div>}
      {!submitted && <footer className="tool-stage-footer">
        <div className="tool-footer-secondary">
          {researchActive.has(view.status) && <button className="secondary-button" disabled={busy} onClick={() => void perform('cancel')}>停止處理</button>}
          {['failed', 'cancelled'].includes(view.status) && <button className="secondary-button" disabled={busy} onClick={() => void perform('retry')}>重試未完成部分</button>}
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
