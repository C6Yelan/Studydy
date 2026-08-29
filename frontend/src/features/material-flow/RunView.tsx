import { useEffect, useState } from "react";

import { ApiClientError, errorMessage, type StudydyApiClient } from "../../api/client";
import type { MaterialProcessingRunView } from "../../api/contracts";
import { writeRoute, type AppRoute } from "../../app/routes";
import { Icon } from "../../ui/Icon";
import { StateView } from "../../ui/StateView";
import {
  automaticPollIntervalMs,
  forgetLatestMaterialRun,
  materialElapsedLabel,
  materialFailureMessage,
  materialProgressStageLabel,
  materialProgressStages,
  materialRunHasUsableMap,
  materialRunLabel,
} from "./material-flow";

export function RunView({ apiClient, route }: {
  apiClient: StudydyApiClient;
  route: Extract<AppRoute, { name: "material-run" }>;
}) {
  const [run, setRun] = useState<MaterialProcessingRunView | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    let cancelled = false;
    let timer: number | null = null;
    const poll = async () => {
      try {
        const next = await apiClient.getMaterialRun(route.runId);
        if (cancelled) return;
        if (next.material_id !== route.materialId) {
          forgetLatestMaterialRun();
          throw new Error("RUN_MATERIAL_MISMATCH");
        }
        setRun(next);
        setMessage(null);
        if (next.status === "pending" || next.status === "running") {
          timer = window.setTimeout(poll, automaticPollIntervalMs);
        }
      } catch (error) {
        if (!cancelled) {
          if (error instanceof ApiClientError && error.reasonCode === "RESOURCE_NOT_FOUND") {
            forgetLatestMaterialRun();
          }
          setMessage(errorMessage(error));
        }
      }
    };
    void poll();
    return () => {
      cancelled = true;
      if (timer !== null) window.clearTimeout(timer);
    };
  }, [apiClient, reload, route.materialId, route.runId]);

  useEffect(() => {
    if (!run || (run.status !== "pending" && run.status !== "running")) return;
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 1_000);
    return () => window.clearInterval(timer);
  }, [run?.run_id, run?.status]);

  if (message) return (
    <StateView
      action={(
        <div className="state-actions">
          <button className="primary-button" type="button" onClick={() => setReload((value) => value + 1)}><Icon name="refresh" />重新讀取</button>
          <button className="secondary-button" type="button" onClick={() => writeRoute({ name: "home" })}><Icon name="arrow-left" />返回上傳</button>
        </div>
      )}
      description={message}
      image="/assets/studydy/failure-confused.png"
      title="無法讀取處理狀態"
      tone="failure"
    />
  );

  if (!run) return (
    <section className="processing-page" aria-live="polite">
      <header className="processing-hero">
        <img src="/assets/studydy/processing-laptop.png" alt="" />
        <p className="eyebrow">Material Processing</p>
        <h1>正在讀取處理狀態</h1>
      </header>
    </section>
  );

  if (run.status === "pending" || run.status === "running") {
    const currentStageIndex = materialProgressStages.indexOf(run.progress_stage);
    const hasPageProgress = (
      run.progress_stage === "page_evidence"
      || run.progress_stage === "concept_generation"
    ) && run.total_pages !== null;
    return (
    <section className="processing-page">
      <header className="processing-hero">
        <img src="/assets/studydy/processing-laptop.png" alt="" />
        <p className="eyebrow">Material Processing</p>
        <h1>{materialRunLabel(run.status)}</h1>
        <p>Studydy 正在整理教材；正式內容會在來源與安全檢查通過後才發布。</p>
      </header>
      <div className="processing-grid">
        <section className="surface processing-card">
          <h2>目前狀態</h2>
          <div className="processing-status" aria-live="polite">
            {hasPageProgress ? (
              <progress
                aria-label={`${materialProgressStageLabel(run.progress_stage)} ${run.completed_pages} / ${run.total_pages} 頁`}
                className="current-stage-progress"
                max={run.total_pages!}
                value={run.completed_pages}
              />
            ) : (
              <div className="indeterminate-progress" aria-label={`${materialProgressStageLabel(run.progress_stage)}，進度估算中`}><span /></div>
            )}
            <strong>{materialProgressStageLabel(run.progress_stage)}</strong>
            {hasPageProgress && <p>目前階段已完成 {run.completed_pages} / {run.total_pages} 頁。</p>}
          </div>
          <dl className="processing-times">
            <div><dt>已經過</dt><dd>{materialElapsedLabel(run.created_at, now)}</dd></div>
            <div><dt>剩餘時間</dt><dd>估算中</dd></div>
            <div><dt>最近更新</dt><dd><time dateTime={run.updated_at}>{new Date(run.updated_at).toLocaleTimeString("zh-TW")}</time></dd></div>
          </dl>
          <p>你可以離開此頁，稍後返回同一處理作業；進度由後端保存。</p>
          {run.progress_stage === "queued" && <p>目前只有一個本機處理工作依序執行，排隊不代表處理失敗。</p>}
        </section>
        <section className="surface processing-card">
          <h2>實際處理階段</h2>
          <ol className="status-timeline">
            {materialProgressStages.slice(0, -1).map((stage, index) => (
              <li className={index < currentStageIndex ? "is-complete" : index === currentStageIndex ? "is-active" : undefined} key={stage}>
                <span><Icon name={index < currentStageIndex ? "check" : stage === "knowledge_map_generation" ? "map" : "process"} /></span>
                <div><strong>{materialProgressStageLabel(stage)}</strong><p>{index < currentStageIndex ? "此階段已完成。" : index === currentStageIndex ? "目前正在這個階段。" : "尚未開始。"}</p></div>
              </li>
            ))}
          </ol>
        </section>
      </div>
    </section>
    );
  }

  if (run.status === "failed") return (
    <section className="terminal-failure">
      <StateView
        action={<button className="secondary-button" type="button" onClick={() => writeRoute({ name: "home" })}><Icon name="arrow-left" />返回上傳</button>}
        description={materialFailureMessage(run.error_code ?? "MATERIAL_ANALYSIS_FAILED")}
        image="/assets/studydy/failure-confused.png"
        title="教材處理失敗"
        tone="failure"
      />
      <p className="failure-progress" role="status">
        最後安全進度：{materialProgressStageLabel(run.progress_stage)}
        {run.total_pages === null ? "" : `，${run.completed_pages} / ${run.total_pages} 頁`}
      </p>
      <code className="failure-code">{run.error_code}</code>
    </section>
  );

  if (!materialRunHasUsableMap(run)) return (
    <StateView
      action={<button className="primary-button" type="button" onClick={() => writeRoute({ name: "home" })}><Icon name="arrow-left" />改用其他教材</button>}
      description="這份教材沒有產生可安全顯示的概念，因此沒有發布知識地圖。請改用包含清楚教學內容的 PDF。"
      image="/assets/studydy/empty-disappointed.png"
      title="目前沒有可開啟的知識地圖"
      tone="empty"
    />
  );

  const binding = run.output_binding!;
  return (
    <section className="processing-page is-complete">
      <header className="processing-hero">
        <img src="/assets/studydy/success-jump.png" alt="" />
        <p className="eyebrow">Processing complete</p>
        <h1>{materialRunLabel(run.status)}</h1>
        <p>教材已完成來源與安全檢查，可以開啟知識地圖進行複核。</p>
      </header>
      <div className="processing-grid">
        <div className="processing-stack">
          <section className="surface processing-card material-result">
            <span className="file-kind"><Icon name="file" /></span>
            <div><h2>教材</h2><p>共處理 {binding.page_count} 頁</p></div>
            <span className="status-badge is-success"><Icon name="check" />處理完成</span>
          </section>
          <section className="surface processing-card result-summary">
            <h2>已發布內容</h2>
            <img src="/assets/studydy/processing-complete.png" alt="" />
            <ul>
              <li><Icon name="check" />可回查的概念與 Claim</li>
              <li><Icon name="check" />三種概念連結</li>
              <li><Icon name="check" />教材建議學習順序</li>
            </ul>
          </section>
        </div>
        <section className="surface processing-card">
          <h2>處理結果</h2>
          <div className="complete-progress"><strong>完成</strong><span><i /></span></div>
          <ol className="status-timeline">
            <li className="is-complete"><span><Icon name="check" /></span><div><strong>教材已接收</strong><p>檔案與處理作業完成綁定。</p></div></li>
            <li className="is-complete"><span><Icon name="check" /></span><div><strong>來源已保留</strong><p>每個重點都可回到原始 PDF 頁面。</p></div></li>
            <li className="is-complete"><span><Icon name="check" /></span><div><strong>知識地圖已發布</strong><p>{run.status === "partial" ? "部分頁面未安全納入，地圖會清楚標示。" : "可安全複核的內容已準備完成。"}</p></div></li>
          </ol>
        </section>
      </div>
      <div className="surface completion-bar">
        <span className="completion-icon"><Icon name="check" /></span>
        <div><strong>一切準備完成</strong><p>接著查看概念、連結、教材來源與建議順序。</p></div>
        <button className="primary-button" type="button" onClick={() => writeRoute({
          name: "knowledge-map",
          materialId: run.material_id,
          runId: run.run_id,
          mapRevision: binding.knowledge_map_revision,
        })}>開啟複核地圖<Icon name="chevron-right" /></button>
      </div>
    </section>
  );
}
