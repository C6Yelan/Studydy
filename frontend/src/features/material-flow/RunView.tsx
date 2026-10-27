import { BackLink } from "../../ui/BackLink";
import { MaterialRunFailure } from "./MaterialRunFailure";
import { useCallback, useEffect, useRef, useState } from "react";

import { ApiClientError, errorMessage, isNotFound, type StudydyApiClient } from "../../api/client";
import { NotFoundView, materialNotFound } from "../../ui/NotFoundView";
import type { MaterialProcessingRunView, MaterialLibraryItem, SourceView } from "../../api/contracts";
import { writeRoute, type AppRoute } from "../../app/routes";
import { Icon } from "../../ui/Icon";
import { ProcessingProgress } from "./ProcessingProgress";
import { ProcessingTimeline } from "./ProcessingTimeline";
import { StateView } from "../../ui/StateView";
import { MaterialRemoveControl } from "./MaterialRemoveControl";
import { FlowHeader, LearningSteps, topicSteps, uploadSteps } from "./Flow";
import {
  automaticPollIntervalMs,
  materialProgressStageLabel,
  materialStageCountLabel,
  materialProgressStages,
} from "./material-flow";

function activeRun(run: MaterialProcessingRunView | null): boolean {
  return run?.status === "pending" || run?.status === "running";
}

// 步驟列依教材來源決定：上傳的教材與從主題建立的教材流程不同；來源混合或讀不到時不顯示步驟列。
function flowSteps(sources: SourceView[]): string[] | null {
  const origins = new Set(sources.map((source) => source.origin ?? "upload"));
  if (origins.size !== 1) return null;
  return origins.has("research") ? topicSteps : uploadSteps;
}

function canRequestDiscard(run: MaterialProcessingRunView | null): boolean {
  return (
    !!run &&
    activeRun(run) &&
    run.cancel_requested_at === null &&
    ["queued", "evidence", "semantics", "review", "indexing"].includes(run.progress_stage)
  );
}

export function RunView({
  apiClient,
  route,
}: {
  apiClient: StudydyApiClient;
  route: Extract<AppRoute, { name: "material-run" }>;
}) {
  const [run, setRun] = useState<MaterialProcessingRunView | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [reload, setReload] = useState(0);
  const [now, setNow] = useState(() => Date.now());

  const currentRun = useRef<MaterialProcessingRunView | null>(null);
  const discardAccepted = useRef(false);
  const [removing, setRemoving] = useState(false);
  const cancelVersion = useRef(0);
  const mounted = useRef(true);
  const cancelInFlight = useRef(false);
  const cancelButton = useRef<HTMLButtonElement>(null);
  const continueButton = useRef<HTMLButtonElement>(null);
  const [confirmingCancel, setConfirmingCancel] = useState(false);
  const [cancelBusy, setCancelBusy] = useState(false);
  const [cancelError, setCancelError] = useState<string | null>(null);
  const [cancelNotice, setCancelNotice] = useState<string | null>(null);
  const [steps, setSteps] = useState<string[] | null>(null);
  const [materialName,setMaterialName]=useState<string|null>(null);
  // 新版更新（base_revision）沒有建立流程的步驟列，不需要讀來源。
  const firstBuild = run ? !run.base_revision : null;

  const applyRun = useCallback((next: MaterialProcessingRunView) => {
    const previous = currentRun.current;
    if (previous && Date.parse(next.updated_at) < Date.parse(previous.updated_at)) return previous;
    // 已保存的取消意圖與結束狀態不可被較舊的 GET/POST 回應撤回。
    if (previous?.cancel_requested_at && next.cancel_requested_at === null) return previous;
    if (previous && !activeRun(previous) && activeRun(next)) return previous;
    currentRun.current = next;
    setRun(next);
    if (!next.base_revision && next.status === "running" && next.cancel_requested_at !== null) {
      discardAccepted.current = true;
      setRemoving(true);
    }
    if (next.cancel_requested_at !== null || !activeRun(next)) {
      setCancelError(null);
      setConfirmingCancel(false);
    }
    return next;
  }, []);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  useEffect(() => {
    if (confirmingCancel) continueButton.current?.focus();
    else cancelButton.current?.focus();
  }, [confirmingCancel]);

  useEffect(() => {
    let cancelled = false;
    let timer: number | null = null;
    const poll = async () => {
      if (currentRun.current && !activeRun(currentRun.current) && !discardAccepted.current) return;
      const version = cancelVersion.current;
      try {
        const next = await apiClient.getMaterialRun(route.runId);
        if (cancelled) return;
        if (next.material_id !== route.materialId || next.run_id !== route.runId)
          throw new Error("RUN_MATERIAL_MISMATCH");
        if (version === cancelVersion.current) {
          applyRun(next);
          setMessage(null);
        }
      } catch (error) {
        if (cancelled) return;
        if (
          discardAccepted.current &&
          error instanceof ApiClientError &&
          error.reasonCode === "RESOURCE_NOT_FOUND"
        ) {
          writeRoute({ name: "materials" });
          return;
        }
        if (version === cancelVersion.current) {
          // 第一次就讀不到：處理紀錄不存在或不屬於此帳號。
          if (!currentRun.current && (isNotFound(error) || (error instanceof Error && error.message === "RUN_MATERIAL_MISMATCH"))) {
            setNotFound(true);
            return;
          }
          setMessage(errorMessage(error));
          return;
        }
      }
      if (!cancelled && (activeRun(currentRun.current) || discardAccepted.current))
        timer = window.setTimeout(poll, automaticPollIntervalMs);
    };
    void poll();
    return () => {
      cancelled = true;
      if (timer !== null) window.clearTimeout(timer);
    };
  }, [apiClient, reload, route.materialId, route.runId, applyRun]);

  useEffect(() => {
    if (!firstBuild) return;
    let cancelled = false;
    setSteps(null);
    void apiClient.getMaterial(route.materialId).then(item=>{if(!cancelled)setMaterialName(item.display_name);},()=>undefined);
    void apiClient.getSources(route.materialId).then(
      (list) => { if (!cancelled) setSteps(flowSteps(list.sources)); },
      () => undefined,
    );
    return () => { cancelled = true; };
  }, [apiClient, route.materialId, firstBuild]);

  const sendDiscard = async () => {
    if (cancelInFlight.current || discardAccepted.current || !canRequestDiscard(currentRun.current))
      return;
    cancelInFlight.current = true;
    setCancelBusy(true);
    setCancelError(null);
    try {
      const next = await apiClient.discardMaterial(route.materialId);
      if (!mounted.current) return;
      cancelVersion.current++;
      discardAccepted.current = true;
      setRemoving(true);
      setMessage(null);
      setConfirmingCancel(false);
      if (next.state === "removed") writeRoute({ name: "materials" });
      else setReload((value) => value + 1);
    } catch (error) {
      if (mounted.current && !discardAccepted.current) {
        if (error instanceof ApiClientError && error.reasonCode === "MATERIAL_NOT_DISCARDABLE") {
          setConfirmingCancel(false);
          setCancelNotice(errorMessage(error));
        } else setCancelError(`無法送出刪除要求，請再試一次。${errorMessage(error)}`);
      }
    } finally {
      cancelInFlight.current = false;
      if (mounted.current) setCancelBusy(false);
    }
  };

  useEffect(() => {
    if (!activeRun(run)) return;
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 1_000);
    return () => window.clearInterval(timer);
  }, [run?.run_id, run?.status]);

  const backLink = <BackLink label={run?.base_revision || run?.output_binding ? "返回教材" : "回到我的教材"} route={run?.base_revision || run?.output_binding ? { name: "material", materialId: route.materialId } : { name: "materials" }} />;
  const back = <div className="run-material-return">{backLink}{materialName && <span>{materialName}</span>}</div>;

  if (notFound) return <NotFoundView title="找不到這次處理" description="這筆處理紀錄可能已被刪除，或不屬於目前登入的帳號。" back={materialNotFound.back} />;
  if (message)
    return (
      <section className="flow-page processing-page task-page">
        {back}
        <StateView
          action={
            <>
              <button
                className="primary-button"
                type="button"
                onClick={() => setReload((value) => value + 1)}
              >
                <Icon name="refresh" />
                重新讀取
              </button>
            </>
          }
          description={message}
          image="/assets/studydy/failure-confused.png"
          title="無法讀取處理狀態"
          tone="failure"
        />
      </section>
    );

  if (!run)
    return (
      <section className="flow-page processing-page task-page" aria-live="polite">
        <FlowHeader back={back} title="正在讀取處理狀態" image="/assets/studydy/processing-laptop.png" />
      </section>
    );

  if (run.base_revision)
    return (
      <RevisionRun
        apiClient={apiClient}
        run={run}
        now={now}
        onChange={(next) => {
          applyRun(next);
          setReload((value) => value + 1);
        }}
      />
    );

  if (activeRun(run)) {
    const cancellationRequested = removing || run.cancel_requested_at !== null;
    return (
      <section className="flow-page processing-page task-page is-processing">
        <FlowHeader back={back}
          title={cancellationRequested ? "正在取消並刪除教材" : run.status === "pending" ? "等待開始處理" : "正在分析教材"}
          description={cancellationRequested
            ? "已收到刪除要求，會在目前進行中的步驟完成後安全停止並刪除教材。"
            : "Studydy 會依序整理教材內容、建立概念關係，並在完成後發布知識地圖。"}
        />
        {steps && <LearningSteps labels={steps} current={steps.length - 1} />}
        <div className="processing-grid">
          <section className="surface processing-card">
            <ProcessingProgress run={run} now={now} />
            {!removing && canRequestDiscard(run) && (
              <div className="run-actions">
                {confirmingCancel ? (
                  <section
                    aria-labelledby="cancel-confirm-title"
                    className="cancel-confirmation"
                    onKeyDown={(event) => {
                      if (event.key === "Escape" && !cancelBusy) {
                        setConfirmingCancel(false);
                        setCancelError(null);
                      }
                    }}
                  >
                    <h3 id="cancel-confirm-title">確定要取消處理並刪除這份教材嗎？</h3>
                    <p>
                      Studydy 會安全停止目前的處理，並刪除原始
                      PDF、處理紀錄，以及既有的知識地圖、學習進度、題目與作答紀錄。此操作無法復原。
                    </p>
                    <div className="state-actions">
                      <button
                        ref={continueButton}
                        className="secondary-button"
                        type="button"
                        disabled={cancelBusy}
                        onClick={() => {
                          setConfirmingCancel(false);
                          setCancelError(null);
                        }}
                      >
                        繼續處理
                      </button>
                      <button
                        className="secondary-button cancel-confirm-button"
                        type="button"
                        disabled={cancelBusy}
                        onClick={() => void sendDiscard()}
                      >
                        確認刪除
                      </button>
                    </div>
                  </section>
                ) : (
                  <button
                    ref={cancelButton}
                    className="secondary-button danger-button processing-destructive"
                    type="button"
                    onClick={() => {
                      setCancelError(null);
                      setConfirmingCancel(true);
                    }}
                  >
                    取消並刪除教材
                  </button>
                )}
              </div>
            )}
            {cancelBusy && <p role="status">正在送出刪除要求…</p>}
            {cancelError && (
              <p className="form-error" role="alert">
                {cancelError}
              </p>
            )}
            {cancelNotice && <p role="status">{cancelNotice}</p>}
          </section>
          <ProcessingTimeline stages={materialProgressStages.slice(0, -1).map(stage => ({ label: materialProgressStageLabel(stage), icon: stage === "semantics" ? "map" : "process" }))} currentIndex={materialProgressStages.indexOf(run.progress_stage)} />
        </div>
      </section>
    );
  }

  // 已受理刪除後，即使發布流程結束，仍須維持刪除狀態。
  if (run.status === "cancelled" || (removing && !activeRun(run)))
    return (
      <section className="flow-page processing-page task-page is-cancelled">
        {back}
        {steps && <LearningSteps labels={steps} current={steps.length - 1} />}
        <StateView
          title={removing ? "正在刪除教材…" : "已取消教材處理"}
          description={
            removing
              ? "分析已停止，正在刪除 PDF 與處理紀錄。"
              : "這次分析已停止。若不再需要這份教材，可以刪除 PDF 與處理紀錄。"
          }
          icon="book"
          tone="empty"
          live
          action={
            !removing && (
              <MaterialRemoveControl
                apiClient={apiClient}
                materialId={route.materialId}
                onAccepted={(state) => {
                  if (state === "removed") writeRoute({ name: "materials" });
                  else {
                    discardAccepted.current = true;
                    setRemoving(true);
                    setReload((value) => value + 1);
                  }
                }}
              />
            )
          }
        />
        <p className="failure-progress">
          停止於：{materialProgressStageLabel(run.progress_stage)}
          {materialStageCountLabel(run) && `，${materialStageCountLabel(run)}`}
        </p>
      </section>
    );

  if (run.status === "failed") return <section className="flow-page processing-page task-page terminal-failure">
    <FlowHeader back={back} title="教材處理失敗"/>
    {steps && <LearningSteps labels={steps} current={steps.length - 1}/>}
    <div className="processing-grid">
      <MaterialRunFailure apiClient={apiClient} run={run}/>
      <ProcessingTimeline stages={materialProgressStages.slice(0,-1).map(stage=>({label:materialProgressStageLabel(stage),icon:stage==='semantics'?'map':'process'}))} currentIndex={materialProgressStages.indexOf(run.progress_stage)} activity="失敗"/>
    </div>
  </section>;

  const binding = run.output_binding!;
  return (
    <section className="flow-page processing-page task-page is-complete">
      <FlowHeader back={back}
        title="教材整理完成"
        description="知識地圖已準備完成，可以查看概念、關係、來源與建議學習順序。"
      />
      {steps && <LearningSteps labels={steps} current={steps.length} />}
      <section className="surface flow-result processing-summary" aria-labelledby="processing-result-title">
        <img src="/assets/studydy/success-jump.png" alt="" />
        <div className="flow-result-body">
          <p className="status-badge is-success"><Icon name="check"/>處理完成</p>
          <h2 id="processing-result-title">知識地圖已準備完成</h2>
          <p>共處理 {binding.page_count} 頁，可以查看概念、關係、來源與建議學習順序。</p>
          <p className="run-ai-notice">由 AI 整理，每個重點都可點開原文對照。</p>
          <ul className="flow-result-stats">
            <li><Icon name="check" size={16} />可回查的概念與學習重點</li>
            <li><Icon name="check" size={16} />教材中的概念關係</li>
            <li><Icon name="check" size={16} />教材建議學習順序</li>
          </ul>
          <div className="flow-result-actions">
            <button
              className="primary-button"
              type="button"
              onClick={() =>
                writeRoute({
                  name: "knowledge-map",
                  materialId: run.material_id,
                  runId: run.run_id,
                  structureRevision: binding.knowledge_structure_revision,
                })
              }
            >
              開啟知識地圖
              <Icon name="chevron-right" />
            </button>
          </div>
        </div>
      </section>
    </section>
  );
}

function RevisionRun({
  apiClient,
  run,
  now,
  onChange,
}: {
  apiClient: StudydyApiClient;
  run: MaterialProcessingRunView;
  now: number;
  onChange: (run: MaterialProcessingRunView) => void;
}) {
  const [material, setMaterial] = useState<MaterialLibraryItem | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let cancelled = false;
    void apiClient.getMaterial(run.material_id).then(
      (value) => {
        if (!cancelled) setMaterial(value);
      },
      (failure) => {
        if (!cancelled) setError(errorMessage(failure));
      },
    );
    return () => {
      cancelled = true;
    };
  }, [apiClient, run.material_id, run.status]);
  const currentStructure =
    material?.available_structures.find(
      (item) => item.knowledge_structure_revision === material.head_revision,
    ) ?? material?.available_structures[0];
  const cancelling = run.cancel_requested_at !== null && activeRun(run);
  const completed = run.status === "succeeded" || run.status === "partial";
  const title =
    run.status === "cancelled"
      ? "已取消這次更新"
      : cancelling
        ? "正在取消這次更新"
        : run.status === "failed"
          ? "這次更新未完成"
          : completed
            ? "教材更新完成"
            : "正在更新教材";
  const cancel = async () => {
    if (busy || !run.base_revision) return;
    setBusy(true);
    setError(null);
    try {
      onChange(await apiClient.cancelRevision(run.run_id, run.base_revision));
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setBusy(false);
    }
  };
  const map = completed && run.output_binding
    ? {name:"knowledge-map" as const,materialId:run.material_id,runId:run.run_id,structureRevision:run.output_binding.knowledge_structure_revision}
    : currentStructure ? {name:"knowledge-map" as const,materialId:run.material_id,runId:currentStructure.run_id,structureRevision:currentStructure.knowledge_structure_revision} : null;
  const mapAction = map && <button className="primary-button" onClick={()=>writeRoute(map)}>{completed?"開啟知識地圖":"先使用目前地圖"}</button>;
  return <section className={`flow-page processing-page task-page${activeRun(run)?" is-processing":""}`}>
    <FlowHeader back={<BackLink label={material?`返回${material.display_name}`:"返回教材"} route={{name:"material",materialId:run.material_id}}/>}
      title={title} description={run.status==='failed'?undefined:completed?"教材已更新，可以開啟知識地圖繼續使用。":"Studydy 正在整理新增內容，期間仍可使用目前地圖與學習紀錄。"}/>
    {error && <p className="form-error" role="alert">{error}</p>}
    <div className="processing-grid">
      {run.status==='failed' ? <MaterialRunFailure apiClient={apiClient} run={run}/> : <section className="surface processing-card">
        {completed ? <>
          <p className="status-badge is-success">處理完成</p>
          <p className="run-ai-notice">由 AI 整理，每個重點都可點開原文對照。</p>
          {run.source_names && <details className="processing-sources"><summary>本次分析 {run.source_names.length} 份來源</summary><ol>{run.source_names.map((name,index)=><li key={index}>{name}</li>)}</ol></details>}
        </> : <ProcessingProgress run={run} now={now}/>}
        <div className="run-actions">
          {activeRun(run) && (cancelling ? <p role="status">取消意圖已保存，後續結果不會套用。正在進行中的步驟可能需要一些時間結束。</p>
            : <button className="secondary-button" disabled={busy} onClick={()=>void cancel()}>{busy?"正在送出取消…":"取消這次更新"}</button>)}
          {mapAction}
        </div>
      </section>}
      <ProcessingTimeline stages={materialProgressStages.slice(0,-1).map(stage=>({label:materialProgressStageLabel(stage),icon:stage==='semantics'?'map':'process'}))} currentIndex={materialProgressStages.indexOf(run.progress_stage)} activity={run.status==='failed'?'失敗':run.status==='cancelled'?'已停止':cancelling?'取消中':'進行中'}/>
    </div>
  </section>;
}
