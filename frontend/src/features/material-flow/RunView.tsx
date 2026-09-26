import { useCallback, useEffect, useRef, useState } from "react";

import { ApiClientError, errorMessage, type StudydyApiClient } from "../../api/client";
import type { MaterialProcessingRunView, MaterialLibraryItem } from "../../api/contracts";
import { writeRoute, type AppRoute } from "../../app/routes";
import { Icon } from "../../ui/Icon";
import { StateView } from "../../ui/StateView";
import { MaterialRunStartControl } from "./MaterialRunStartControl";
import { MaterialRemoveControl } from "./MaterialRemoveControl";
import {
  automaticPollIntervalMs,
  materialElapsedLabel,
  materialFailureMessage,
  materialProgressStageLabel,
  materialProgressStages,
  materialCurrentStagePercent,
  materialOverallProgressPercent,
} from "./material-flow";

function activeRun(run: MaterialProcessingRunView | null): boolean {
  return run?.status === "pending" || run?.status === "running";
}

function canRequestDiscard(run: MaterialProcessingRunView | null): boolean {
  return (
    !!run &&
    activeRun(run) &&
    run.cancel_requested_at === null &&
    ["queued", "evidence", "semantics"].includes(run.progress_stage)
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

  const applyRun = useCallback((next: MaterialProcessingRunView) => {
    const previous = currentRun.current;
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

  if (message)
    return (
      <section className="processing-page task-page">
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
              <button
                className="secondary-button"
                type="button"
                onClick={() => writeRoute({ name: "materials" })}
              >
                返回教材庫
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
      <section className="processing-page task-page" aria-live="polite">
        <header className="processing-hero">
          <img src="/assets/studydy/processing-laptop.png" alt="" />
          <div>
            <p className="eyebrow">教材處理</p>
            <h1>正在讀取處理狀態</h1>
          </div>
        </header>
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
      <section className="processing-page task-page is-processing">
        <header className="processing-hero">
          <div>
            <p className="eyebrow">教材處理</p>
            <h1>
              {cancellationRequested
                ? "正在取消並刪除教材"
                : run.status === "pending"
                  ? "等待開始處理"
                  : "正在分析教材"}
            </h1>
            <p>
              {cancellationRequested
                ? "已收到刪除要求，會在目前進行中的步驟完成後安全停止並刪除教材。"
                : "Studydy 會依序整理教材內容、建立概念關係，並在完成後發布知識地圖。"}
            </p>
          </div>
        </header>
        <div className="processing-grid">
          <section className="surface processing-card">
            <ProcessingProgress run={run} now={now} />
            {!removing && canRequestDiscard(run) && (
              <div className="processing-cancel">
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
                    className="secondary-button processing-destructive"
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
          <ProcessingTimeline run={run} />
        </div>
      </section>
    );
  }

  // 已受理刪除後，即使發布流程結束，仍須維持刪除狀態。
  if (run.status === "cancelled" || (removing && !activeRun(run)))
    return (
      <section className="processing-page task-page is-cancelled">
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
          {run.total_pages !== null && `，已處理 ${run.completed_pages} / ${run.total_pages} 頁`}
        </p>
      </section>
    );

  if (run.status === "failed")
    return (
      <section className="processing-page task-page terminal-failure">
        <StateView
          action={
            <>
              <MaterialRunStartControl
                apiClient={apiClient}
                materialId={run.material_id}
                retryRun={{ runId: run.run_id, saved: !!run.analysis_saved }}
              />
              <button
                className="secondary-button"
                type="button"
                onClick={() => writeRoute({ name: "materials" })}
              >
                返回教材庫
              </button>
            </>
          }
          description={materialFailureMessage(run.error_code ?? "MATERIAL_ANALYSIS_FAILED")}
          image="/assets/studydy/failure-confused.png"
          title="教材處理失敗"
          tone="failure"
        />
        <p className="failure-progress" role="status">
          最後記錄進度：{materialProgressStageLabel(run.progress_stage)}
          {run.total_pages === null ? "" : `，${run.completed_pages} / ${run.total_pages} 頁`}
        </p>
        {run.analysis_saved && (
          <p role="status">
            已完成的分析批次保存在本機。使用相同來源與設定重試，會接續未完成的部分；若只剩地圖組裝，就不再呼叫模型。
          </p>
        )}
        {run.input_source_set_id && !run.analysis_saved && (
          <p role="status">這次沒有已保存的分析進度；重試將重新分析原來源。</p>
        )}
        {run.input_source_set_id && (
          <button
            className="text-button"
            onClick={() => writeRoute({ name: "material-sources", materialId: run.material_id })}
          >
            修改來源與重新分析
          </button>
        )}
        {run.error_code && (
          <details className="processing-technical">
            <summary>技術資訊</summary>
            <code>{run.error_code}</code>
          </details>
        )}
      </section>
    );

  const binding = run.output_binding!;
  const partial = run.status === "partial";
  return (
    <section className="processing-page task-page is-complete">
      <header className="processing-hero">
        <img src="/assets/studydy/success-jump.png" alt="" />
        <div>
          <p className="eyebrow">教材處理</p>
          <h1>教材整理完成</h1>
          <p>
            {partial
              ? "知識地圖已建立，可先查看已整理的內容；部分內容未完整整理。"
              : "知識地圖已準備完成，可以查看概念、關係、來源與建議學習順序。"}
          </p>
        </div>
      </header>
      <div className="processing-grid">
        <section className="surface processing-card processing-summary">
          <h2>處理摘要</h2>
          <div className="processing-summary-material">
            <span className="file-kind">
              <Icon name="file" />
            </span>
            <div>
              <h3>教材</h3>
              <p>共處理 {binding.page_count} 頁</p>
            </div>
          </div>
          <p className={`status-badge ${partial ? "is-partial" : "is-success"}`}>
            {!partial && <Icon name="check" />}
            {partial ? "部分結果可用" : "處理完成"}
          </p>
          <h3>可查看內容</h3>
          <ul>
            <li>
              <Icon name="check" />
              可回查的概念與學習重點
            </li>
            <li>
              <Icon name="check" />
              教材中的概念關係
            </li>
            <li>
              <Icon name="check" />
              教材建議學習順序
            </li>
          </ul>
        </section>
        <section className="surface processing-card">
          <h2>處理流程</h2>
          <ol className="status-timeline">
            {materialProgressStages.slice(0, -1).map((stage) => (
              <li className="is-complete" key={stage}>
                <span>
                  <Icon name="check" />
                </span>
                <div>
                  <strong>{materialProgressStageLabel(stage)}</strong>
                  <p>此階段已完成。</p>
                </div>
              </li>
            ))}
          </ol>
        </section>
      </div>
      <div className="surface completion-bar">
        <span className={`completion-icon${partial ? " is-partial" : ""}`}>
          <Icon name={partial ? "map" : "check"} />
        </span>
        <div>
          <strong>{partial ? "知識地圖已建立" : "知識地圖已準備完成"}</strong>
          <p>
            {partial
              ? "可以查看已整理的概念、關係與來源。"
              : "可以查看概念、關係、來源與建議學習順序。"}
          </p>
        </div>
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
    </section>
  );
}

function ProcessingProgress({ run, now }: { run: MaterialProcessingRunView; now: number }) {
  const currentPercent = materialCurrentStagePercent(run);
  const overallPercent = materialOverallProgressPercent(run);
  const stageLabel = materialProgressStageLabel(run.progress_stage);
  const stageActivity =
    run.progress_stage === "queued"
      ? "排隊中"
      : run.progress_stage === "publishing"
        ? "發布中"
        : "處理中";
  return (
    <>
      <div className="processing-status" aria-live="polite">
        <div className="progress-heading overall-heading">
          <h2>整體流程進度（估計）</h2>
          <strong>{overallPercent === null ? "—" : `${overallPercent}%`}</strong>
        </div>
        <progress
          className="processing-progress"
          max={100}
          value={overallPercent ?? undefined}
          aria-label={
            overallPercent === null
              ? "整體流程進度（估計），尚無可估計資料"
              : `整體流程進度（估計） ${overallPercent}%`
          }
        />
        <p className="progress-estimate-note">依處理階段與頁數估算，不代表剩餘時間。</p>
        <section className="processing-current">
          <h3>{currentPercent === null ? "目前狀態" : "本階段進度"}</h3>
          <div className="progress-heading">
            <strong
              className={`stage-label${currentPercent === null ? " stage-status-label" : ""}`}
            >
              {currentPercent === null && (
                <span className="processing-status-indicator" aria-hidden="true" />
              )}
              {stageLabel}
            </strong>
            <strong>{currentPercent === null ? stageActivity : `${currentPercent}%`}</strong>
          </div>
          {currentPercent === null ? (
            <p>
              {run.progress_stage === "queued"
                ? "正在等待本機處理資源，開始後會自動更新進度。"
                : run.progress_stage === "publishing"
                  ? "正在整理並發布可開啟的知識地圖。"
                  : "正在處理教材內容。"}
            </p>
          ) : (
            <progress
              className="processing-progress"
              max={100}
              value={currentPercent}
              aria-label={`本階段進度 ${currentPercent}%，已完成 ${run.completed_pages} / ${run.total_pages} 頁`}
            />
          )}
          {currentPercent !== null && (
            <p className="stage-pages">
              已完成 {run.completed_pages} / {run.total_pages} 頁
            </p>
          )}
        </section>
      </div>
      {run.source_names && (
        <div className="processing-sources">
          <h2>這次分析的來源（{run.source_names.length} 份）</h2>
          <ol>
            {run.source_names.map((name, index) => (
              <li key={index}>{name}</li>
            ))}
          </ol>
        </div>
      )}
      <dl className="processing-times">
        <div>
          <dt>已耗時</dt>
          <dd>{materialElapsedLabel(run.created_at, now)}</dd>
        </div>
        <div>
          <dt>最近更新</dt>
          <dd>
            <time dateTime={run.updated_at}>
              {new Date(run.updated_at).toLocaleTimeString("zh-TW")}
            </time>
          </dd>
        </div>
      </dl>
      <p className="processing-leave-note">進度會自動保存，可稍後從「我的教材」返回查看。</p>
    </>
  );
}

function ProcessingTimeline({ run }: { run: MaterialProcessingRunView }) {
  const currentStageIndex = materialProgressStages.indexOf(run.progress_stage);
  return (
    <section className="surface processing-card processing-timeline">
      <header className="processing-timeline-heading">
        <h2>處理流程</h2>
        <img src="/assets/studydy/processing-laptop.png" alt="" />
      </header>
      <ol className="status-timeline">
        {materialProgressStages.slice(0, -1).map((stage, index) => (
          <li
            aria-current={index === currentStageIndex ? "step" : undefined}
            className={
              index < currentStageIndex
                ? "is-complete"
                : index === currentStageIndex
                  ? "is-active"
                  : undefined
            }
            key={stage}
          >
            <span>
              <Icon
                name={
                  index < currentStageIndex ? "check" : stage === "semantics" ? "map" : "process"
                }
              />
            </span>
            <div>
              <strong>{materialProgressStageLabel(stage)}</strong>
              <p>
                {index < currentStageIndex
                  ? "已完成"
                  : index === currentStageIndex
                    ? "進行中"
                    : "尚未開始"}
              </p>
            </div>
          </li>
        ))}
      </ol>
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
  const savedSession =
    currentStructure &&
    material?.study_sessions.find(
      (item) => item.knowledge_structure_revision === currentStructure.knowledge_structure_revision,
    );
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
  const publishedActions = (
    <>
      {currentStructure && (
        <button
          className="primary-button"
          onClick={() =>
            writeRoute({
              name: "knowledge-map",
              materialId: run.material_id,
              runId: currentStructure.run_id,
              structureRevision: currentStructure.knowledge_structure_revision,
            })
          }
        >
          開啟目前地圖
        </button>
      )}
      {savedSession && (
        <button
          className="secondary-button"
          onClick={() =>
            writeRoute({
              name: "study-session",
              materialId: run.material_id,
              runId: savedSession.run_id,
              structureRevision: savedSession.knowledge_structure_revision,
              studySessionId: savedSession.study_session_id,
            })
          }
        >
          繼續學習
        </button>
      )}
    </>
  );
  const libraryActions = (
    <>
      <button
        className="text-button"
        onClick={() => writeRoute({ name: "material-sources", materialId: run.material_id })}
      >
        查看來源與新增教材
      </button>
      <button className="text-button" onClick={() => writeRoute({ name: "materials" })}>
        返回教材庫
      </button>
    </>
  );

  if (activeRun(run))
    return (
      <section className="processing-page task-page is-processing">
        <header className="processing-hero">
          <div>
            <p className="eyebrow">教材更新</p>
            <h1>{title}</h1>
            <p>Studydy 正在整理新增內容並更新知識地圖，期間仍可使用目前地圖與學習紀錄。</p>
          </div>
        </header>
        <div className="processing-grid">
          <section className="surface processing-card">
            <ProcessingProgress run={run} now={now} />
            <div className="state-actions">{publishedActions}</div>
            <div className="processing-cancel">
              {cancelling ? (
                <p role="status">
                  取消意圖已保存，後續結果不會套用。正在進行中的步驟可能需要一些時間結束。
                </p>
              ) : (
                <button className="secondary-button" disabled={busy} onClick={() => void cancel()}>
                  {busy ? "正在送出取消…" : "取消這次更新"}
                </button>
              )}
              {error && (
                <p className="form-error" role="alert">
                  {error}
                </p>
              )}
            </div>
          </section>
          <ProcessingTimeline run={run} />
        </div>
        <div className="state-actions">{libraryActions}</div>
      </section>
    );
  return (
    <section className="processing-page task-page">
      <header className="processing-hero">
        <img
          src={`/assets/studydy/${run.status === "failed" ? "failure-confused" : completed ? "success-jump" : "processing-laptop"}.png`}
          alt=""
        />
        <div>
          <p className="eyebrow">教材更新</p>
          <h1>{title}</h1>
          <p>
            {completed
              ? "新版地圖已可使用；未變知識點會依原有作答證據承接進度。"
              : "目前地圖與學習紀錄仍保留，更新完成後即可使用新版地圖。"}
          </p>
        </div>
      </header>
      <section className="surface processing-card">
        {run.status === "partial" && (
          <p role="status">部分內容建議對照來源確認，不影響使用更新後的地圖。</p>
        )}
        {run.source_names && (
          <>
            <h2>這次分析的來源</h2>
            <ul>
              {run.source_names.map((name, index) => (
                <li key={index}>{name}</li>
              ))}
            </ul>
          </>
        )}
        {run.error_code && (
          <p className="form-error" role="status">
            {run.error_code === "SOURCE_UPDATE_NEEDS_REVIEW"
              ? "先前流程因模型的複核提示而停止，這不代表教材已確認有矛盾。可接續已保存的結果完成更新。"
              : materialFailureMessage(run.error_code)}
          </p>
        )}
        {run.status === "failed" && run.analysis_saved && (
          <p role="status">
            已完成的分析批次保存在本機，使用相同來源與設定重試會接續未完成的部分。
          </p>
        )}
        {error && (
          <p className="form-error" role="alert">
            {error}
          </p>
        )}
        <div className="state-actions">
          {run.status === "failed" && (
            <MaterialRunStartControl
              apiClient={apiClient}
              materialId={run.material_id}
              retryRun={{ runId: run.run_id, saved: !!run.analysis_saved }}
            />
          )}
          {publishedActions}
          {libraryActions}
        </div>
      </section>
    </section>
  );
}
